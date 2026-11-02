"""Мини-цель недели и итог прошлой: правила без LLM, прогресс из записей."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app.domain import weekgoal as wg
from app.services import insights
from app.storage import repo


def _meta(kind_id: str) -> dict:
    k = wg.BY_ID[kind_id]
    return {"kind": k.id, "icon": k.icon, "title": k.title, "hint": k.hint}


def _cheer(p: dict, today_in_week: bool) -> str:
    if p["achieved"]:
        return "Цель недели выполнена. Это уже привычка, а не случайность."
    if not p["possible"]:
        return "На этой неделе планка не взялась, и это нормально. С понедельника попробуем снова."
    left = p["target"] - p["done"]
    return f"Осталось {left} {insights.days_word(left)} из запланированных."


def build(db: sqlite3.Connection, user, today: date) -> dict:
    """Состояние для экрана: цель этой недели с прогрессом, либо варианты на выбор."""
    start = wg.week_start(today)
    row = repo.weekly_goal(db, user["id"], start.isoformat())
    cache: dict[str, dict] = {}

    def facts(day: str) -> dict:
        if day not in cache:
            cache[day] = insights.day_facts(db, user, day)
        return cache[day]

    if row and row["kind"] in wg.BY_ID:
        p = wg.progress(row["kind"], row["target_days"], start, today, facts)
        return {"week_start": start.isoformat(), "goal": {**_meta(row["kind"]), **p,
                                                           "message": _cheer(p, True)},
                "options": []}

    last_week = [facts((today - timedelta(days=i)).isoformat()) for i in range(7, 0, -1)]
    options = [{**_meta(s["kind"]), "target": s["target"], "hits": s["hits"]}
               for s in wg.suggest(last_week)]
    return {"week_start": start.isoformat(), "goal": None, "options": options}


def choose(db: sqlite3.Connection, user, today: date, kind: str, target: int) -> None:
    if kind not in wg.BY_ID:
        raise ValueError("Неизвестная цель.")
    if not wg.MIN_TARGET <= target <= wg.MAX_TARGET:
        raise ValueError(f"Дней в неделе: от {wg.MIN_TARGET} до {wg.MAX_TARGET}.")
    repo.set_weekly_goal(db, user["id"], wg.week_start(today).isoformat(), kind, target)


def recap(db: sqlite3.Connection, user, today: date) -> dict | None:
    """Итог прошлой недели: что получилось (по тем же правилам, что и недельный отчёт) и
    выполнена ли была цель. Показываем только при данных хотя бы за 3 дня."""
    this_start = wg.week_start(today)
    prev_start = this_start - timedelta(days=7)
    days = [insights.day_facts(db, user, (prev_start + timedelta(days=i)).isoformat())
            for i in range(7)]
    if sum(1 for d in days if d["meals"]) < 3:
        return None
    prev_days = [insights.day_facts(db, user, (prev_start - timedelta(days=7 - i)).isoformat())
                 for i in range(7)]
    care = bool(user["care_mode"])
    good, _ = insights.week_tips({"days": days, "prev": prev_days, "care": care,
                                  "tdee_est": None, "bmr": None})
    row = repo.weekly_goal(db, user["id"], prev_start.isoformat())
    goal = None
    if row and row["kind"] in wg.BY_ID:
        p = wg.progress(row["kind"], row["target_days"], prev_start, prev_start + timedelta(days=6),
                        lambda d: days[(date.fromisoformat(d) - prev_start).days])
        goal = {**_meta(row["kind"]), "done": p["done"], "target": p["target"],
                "achieved": p["achieved"]}
    if not good and not goal:
        return None
    return {"week_start": prev_start.isoformat(),
            "good": [g[:1].upper() + g[1:] for g in good], "goal": goal}
