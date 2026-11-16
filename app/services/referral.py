"""Реферальная программа (этап 1) и заморозки серии. Вся логика здесь, SQL — в repo.

Поток: у человека с готовым профилем есть код и ссылка t.me/<бот>?start=r_<код>, три приглашения в запасе.
Незнакомец, открывший бот (или Mini App) по ссылке, получает доступ (access_grants), приглашение списывается.
Когда друг завершает настройку, он получает заморозку серии. Когда за первую неделю у него записи
в три разных дня, пригласивший получает заморозку и ещё одно приглашение (не больше чем за 10 друзей).
Заморозка закрывает один пропущенный вчера день, если позавчера запись была. В текстах нет весов и калорий:
механика одинакова для режима заботы.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.config import get_config
from app.storage import repo
from app.textutil import esc
from app.timeutils import local_today

log = logging.getLogger(__name__)

INITIAL_INVITES = 3            # стартовый запас приглашений (значение по умолчанию в миграции 031)
REWARD_CAP = 10                # награда пригласившему — максимум за столько активированных друзей
ACTIVE_MEAL_DAYS = 3           # разных дней с приёмами пищи для «активации»
ACTIVE_WINDOW_DAYS = 7         # окно: календарные дни, считая день настройки (день 0 … день 6)
CODE_LEN = 8
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"      # a-z0-9 без похожих знаков: i l o 0 1
START_PREFIX = "r_"
_PAYLOAD_RE = re.compile(r"^r_([a-z0-9]{4,32})$", re.IGNORECASE)

SHARE_TEXT = "Я веду дневник питания с этим ботом: мягко и без сложных подсчётов. Заходи по моей ссылке."
INVALID_TEXT = "Эта ссылка-приглашение не работает. Попроси друга прислать новую."
EXHAUSTED_TEXT = "Приглашения по этой ссылке закончились. Попроси друга прислать новую."
WELCOME_LINE = ("🎁 Тебя пригласил друг, доступ открыт. После настройки тебя ждёт в подарок заморозка серии: "
                "она закроет один пропущенный день.")
BONUS_NOTE = ("🎁 Заморозка серии в подарок за приглашение: она закроет один пропущенный день, "
              "и серия не прервётся.")

# Кто отправляет сообщения в чат бота: функция (tg_id, text). Без неё события просто не уведомляют.
_notifier = None


def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


def install(notifier=None) -> None:
    """Подключить к записи еды (repo.save_meal / repeat_meal) и задать способ отправки сообщений."""
    repo.set_meal_listener(on_meal_saved)
    set_notifier(notifier)


def uninstall() -> None:
    repo.set_meal_listener(None)
    set_notifier(None)


def bot_notifier(bot, loop: asyncio.AbstractEventLoop):
    """Отправка через aiogram-бота из любого потока; сбой доставки только в лог."""
    pending: set = set()

    async def _send(tg_id: int, text: str) -> None:
        try:
            await bot.send_message(tg_id, text)
        except Exception:  # noqa: BLE001 — недоставленное уведомление не должно ничего ломать
            log.warning("referral: не удалось отправить сообщение %s", tg_id, exc_info=True)

    def _start(tg_id: int, text: str) -> None:
        task = loop.create_task(_send(tg_id, text))
        pending.add(task)
        task.add_done_callback(pending.discard)

    def notify(tg_id: int, text: str) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _start(tg_id, text)
        else:
            loop.call_soon_threadsafe(_start, tg_id, text)

    return notify


def _tell(tg_id: int | None, text: str) -> None:
    fn = _notifier
    if fn is None or not tg_id:
        return
    try:
        fn(tg_id, text)
    except Exception:  # noqa: BLE001
        log.warning("referral: уведомление не ушло", exc_info=True)


# ------------------------------------------------------------------ код и ссылка

def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def ensure_code(db: sqlite3.Connection, user) -> str:
    """Код человека; создаётся при первом запросе. Занятый код пробуем заменить новым."""
    if user["ref_code"]:
        return user["ref_code"]
    for _ in range(20):
        if repo.set_ref_code(db, user["id"], new_code()):
            break
        fresh = repo.get_user_by_id(db, user["id"])
        if fresh and fresh["ref_code"]:                    # параллельный запрос успел раньше
            return fresh["ref_code"]
    fresh = repo.get_user_by_id(db, user["id"])
    if not fresh or not fresh["ref_code"]:
        raise RuntimeError("не удалось создать код приглашения")
    return fresh["ref_code"]


def parse_payload(arg: str | None) -> str | None:
    """«r_abcd2345» из /start или start_param → код в нижнем регистре; чужие параметры → None."""
    m = _PAYLOAD_RE.match((arg or "").strip())
    return m.group(1).lower() if m else None


def link_for(username: str, code: str) -> str:
    return f"https://t.me/{username}?start={START_PREFIX}{code}"


def share_url(link: str) -> str:
    return f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(SHARE_TEXT, safe='')}"


async def bot_username(bot) -> str:
    """Username бота из getMe (aiogram сам кеширует ответ). Пусто, если узнать не вышло."""
    if bot is None:
        return ""
    try:
        return (await bot.me()).username or ""
    except Exception:  # noqa: BLE001
        log.warning("referral: getMe не удался, ссылку собрать не из чего", exc_info=True)
        return ""


# ------------------------------------------------------------------ доступ

def has_access(db: sqlite3.Connection, tg_id: int, cfg=None) -> bool:
    """Допущен тот, кто проходит Config.is_allowed, или тот, кому выдан доступ по приглашению."""
    return (cfg or get_config()).is_allowed(tg_id) or repo.has_access_grant(db, tg_id)


def redeem(db: sqlite3.Connection, tg_id: int, code: str, first_name: str | None = None, cfg=None) -> str:
    """Обработать переход по ссылке. Статусы:
    granted — доступ выдан, приглашение списано; invalid — кода нет; exhausted — приглашения кончились;
    ignored — человек уже допущен, уже зарегистрирован, уже учтён или это его собственный код (молча)."""
    if has_access(db, tg_id, cfg) or repo.get_user_by_tg(db, tg_id) or repo.referral_of_invitee_tg(db, tg_id):
        return "ignored"
    inviter = repo.user_by_ref_code(db, code.strip().lower())
    if inviter is None or not inviter["onboarded"]:
        return "invalid"
    if inviter["tg_id"] == tg_id:
        return "ignored"
    if not repo.redeem_referral(db, inviter["id"], tg_id, first_name):
        return "ignored" if repo.referral_of_invitee_tg(db, tg_id) else "exhausted"
    return "granted"


REDEEM_TEXTS = {"invalid": INVALID_TEXT, "exhausted": EXHAUSTED_TEXT}


def welcome_line(db: sqlite3.Connection, tg_id: int) -> str | None:
    """Строка для /start друга, который пришёл по приглашению и ещё не настроил профиль."""
    ref = repo.referral_of_invitee_tg(db, tg_id)
    if ref is None or ref["onboarded_at"]:
        return None
    return WELCOME_LINE


# ------------------------------------------------------------------ бонус другу

def _utcnow() -> str:
    return repo.utcnow_iso()


def on_onboarded(db: sqlite3.Connection, tg_id: int, user_id: int) -> bool:
    """Друг завершил настройку: связать с пригласившим и подарить заморозку. Повторный вызов ничего не делает."""
    ref = repo.referral_of_invitee_tg(db, tg_id)
    if ref is None or ref["onboarded_at"]:
        return False
    try:
        if not repo.mark_referral_onboarded(db, ref["id"], user_id, ref["inviter_user_id"], _utcnow()):
            return False
        repo.add_reward(db, user_id, "freeze", 1, "invited_welcome", ref["id"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True


# ------------------------------------------------------------------ активация

def _local_date_of(utc_text: str, tz: str) -> date:
    moment = datetime.strptime(utc_text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(tz)).date()


def _friend_label(ref) -> str:
    name = (ref["invitee_name"] or "").strip()
    return f" ({esc(name)})" if name else ""


def check_activation(db: sqlite3.Connection, user_id: int) -> None:
    """Дёшево: сначала «есть ли неактивированный referral у этого человека». Награда выдаётся один раз."""
    ref = repo.referral_of_invitee_user(db, user_id)
    if ref is None or ref["activated_at"] or not ref["onboarded_at"]:
        return
    friend = repo.get_user_by_id(db, user_id)
    if friend is None:
        return
    first = _local_date_of(ref["onboarded_at"], friend["tz"])
    last = first + timedelta(days=ACTIVE_WINDOW_DAYS - 1)
    today = _local_date_of(_utcnow(), friend["tz"])
    if today < first or today > last:
        return
    last = min(last, today)
    if repo.meal_days_between(db, user_id, first.isoformat(), last.isoformat()) < ACTIVE_MEAL_DAYS:
        return
    inviter_id = ref["inviter_user_id"]
    inviter = repo.get_user_by_id(db, inviter_id) if inviter_id else None
    rewarded = False
    try:
        if not repo.mark_referral_activated(db, ref["id"], _utcnow()):
            db.rollback()
            return
        if inviter is not None and repo.rewarded_referrals(db, inviter["id"]) < REWARD_CAP:
            repo.add_reward(db, inviter["id"], "freeze", 1, "friend_activated", ref["id"])
            repo.add_reward(db, inviter["id"], "invite", 1, "friend_activated", ref["id"])
            repo.mark_referral_rewarded(db, ref["id"], _utcnow())
            rewarded = True
        db.commit()
    except Exception:
        db.rollback()
        raise
    if inviter is not None:
        fresh = repo.get_user_by_id(db, inviter["id"])
        head = f"🎉 У приглашённого тобой друга{_friend_label(ref)} уже сложилась привычка: записи в три разных дня за первую неделю."
        if rewarded:
            _tell(inviter["tg_id"], f"{head} Тебе +1 заморозка серии и +1 приглашение. "
                                    f"Заморозок: {fresh['streak_freezes']}, приглашений: {fresh['invites_left']}.")
        else:
            _tell(inviter["tg_id"], f"{head} Награды за приглашения уже выданы за {REWARD_CAP} друзей, это максимум. "
                                    "Спасибо, что зовёшь!")
    _tell(friend["tg_id"], "🌱 Три дня с записями за первую неделю: привычка уже складывается. Спасибо, что остаёшься!")


# ------------------------------------------------------------------ заморозка серии

def consume_freeze(db: sqlite3.Connection, user, local_date: str) -> int | None:
    """Лениво при записи еды «сегодня»: если вчера записи нет, позавчера была (запись или заморозка)
    и заморозки есть, закрыть вчера. Возвращает, сколько заморозок осталось, или None, если ничего не тратили.
    Два пропуска подряд заморозка не закрывает: серия рвётся, как раньше."""
    if not user["streak_freezes"] or user["streak_freezes"] <= 0:
        return None
    today = local_today(user["tz"])
    if local_date != today.isoformat():                      # запись задним числом серию не лечит
        return None
    yesterday, before = (today - timedelta(days=1)).isoformat(), (today - timedelta(days=2)).isoformat()
    if repo.day_has_record(db, user["id"], yesterday) or not repo.day_has_record(db, user["id"], before):
        return None
    if not repo.spend_freeze(db, user["id"], yesterday):
        return None
    return int(user["streak_freezes"]) - 1


def on_meal_saved(db: sqlite3.Connection, user_id: int, local_date: str) -> None:
    """Подписчик repo.save_meal / repeat_meal. Ошибки наружу не идут (их ловит repo.meal_saved)."""
    user = repo.get_user_by_id(db, user_id)
    if user is None:
        return
    left = consume_freeze(db, user, local_date)
    if left is not None:
        _tell(user["tg_id"], f"❄️ Серия сохранена: пропущенный вчера день закрыт заморозкой. Заморозок осталось: {left}.")
    check_activation(db, user_id)


# ------------------------------------------------------------------ сводка для экрана

def status_of(ref) -> str:
    return "activated" if ref["activated_at"] else "onboarded" if ref["onboarded_at"] else "joined"


def summary(db: sqlite3.Connection, user, username: str) -> dict:
    from app.services import motivation
    code = ensure_code(db, user)
    fresh = repo.get_user_by_id(db, user["id"])
    today = local_today(fresh["tz"])
    return {
        "link": link_for(username, code) if username else None,
        "invites_left": fresh["invites_left"],
        "freezes": fresh["streak_freezes"],
        "streak": motivation.logging_streak(db, fresh["id"], today),
        "invited": [{"name": r["invitee_name"] or "", "status": status_of(r)}
                    for r in repo.list_referrals(db, fresh["id"])],
        "rewards_cap_left": max(0, REWARD_CAP - repo.rewarded_referrals(db, fresh["id"])),
    }
