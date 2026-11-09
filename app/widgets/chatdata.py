"""Данные виджетов (chatData, до 1024 байт) и подпись карточки.

Цифры берутся из insights.day_facts: те же, что в дневнике, отчётах и Mini App. В режиме
заботы (care) калорий нет ни в данных, ни в подписи.
"""
from __future__ import annotations

import json
import sqlite3
import time

from app.services import food as foodsvc
from app.services import insights, rhythm
from app.storage import repo
from app.web import queries as webq
from app.web.serializers import targets_json

MAX_BYTES = 1024
MAX_MEALS = 6
NAME_LEN = 22
MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def date_label(local_date: str) -> str:
    """«2026-11-09» → «9 ноя»."""
    y, m, d = local_date.split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]}"


def caption(local_date: str) -> str:
    return f"Карточка дня · {date_label(local_date)}"


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _meal_name(meal) -> str:
    kind = foodsvc.meal_type_ru(meal["meal_type"] or "")
    if kind == (meal["meal_type"] or "") or not kind:
        kind = "Приём"                              # неизвестный тип не светим как есть
    return f"{kind} {meal['local_time']}"[:NAME_LEN]


def day_data(db: sqlite3.Connection, user, local_date: str, now_hhmm: str) -> str:
    """JSON карточки дня. Длина гарантирована: лишние приёмы отбрасываются с начала дня."""
    f = insights.day_facts(db, user, local_date)
    care = bool(f["care"])
    meals = rhythm.eaten(repo.day_meals(db, user["id"], local_date))[-MAX_MEALS:]
    rows = [[_meal_name(m), 0 if care else int(m["kcal_total"])] for m in meals]
    # белки, жиры и углеводы: ровно то, что отдаёт экран «Сегодня» Mini App (/api/day)
    totals, targets = webq.day_totals(db, user["id"], local_date), targets_json(db, user)
    last = repo.last_manual_water(db, user["id"], local_date)
    data = {
        "d": date_label(local_date),
        # виджет читает k как признак корректных данных; в режиме заботы он нулевой и не показывается
        "k": [0, 0] if care else [f["kcal"], f["kcal_target"]],
        "p": [totals["protein"], targets["protein"]],
        "u": [totals["carbs"], targets["carbs"]],
        "f": [totals["fat"], targets["fat"]],
        "w": [f["water"], f["water_goal"]],
        "m": rows,
        "c": 1 if care else 0,
        "t": now_hhmm,
        "lw": 1 if last else 0,
    }
    while len(_dump(data).encode("utf-8")) > MAX_BYTES and data["m"]:
        data["m"] = data["m"][1:]
    out = _dump(data)
    if len(out.encode("utf-8")) > MAX_BYTES:       # недостижимо при нормальных числах
        data.pop("d", None)
        out = _dump(data)
    return out


def pause_data(state: str, since: int | None = None, minutes: int = 10) -> str:
    return _dump({"min": minutes, "st": state, "since": int(since or time.time())})
