"""Анализ дня для Mini App: что получилось, что поправить. Только правила, без LLM и сети.

Берёт те же факты и советы, что вечерний итог раньше присылал в чат (`services.insights`)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from app.services import insights, motivation

CLOSE_HOUR = 20          # после этого часа сегодняшний день уже можно оценивать целиком
STATUS = {"✅": "good", "🟡": "ok", "⚠️": "bad", "—": "none"}


KEYS = {"Калории": "kcal", "Белок": "protein", "Вода": "water", "Шаги": "steps", "Сон": "sleep"}
MORE_IS_BETTER = {"protein", "water", "steps", "sleep"}     # перебор по ним — не ошибка
FILLS_BY_DAY = {"kcal", "protein", "water", "steps"}        # копятся в течение дня: до вечера недобор не оцениваем


def _n(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ")


def _row_numbers(key: str, f: dict) -> tuple[float | None, float | None]:
    """(факт, ориентир) числами: экран рисует полоску по ним, а не разбирает строки."""
    if key == "kcal":
        return f["kcal"], f["kcal_target"]
    if key == "protein":
        return f["protein"], f["protein_target"]
    if key == "water":
        return f["water"], f["water_goal"]
    if key == "steps":
        return f["steps"], insights.STEPS_GOAL
    return f["sleep_min"], insights.SLEEP_GOAL_MIN


def _hint(key: str, value: float | None, goal: float | None, status: str, closed: bool) -> str | None:
    """Короткая подпись под полоской: сколько осталось, не хватило или сверх нормы."""
    if value is None or not goal or status == "none":
        return None
    unit = {"kcal": " ккал", "protein": " г", "water": " мл", "steps": "", "sleep": " мин"}[key]
    diff = goal - value
    if key == "kcal":
        if diff < 0:
            return "в норме" if status == "good" else f"на {_n(-diff)} ккал выше нормы"
        if not closed:
            return f"осталось {_n(diff)} ккал"
        return "в норме" if status == "good" else f"на {_n(diff)} ккал ниже нормы"
    if diff <= 0:
        return "норма выполнена" if key == "sleep" else "цель выполнена"
    if key == "sleep":
        return f"меньше нормы на {_n(diff)} мин"
    return f"ещё {_n(diff)}{unit}" if not closed else f"не хватило {_n(diff)}{unit}"


def _rows(f: dict, closed: bool) -> list[dict]:
    out = []
    for label, value, goal, mark in insights.scorecard_rows(f):
        key = KEYS.get(label, "")
        status = STATUS.get(mark, "none")
        num, target = _row_numbers(key, f) if key else (None, None)
        # день ещё идёт: недобор по тому, что копится за день, не ошибка, а «в процессе»
        if (not closed and key in FILLS_BY_DAY and status in ("ok", "bad")
                and num is not None and target and num < target):
            status = "progress"
        out.append({"key": key, "label": label, "value": value, "goal": goal, "status": status,
                    "num": num, "max": target, "more_is_better": key in MORE_IS_BETTER,
                    "hint": _hint(key, num, target, status, closed) if key else None})
    return out


def score_of(rows: list[dict]) -> int | None:
    """Оценка дня, %: доля показателей в норме, «почти» считается за половину. «В процессе» не учитываем."""
    rated = [r["status"] for r in rows if r["status"] in ("good", "ok", "bad")]
    if not rated:
        return None
    return round(100 * (rated.count("good") + 0.5 * rated.count("ok")) / len(rated))


def _headline(f: dict, score: int | None, closed: bool, incomplete: bool) -> str:
    """Заголовок согласован с оценкой: кольцо, картинка и слова говорят одно и то же."""
    if not closed:
        return "День ещё идёт"
    if not f["meals"]:
        return "Еда за день не записана"
    if incomplete:
        return "Похоже, записано не всё"
    if score is None:
        return "Спокойный день"
    return "Хороший день" if score >= 75 else "В целом неплохо" if score >= 50 else "Есть что поправить"


def build(db: sqlite3.Connection, user, day: date, now: datetime) -> dict:
    key = day.isoformat()
    facts = insights.day_facts(db, user, key)
    closed = day < now.date() or now.hour >= CLOSE_HOUR
    good, tips = insights.day_tips(facts, closed=closed)
    incomplete = closed and insights.maybe_incomplete(facts)
    mot = motivation.build(db, user, day)
    rows = _rows(facts, closed)
    score = score_of(rows)
    return {
        "date": key,
        "closed": closed,
        "motivation": mot["lines"],
        "headline": _headline(facts, score, closed, incomplete),
        "score": score,
        "incomplete": incomplete,
        "rows": rows,
        "good": [g[:1].upper() + g[1:] for g in good],
        "tips": [{"icon": t.icon, "title": t.title, "text": t.text,
                  **({"action": t.action} if t.action else {})} for t in tips],
    }
