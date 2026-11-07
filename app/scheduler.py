"""Напоминания по локальному времени пользователя.

Один минутный job читает таблицу reminders. Поэтому новый пользователь и изменения
настроек начинают работать сразу, а не только после перезапуска процесса.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_config
from app.services import reminders as remsvc
from app.storage import repo
from app.timeutils import minutes_before

log = logging.getLogger(__name__)


def build_scheduler(db: sqlite3.Connection, bot) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=timezone.utc)
    sched.add_job(
        dispatch_due_reminders,
        CronTrigger(second=0, timezone=timezone.utc),
        args=(db, bot),
        id="reminder-dispatcher",
        name="reminder-dispatcher",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=50,
    )
    cfg_ = get_config()
    if (cfg_.keepalive_window or cfg_.keepalive_hours > 0) and (cfg_.webhook_base_url or os.environ.get("RENDER_EXTERNAL_URL")):
        sched.add_job(keepalive_ping, "interval", minutes=9, id="keepalive", name="keepalive",
                      coalesce=True, max_instances=1, misfire_grace_time=60)
    if cfg_.backup_in_process and not cfg_.turso_database_url:   # в облаке данные уже в Turso
        sched.add_job(
            run_daily_backup,
            CronTrigger(hour=3, minute=30, timezone=timezone.utc),
            args=(),
            id="daily-backup",
            name="daily-backup",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
    return sched


def in_keepalive_window(window: str, tz_name: str, now_utc: datetime | None = None) -> bool:
    """«08:00-23:30» по локальному времени; окно через полночь («22:00-02:00») тоже понимает."""
    try:
        start, end = (part.strip() for part in window.split("-", 1))
        now = (now_utc or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name)).strftime("%H:%M")
        start, end = start.zfill(5), end.zfill(5)
    except Exception:  # noqa: BLE001
        return False
    return start <= now < end if start <= end else (now >= start or now < end)


_last_activity: float | None = None   # time.monotonic() последнего реального запроса


def touch_activity() -> None:
    """Отметить, что кто-то пользуется ботом (его вызывает веб-сервер на каждый запрос, кроме /healthz)."""
    global _last_activity
    _last_activity = time.monotonic()


def recently_active(hours: float, now: float | None = None) -> bool:
    """Была ли активность за последние `hours` часов: окно скользит, каждое действие продлевает его."""
    if hours <= 0 or _last_activity is None:
        return False
    return (now if now is not None else time.monotonic()) - _last_activity < hours * 3600


async def keepalive_ping() -> None:
    """Запрос на собственный публичный адрес: Render считает его входящим трафиком и не усыпляет сервис."""
    import httpx
    cfg = get_config()
    if not (recently_active(cfg.keepalive_hours)
            or (cfg.keepalive_window and in_keepalive_window(cfg.keepalive_window, cfg.keepalive_tz))):
        return
    base = (cfg.webhook_base_url or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.get(base + "/healthz")
    except Exception:  # noqa: BLE001
        log.warning("keepalive: пинг не прошёл", exc_info=True)


async def run_daily_backup() -> None:
    """Ежедневная копия БД и фото; блокирующая работа уходит в поток, чтобы не стопорить бота."""
    from app.backup import run_backup
    try:
        path, ok, photos = await asyncio.to_thread(run_backup)
        log.info("бэкап %s проверка=%s новых фото=%s", path, "ok" if ok else "FAIL", photos)
    except Exception:  # noqa: BLE001
        log.exception("ежедневный бэкап не удался")


async def dispatch_due_reminders(db: sqlite3.Connection, bot,
                                 now_utc: datetime | None = None) -> None:
    """Отправить активные напоминания, наступившие в текущую локальную минуту.

    Пользователи обрабатываются параллельно: один долгий вызов LLM не задерживает остальных.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    if getattr(db, "cloud", False):
        # Облачная БД отвечает секундами, а запросы синхронные и стопорят весь event loop (и мини-апп).
        # Поэтому «есть ли что слать» выясняем в потоке на отдельном соединении; в большинстве
        # минут ответ «нет», и общее соединение вообще не трогаем.
        try:
            if not await asyncio.to_thread(_has_due_work, db, now_utc):
                return
        except Exception:  # noqa: BLE001 — при сбое проверки работаем как раньше
            log.warning("предпроверка напоминаний не удалась", exc_info=True)

    users = db.execute("SELECT * FROM users WHERE onboarded=1").fetchall()
    # Сбой у одного человека не должен останавливать остальных и продолжения эпизодов.
    results = await asyncio.gather(*(_dispatch_user_safe(db, bot, user, now_utc) for user in users),
                                   return_exceptions=True)
    for user, res in zip(users, results):
        if isinstance(res, BaseException):
            log.error("напоминания: сбой у user=%s", user["id"], exc_info=res)
    try:
        await dispatch_craving_followups(db, bot)
    except Exception:  # noqa: BLE001
        log.exception("продолжения эпизодов тяги не обработаны")


def _has_due_work(db, now_utc: datetime) -> bool:
    """Блокирующая проверка (вызывать в потоке): есть ли напоминание, правило или эпизод к отправке."""
    conn = db.spawn() if hasattr(db, "spawn") else db
    try:
        repo.dismiss_stale_cravings(conn)
        if repo.due_craving_followups(conn):
            return True
        custom: dict[int, list] = {}
        for r in conn.execute("SELECT * FROM user_reminders WHERE enabled=1").fetchall():
            custom.setdefault(r["user_id"], []).append(r)
        for user in conn.execute("SELECT * FROM users WHERE onboarded=1").fetchall():
            try:
                now = now_utc.astimezone(ZoneInfo(user["tz"] or "Europe/Minsk"))
            except Exception:  # noqa: BLE001 — как и в _dispatch_user: плохой tz пропускаем
                continue
            marker = now.strftime("%Y-%m-%d %H:%M")
            if any(remsvc.row_is_due(r, now) for r in custom.get(user["id"], ())):
                return True
            for r in conn.execute("SELECT * FROM reminders WHERE user_id=? AND enabled=1",
                                  (user["id"],)).fetchall():
                time_ = (minutes_before(user["eating_cutoff"], 15)
                         if r["kind"] == "kitchen_close" else r["local_time"])
                if _is_due(r["kind"], time_, now, r["days"]) and r["last_sent_at"] != marker:
                    return True
            if repo.due_rules(conn, user["id"], now.strftime("%H:%M"), now.strftime("%Y-%m-%d")):
                return True
        return False
    finally:
        if conn is not db:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                log.debug("close spawn", exc_info=True)


async def _dispatch_user_safe(db: sqlite3.Connection, bot, user, now_utc: datetime) -> None:
    try:
        await _dispatch_user(db, bot, user, now_utc)
    except Exception:  # noqa: BLE001
        log.exception("напоминания: ошибка у user=%s", user["id"])
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            log.debug("rollback после сбоя пользователя не удался", exc_info=True)


async def _dispatch_user(db: sqlite3.Connection, bot, user, now_utc: datetime) -> None:
    try:
        now = now_utc.astimezone(ZoneInfo(user["tz"] or "Europe/Minsk"))
    except Exception:  # некорректные старые данные не должны останавливать всех
        log.error("некорректный timezone пользователя id=%s: %r", user["id"], user["tz"])
        return
    reminders = db.execute(
        "SELECT * FROM reminders WHERE user_id=? AND enabled=1", (user["id"],)
    ).fetchall()
    for reminder in reminders:
        reminder_time = (minutes_before(user["eating_cutoff"], 15)
                         if reminder["kind"] == "kitchen_close" else reminder["local_time"])
        if not _is_due(reminder["kind"], reminder_time, now, reminder["days"]):
            continue
        marker = now.strftime("%Y-%m-%d %H:%M")
        if reminder["last_sent_at"] == marker:
            continue
        try:
            await _send_reminder(db, bot, user, reminder["kind"], now)
        except Exception:  # noqa: BLE001 - логируем, не блокируя других пользователей
            log.exception("не отправлено напоминание user=%s kind=%s",
                          user["id"], reminder["kind"])
            continue
        db.execute("UPDATE reminders SET last_sent_at=? WHERE id=?", (marker, reminder["id"]))
        db.commit()
    await _dispatch_custom(db, bot, user, now)
    await _dispatch_rules(db, bot, user, now)


async def _dispatch_custom(db: sqlite3.Connection, bot, user, now: datetime) -> None:
    """Свои напоминания человека: сбой одного не мешает остальным."""
    marker = now.strftime("%Y-%m-%d %H:%M")
    rows = db.execute("SELECT * FROM user_reminders WHERE user_id=? AND enabled=1", (user["id"],)).fetchall()
    for r in rows:
        if not remsvc.row_is_due(r, now):
            continue
        try:
            await _send_custom(db, bot, user, r, now)
        except Exception:  # noqa: BLE001
            log.exception("не отправлено своё напоминание user=%s id=%s", user["id"], r["id"])
            continue
        # метка и при пропуске: в эту же минуту второй раз не пробуем
        db.execute("UPDATE user_reminders SET last_sent_at=? WHERE id=?", (marker, r["id"]))
        db.commit()


async def _send_custom(db: sqlite3.Connection, bot, user, r, now: datetime) -> None:
    tpl = remsvc.TEMPLATES.get(r["template"])
    if tpl is not None:
        if user["care_mode"] and tpl.get("care_hidden"):
            return                                    # осторожный режим: без напоминаний о весе
        if remsvc.should_skip(db, user, tpl.get("skip"), now):
            return                                    # уже сделано сегодня
    text = html.escape((r["text"] or (tpl["text"] if tpl else "") or r["title"]).strip(), quote=False)
    if tpl is not None and tpl.get("skip") == "water":
        from app.services import water as watersvc
        total, goal = repo.day_water_ml(db, user["id"], now.strftime("%Y-%m-%d")), watersvc.goal_for(db, user)
        text += f"\n\nСегодня выпито {total} из {goal} мл."
    screen = tpl.get("screen") if tpl else None
    if screen:
        await _app_pointer(bot, user["tg_id"], text, screen, "📱 " + tpl.get("button", remsvc.DEFAULT_BUTTON))
    else:
        await bot.send_message(user["tg_id"], text)


async def _dispatch_rules(db: sqlite3.Connection, bot, user, now: datetime) -> None:
    """Правила «если-то» с временем напоминания: «в 21:00 поужинал(а) → почищу зубы…»."""
    from app.services import plan as plansvc
    today = now.strftime("%Y-%m-%d")
    for rule in repo.due_rules(db, user["id"], now.strftime("%H:%M"), today):
        try:
            await bot.send_message(user["tg_id"], "🧭 Твоё правило на этот час:\n" +
                                   plansvc.format_rule(rule))
        except Exception:  # noqa: BLE001
            log.exception("не отправлено правило id=%s", rule["id"])
            continue
        repo.mark_rule_sent(db, rule["id"], today)


async def dispatch_craving_followups(db: sqlite3.Connection, bot) -> None:
    """Через несколько минут после реплики бота спросить, как дела; старые открытые эпизоды закрыть."""
    from app.services import craving
    repo.dismiss_stale_cravings(db)
    for event in repo.due_craving_followups(db):
        try:
            user = repo.get_user_by_tg(db, event["tg_id"])
            text = (await craving.followup_message(db, user, event) if user
                    else craving.followup_text(event))
            await bot.send_message(event["tg_id"], text)
        except Exception:  # noqa: BLE001
            log.exception("не отправлено продолжение эпизода id=%s", event["id"])
        repo.update_craving(db, event["id"], followup_sent=1)


def _is_due(kind: str, local_time: str, now: datetime, days: str | None = None) -> bool:
    if local_time != now.strftime("%H:%M"):
        return False
    if days and str(now.weekday()) not in days:       # напоминание только в выбранные дни
        return False
    return kind != "weekly" or now.weekday() == 6


async def _app_pointer(bot, tg_id: int, text: str, screen: str | None = None,
                       button: str = "📱 Открыть приложение") -> None:
    """Короткий пуш без цифр: сами отчёты читаются в Mini App, кнопка ведёт сразу на экран."""
    from app.routers import menu
    await bot.send_message(tg_id, text, reply_markup=menu.main_kb(screen, button))


async def _send_reminder(db: sqlite3.Connection, bot, user, kind: str,
                         now: datetime) -> None:
    uid, tg_id = user["id"], user["tg_id"]
    local_day = now.date()
    if kind == "morning":
        # Весы синхронизируем до сводки, но их сбой не должен её ломать.
        from app.config import get_config
        from app.services import scale
        if (get_config().picooc_user and get_config().picooc_password
                and scale.is_picooc_owner(user)):
            try:
                await scale.sync_picooc(db, user, days=3)
            except Exception:  # noqa: BLE001
                log.warning("утренний синк Picooc не удался", exc_info=True)
        # сводка утра в приложении («Анализ»); в чат — короткое приглашение, без цифр и калорий
        await _app_pointer(bot, tg_id, "☀️ Доброе утро! План на день уже ждёт в приложении.",
                           "analysis", "☀️ Открыть план дня")
    elif kind == "midday":
        from app.storage import repo
        day = repo.day_totals(db, uid, local_day.isoformat())
        if day["meals"] == 0:
            await bot.send_message(tg_id, "🍽 Пока пусто в дневнике. Запиши завтрак/обед — "
                                             "хотя бы текстом, это 10 секунд.")
    elif kind == "kitchen_close":
        await bot.send_message(
            tg_id,
            f"🍽 Кухня закрывается через 15 минут ({user['eating_cutoff']}). "
            "Если голодно — план Б: кефир/творог/овощи на 150–200 ккал. "
            "А если вечером потянет переесть, просто напиши мне, поговорим.",
        )
    elif kind == "evening":
        await _app_pointer(bot, tg_id, "🌙 Итог дня готов: что получилось и что поправить завтра.",
                           "analysis", "🌙 Открыть итог дня")
    elif kind == "weigh":
        from app.storage import repo
        if user["care_mode"]:
            return                                    # осторожный режим: без напоминаний о весе
        if repo.weight_series(db, uid, local_day.isoformat(), local_day.isoformat()):
            return                                    # уже взвесился сегодня
        await bot.send_message(
            tg_id,
            "⚖️ Утреннее взвешивание: после туалета, до еды и воды, в одно и то же время. "
            "Пришли скриншот весов. Одна цифра ничего не значит, смотрим на тренд.")
    elif kind == "water":
        from app.services import water as watersvc
        from app.storage import repo
        total = repo.day_water_ml(db, uid, local_day.isoformat())
        goal = watersvc.goal_for(db, user)
        if total < goal // 2:
            await bot.send_message(
                tg_id, f"💧 Воды сегодня пока {total} мл из ориентира {goal}. "
                       "Выпей стакан и напиши «выпил стакан воды».")
    elif kind == "weekly":
        await _app_pointer(bot, tg_id, "📅 Неделя закончилась: итог и цель на новую неделю "
                                        "готовы в приложении.", "analysis", "📅 Открыть итог недели")
    else:
        log.warning("неизвестный тип напоминания: %s", kind)
