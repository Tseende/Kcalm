"""Поддержка при тяге к еде: эпизод → живой разговор по данным → итог → память.

Безопасность и цифры детерминированы: тревожные фразы получают только текст со ссылкой на
специалиста, а калорийность считает код. LLM ведёт сам разговор (начало, отклик на кнопку,
свободный текст, проверку через 15 минут); каждый её ответ проходит проверку (числа из
контекста, нет стыда и компенсации), иначе показывается запасной текст. Без сети бот
работает на запасных текстах — ночью это важно.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import get_config
from app.domain import craving as dom
from app.domain.matching import find_food
from app.llm import client as llm
from app.services import food as foodsvc
from app.services.coach import check_numbers
from app.services import plan as plansvc
from app.services import water as watersvc
from app.storage import repo
from app.textutil import esc

log = logging.getLogger(__name__)

FOLLOWUP_MIN = 15
ACTION_NUMBERS = [5, 10, 15, 20, 30, 60]       # минуты в советах — не «статистика»
OUTCOMES = ("resisted", "ate_planned", "overate")

# перекусы на ≈100–220 ккал с белком; ккал и белок берутся из базы, не из головы модели
SNACKS = [
    ("творог 5%", 150), ("кефир 1%", 250), ("яйцо куриное варёное", 110),
    ("йогурт натуральный", 150), ("куриная грудка варёная", 100),
]


def _g(user, male: str, female: str) -> str:
    return female if user["sex"] == "female" else male


# ------------------------------------------------------------------------- факты

def build_facts(db: sqlite3.Connection, user, now_local: datetime | None = None) -> dom.Facts:
    uid = user["id"]
    now_local = now_local or foodsvc.local_now(user["tz"])
    today = now_local.strftime("%Y-%m-%d")
    # после полуночи «день» по смыслу ещё вчерашний: еда ночью относится к уже прожитому дню
    day_for_food = today
    if now_local.strftime("%H:%M") < dom.NIGHT_END:
        day_for_food = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
    totals = repo.day_totals(db, uid, day_for_food)
    tgt = repo.current_target(db, uid)
    last = repo.last_meal_time(db, uid, day_for_food)
    hours = None
    if last:
        last_dt = datetime.strptime(f"{day_for_food} {last}", "%Y-%m-%d %H:%M")
        gap = (now_local.replace(tzinfo=None) - last_dt).total_seconds() / 3600
        hours = gap if gap >= 0 else None
    sleep = db.execute("SELECT duration_min FROM sleep_sessions WHERE user_id=? AND wake_date=?",
                       (uid, today)).fetchone()
    week_from = (now_local - timedelta(days=7)).strftime("%Y-%m-%d")
    two_weeks = (now_local - timedelta(days=14)).strftime("%Y-%m-%d")
    events7 = repo.cravings_range(db, uid, week_from, today)
    events14 = repo.cravings_range(db, uid, two_weeks, today)
    return dom.Facts(
        local_time=now_local.strftime("%H:%M"),
        kcal_so_far=int(totals["kcal"]),
        kcal_target=int(tgt["kcal_target"]) if tgt else 0,
        protein_so_far=float(totals["protein"]),
        protein_target=int(tgt["protein_g"] or 0) if tgt else 0,
        meals_today=int(totals["meals"]),
        hours_since_last_meal=hours,
        sleep_min=int(sleep["duration_min"]) if sleep and sleep["duration_min"] else None,
        water_ml=repo.day_water_ml(db, uid, day_for_food),
        water_goal_ml=watersvc.goal_for(db, user),
        craving_count_7d=len(events7),
        overate_count_14d=sum(1 for e in events14 if e["outcome"] == "overate"),
    )


def diary_maybe_incomplete(f: dom.Facts) -> bool:
    """Записано подозрительно мало для этого времени: возможно, не всё внесено в дневник.
    Тогда поддержка уточняет, а не объявляет «настоящий голод» по неполным данным."""
    late = f.local_time >= "15:00" or f.local_time < dom.NIGHT_END
    return bool(f.kcal_target) and late and f.kcal_so_far < 0.35 * f.kcal_target


def snack_lines(db: sqlite3.Connection, user_id: int, limit: int = 3) -> list[str]:
    lines = []
    for query, grams in SNACKS:
        ref = find_food(db, "", query, user_id=user_id)
        if not ref:
            continue
        kcal = round(ref.kcal_100g * grams / 100 / 10) * 10
        protein = round(ref.protein_100g * grams / 100)
        if kcal > 230:
            continue
        lines.append(f"• {esc(ref.name_ru)} {grams} г ≈ {kcal} ккал, белка {protein} г")
        if len(lines) >= limit:
            break
    return lines


# ---------------------------------------------------------------------- клавиатуры

def triage_keyboard(event_id: int, user=None) -> InlineKeyboardMarkup:
    def b(text: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=data)
    overate = _g(user, "😔 Уже переел", "😔 Уже переела") if user else "😔 Уже переел(а)"
    tired = _g(user, "😴 Устал", "😴 Устала") if user else "😴 Усталость"
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("🥣 Голод", f"cr:cat:{event_id}:hunger"),
         b("😩 Эмоции", f"cr:cat:{event_id}:emotion"),
         b("🔁 Привычка", f"cr:cat:{event_id}:habit")],
        [b(tired, f"cr:cat:{event_id}:tired"),
         b(overate, f"cr:cat:{event_id}:overate"),
         b(f"⏱ {FOLLOWUP_MIN} мин", f"cr:wait:{event_id}")],
    ])


def outcome_keyboard(user, event_id: int, with_wait: bool = True) -> InlineKeyboardMarkup:
    def b(text: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=data)
    rows = [[b(_g(user, "✅ Справился", "✅ Справилась"), f"cr:out:{event_id}:resisted"),
             b(_g(user, "🍽 Поел спокойно", "🍽 Поела спокойно"), f"cr:out:{event_id}:ate_planned")],
            [b(_g(user, "😔 Переел", "😔 Переела"), f"cr:out:{event_id}:overate")]]
    if with_wait:
        rows[1].append(b(f"⏱ +{FOLLOWUP_MIN} мин", f"cr:wait:{event_id}"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ----------------------------------------------------------------------- старт

def _create_episode(db: sqlite3.Connection, user, trigger_text: str | None,
                    mode: str = "craving") -> tuple[int, dom.Facts, str]:
    now_local = foodsvc.local_now(user["tz"])
    facts = build_facts(db, user, now_local)
    risk = dom.risk_level(trigger_text or "")
    event_id = repo.create_craving(
        db, user["id"], now_local.strftime("%Y-%m-%d"), facts.local_time, trigger_text,
        facts.kcal_so_far, facts.kcal_target, risk_flag=int(risk != "none"), mode=mode)
    if trigger_text:
        repo.add_craving_message(db, event_id, "user", trigger_text)
    if risk != "none":
        repo.close_craving(db, event_id, "dismissed")
    return event_id, facts, risk


def _risk_text(risk: str) -> str:
    return esc(dom.SELF_HARM_TEXT if risk == "self_harm" else dom.PURGING_TEXT)


def _opening_fallback(facts: dom.Facts) -> str:
    lines = ["🌙 Я рядом. Тяга к еде — это нормально, а не провал и не слабость. "
             "Давай спокойно разберёмся за пару минут."]
    notes = dom.insights(facts)
    if notes:
        lines += ["", "📊 Что вижу по твоему дню:"] + [f"• {esc(n)}" for n in notes[:3]]
    budget = dom.budget_line(facts)
    if budget:
        lines += ["", esc(budget)]
    lines += ["", "Что ближе всего к тому, что происходит сейчас?"]
    return "\n".join(lines)


def start_episode(db: sqlite3.Connection, user, trigger_text: str | None
                  ) -> tuple[int, str, InlineKeyboardMarkup | None]:
    """Создать эпизод без сети и вернуть (id, текст, клавиатура). Тяжёлые сигналы получают
    только текст безопасности, без диетических советов."""
    event_id, facts, risk = _create_episode(db, user, trigger_text)
    if risk != "none":
        return event_id, _risk_text(risk), None
    text = _opening_fallback(facts)
    repo.add_craving_message(db, event_id, "assistant", text)
    return event_id, text, triage_keyboard(event_id, user)


async def open_episode(db: sqlite3.Connection, user, trigger_text: str | None,
                       mode: str = "craving") -> tuple[int, str, InlineKeyboardMarkup | None]:
    """Начало живого разговора: короткая реплика модели и один вопрос, кнопки как быстрые
    ответы. Нет сети или ответ не прошёл проверку — детерминированное начало."""
    event_id, facts, risk = _create_episode(db, user, trigger_text, mode)
    if risk != "none":
        return event_id, _risk_text(risk), None
    event = repo.get_craving(db, user["id"], event_id)
    kb = triage_keyboard(event_id, user) if mode == "craving" else None
    data = await _ask_model(db, user, event, trigger_text or "", "opening")
    if data:
        repo.add_craving_message(db, event_id, "assistant", data["reply"])
        if data["outcome"] == "overate":
            repo.update_craving(db, event_id, category="overate")
        extra = await _apply_turn(db, user, event_id, data, allow_outcome=False)
        return event_id, esc(data["reply"]) + extra, kb
    if mode == "talk":
        text = "Я здесь 🤍 Рассказывай, что на душе: про еду, про день, про что угодно."
    else:
        text = _opening_fallback(facts)
    repo.add_craving_message(db, event_id, "assistant", text)
    return event_id, text, kb


# ------------------------------------------------------------ планы по категориям

def category_plan(db: sqlite3.Connection, user, event: sqlite3.Row, category: str) -> str:
    """Детерминированный план по причине — запасной путь, когда модель недоступна."""
    repo.update_craving(db, event["id"], category=category)
    facts = build_facts(db, user)
    ate = _g(user, "съел", "съела")
    parts: list[str] = []
    if category == "hunger":
        parts.append("🥣 Тогда это, скорее всего, настоящий голод, и с ним не нужно воевать. "
                     "Поешь спокойно, на тарелку и сидя, лучше с белком:")
        snacks = snack_lines(db, user["id"])
        parts += snacks or ["• Творог, кефир, яйцо или кусок варёной курицы"]
        parts.append("Потом стакан воды и чистка зубов: это сигнал «кухня закрыта».")
        budget = dom.budget_line(facts)
        if budget:
            parts.append(esc(budget))
    elif category == "emotion":
        parts.append("😩 Еда — самый быстрый способ успокоиться, поэтому тянет. Это понятно. "
                     f"Дай себе {FOLLOWUP_MIN} минут и попробуй: стакан воды или тёплый чай, "
                     "умыться прохладной водой, 10 медленных вдохов (вдох на 4, выдох на 6) "
                     "или выйти из кухни на пару минут.")
        parts.append("Если хочется — напиши мне, что именно случилось. А если после этого всё ещё "
                     "хочется есть, это не провал: возьми порцию на тарелку, а не из пачки.")
    elif category == "habit":
        parts.append("🔁 Похоже на привычку или скуку: хочется «чего-нибудь», а не еды. "
                     f"Попробуй на {FOLLOWUP_MIN} минут сменить обстановку: выключи свет на кухне, "
                     "почисти зубы, налей чай без сахара, займи руки (сериал не на кухне, душ, "
                     "короткая прогулка). Если голод правда вернётся, значит это был голод.")
    elif category == "tired":
        parts.append("😴 Усталость часто маскируется под голод. Если можешь, лучше лечь спать: "
                     "после сна тяга обычно слабее. Пока стакан воды или тёплый чай. "
                     "Если уснуть не выходит и живот правда урчит, возьми небольшой перекус с белком:")
        parts += snack_lines(db, user["id"], limit=2)
    elif category == "overate":
        parts.append("😔 Всё в порядке: один вечер не отменяет прогресс, и наказывать себя не за что. "
                     "Сейчас лучшее, что можно сделать, это ничего не «исправлять»: выпей воды, "
                     "дай организму спокойно переварить и ложись спать. Завтра обычный завтрак "
                     "с белком, без голода и без «отработки»: компенсация только раскачивает "
                     "эти качели.")
        parts.append(f"Если хочешь, напиши, что {ate}, я запишу без оценок. Это нужно только для "
                     "точной картины.")
        if facts.overate_count_14d >= 2:
            parts.append(esc(dom.REPEAT_NOTE))
    own_rules = plansvc.rules_text(db, user["id"], category)
    if own_rules:
        parts += ["", own_rules]
    text = "\n".join(parts)
    repo.add_craving_message(db, event["id"], "assistant", text)
    return text


_CATEGORY_LABELS = {"hunger": "физический голод", "emotion": "стресс или эмоции",
                    "habit": "привычка или скука", "tired": "усталость",
                    "overate": "уже переел(а)"}


async def category_reply(db: sqlite3.Connection, user, event: sqlite3.Row, category: str) -> str:
    """Отклик на кнопку с причиной: реплика модели плюс точные перекусы из базы и свои правила.
    Без модели — детерминированный план."""
    repo.update_craving(db, event["id"], category=category)
    event = repo.get_craving(db, user["id"], event["id"])
    repo.add_craving_message(db, event["id"], "user",
                             f"(нажата кнопка: {_CATEGORY_LABELS.get(category, category)})")
    data = await _ask_model(db, user, event, "", "category")
    if not data:
        return category_plan(db, user, event, category)
    repo.add_craving_message(db, event["id"], "assistant", data["reply"])
    parts = [esc(data["reply"])]
    if category in ("hunger", "tired"):
        snacks = snack_lines(db, user["id"], limit=3 if category == "hunger" else 2)
        if snacks:
            parts += ["", "Если решишь перекусить:"] + snacks
    if category == "overate" and build_facts(db, user).overate_count_14d >= 2:
        parts += ["", esc(dom.REPEAT_NOTE)]
    own_rules = plansvc.rules_text(db, user["id"], category)
    if own_rules:
        parts += ["", own_rules]
    return "\n".join(parts)


def debrief_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="📝 Короткий разбор (1 минута)", callback_data=f"cr:deb:{event_id}")]])


def category_keyboard(event_id: int) -> InlineKeyboardMarkup:
    def b(text: str, cat: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"cr:dcat:{event_id}:{cat}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("🥣 Голод", "hunger"), b("😩 Стресс / эмоции", "emotion")],
        [b("🔁 Привычка / скука", "habit"), b("😴 Усталость", "tired")],
    ])


# ---------------------------------------------------------------------- диалог

def _memory(db: sqlite3.Connection, user_id: int, current_id: int) -> list[str]:
    out = []
    for r in repo.craving_memory(db, user_id, limit=4):
        bits = [b for b in (r["summary"], r["lesson"] and f"решение: {r['lesson']}") if b]
        if bits:
            out.append(f"{r['local_date']} {r['local_time']}: " + "; ".join(bits))
    return out[:3]


def _today_facts(db: sqlite3.Connection, user, now_hhmm: str) -> dict:
    """Факты текущего дня целиком (активность, сон, паузы, прошлые тяги) для разговора."""
    from app.services import insights
    from app.timeutils import local_today
    day = local_today(user["tz"]).isoformat()
    return insights.llm_facts(insights.day_facts(db, user, day), now_hhmm)


def _context(db: sqlite3.Connection, user, event: sqlite3.Row, stage: str = "dialog") -> dict:
    facts = build_facts(db, user)
    rules = [f"если {r['if_text']} — то {r['then_text']}"
             for r in repo.rules_for_category(db, user["id"], event["category"], limit=3)]
    return {
        "stage": stage,
        "mode": event["mode"] if "mode" in event.keys() else "craving",
        "sex": user["sex"] or "unknown",
        "time_now": facts.local_time,
        "kcal_eaten_today": facts.kcal_so_far,
        "kcal_plan": facts.kcal_target,
        "protein_eaten_g": facts.protein_so_far,
        "protein_plan_g": facts.protein_target,
        "meals_today": facts.meals_today,
        "diary_maybe_incomplete": diary_maybe_incomplete(facts),
        "hours_since_last_meal": (None if facts.hours_since_last_meal is None
                                  else round(facts.hours_since_last_meal)),
        "sleep_min": facts.sleep_min,
        "water_ml": facts.water_ml,
        "water_goal_ml": facts.water_goal_ml,
        "cravings_last_7_days": facts.craving_count_7d,
        "category": event["category"],
        "today_facts": _today_facts(db, user, facts.local_time),
        "insights": dom.insights(facts),
        "memory": _memory(db, user["id"], event["id"]),
        "own_rules": rules,
        "allowed_extra": ACTION_NUMBERS,
    }


async def _ask_model(db: sqlite3.Connection, user, event: sqlite3.Row, user_text: str,
                     stage: str) -> dict | None:
    """Один ход модели. Возвращает проверенный ответ (reply, risk, food_eaten, outcome,
    summary) или None, если модель недоступна или ответ не прошёл проверку."""
    ctx = _context(db, user, event, stage)
    rows = repo.craving_messages(db, event["id"])
    if user_text and rows and rows[-1]["role"] == "user" and rows[-1]["text"] == user_text[:2000]:
        rows = rows[:-1]                     # текущее сообщение уйдёт отдельно, с контекстом
    # свои прошлые реплики модель видит в том же JSON, который должна вернуть: иначе
    # она подхватывает простой текст из истории и перестаёт отвечать JSON-ом
    history = [{"role": m["role"],
                "content": (json.dumps({"reply": m["text"]}, ensure_ascii=False)
                            if m["role"] == "assistant" else m["text"])} for m in rows]
    messages = [{"role": "system", "content": foodsvc.load_prompt("craving")},
                *history,
                {"role": "user", "content": json.dumps(
                    {"context": ctx, "user_message": user_text}, ensure_ascii=False)}]
    try:
        data, _ = await llm.chat_json("craving_dialog", messages,
                                      model=get_config().model_dialog or None,
                                      temperature=0.7, user_id=user["id"], db=db,
                                      max_tokens=700)
    except llm.LLMError:
        log.warning("craving: LLM недоступен, запасной ответ", exc_info=True)
        return None
    reply = str(data.get("reply") or "").strip()
    if not reply or len(reply) > 900 or dom.violates_policy(reply) or check_numbers(reply, ctx):
        if reply:
            log.warning("craving: ответ модели отклонён проверкой")
        return None
    summary = str(data.get("summary") or "").strip()[:200]
    if summary and dom.violates_policy(summary):
        summary = ""
    outcome = str(data.get("outcome") or "none")
    return {
        "reply": reply,
        "risk": bool(data.get("risk")),
        "food_eaten": str(data.get("food_eaten") or "").strip()[:300],
        "outcome": outcome if outcome in OUTCOMES else "none",
        "summary": summary,
    }


def _fallback_reply(user) -> str:
    return ("Я тебя слышу. Давай по шагам: стакан воды, пара минут вне кухни. "
            "Скажи, что ближе: настоящий голод, эмоции или привычка? Нажми кнопку ниже "
            "или просто напиши, что происходит.")


async def log_food_from_dialog(db: sqlite3.Connection, user, event_id: int,
                               food_text: str) -> str | None:
    """Еда, о которой рассказали в разговоре, записывается в дневник тем же конвейером, что и
    обычный текст (калории считает код по базе). Возвращает короткую строку для ответа."""
    try:
        u = await foodsvc.parse_text(food_text, user["id"], db)
        if not u.items:
            return None
        calc = await foodsvc.build_calc(u, db, user["id"])
    except Exception:                                     # noqa: BLE001 — разговор важнее записи
        log.warning("craving: не удалось записать еду из разговора", exc_info=True)
        return None
    now_local = foodsvc.local_now(user["tz"])
    local_time = now_local.strftime("%H:%M")
    meal_type = foodsvc.guess_meal_type(u, local_time, None)
    repo.save_meal(db, user["id"], datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                   now_local.strftime("%Y-%m-%d"), local_time, meal_type, "text", calc,
                   transcript=food_text)
    names = ", ".join(i.name_ru for i in calc.items[:5])
    repo.add_craving_message(db, event_id, "user", f"[записано в дневник: {names}]")
    return (f"📝 {_g(user, 'Записал', 'Записала')} в дневник без оценок: {esc(names)} "
            f"≈ {int(calc.kcal_total)} ккал. Если не так, напиши «отмени последнее».")


async def _apply_turn(db: sqlite3.Connection, user, event_id: int, data: dict,
                      allow_outcome: bool = True) -> str:
    """Побочные действия хода модели: память, запись названной еды, риск, итог эпизода.
    Возвращает добавку к ответу (строка о записи еды, заметка о специалисте)."""
    extra = ""
    if data["summary"]:
        repo.update_craving(db, event_id, summary=data["summary"])
    if data["food_eaten"]:
        logged = await log_food_from_dialog(db, user, event_id, data["food_eaten"])
        if logged:
            extra += "\n\n" + logged
    if data["risk"]:
        repo.update_craving(db, event_id, risk_flag=1)
        extra += "\n\n" + esc(dom.REPEAT_NOTE)
    if allow_outcome and data["outcome"] != "none":
        repo.close_craving(db, event_id, data["outcome"])
    return extra


async def dialog_reply(db: sqlite3.Connection, user, event: sqlite3.Row,
                       user_text: str) -> tuple[str, bool]:
    """Ответ на свободный текст внутри открытого эпизода → (текст, risk).
    Попутно записывает названную еду и закрывает эпизод, если человек дал понять итог."""
    level = dom.risk_level(user_text)
    repo.add_craving_message(db, event["id"], "user", user_text)
    if level != "none":
        repo.update_craving(db, event["id"], risk_flag=1)
        return _risk_text(level), True

    data = await _ask_model(db, user, event, user_text, "dialog")
    if not data:
        return esc(_fallback_reply(user)), False
    repo.add_craving_message(db, event["id"], "assistant", data["reply"])
    out = esc(data["reply"]) + await _apply_turn(db, user, event["id"], data)
    return out, data["risk"]


def note_food_logged(db: sqlite3.Connection, user, meal_text: str) -> str | None:
    """Еду записали обычным путём (фото, текст, голос), пока идёт разговор о тяге: разговор
    должен об этом знать. Возвращает короткую реплику или None, если разговора нет."""
    event = repo.open_craving(db, user["id"], dom.EPISODE_WINDOW_MIN)
    if not event:
        return None
    repo.add_craving_message(db, event["id"], "user", f"[записано в дневник: {meal_text}]")
    return _g(user, "📝 Записал, без оценок. Как ты сейчас, стало спокойнее?",
              "📝 Записала, без оценок. Как ты сейчас, стало спокойнее?")


# ------------------------------------------------------------------------ итоги

def finish_episode(db: sqlite3.Connection, user, event: sqlite3.Row, outcome: str) -> str:
    repo.close_craving(db, event["id"], outcome)
    facts = build_facts(db, user)
    category = event["category"]
    ate = _g(user, "съел", "съела")
    if outcome == "resisted":
        head = ("💪 " + _g(user, "Ты справился", "Ты справилась") + ". Без борьбы на износ: "
                "ты просто " + _g(user, "прошёл", "прошла") + " этот отрезок. Запомни, что именно помогло, "
                "и повтори в следующий раз.")
    elif outcome == "ate_planned":
        head = (f"🍽 Хорошо, что ты {_g(user, 'поел', 'поела')} осознанно, а не «в тумане». "
                f"Это не срыв. Если хочешь, напиши, что {ate}, и я запишу без оценок.")
    else:
        head = ("😔 Бывает, и это не конец прогресса. Без наказаний: вода, сон, завтра обычный "
                f"завтрак с белком. Если напишешь, что {ate}, я просто запишу, без оценок.")
    lines = [head, "", "🌤 Чтобы завтра вечером было спокойнее:"]
    lines += [f"• {esc(t)}" for t in dom.prevention_tips(facts, category)]
    if outcome == "overate" and facts.overate_count_14d >= 3:
        lines += ["", esc(dom.REPEAT_NOTE)]
    return "\n".join(lines)


def schedule_followup(db: sqlite3.Connection, event_id: int, minutes: int = FOLLOWUP_MIN) -> None:
    when = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    repo.update_craving(db, event_id, followup_at_utc=when, followup_sent=0)


def followup_text(user) -> str:
    return (f"⏱ Прошло {FOLLOWUP_MIN} минут. Как ты сейчас? Тяга обычно приходит волной и "
            "спадает. Выбери, что ближе всего, или просто напиши мне.")


async def followup_message(db: sqlite3.Connection, user, event: sqlite3.Row) -> str:
    """Проверка через 15 минут с опорой на разговор; без модели — запасной текст."""
    data = await _ask_model(db, user, event, "", "followup")
    if not data:
        return followup_text(user)
    repo.add_craving_message(db, event["id"], "assistant", data["reply"])
    return "⏱ " + esc(data["reply"])


# ------------------------------------------------------------------ отчёты и утро

def week_lines(db: sqlite3.Connection, uid: int, frm: str, to: str) -> list[str]:
    events = [e for e in repo.cravings_range(db, uid, frm, to) if e["outcome"] != "dismissed"
              or e["risk_flag"]]
    if not events:
        return []
    count = lambda o: sum(1 for e in events if e["outcome"] == o)  # noqa: E731
    n = len(events)
    times_word = ("раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз")
    lines = [f"🌙 Тяга к еде: {n} {times_word} за неделю (справился: {count('resisted')}, "
             f"поел осознанно: {count('ate_planned')}, переел: {count('overate')})"]
    if len(events) >= 3:
        times = sorted(e["local_time"] for e in events)
        lines.append(f"Чаще всего около {times[len(times) // 2]}. Вечерняя тяга нередко следует из "
                     "недоедания днём: сдвинь часть калорий на обед и полдник и добавь белок.")
    if count("overate") >= 3 or any(e["risk_flag"] for e in events):
        lines.append(dom.REPEAT_NOTE)
    return lines


def morning_note(db: sqlite3.Connection, user, today) -> str | None:
    """Если вечером был тяжёлый эпизод — утром поддержка без упрёков и с тем, что человек
    сам решил делать иначе."""
    yesterday = (today - timedelta(days=1)).isoformat()
    events = [e for e in repo.cravings_range(db, user["id"], yesterday, today.isoformat())
              if e["outcome"] in ("overate", "resisted")]
    if not events:
        return None
    last = events[-1]
    if last["outcome"] == "resisted":
        text = (f"🌙 Вчера вечером тянуло к еде, и ты {_g(user, 'справился', 'справилась')}. "
                "Это навык, и он растёт.")
    else:
        text = ("💛 Вчера вечером было тяжело, и это нормально. Сегодня просто вернись к обычному "
                "плану: завтрак с белком, вода, без голода и «отработки».")
    if last["lesson"]:
        text += f" Ты решила попробовать: «{esc(last['lesson'])}»." if user["sex"] == "female" \
            else f" Ты решил попробовать: «{esc(last['lesson'])}»."
    return text
