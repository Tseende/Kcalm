"""Онбординг /start: 8 вопросов с дефолтами, каждый можно пропустить."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, date

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.config import get_config
from app.domain.targets import MIN_AGE, MIN_BMI, bmi, compute_target, min_goal_weight
from app.routers import menu
from app.storage import repo
from app.timeutils import local_today, minutes_before, validate_hhmm, validate_timezone

router = Router(name="onboarding")

UNDERAGE_TEXT = (
    f"Бот считает дефицит калорий и рассчитан на взрослых ({MIN_AGE}+). "
    "Детям и подросткам диету подбирает только врач: ограничения в питании "
    "в этом возрасте могут навредить росту и здоровью."
)
CARE_NOTE = (
    "Если у тебя был или есть опыт расстройства пищевого поведения или тебе тревожно от цифр, "
    "включи осторожный режим: /care."
)
HEALTH_NOTE = (
    "Если ты беременна или кормишь грудью, у тебя диабет, расстройство пищевого "
    "поведения или другие хронические болезни — обсуди похудение с врачом."
)

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
    user = repo.get_user_by_tg(db, tg_id)
    uid = user["id"] if user else repo.create_user(db, tg_id)
    sex = "male" if data.get("sex", "м").lower().startswith(("м", "m")) else "female"
    today = local_today(data.get("tz", "Europe/Minsk"))
    birth_year = today.year - int(data["age"])
    weight = float(data["weight"])
    height = float(data["height"])
    rate = abs(float(data.get("rate", "0.5")))
    safety_notes: list[str] = []
    if bmi(weight, height) < MIN_BMI:
        # ИМТ уже ниже нормы: дефицит не ставим, цель — поддержание веса
        rate = 0.0
        safety_notes.append(f"ИМТ {bmi(weight, height):.1f} ниже нормы — дефицит не включаю, "
                            "цель считаю на поддержание веса.")
    goal_weight = round(weight - rate * 12, 1)
    floor_weight = min_goal_weight(height)
    if rate and goal_weight < floor_weight:
        goal_weight = min(weight, floor_weight)         # ниже ИМТ 18.5 цель не ставим
        safety_notes.append(f"Целевой вес ограничил {goal_weight:g} кг (ИМТ 18.5).")
    repo.update_user(
        db, uid,
        sex=sex,
        birth_date=f"{birth_year}-01-01",
        height_cm=float(data["height"]),
        start_weight_kg=float(data["weight"]),
        tz=data.get("tz", "Europe/Minsk"),
        eating_cutoff=data.get("cutoff") or _cutoff_default(),
        activity_pa={"1": 1.0, "2": 1.12, "3": 1.27, "4": 1.54}
        .get(data.get("activity", "2"), 1.12),
        goal_weight_kg=goal_weight,
        target_rate_kg_week=-rate,
        onboarded=1,
    )
    t = compute_target(
        sex=sex, age=int(data["age"]), weight_kg=float(data["weight"]),
        height_cm=float(data["height"]),
        pal={"1": 1.25, "2": 1.5, "3": 1.75, "4": 2.2}.get(data.get("activity", "2"), 1.5),
        rate_kg_week=-rate,
        goal_weight_kg=goal_weight,
    )
    kcal = int(t.kcal_target)
    repo.insert_target(db, uid, today.isoformat(), kcal,
                       t.protein_g, t.fat_g, t.carbs_g, t.tdee_est, "onboarding")
    for kind, lt in [("morning", "08:30"), ("midday", "13:30"),
                     ("kitchen_close", minutes_before(data.get("cutoff") or _cutoff_default(), 15)), ("evening", "21:30"),
                     ("weekly", "19:00"), ("water", "16:00"), ("weigh", "07:30")]:
        repo.set_reminder(db, uid, kind, lt)
    if t.floor_applied:
        safety_notes.append("Расчётная цель была ниже безопасного минимума — поднял до порога.")
    note = "".join(f"\n⚠️ {n}" for n in safety_notes)
    return uid, kcal, note


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
    await state.set_state(Onboard.q)
    await state.update_data(qi=0, answers={})
    await msg.answer(
        "Привет! Это дневник питания и здоровья. Это не медицинский сервис, "
        "все оценки приблизительные. Настрою профиль — 8 коротких вопросов, "
        "необязательные можно взять дефолтом словом «ок».\n\n" + QUESTIONS[0][1]
    )


@router.message(Onboard.q)
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
            if key == "sex":
                answers[key] = "м" if val.startswith(("м", "m")) else "ж"
            elif key in ("age",):
                if val.isdigit() and int(val) < MIN_AGE:
                    await msg.answer(UNDERAGE_TEXT)
                    return
                if val.isdigit() and int(val) <= 100:
                    answers[key] = int(val)
            elif key in ("height", "weight"):
                try:
                    answers[key] = float(val.replace(",", "."))
                except ValueError:
                    pass
            elif key == "rate":
                try:
                    answers[key] = str(min(0.7, max(0.4, float(val.replace(",", ".")))))
                except ValueError:
                    pass
            elif key == "cutoff":
                try:
                    answers[key] = validate_hhmm(val)
                except ValueError:
                    pass
            elif key == "activity":
                if val in ("1", "2", "3", "4"):
                    answers[key] = val
            elif key == "tz":
                try:
                    answers[key] = validate_timezone(val)
                except ValueError:
                    pass
            await state.update_data(answers=answers)
            await msg.answer("Обновил. Всё верно?\n" + _summary(answers))
            return
        await msg.answer("Напиши «да» — сохраню, или исправь пункт: «рост 180».")
        return

    key, _, default = _question(qi)
    ok = True
    if key == "sex":
        answers[key] = "м" if text.lower().startswith(("м", "m")) else "ж"
    elif key == "age":
        if text.isdigit() and int(text) < MIN_AGE:
            await msg.answer(UNDERAGE_TEXT)
            return
        if text.isdigit() and int(text) <= 100:
            answers[key] = int(text)
        else:
            ok = False
    elif key == "height":
        try:
            v = float(text.replace(",", "."))
            assert 100 <= v <= 230
            answers[key] = v
        except (ValueError, AssertionError):
            ok = False
    elif key == "weight":
        try:
            v = float(text.replace(",", "."))
            assert 35 <= v <= 300
            answers[key] = v
        except (ValueError, AssertionError):
            ok = False
    elif key == "rate":
        try:
            v = float(text.replace(",", ".")) if text.lower() not in ("ок", "окей") else 0.5
            answers[key] = str(min(0.7, max(0.4, v)))
        except ValueError:
            ok = False
    elif key == "cutoff":
        if text.lower() in ("ок", "окей"):
            answers[key] = default
        else:
            try:
                answers[key] = validate_hhmm(text)
            except ValueError:
                ok = False
    elif key == "activity":
        answers[key] = text if text.strip() in ("1", "2", "3", "4") else default
    elif key == "tz":
        try:
            answers[key] = validate_timezone(text) if text else default
        except ValueError:
            ok = False

    if not ok and key in ("age", "height", "weight"):
        await msg.answer("Не понял ответ. Напиши числом.")
        return
    if not ok:
        answers[key] = default

    qi += 1
    await state.update_data(qi=qi, answers=answers)
    if qi < len(QUESTIONS):
        _, qtext, dflt = _question(qi)
        await msg.answer(f"{qtext}\n(дефолт: {dflt} — можно «ок»)")
    else:
        await msg.answer("Всё верно?\n" + _summary(answers) +
                         "\n\n«да» — сохраню; или исправь пункт: «рост 180»")
