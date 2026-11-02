"""Мини-цель недели: одно простое поведение на «N дней из 7».

Цели только про привычки (белок, вода, запись еды, шаги, сон, поздняя еда): вес и калории не
трогаем, чтобы цель не превращалась в давление на цифры. День засчитывается автоматически по
тем же фактам, что и анализ дня (`insights.day_facts`)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

STEPS_GOAL = 8000
SLEEP_OK_MIN = 7 * 60 * 0.95
MIN_TARGET, MAX_TARGET = 3, 6


@dataclass(frozen=True)
class Kind:
    id: str
    icon: str
    title: str
    ok: Callable[[dict], bool]
    has_data: Callable[[dict], bool]
    hint: str


KINDS: tuple[Kind, ...] = (
    Kind("logging", "📝", "Записывать еду",
         lambda f: f["meals"] >= 2, lambda f: True,
         "Хотя бы два приёма пищи в день, пусть и короткой записью."),
    Kind("protein", "🥩", "Белок в норме",
         lambda f: f["meals"] > 0 and f["protein"] >= 0.9 * f["protein_target"],
         lambda f: f["meals"] > 0,
         "Белок в каждый приём пищи: яйца или творог, курица или рыба, кефир."),
    Kind("water", "💧", "Вода в норме",
         lambda f: f["water"] >= 0.8 * f["water_goal"], lambda f: True,
         "Стакан после пробуждения, перед едой и в 16:00. Чай и кофе тоже считаются."),
    Kind("late", "🌙", "Без поздней еды",
         lambda f: f["meals"] > 0 and f["late_kcal"] == 0, lambda f: f["meals"] > 0,
         "Ужин на час раньше и с белком, так вечером меньше тянет."),
    Kind("steps", "🚶", "Шаги",
         lambda f: (f["steps"] or 0) >= STEPS_GOAL, lambda f: f["steps"] is not None,
         "Одна короткая прогулка в день закрывает почти половину нормы."),
    Kind("sleep", "😴", "Сон 7 часов",
         lambda f: (f["sleep_min"] or 0) >= SLEEP_OK_MIN, lambda f: f["sleep_min"] is not None,
         "Сдвиньте отбой на 30 минут раньше, это самый дешёвый рычаг."),
)
BY_ID = {k.id: k for k in KINDS}


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def suggest(days: list[dict], limit: int = 3) -> list[dict]:
    """Варианты на неделю по слабым местам прошлых 7 дней. Цель чуть выше текущего результата:
    нужна достижимая планка, а не идеал."""
    logged = sum(1 for d in days if d["meals"])
    out: list[dict] = []
    for kind in KINDS:
        with_data = [d for d in days if kind.has_data(d)]
        if kind.id != "logging" and len(with_data) < 4:
            continue                                  # мало данных: не угадываем слабое место
        hits = sum(1 for d in with_data if kind.ok(d))
        if hits >= 6:
            continue                                  # уже получается, цель не нужна
        out.append({"kind": kind.id, "hits": hits,
                    "target": max(MIN_TARGET, min(MAX_TARGET, hits + 2))})
    if logged < 4:                                    # сначала привычка записывать
        out.sort(key=lambda s: s["kind"] != "logging")
    else:
        out.sort(key=lambda s: s["hits"])
    if not out:                                       # всё и так хорошо: держим планку
        out = [{"kind": "protein", "hits": 7, "target": 6}]
    return out[:limit]


def progress(kind_id: str, target: int, start: date, today: date,
             facts_by_day: Callable[[str], dict]) -> dict:
    kind = BY_ID[kind_id]
    cells = []
    for i in range(7):
        d = start + timedelta(days=i)
        if d > today:
            cells.append({"date": d.isoformat(), "state": "future"})
            continue
        ok = kind.ok(facts_by_day(d.isoformat()))
        state = "ok" if ok else ("today" if d == today else "miss")
        cells.append({"date": d.isoformat(), "state": state})
    done = sum(1 for c in cells if c["state"] == "ok")
    left = sum(1 for c in cells if c["state"] in ("future", "today"))
    return {"cells": cells, "done": done, "target": target,
            "achieved": done >= target, "possible": done + left >= target}
