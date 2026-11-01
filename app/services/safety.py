"""Сбор сигналов заботы из записей, мягкие меры и /care."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app.domain import safety as dom
from app.domain.targets import bmi, weekly_slope
from app.storage import repo
from app.textutil import esc


def gather(db: sqlite3.Connection, user, today: date) -> dom.SafetyInputs:
    uid = user["id"]
    frm14 = (today - timedelta(days=13)).isoformat()
    to = today.isoformat()
    very_low = logged = 0
    for offset in range(14):
        day = (today - timedelta(days=offset)).isoformat()
        totals = repo.day_totals(db, uid, day)
        if totals["meals"]:
            logged += 1
        if totals["meals"] >= 2 and totals["kcal"] < dom.VERY_LOW_KCAL:
            very_low += 1
    events14 = repo.cravings_range(db, uid, frm14, to)
    events30 = repo.cravings_range(db, uid, (today - timedelta(days=29)).isoformat(), to)
    busy = db.execute(
        """SELECT COUNT(*) AS n FROM (
             SELECT date(created_at) d FROM raw_observations
             WHERE user_id=? AND kind='picooc_measure' AND date(created_at) >= ?
             GROUP BY date(created_at) HAVING COUNT(*) >= 3)""",
        (uid, frm14)).fetchone()["n"]

    rows, _ = repo.trend_weight_series(db, uid, frm14, to)
    points = [(date.fromisoformat(r["local_date"]).toordinal(), r["weight_kg"]) for r in rows]
    slope = weekly_slope(points, window_days=14) if len(points) >= 4 else None
    weight = rows[-1]["weight_kg"] if rows else None
    loss_pct = None
    if slope is not None and weight and points[-1][0] - points[0][0] >= 7:
        loss_pct = -slope / weight * 100
    current_bmi = bmi(weight, user["height_cm"]) if weight and user["height_cm"] else None
    return dom.SafetyInputs(
        very_low_days=very_low, logged_days=logged,
        overate_14d=sum(1 for e in events14 if e["outcome"] == "overate"),
        risk_events_30d=sum(1 for e in events30 if e["risk_flag"]),
        busy_weigh_days=busy, bmi=current_bmi,
        target_rate=float(user["target_rate_kg_week"] or 0),
        loss_pct_week=loss_pct, care_mode=bool(user["care_mode"]))


def assess(db: sqlite3.Connection, user, today: date) -> list[dom.Flag]:
    return dom.assess(gather(db, user, today))


def weekly_lines(db: sqlite3.Connection, user, today: date,
                 flags: list[dom.Flag] | None = None) -> list[str]:
    """Блок «забота» для недельного отчёта; пусто, если сигналов нет."""
    flags = assess(db, user, today) if flags is None else flags
    if not flags:
        return []
    lines = ["🛡 Хочу сказать по-доброму:"] + [f"• {esc(f.text)}" for f in flags]
    if dom.reduce_weighing(flags, bool(user["care_mode"])):
        note = _reduce_weighing(db, user)
        if note:
            lines.append(note)
    if dom.freeze_target_decrease(flags, bool(user["care_mode"])):
        lines.append("Пока это так, я не буду снижать цель по калориям.")
    if dom.needs_professional(flags):
        lines.append(esc(dom.CARE_PROFESSIONAL))
    return lines


def _reduce_weighing(db: sqlite3.Connection, user) -> str | None:
    """Ежедневное напоминание о весе → 2 раза в неделю (пн, чт). Один раз, с объяснением."""
    reminder = repo.due_reminders(db, user["id"], "weigh")
    if not reminder or not reminder["enabled"]:
        return None
    if reminder["days"] and len(reminder["days"]) < 7:
        return None
    repo.set_reminder(db, user["id"], "weigh", reminder["local_time"], True, days="03")
    return ("Поэтому напоминание о взвешивании теперь по понедельникам и четвергам. "
            "Изменить: /weighin.")


CARE_EXPLAIN = (
    "🛡 Осторожный режим для тех, у кого был или есть опыт расстройства пищевого поведения, "
    "кому тревожно от цифр, или кто просто хочет мягче.\n"
    "Что меняется: напоминание о взвешивании выключается; темп не быстрее 0.25 кг в неделю; "
    "цель по калориям автоматически не снижается; в отчётах нет разбора плато и недоучёта.\n"
    "Включить: /care on. Выключить: /care off.\n"
    "Если вы сейчас лечитесь или наблюдаетесь у врача, диету лучше согласовать с ним."
)


def set_care(db: sqlite3.Connection, user, on: bool) -> str:
    uid = user["id"]
    repo.update_user(db, uid, care_mode=int(on))
    if not on:
        return ("Осторожный режим выключен. Напоминание о взвешивании само не включается: "
                "/weighin on. Темп цели остался прежним, поменять его можно, написав мне.")
    rate = max(float(user["target_rate_kg_week"] or 0), -0.25)
    repo.update_user(db, uid, target_rate_kg_week=rate)
    reminder = repo.due_reminders(db, uid, "weigh")
    if reminder:
        repo.set_reminder(db, uid, "weigh", reminder["local_time"], False)
    return ("🛡 Осторожный режим включён: без напоминаний о весе, темп не быстрее 0.25 кг "
            "в неделю, цель автоматически не снижается. Я рядом, без давления на цифры. "
            "Если захочется поговорить, пиши /craving в любое время.")
