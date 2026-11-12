"""Мотивация: серия дней с записью, вехи по весу, честный прогноз. Только правила, без LLM.

В режиме заботы (care_mode) говорим только о привычке записывать, без веса и калорий.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app.storage import repo

STREAK_MILESTONES = (3, 7, 14, 30, 60, 100)
WEIGHT_STEPS_KG = (1, 2, 3, 5, 7, 10, 15, 20, 25, 30)


def _days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def logging_streak(db: sqlite3.Connection, user_id: int, today: date) -> int:
    """Сколько дней подряд есть хотя бы одна запись о еде. Сегодня без записи серию не рвёт."""
    rows = db.execute(
        """SELECT DISTINCT local_date FROM meals
           WHERE user_id=? AND deleted_at IS NULL AND local_date<=?
           ORDER BY local_date DESC LIMIT 400""",
        (user_id, today.isoformat())).fetchall()
    have = {r["local_date"] for r in rows}
    day = today if today.isoformat() in have else today - timedelta(days=1)
    n = 0
    while day.isoformat() in have:
        n += 1
        day -= timedelta(days=1)
    return n


def weight_progress(db: sqlite3.Connection, user, today: date) -> dict | None:
    """Сколько сброшено с начала пути по сглаженному тренду и ориентир по дате цели."""
    from app.domain.targets import ewma_dated
    start, goal = user["start_weight_kg"], user["goal_weight_kg"]
    rows, _ = repo.trend_weight_series(db, user["id"], "1900-01-01", today.isoformat())
    if not start or len(rows) < 4:
        return None
    pts = ewma_dated([(date.fromisoformat(r["local_date"]).toordinal(), r["weight_kg"])
                      for r in rows])
    now_w = pts[-1][1]
    out = {"start": float(start), "now": round(now_w, 1),
           "lost": round(float(start) - now_w, 1)}
    if goal and start > goal:
        out["percent"] = max(0, min(100, round(out["lost"] / (start - goal) * 100)))
        # скорость по последним 28 дням; прогноз только если вес реально идёт вниз
        old = [p for p in pts if p[0] >= pts[-1][0] - 28]
        span = old[-1][0] - old[0][0]
        if span >= 14:
            per_week = (old[0][1] - old[-1][1]) / span * 7
            if per_week >= 0.1 and now_w > goal:
                weeks = (now_w - goal) / per_week
                if weeks <= 104:
                    out["eta"] = (today + timedelta(days=round(weeks * 7))).isoformat()
                    out["per_week"] = round(per_week, 2)
    return out


def _weight_milestone(lost: float) -> int | None:
    reached = [s for s in WEIGHT_STEPS_KG if lost >= s]
    return reached[-1] if reached else None


def build(db: sqlite3.Connection, user, today: date) -> dict:
    """{lines: [...]} — короткие тёплые строки для «Анализа» и пушей, без калорий."""
    care = bool(user["care_mode"])
    streak = logging_streak(db, user["id"], today)
    lines: list[str] = []
    if streak >= 2:
        msg = f"🔥 Записываешь питание {streak} {_days_word(streak)} подряд."
        if streak in STREAK_MILESTONES:
            msg += " Это серьёзная привычка, так держать!"
        lines.append(msg)
    elif streak == 0:
        lines.append("🌱 Начать серию просто: запиши сегодня любой приём пищи.")
    out = {"streak": streak, "lines": lines}
    if care:
        return out
    wp = weight_progress(db, user, today)
    if wp:
        out["weight"] = wp
        if wp["lost"] >= 0.5:
            step = _weight_milestone(wp["lost"])
            msg = f"🏁 С начала пути −{wp['lost']:g} кг"
            if wp.get("percent"):
                msg += f" ({wp['percent']}% пути к цели)"
            lines.append(msg + ("." if not step else f". Рубеж −{step} кг пройден!"))
        if wp.get("eta"):
            d = date.fromisoformat(wp["eta"])
            lines.append(f"📅 Если темп сохранится (~{wp['per_week']:g} кг/нед), цель ≈ {d:%d.%m.%Y}.")
    return out
