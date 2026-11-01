"""Чат Mini App: та же логика, что у текстового сообщения в боте, но без aiogram.

Порядок как в боте: разговор о тяге (открытый эпизод, фраза о тяге, «поговорить») → вода →
явные фразы (справка, «отмени последнее», сон/шаги/тренировка, калорийность продукта) →
вопрос ассистенту → запись еды. Ответ — обычный текст (без HTML), потому что экранирует и
показывает его само приложение. Кнопки бота (callback_data «cr:...») приложение получает как
данные `actions` [{id, label}], а нажатие приходит в perform_action с тем же id.
"""
from __future__ import annotations

import html
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta

from app.domain import analytics, intents, water
from app.domain import craving as craving_dom
from app.domain import dishes as dishdom
from app.domain import tracking as trk
from app.services import assistant
from app.services import craving as craving_svc
from app.services import food as foodsvc
from app.services import meals as meals_svc
from app.services import plan as plansvc
from app.services import tracking as tracksvc
from app.services import water as watersvc
from app.storage import repo
from app.timeutils import local_today

log = logging.getLogger(__name__)

MAX_TEXT = 2000
MAX_DISHES = 20                 # как в routers/dishes
TALK_MODES = ("talk", "craving")


@dataclass
class Reply:
    """Результат хода чата: текст, id записанного приёма, быстрые кнопки."""
    text: str
    meal_id: int | None = None
    actions: list[dict] = field(default_factory=list)


class ChatActionError(Exception):
    """Нажатие кнопки нельзя выполнить (эпизод закрыт, чужой, неизвестная кнопка)."""

    def __init__(self, message: str, code: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _plain(text: str) -> str:
    """assistant.* возвращают HTML-экранированный текст для Telegram: приложению нужен чистый."""
    return html.unescape(text)


def kb_actions(kb) -> list[dict]:
    """aiogram-клавиатура бота → кнопки приложения: callback_data становится id действия."""
    if kb is None:
        return []
    return [{"id": b.callback_data, "label": b.text}
            for row in kb.inline_keyboard for b in row if b.callback_data]


async def _answer(db: sqlite3.Connection, user, text: str, focus: str | None = None,
                  history: list[dict] | None = None) -> str:
    kw: dict = {}
    if focus:
        kw["focus"] = focus
    if history:
        kw["history"] = history
    return _plain(await assistant.answer(db, user, text, **kw))


def meal_summary(db: sqlite3.Connection, user, meal_id: int) -> str:
    meal = repo.get_meal(db, user["id"], meal_id)
    items = ", ".join(f"{i['name_ru']} {i['grams']:g} г" for i in repo.get_meal_items(db, meal_id))
    totals = repo.day_totals(db, user["id"], meal["local_date"])
    tgt = repo.current_target(db, user["id"])
    day = f"За день {int(totals['kcal'])}" + (f" из {int(tgt['kcal_target'])}" if tgt else "") + " ккал."
    return (f"Записал: {items}. {meal['kcal_total']} ккал "
            f"(Б {meal['protein_g']:g} / Ж {meal['fat_g']:g} / У {meal['carbs_g']:g}). {day}")


def meal_focus(db: sqlite3.Connection, user, meal) -> str:
    """Описание приёма для ассистента (поле focus): состав с граммами, КБЖУ, время, комментарий."""
    items = "; ".join(
        f"{i['name_ru']} {i['grams']:g} г ({int(i['kcal'])} ккал, Б {i['protein_g']:g} / "
        f"Ж {i['fat_g']:g} / У {i['carbs_g']:g})" for i in repo.get_meal_items(db, meal["id"]))
    text = (f"Обсуждается один приём пищи: {foodsvc.meal_type_ru(meal['meal_type'])} в "
            f"{meal['local_time']} ({meal['local_date']}), {int(meal['kcal_total'])} ккал "
            f"(Б {meal['protein_g']:g} / Ж {meal['fat_g']:g} / У {meal['carbs_g']:g}). "
            f"Состав: {items or 'позиций нет'}.")
    if meal["transcript"]:
        text += f" Комментарий человека: {meal['transcript']}."
    return text


# ------------------------------------------------------------------ тяга к еде

def _after_reply_kb(db: sqlite3.Connection, user, event_id: int):
    """Как routers/craving._after_reply_kb: разбор после закрытия, итог пока эпизод идёт."""
    event = repo.get_craving(db, user["id"], event_id)
    if event is None:
        return None
    if event["outcome"] != "open":
        return craving_svc.debrief_keyboard(event_id) if event["outcome"] in craving_svc.OUTCOMES \
            else None
    if event["mode"] == "talk":
        return None
    return craving_svc.outcome_keyboard(user, event_id)


async def _lesson_text(db: sqlite3.Connection, user, text: str) -> Reply | None:
    """Свободный текст в ответ на «напиши своё» после разбора (routers/craving._handle_lesson_text)."""
    uid = user["id"]
    pending = repo.latest_pending_input(db, uid, "craving_lesson", max_age_min=30)
    if not pending:
        return None
    repo.delete_pending_input(db, pending["id"], uid)
    level = craving_dom.risk_level(text)
    if level != "none":
        return Reply(craving_dom.SELF_HARM_TEXT if level == "self_harm"
                     else craving_dom.PURGING_TEXT)
    if craving_dom.violates_policy(text) or not 3 <= len(text) <= 160:
        repo.save_pending_input(db, uid, "craving_lesson", json.loads(pending["payload_json"]))
        return Reply("Давай выберем такой шаг, который о тебе заботится: вода, чай, сон, "
                     "нормальный приём пищи. Напиши ещё раз, коротко (до 160 символов).")
    event = repo.get_craving(db, uid, json.loads(pending["payload_json"])["event_id"])
    if not event:
        return None
    return _save_lesson(db, user, event, text)


def _save_lesson(db: sqlite3.Connection, user, event, lesson: str) -> Reply:
    repo.set_craving_lesson(db, user["id"], event["id"], lesson)
    return Reply(_plain(plansvc.lesson_saved_text(lesson)),
                 actions=[{"id": f"cr:rule:{event['id']}", "label": "🧭 Сделать правилом «если-то»"}])


async def _craving_text(db: sqlite3.Connection, user, text: str) -> Reply | None:
    """routers/craving.maybe_handle: открытый эпизод → продолжить разговор (кроме явной записи
    еды, воды и команд), иначе фраза о тяге открывает эпизод. None — не про тягу."""
    lesson = await _lesson_text(db, user, text)
    if lesson is not None:
        return lesson
    uid = user["id"]
    local_time = foodsvc.local_now(user["tz"]).strftime("%H:%M")
    event = repo.open_craving(db, uid, craving_dom.EPISODE_WINDOW_MIN)
    if event:
        if craving_dom.looks_like_food_log(text) or water.extract_water(text)[0]:
            return None
        if intents.detect(text) is not None:
            return None
        reply, _ = await craving_svc.dialog_reply(db, user, event, text)
        return Reply(_plain(reply), actions=kb_actions(_after_reply_kb(db, user, event["id"])))
    if craving_dom.detect_craving(text, local_time, user["eating_cutoff"]):
        _, reply, kb = await craving_svc.open_episode(db, user, text)
        return Reply(_plain(reply), actions=kb_actions(kb))
    return None


async def start_dialog(db: sqlite3.Connection, user, text: str | None, mode: str) -> Reply:
    """Явный вход: «поговорить» (/talk) и «тянет» (/craving), с текстом или без."""
    text = (text or "").strip() or None
    event = repo.open_craving(db, user["id"], craving_dom.EPISODE_WINDOW_MIN)
    if event:
        if text:
            reply, _ = await craving_svc.dialog_reply(db, user, event, text)
            return Reply(_plain(reply), actions=kb_actions(_after_reply_kb(db, user, event["id"])))
        if mode == "talk":
            return Reply("Я здесь, продолжаем 🙂 Пиши.")
        return Reply("Я здесь. Выбери, что ближе, или просто напиши, что происходит.",
                     actions=kb_actions(craving_svc.triage_keyboard(event["id"], user)))
    _, reply, kb = await craving_svc.open_episode(db, user, text, mode=mode)
    return Reply(_plain(reply), actions=kb_actions(kb if mode == "craving" else None))


def _own_event(db: sqlite3.Connection, user, event_id: int):
    event = repo.get_craving(db, user["id"], event_id)
    if event is None:
        raise ChatActionError("Не нашёл эпизод.", "not_found", 404)
    return event


def _open_event(db: sqlite3.Connection, user, event_id: int):
    event = _own_event(db, user, event_id)
    if event["outcome"] != "open":
        raise ChatActionError("Этот эпизод уже закрыт.", "episode_closed", 409)
    return event


async def perform_action(db: sqlite3.Connection, user, action_id: str) -> Reply:
    """Нажатие кнопки: то же, что делает callback бота (routers/craving.cb_*)."""
    parts = str(action_id).split(":")
    try:
        if len(parts) < 3 or parts[0] != "cr":
            raise ValueError
        kind, event_id = parts[1], int(parts[2])
        arg = parts[3] if len(parts) > 3 else ""
    except ValueError:
        raise ChatActionError("Неизвестное действие.", "bad_action") from None

    if kind == "cat" and arg in craving_dom.CATEGORIES:
        event = _open_event(db, user, event_id)
        text = await craving_svc.category_reply(db, user, event, arg)
        return Reply(_plain(text), actions=kb_actions(craving_svc.outcome_keyboard(user, event_id)))
    if kind == "wait":
        event = _open_event(db, user, event_id)
        craving_svc.schedule_followup(db, event["id"])
        return Reply(f"⏳ Договорились: {craving_svc.FOLLOWUP_MIN} минут. Пока: стакан воды, выйди "
                     "из кухни, займи руки. Я напишу через это время, а если захочешь поговорить, "
                     "пиши сразу.")
    if kind == "out" and arg in craving_svc.OUTCOMES:
        event = _open_event(db, user, event_id)
        text = craving_svc.finish_episode(db, user, event, arg)
        return Reply(_plain(text), actions=kb_actions(craving_svc.debrief_keyboard(event_id)))
    if kind == "deb":
        event = _own_event(db, user, event_id)
        if event["category"] == "unknown":
            return Reply("Давай коротко. Что это было скорее всего?",
                         actions=kb_actions(craving_svc.category_keyboard(event_id)))
        return Reply("Давай коротко, без вины. Что поможет тебе в следующий раз в такой ситуации?",
                     actions=kb_actions(plansvc.lesson_keyboard(event_id, event["category"])))
    if kind == "dcat" and arg in craving_dom.CATEGORIES:
        _own_event(db, user, event_id)
        repo.update_craving(db, event_id, category=arg)
        return Reply("Что поможет тебе в следующий раз в такой ситуации?",
                     actions=kb_actions(plansvc.lesson_keyboard(event_id, arg)))
    if kind == "less" and arg:
        event = _own_event(db, user, event_id)
        if arg == "own":
            repo.save_pending_input(db, user["id"], "craving_lesson", {"event_id": event_id})
            return Reply("Напиши одной фразой, что сделаешь иначе. Например: "
                         "«выпью воды и подожду 15 минут».")
        lesson = plansvc.lesson_by_index(event["category"], int(arg)) if arg.isdigit() else None
        if not lesson:
            raise ChatActionError("Не нашёл вариант.", "not_found", 404)
        return _save_lesson(db, user, event, lesson)
    if kind == "rule":
        event = _own_event(db, user, event_id)
        return Reply(_plain(plansvc.rule_from_lesson(db, user["id"], event)))
    raise ChatActionError("Неизвестное действие.", "bad_action")


# ------------------------------------------------------------------ сон, шаги, тренировки

def log_tracking(db: sqlite3.Connection, user, text: str) -> str | None:
    """«спал 7 часов», «10 тысяч шагов», «зал 1 час»: те же записи и тот же текст, что в
    routers/tracking.log_from_text, но простым текстом. None — ничего не распознано."""
    today = local_today(user["tz"])
    lines: list[str] = []

    def day_of(spec: dict):
        return today - timedelta(days=1) if spec.get("yesterday") else today

    def steps_fmt(n: int) -> str:
        return f"{n:,}".replace(",", " ")

    sleep = trk.parse_sleep_text(text)
    if sleep:
        try:
            res = tracksvc.log_sleep(db, user, date=day_of(sleep),
                                     duration_min=sleep["duration_min"],
                                     start=sleep["start"], end=sleep["end"])
        except ValueError as exc:
            return str(exc)
        line = f"😴 Записал сон: {analytics.format_minutes(res['duration_min'])}"
        if res["start"]:
            line += f" ({res['start']}–{res['end']})"
        avg = tracksvc.sleep_avg_7d(db, user, today)
        if avg:
            line += f". Среднее за 7 дней: {analytics.format_minutes(avg[0])} ({avg[1]} ноч.)"
        lines.append(line)
    steps = trk.parse_steps_text(text)
    if steps:
        try:
            tracksvc.log_activity(db, user, date=day_of(steps), steps=steps["steps"])
        except ValueError as exc:
            return str(exc)
        line = f"👟 Записал шаги: {steps_fmt(steps['steps'])}"
        avg = tracksvc.steps_avg_7d(db, user, today)
        if avg:
            line += f". Среднее за 7 дней: {steps_fmt(avg[0])} ({avg[1]} дн.)"
        lines.append(line)
    workout = trk.parse_workout_text(text)
    if workout:
        try:
            res = tracksvc.log_activity(db, user, date=day_of(workout), workout=workout["name"],
                                        active_minutes=None)
        except ValueError as exc:
            return str(exc)
        line = f"🏃 Записал тренировку: {workout['name']}"
        if len(res["workouts"]) > 1:
            line += f". Тренировок за день: {len(res['workouts'])}"
        lines.append(line)
    return "\n".join(lines) or None


def add_dish(db: sqlite3.Connection, user, text: str) -> str:
    """«добавь посуду: ...» как routers/dishes.add_from_text, без кнопки «по умолчанию»."""
    spec = dishdom.parse_dish_text(text)
    if spec is None:
        return "Не понял, что за посуда. " + dishdom.HELP_TEXT
    if len(repo.list_dishes(db, user["id"])) >= MAX_DISHES:
        return f"Посуды уже {MAX_DISHES}. Удали ненужную в Меню → Посуда."
    dish = repo.get_dish(db, user["id"], repo.create_dish(db, user["id"], **spec))
    tail = ("Она по умолчанию: буду учитывать её на новых фото." if dish["is_default"]
            else "Теперь её можно выбрать при добавлении приёма с фото.")
    return f"🍽 Добавил: {dishdom.describe_dish(dish)}.\n{tail}"


async def handle(db: sqlite3.Connection, user, text: str, *, meal_id: int | None = None,
                 history: list[dict] | None = None, mode: str | None = None) -> Reply:
    """Ход чата. meal_id — приём, который обсуждают (фокус для ассистента, уже проверен на
    владельца), history — последние сообщения чата [{role, text}], mode — «talk»/«craving»
    (явный вход в разговор о тяге, текст может быть пустым)."""
    uid = user["id"]
    if mode in TALK_MODES:
        return await start_dialog(db, user, text, mode)
    crav = await _craving_text(db, user, text)
    if crav is not None:
        return crav

    focus = None
    if meal_id:
        meal = repo.get_meal(db, uid, meal_id)
        focus = meal_focus(db, user, meal) if meal else None

    water_ml, rest = water.extract_water(text)
    if water_ml:
        _, reply = watersvc.log_water(db, user, water_ml, "text", text)
        if water.is_water_only(rest):
            return Reply(reply)
        text = rest                                   # остальное в сообщении — еда
        reply_prefix = reply + "\n"
    else:
        reply_prefix = ""

    intent = None if water_ml else intents.detect(text)
    if intent is not None:
        reply = await _intent_reply(db, user, intent, text, focus, history)
        return Reply(reply_prefix + reply)
    if not water_ml and (focus or intents.is_question(text)):
        # про конкретный приём это разговор о нём, а не новая запись
        return Reply(await _answer(db, user, text, focus, history))

    try:
        u = await foodsvc.parse_text(text, uid, db)
    except Exception:                                  # noqa: BLE001
        log.warning("чат: parse_text не удался", exc_info=True)
        return Reply(reply_prefix + "Не смог разобрать сообщение: нет связи с моделью. "
                     "Попробуй ещё раз чуть позже.")
    if not u.items and getattr(u, "intent", None) == "craving":
        _, reply, kb = await craving_svc.open_episode(db, user, text)     # как route_llm_intent
        return Reply(reply_prefix + _plain(reply), actions=kb_actions(kb))
    if not u.items:
        # не еда: реплика, команда или вопрос в свободной форме
        return Reply(reply_prefix + await _answer(db, user, text, focus, history))
    saved = await meals_svc.save_understanding(db, user, u, text=text, source="text")
    out = reply_prefix + meal_summary(db, user, saved.meal_id)
    note = _food_logged_note(db, user, saved.meal_id)
    return Reply(out + ("\n\n" + note if note else ""), saved.meal_id)


def _food_logged_note(db: sqlite3.Connection, user, meal_id: int) -> str | None:
    """Еду записали, пока идёт разговор о тяге: разговор должен знать (как _food_logged_hook)."""
    try:
        items = repo.get_meal_items(db, meal_id)
        meal = repo.get_meal(db, user["id"], meal_id)
        names = ", ".join(f"{i['name_ru']} ({i['kcal']} ккал)" for i in items)
        return craving_svc.note_food_logged(db, user, f"{names}; всего {meal['kcal_total']} ккал")
    except Exception:                                  # noqa: BLE001
        log.warning("note_food_logged упала", exc_info=True)
        return None


async def _intent_reply(db: sqlite3.Connection, user, intent: intents.Intent, text: str,
                        focus: str | None = None, history: list[dict] | None = None) -> str:
    name = intent.name
    if name == "help":
        return assistant.HELP_TEXT
    if name == "greeting":
        return assistant.GREETING
    if name == "thanks":
        return _plain(assistant.THANKS)
    if name == "food_info":
        return _plain(assistant.food_info(db, intent.arg))
    if name == "undo":
        last = repo.get_last_meal(db, user["id"])
        if last and repo.soft_delete_meal(db, user["id"], last["id"]):
            return f"Отменил последнюю запись: {last['kcal_total']} ккал."
        return "Нечего отменять."
    if name in ("sleep_log", "steps_log", "workout_log"):
        logged = log_tracking(db, user, intent.arg or text)
        if logged:
            return logged
    if name == "dish_add":
        return add_dish(db, user, intent.arg)
    if name == "dishes":
        return "Твоя посуда в приложении: Меню → Посуда. Там можно добавить, изменить и удалить."
    if name in ("product_add", "products"):
        return ("Свои продукты в приложении: Меню → Мои продукты. Там можно добавить продукт "
                "с числами на 100 г или по фото этикетки.")
    # остальные команды бота (отчёты, напоминания, правила) в приложении есть отдельными
    # экранами; по тексту отвечаем как на вопрос
    return await _answer(db, user, text, focus, history)
