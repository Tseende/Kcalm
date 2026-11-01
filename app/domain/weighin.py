"""Взвешивание натощак: классификация замера и выбор точек для тренда.

Вечерний вес на 0.5–1.5 кг выше утреннего из-за еды, воды и соли. Если смешать их в
одном ряду, тренд «шумит» и дневные скачки пугают. Поэтому замеры помечаются fasting
(1 — натощак, 0 — нет, None — неизвестно), а тренд строится по подходящим.
"""
from __future__ import annotations

import re

FASTING_FROM = "04:00"
FASTING_UNTIL = "12:00"
MIN_FASTING_POINTS = 4          # меньше — тренд строим по всем замерам и предупреждаем
WEEKDAYS = "пн вт ср чт пт сб вс".split()

_NOT_FASTING = re.compile(r"после (?:еды|ужина|обеда|завтрака|тренировки)|не натощак|вечером")
_FASTING = re.compile(r"натощак|утром до еды|до завтрака")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_time(value) -> str | None:
    """'7:05' → '07:05'; всё остальное → None."""
    m = _HHMM.match(str(value or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def caption_hint(text: str | None) -> int | None:
    """Явная подсказка в подписи: «натощак» / «после ужина»."""
    t = (text or "").lower()
    if _NOT_FASTING.search(t):
        return 0
    if _FASTING.search(t):
        return 1
    return None


def classify_fasting(local_time: str | None, first_meal_time: str | None,
                     hint: int | None = None) -> int | None:
    """1 — похоже на утреннее взвешивание натощак, 0 — точно нет, None — не знаем.

    Натощак: между 04:00 и 12:00 и до первого записанного приёма пищи в этот день.
    Если время замера неизвестно, полагаемся только на подсказку пользователя."""
    if hint is not None:
        return hint
    t = normalize_time(local_time)
    if t is None:
        return None
    if t < FASTING_FROM or t >= FASTING_UNTIL:
        return 0
    if first_meal_time and first_meal_time <= t:
        return 0
    return 1


def pick_trend_rows(rows: list, min_fasting: int = MIN_FASTING_POINTS) -> tuple[list, bool]:
    """Замеры для тренда → (строки, пришлось_взять_все).

    Берём замеры с fasting=1 или неизвестным (None); fasting=0 откладываем. Если таких
    слишком мало, ряд строился бы из ничего, и тогда берём все, а вызывающий код
    предупреждает пользователя."""
    good = [r for r in rows if r["fasting"] != 0]
    if len([r for r in good if r["fasting"] == 1]) >= min_fasting or len(good) >= 7:
        return good, False
    return list(rows), len(good) != len(rows)


def reminder_days_label(days: str | None) -> str:
    if not days:
        return "ежедневно"
    if len(days) == 7:
        return "ежедневно"
    return ", ".join(WEEKDAYS[int(d)] for d in days)


def parse_days(text: str) -> str | None:
    """'ежедневно' | 'пн ср пт' | 'пн,чт' → строка цифр или None."""
    t = text.strip().lower()
    if t in ("ежедневно", "каждый день", "daily", "все"):
        return "0123456"
    found = [str(i) for i, name in enumerate(WEEKDAYS) if re.search(rf"\b{name}\b", t)]
    return "".join(sorted(set(found))) or None
