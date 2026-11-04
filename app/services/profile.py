"""Профиль, цели и напоминания для Mini App: те же расчёты, что и в онбординге бота."""
from __future__ import annotations

import sqlite3
from datetime import date

from app.domain.targets import (
    MAX_RATE, bmi, compute_target, kcal_floor_check, min_goal_weight, pal_from_pa,
)
from app.storage import repo

# уровень активности → (PA для профиля, PAL для расчёта); как в онбординге
ACTIVITY = {1: (1.0, 1.25), 2: (1.12, 1.5), 3: (1.27, 1.75), 4: (1.54, 2.2)}
DEFAULT_AGE = 35

# kind → (название, подпись, время по умолчанию). kitchen_close считается от «не есть после».
REMINDERS = {
    "morning": ("Утро", "План на день", "08:30"),
    "midday": ("День", "Как идёт питание", "13:30"),
    "evening": ("Вечер", "Итоги дня", "21:30"),
    "weekly": ("Итоги недели", "По воскресеньям", "19:00"),
    "water": ("Вода", "Напомнить выпить воды", "16:00"),
    "weigh": ("Взвешивание", "Записать вес", "07:30"),
}


class ProfileError(ValueError):
    pass


def activity_level(pa: float | None) -> int:
    if pa is None:
        return 2
    return min(ACTIVITY, key=lambda k: abs(ACTIVITY[k][0] - pa))


def age_of(user, today: date) -> int:
    bd = user["birth_date"]
    return today.year - int(str(bd)[:4]) if bd else DEFAULT_AGE


def profile_json(db: sqlite3.Connection, user, today: date) -> dict:
    rate = -float(user["target_rate_kg_week"] or 0)
    return {
        "sex": user["sex"], "birth_year": int(str(user["birth_date"])[:4]) if user["birth_date"] else None,
        "age": age_of(user, today) if user["birth_date"] else None,
        "height_cm": user["height_cm"], "goal_weight_kg": user["goal_weight_kg"],
        "weight_kg": repo.latest_weight_kg(db, user["id"]),
        "activity": activity_level(user["activity_pa"]), "rate_kg_week": max(0.0, round(rate, 2)),
        "tz": user["tz"], "eating_cutoff": user["eating_cutoff"],
    }


def recompute_target(db: sqlite3.Connection, user, today: date, reason: str) -> dict:
    """Новая норма по текущему профилю и последнему весу. Возвращает {old, new, notes}."""
    weight = repo.latest_weight_kg(db, user["id"])
    height = user["height_cm"]
    if not weight or not height:
        raise ProfileError("Нужны рост и вес: сначала запишите вес.")
    age = age_of(user, today)
    rate = -float(user["target_rate_kg_week"] or 0)
    notes: list[str] = []
    if rate > 0 and (age < 18 or bmi(weight, height) < 18.5):
        notes.append("Дефицит не включён: возраст или ИМТ ниже безопасного порога, считаю поддержание.")
    t = compute_target(sex=user["sex"], age=age, weight_kg=weight, height_cm=height,
                       pal=pal_from_pa(user["activity_pa"]), rate_kg_week=-rate,
                       goal_weight_kg=user["goal_weight_kg"])
    if t.floor_applied:
        notes.append("Расчёт был ниже безопасного минимума, поднял до порога.")
    old = repo.current_target(db, user["id"])
    repo.insert_target(db, user["id"], today.isoformat(), int(t.kcal_target),
                       t.protein_g, t.fat_g, t.carbs_g, t.tdee_est, reason)
    return {"old": int(old["kcal_target"]) if old else None, "new": int(t.kcal_target), "notes": notes}


def update_profile(db: sqlite3.Connection, user, data: dict, today: date) -> dict:
    """data уже проверен по границам; здесь бизнес-правила. Пересчитывает норму, если изменилось влияющее поле."""
    fields: dict = {}
    recalc = bool(data.get("recalc"))               # «пересчитать норму по профилю» без других правок
    if data.get("sex") is not None:
        fields["sex"] = data["sex"]
        recalc = True
    if data.get("birth_year") is not None:
        fields["birth_date"] = f"{int(data['birth_year'])}-01-01"
        recalc = True
    if data.get("height_cm") is not None:
        fields["height_cm"] = float(data["height_cm"])
        recalc = True
    if data.get("activity") is not None:
        fields["activity_pa"] = ACTIVITY[int(data["activity"])][0]
        recalc = True
    if data.get("rate_kg_week") is not None:
        fields["target_rate_kg_week"] = -min(MAX_RATE, max(0.0, float(data["rate_kg_week"])))
        recalc = True
    height = fields.get("height_cm", user["height_cm"])
    if data.get("goal_weight_kg") is not None:
        goal = round(float(data["goal_weight_kg"]), 1)
        if height and goal < min_goal_weight(height):
            raise ProfileError(f"Ниже {min_goal_weight(height):g} кг цель не ставим (ИМТ 18,5).")
        fields["goal_weight_kg"] = goal
        recalc = True
    for key in ("tz", "eating_cutoff"):
        if data.get(key) is not None:
            fields[key] = data[key]
    if not fields and not recalc:
        return {"changed": False, "target": None}
    if fields:
        repo.update_user(db, user["id"], **fields)
    if "eating_cutoff" in fields:
        from app.timeutils import minutes_before
        repo.set_reminder(db, user["id"], "kitchen_close", minutes_before(fields["eating_cutoff"], 15))
    result = None
    if recalc:
        fresh = repo.get_user_by_tg(db, user["tg_id"])
        result = recompute_target(db, fresh, today, "profile")
    return {"changed": True, "target": result}


def set_manual_target(db: sqlite3.Connection, user, today: date, kcal: int,
                      protein: float, fat: float, carbs: float) -> dict:
    age = age_of(user, today)
    weight = repo.latest_weight_kg(db, user["id"]) or 70.0
    height = user["height_cm"] or 170.0
    floor = kcal_floor_check(kcal, user["sex"], age, weight, height)
    final, raised = floor
    old = repo.current_target(db, user["id"])
    repo.insert_target(db, user["id"], today.isoformat(), int(final), protein, fat, carbs,
                       int(old["tdee_est"]) if old and old["tdee_est"] else None, "manual")
    return {"kcal": int(final), "raised": bool(raised)}


def reminders_json(db: sqlite3.Connection, user) -> list[dict]:
    rows = {r["kind"]: r for r in db.execute("SELECT * FROM reminders WHERE user_id=?", (user["id"],))}
    out = []
    for kind, (title, sub, default) in REMINDERS.items():
        r = rows.get(kind)
        out.append({"kind": kind, "title": title, "sub": sub,
                    "time": r["local_time"] if r else default, "enabled": bool(r["enabled"]) if r else False})
    return out


def set_reminder(db: sqlite3.Connection, user, kind: str, time: str | None, enabled: bool | None) -> None:
    if kind not in REMINDERS:
        raise ProfileError("Неизвестное напоминание.")
    cur = repo.due_reminders(db, user["id"], kind)
    repo.set_reminder(db, user["id"], kind,
                      time or (cur["local_time"] if cur else REMINDERS[kind][2]),
                      bool(cur["enabled"]) if cur and enabled is None else (True if enabled is None else enabled))


def delete_all_data(db: sqlite3.Connection, user) -> None:
    """Полное удаление: пользователь и всё, что связано (каскадом), плюс таблицы без внешнего ключа."""
    uid = user["id"]
    for sql in ("DELETE FROM recent_files WHERE user_id=?", "DELETE FROM chat_messages WHERE user_id=?",
                "DELETE FROM llm_calls WHERE user_id=?", "DELETE FROM model_usage_daily WHERE user_id=?",
                "DELETE FROM audit_events WHERE user_id=?", "DELETE FROM foods WHERE owner_user_id=?",
                "DELETE FROM users WHERE id=?"):
        try:
            db.execute(sql, (uid,))
        except sqlite3.OperationalError:
            continue
    db.commit()
