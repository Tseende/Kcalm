"""Онбординг /start: 8 вопросов с дефолтами, каждый можно пропустить."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, date

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.config import get_config
from app.domain.targets import MIN_AGE
from app.routers import menu
from app.services.onboarding import CARE_NOTE, HEALTH_NOTE, UNDERAGE_TEXT, complete
from app.storage import repo
from app.timeutils import local_today, validate_hhmm, validate_timezone

router = Router(name="onboarding")

QUESTIONS = [
    # (ключ, текст, дефолт-строка или None = обязателен)
    ("sex", "Пол: <b>м</b> / <b>ж</b>", "м"),
    ("age", "Возраст (полных лет)?", None),
    ("height", "Рост в см?", None),
    ("weight", "Текущий вес в кг? (потом будешь просто кидать скриншот PICOOC)", None),
    ("rate", "Целевой темп, кг/нед (0.4–0.7)?", "0.5"),
    ("cutoff", "С какого часа кухня закрыта (ЧЧ:ММ)?", None),   # дефолт — из конфига
    ("activity", "Активность: 1 сидячий / 2 низкая / 3 активный / 4 очень активный?", "2"),
    ("tz", "Часовой пояс (IANA)?", "Europe/Minsk"),
]


def _cutoff_default() -> str:
    return get_config().eating_cutoff_default


def _question(index: int) -> tuple[str, str, str | None]:
    key, text, default = QUESTIONS[index]
    return key, text, (_cutoff_default() if key == "cutoff" else default)


CHAT_FLOW_CB = "onb:chat"
WELCOME_TEXT = (
    "Привет! Я помогу вести дневник питания и не срываться вечером.\n"
    "Настройка займёт минуту: пара касаний, без клавиатуры."
)


class Onboard(StatesGroup):
    q = State()


def _summary(data: dict) -> str:
    act = {"1": "сидячий", "2": "низкая", "3": "активный", "4": "очень активный"} \
        .get(data.get("activity", ""), "низкая")
    return (
        f"Пол: {data.get('sex', 'м')}\nВозраст: {data.get('age')}\n"
        f"Рост: {data.get('height')} см\nВес: {data.get('weight')} кг\n"
        f"Темп: −{data.get('rate', '0.5')} кг/нед\nКухня закрыта: {data.get('cutoff') or _cutoff_default()}\n"
        f"Активность: {act}\nЧасовой пояс: {data.get('tz', 'Europe/Minsk')}"
    )


def _finish(db: sqlite3.Connection, tg_id: int, data: dict) -> tuple[int, int, str]:
    """Ответы чата → общий расчёт (services.onboarding.complete). Возвращает (uid, ккал, заметки)."""
    tz = data.get("tz", "Europe/Minsk")
    rate = abs(float(data.get("rate", "0.5")))
    res = complete(db, tg_id, {
        "sex": "male" if (parse_sex(str(data.get("sex", "м"))) or "м") == "м" else "female",
        "birth_year": local_today(tz).year - int(data["age"]),
        "height_cm": float(data["height"]), "weight_kg": float(data["weight"]),
        "rate_kg_week": rate, "tz": tz, "eating_cutoff": data.get("cutoff") or _cutoff_default(),
        "activity": int(data.get("activity", "2")),
    }, log_weight=False)
    note = "".join(f"\n⚠️ {n}" for n in res["notes"])
    return res["uid"], res["kcal"], note


OK_WORDS = ("ок", "окей", "ok", "okay")
TZ_HINT = "Часовой пояс нужен в виде «Europe/Moscow» или «Europe/Minsk». Если не знаешь, напиши «ок», возьму Europe/Minsk."
SEX_HINT = "Не поняла ответ: напиши «м» или «ж»."
_MALE = re.compile(r"^(м|муж\w*|m|male|man)$")
_FEMALE = re.compile(r"^(ж|жен\w*|f|female|woman)$")

# Допустимые границы и подсказки: одни и те же для вопросов и для правок «рост 5000».
HEIGHT_RANGE = (100, 230)
WEIGHT_RANGE = (35, 300)


def parse_sex(text: str) -> str | None:
    """«м» / «ж» из свободного ответа; всё остальное (в том числе «ок») — None."""
    t = (text or "").strip().lower().rstrip(".!")
    if _MALE.match(t):
        return "м"
    if _FEMALE.match(t):
        return "ж"
    return None


def parse_age(text: str) -> int | None:
    t = (text or "").strip()
    return int(t) if t.isdigit() and int(t) <= 100 else None


def parse_number(text: str, low: float, high: float) -> float | None:
    try:
        v = float((text or "").strip().replace(",", "."))
    except ValueError:
        return None
    return v if low <= v <= high else None          # nan и inf сюда не проходят


def parse_rate(text: str) -> str | None:
    t = (text or "").strip().lower()
    if t in OK_WORDS:
        return "0.5"
    try:
        v = float(t.replace(",", "."))
    except ValueError:
        return None
    return str(min(0.7, max(0.4, v))) if v == v else None


def parse_tz(text: str) -> str | None:
    try:
        return validate_timezone((text or "").strip())
    except ValueError:
        return None


def parse_answer(key: str, text: str, default: str | None):
    """Разобрать ответ на вопрос key. None — не подошло. Слово «ок» берёт дефолт там, где он есть."""
    t = (text or "").strip()
    if key == "sex":
        return parse_sex(t)
    if key == "age":
        return parse_age(t)
    if key == "height":
        return parse_number(t, *HEIGHT_RANGE)
    if key == "weight":
        return parse_number(t, *WEIGHT_RANGE)
    if key == "rate":
        return parse_rate(t)
    if key == "cutoff":
        if t.lower() in OK_WORDS:
            return default
        try:
            return validate_hhmm(t)
        except ValueError:
            return None
    if key == "activity":
        return t if t in ("1", "2", "3", "4") else (default if t.lower() in OK_WORDS or not t else None)
    if key == "tz":
        if not t or t.lower() in OK_WORDS:
            return default
        return parse_tz(t)
    return None


RETRY_HINTS = {
    "sex": SEX_HINT,
    "age": "Не поняла ответ. Напиши возраст числом, например 35.",
    "height": f"Рост нужен числом в сантиметрах, от {HEIGHT_RANGE[0]} до {HEIGHT_RANGE[1]}.",
    "weight": f"Вес нужен числом в килограммах, от {WEIGHT_RANGE[0]} до {WEIGHT_RANGE[1]}.",
    "rate": "Темп нужен числом, от 0.4 до 0.7 кг в неделю, или «ок».",
    "cutoff": "Время нужно в формате ЧЧ:ММ, например 21:00, или «ок».",
    "activity": "Выбери цифру от 1 до 4 или напиши «ок».",
    "tz": TZ_HINT,
}


@router.message(CommandStart())
async def start(msg: Message, state: FSMContext, db: sqlite3.Connection) -> None:
    user = repo.get_user_by_tg(db, msg.from_user.id)
    if user and user["onboarded"]:
        await state.clear()
        await msg.answer(
            "Профиль уже заполнен. Кидай еду текстом/фото/голосом, скриншоты "
            "Mi Fitness и PICOOC — сделаю остальное.\nОтчёты и настройки в приложении: /app",
            reply_markup=menu.bottom_kb(),
        )
        return
    url = get_config().webapp_url
    if url:
        # новому человеку сначала предлагаем настройку в приложении; чат остаётся запасным путём
        await state.clear()
        await msg.answer(WELCOME_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✨ Настроить за минуту", web_app=WebAppInfo(url=url))],
            [InlineKeyboardButton(text="Ответить в чате", callback_data=CHAT_FLOW_CB)],
        ]))
        return
    await _begin_chat_flow(msg, state)


async def _begin_chat_flow(msg: Message, state: FSMContext) -> None:
    await state.set_state(Onboard.q)
    await state.update_data(qi=0, answers={})
    await msg.answer(
        "Привет! Это дневник питания и здоровья. Это не медицинский сервис, "
        "все оценки приблизительные. Настрою профиль — 8 коротких вопросов, "
        "необязательные можно взять дефолтом словом «ок».\n\n" + QUESTIONS[0][1]
    )


@router.callback_query(F.data == CHAT_FLOW_CB)
async def start_chat_flow(cb: CallbackQuery, state: FSMContext, db: sqlite3.Connection) -> None:
    await cb.answer()
    user = repo.get_user_by_tg(db, cb.from_user.id)
    if cb.message is None or (user and user["onboarded"]):
        return
    await _begin_chat_flow(cb.message, state)


@router.message(Onboard.q, ~F.text)
async def onboard_not_text(msg: Message, state: FSMContext) -> None:
    """Стикер, фото, голос посреди настройки: не принимаем за ответ, повторяем вопрос."""
    data = await state.get_data()
    qi = data.get("qi", 0)
    if qi >= len(QUESTIONS):
        await msg.answer("Напиши «да» — сохраню, или исправь пункт: «рост 180».")
        return
    await msg.answer("Мне нужен ответ текстом.\n" + _question(qi)[1])


@router.message(Onboard.q, F.text)
async def onboard_answer(msg: Message, state: FSMContext, db: sqlite3.Connection) -> None:
    data = await state.get_data()
    qi = data["qi"]
    answers = dict(data["answers"])
    text = (msg.text or "").strip()

    # фаза подтверждения: все вопросы собраны
    if qi >= len(QUESTIONS):
        if re.match(r"^(да|верно|ок|сохраняй|всё верно)\b", text.lower()):
            missing = [k for k, _, d in QUESTIONS if d is None and k not in answers and k != "cutoff"]
            if missing:
                await msg.answer(f"Не хватает обязательных данных: {', '.join(missing)}. "
                                 "Напиши их текстом, например «возраст 35».")
                return
            uid, kcal, note = _finish(db, msg.from_user.id, answers)
            await state.clear()
            await msg.answer(
                f"Готово, профиль сохранён. Стартовая цель: <b>≈{kcal} ккал/день</b>.{note}\n\n"
                "Как пользоваться:\n"
                "• еда — просто текстом («съел две котлеты и гречку»), фото или голосовым;\n"
                "• скриншоты Mi Fitness и PICOOC — кидай как есть, распознаю;\n"
                "• вечером пришлю итог дня, по воскресеньям — недельный разбор.\n\n"
                "• отчёты, графики и настройки — в приложении: /app.\n\n"
                f"Помни: это не медицинский сервис, оценки приблизительные. {HEALTH_NOTE} {CARE_NOTE}",
                reply_markup=menu.bottom_kb(),
            )
            return
        # правка пункта текстом: «рост 180», «темп 0.6», «кухня 20:30»
        m = re.match(r"^(пол|возраст|рост|вес|темп|кухня|активность|пояс)\s+(.+)$",
                     text.lower())
        keymap = {"пол": "sex", "возраст": "age", "рост": "height", "вес": "weight",
                  "темп": "rate", "кухня": "cutoff", "активность": "activity",
                  "пояс": "tz"}
        if m:
            key, val = keymap[m.group(1)], m.group(2).strip()
            if key == "age" and val.isdigit() and int(val) < MIN_AGE:
                await msg.answer(UNDERAGE_TEXT)
                return
            parsed = parse_answer(key, val, _question([k for k, _, _ in QUESTIONS].index(key))[2])
            if parsed is None:
                await msg.answer(RETRY_HINTS[key])
                return
            answers[key] = parsed
            await state.update_data(answers=answers)
            await msg.answer("Обновил. Всё верно?\n" + _summary(answers))
            return
        await msg.answer("Напиши «да» — сохраню, или исправь пункт: «рост 180».")
        return

    key, _, default = _question(qi)
    if key == "age" and text.isdigit() and int(text) < MIN_AGE:
        await msg.answer(UNDERAGE_TEXT)
        return
    parsed = parse_answer(key, text, default)
    if parsed is None:
        await msg.answer(RETRY_HINTS[key] + "\n\n" + _question(qi)[1])
        return
    answers[key] = parsed

    qi += 1
    await state.update_data(qi=qi, answers=answers)
    if qi < len(QUESTIONS):
        _, qtext, dflt = _question(qi)
        await msg.answer(f"{qtext}\n(дефолт: {dflt} — можно «ок»)")
    else:
        await msg.answer("Всё верно?\n" + _summary(answers) +
                         "\n\n«да» — сохраню; или исправь пункт: «рост 180»")
