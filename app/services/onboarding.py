"""Первичная настройка профиля: одна логика для чата (/start) и Mini App (POST /api/onboarding).

Безопасность: 18+, при ИМТ < 18,5 дефицит не включается, цель не ниже ИМТ 18,5,
в осторожном режиме (care_mode) темп не быстрее 0,25 кг/нед и без напоминаний о весе.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import timedelta

from app.domain.targets import MAX_RATE, MIN_AGE, MIN_BMI, bmi, compute_target, min_goal_weight
from app.services import profile as profilesvc
from app.services import water as watersvc
from app.storage import repo
from app.timeutils import local_today, minutes_before

UNDERAGE_TEXT = (
    f"Бот считает дефицит калорий и рассчитан на взрослых ({MIN_AGE}+). "
    "Детям и подросткам диету подбирает только врач: ограничения в питании "
    "в этом возрасте могут навредить росту и здоровью."
)
HEALTH_NOTE = (
    "Если ты беременна или кормишь грудью, у тебя диабет, расстройство пищевого "
    "поведения или другие хронические болезни — обсуди похудение с врачом."
)
CARE_NOTE = (
    "Если у тебя был или есть опыт расстройства пищевого поведения или тебе тревожно от цифр, "
    "включи осторожный режим: /care."
)

CARE_MAX_RATE = 0.25
DEFAULT_TZ = "Europe/Minsk"
# kind → время по умолчанию (kitchen_close считается от eating_cutoff)
REMINDER_TIMES = {"morning": "08:30", "midday": "13:30", "evening": "21:30",
                  "weekly": "19:00", "water": "16:00", "weigh": "07:30"}


class OnboardingError(ValueError):
    """Данные не годятся для расчёта; текст можно показывать человеку."""

    def __init__(self, message: str, code: str = "bad_profile") -> None:
        super().__init__(message)
        self.code = code


def complete(db: sqlite3.Connection, tg_id: int, data: dict, *, log_weight: bool = True) -> dict:
    """Сохранить профиль, посчитать норму, поставить напоминания, записать стартовый вес.

    data: sex ('male'|'female'), birth_year, height_cm, weight_kg, eating_cutoff, tz, activity (1–4),
    rate_kg_week (0–0.7, по умолчанию 0.5), goal_weight_kg (по умолчанию вес − темп×12 недель),
    care_mode (bool), reminders ({morning, midday, evening, weekly, water, weigh: bool}; нет — все включены).
    log_weight: записать стартовый вес в журнал (чат-онбординг не пишет: вес берётся из профиля).
    Возвращает {"uid", "kcal", "profile", "targets", "notes", "goal_date"}.
    """
    tz = data.get("tz") or DEFAULT_TZ
    today = local_today(tz)
    sex = data["sex"]
    age = today.year - int(data["birth_year"])
    if age < MIN_AGE:
        raise OnboardingError(UNDERAGE_TEXT, "underage")
    weight, height = float(data["weight_kg"]), float(data["height_cm"])
    activity = int(data.get("activity") or 2)
    care = bool(data.get("care_mode"))
    cutoff = data.get("eating_cutoff") or "21:00"
    rate = data.get("rate_kg_week")
    rate = 0.5 if rate is None else abs(float(rate))
    rate = min(rate, MAX_RATE)
    notes: list[str] = []

    if bmi(weight, height) < MIN_BMI:
        # ИМТ уже ниже нормы: дефицит не ставим, цель — поддержание веса
        rate = 0.0
        notes.append(f"ИМТ {bmi(weight, height):.1f} ниже нормы — дефицит не включаю, "
                     "цель считаю на поддержание веса.")
    if care and rate > CARE_MAX_RATE:
        rate = CARE_MAX_RATE
    goal = data.get("goal_weight_kg")
    goal = round(weight - rate * 12, 1) if goal is None else round(float(goal), 1)
    floor_weight = min_goal_weight(height)
    if rate and goal < floor_weight:
        goal = min(weight, floor_weight)                 # ниже ИМТ 18.5 цель не ставим
        notes.append(f"Целевой вес ограничил {goal:g} кг (ИМТ 18.5).")
    if not rate or goal >= weight:
        rate, goal = 0.0, weight                          # держим вес: цель равна текущему весу

    user = repo.get_user_by_tg(db, tg_id)
    uid = user["id"] if user else repo.create_user(db, tg_id)
    pa, pal = profilesvc.ACTIVITY.get(activity, profilesvc.ACTIVITY[2])
    repo.update_user(
        db, uid,
        sex=sex, birth_date=f"{int(data['birth_year'])}-01-01", height_cm=height,
        start_weight_kg=weight, tz=tz, eating_cutoff=cutoff, activity_pa=pa,
        goal_weight_kg=goal, target_rate_kg_week=-rate, care_mode=int(care), onboarded=1,
    )
    # стартовый вес — первая запись журнала веса (без неё график и тренд пусты до первого взвешивания)
    if log_weight:
        repo.save_body_measurement(db, uid, today.isoformat(), {"weight_kg": weight}, "manual", 1.0)

    t = compute_target(sex=sex, age=age, weight_kg=weight, height_cm=height, pal=pal,
                       rate_kg_week=-rate, goal_weight_kg=goal)
    kcal = int(t.kcal_target)
    repo.insert_target(db, uid, today.isoformat(), kcal, t.protein_g, t.fat_g, t.carbs_g,
                       t.tdee_est, "onboarding")
    if t.floor_applied:
        notes.append("Расчётная цель была ниже безопасного минимума — поднял до порога.")

    wanted = data.get("reminders") or {}
    for kind, local_time in REMINDER_TIMES.items():
        enabled = bool(wanted.get(kind, True))
        if kind == "weigh" and care:
            enabled = False                              # осторожный режим: без напоминаний о весе
        repo.set_reminder(db, uid, kind, local_time, enabled)
    repo.set_reminder(db, uid, "kitchen_close", minutes_before(cutoff, 15), True)

    fresh = repo.get_user_by_tg(db, tg_id)
    goal_date = None
    if rate and goal < weight:
        goal_date = (today + timedelta(days=math.ceil((weight - goal) / rate * 7))).isoformat()
    return {
        "uid": uid, "kcal": kcal,
        "profile": profilesvc.profile_json(db, fresh, today),
        "targets": {"kcal": kcal, "protein": round(t.protein_g), "fat": round(t.fat_g),
                    "carbs": round(t.carbs_g), "water_ml": watersvc.goal_for(db, fresh)},
        "notes": notes, "goal_date": goal_date,
    }
