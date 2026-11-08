"""Анализ дня для Mini App: что получилось, что поправить. Только правила, без LLM и сети.

Берёт те же факты и советы, что вечерний итог раньше присылал в чат (`services.insights`)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from app.services import insights

CLOSE_HOUR = 20          # после этого часа сегодняшний день уже можно оценивать целиком
STATUS = {"✅": "good", "🟡": "ok", "⚠️": "bad", "—": "none"}


def _headline(f: dict, good: list[str], tips: list[insights.Tip], incomplete: bool) -> str:
    if not f["meals"]:
        return "Еда за день не записана"
    if incomplete:
        return "Похоже, записано не всё"
    urgent = [t for t in tips if t.prio <= 2]
    if urgent:
        return "Есть что поправить"
    if len(good) >= 3:
        return "Хороший день"
    return "Спокойный день" if not tips else "В целом неплохо"


def build(db: sqlite3.Connection, user, day: date, now: datetime) -> dict:
    key = day.isoformat()
    facts = insights.day_facts(db, user, key)
    closed = day < now.date() or now.hour >= CLOSE_HOUR
    good, tips = insights.day_tips(facts, closed=closed)
    incomplete = closed and insights.maybe_incomplete(facts)
    return {
        "date": key,
        "closed": closed,
        "headline": _headline(facts, good, tips, incomplete),
        "incomplete": incomplete,
        "rows": [{"label": r[0], "value": r[1], "goal": r[2], "status": STATUS.get(r[3], "none")}
                 for r in insights.scorecard_rows(facts)],
        "good": [g[:1].upper() + g[1:] for g in good],
        "tips": [{"icon": t.icon, "title": t.title, "text": t.text,
                  **({"action": t.action} if t.action else {})} for t in tips],
    }
