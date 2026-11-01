"""Аналитика: поздняя еда, сон↔еда, плато, приверженность. Только описательные
статистики с явными порогами; порог не достигнут — молчать."""
from __future__ import annotations

from dataclasses import dataclass, field


def _parse_hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def is_late(local_time: str, cutoff: str) -> bool:
    """Поздняя еда. Ровно в cutoff — ещё не поздно (golden-кейс §6 формул)."""
    t = _parse_hhmm(local_time)
    c = _parse_hhmm(cutoff)
    return t > c


@dataclass
class DayIntake:
    local_date: str
    kcal: int
    late_kcal: int
    meals: int


def late_kcal_for_day(meal_times_kcal: list[tuple[str, int]], cutoff: str) -> int:
    """late_kcal = Σ ккал приёмов с local_time > cutoff. 21:00 ровно — НЕ поздний."""
    return sum(k for t, k in meal_times_kcal if is_late(t, cutoff))


def week_late_summary(days: list[DayIntake]) -> dict:
    evenings = [d for d in days if d.late_kcal > 0]
    kcal_all = sum(d.kcal for d in days) or 1
    return {
        "late_evenings": len(evenings),
        "avg_late_kcal": int(sum(d.late_kcal for d in evenings) / len(evenings)) if evenings else 0,
        "late_share": round(sum(d.late_kcal for d in days) / kcal_all, 2),
        "days": len(days),
    }


def compare_late_weeks(week_summaries: list[dict]) -> dict | None:
    """Недели ≤1 позднего вечера против ≥4. Показывать только при ≥3 неделях в группе."""
    calm = [w["avg_late_kcal"] and w for w in week_summaries if w["late_evenings"] <= 1]
    wild = [w for w in week_summaries if w["late_evenings"] >= 4]
    if len(calm) < 3 or len(wild) < 3:
        return None
    return {
        "calm_avg_kcal": int(sum(w["avg_late_kcal"] for w in calm) / len(calm)),
        "wild_avg_kcal": int(sum(w["avg_late_kcal"] for w in wild) / len(wild)),
        "calm_weeks": len(calm),
        "wild_weeks": len(wild),
    }


# ------------------------------------------------------------------ сон ↔ еда

@dataclass
class SleepFoodPair:
    wake_date: str
    sleep_hours: float
    next_day_kcal: int
    next_day_late_kcal: int


def sleep_food_insight(pairs: list[SleepFoodPair], min_n: int = 10,
                       min_diff: int = 150) -> dict | None:
    """Группы < 6.5 ч и ≥ 7.0 ч; n ≥ 10 в каждой и разница ≥ 150 ккал, иначе None."""
    short = [p for p in pairs if p.sleep_hours < 6.5]
    good = [p for p in pairs if p.sleep_hours >= 7.0]
    if len(short) < min_n or len(good) < min_n:
        return None
    avg_short = sum(p.next_day_kcal for p in short) / len(short)
    avg_good = sum(p.next_day_kcal for p in good) / len(good)
    diff = avg_short - avg_good
    if abs(diff) < min_diff:
        return None
    return {
        "short_n": len(short),
        "good_n": len(good),
        "short_avg_kcal": int(avg_short),
        "good_avg_kcal": int(avg_good),
        "diff_kcal": int(diff),
        "note": "наблюдение, не доказательство",
    }


# ------------------------------------------------------------------- плато

@dataclass
class PlateauCheck:
    is_plateau: bool
    slope_kg_week: float | None = None
    avg_deficit: int | None = None


def plateau_check(slope_kg_week: float | None, avg_deficit_kcal: int | None) -> PlateauCheck:
    """плато = |slope| < 0.1 кг/нед при среднем дефиците ≥ 200 ккал/день."""
    if slope_kg_week is None or avg_deficit_kcal is None:
        return PlateauCheck(False)
    return PlateauCheck(
        is_plateau=abs(slope_kg_week) < 0.1 and avg_deficit_kcal >= 200,
        slope_kg_week=round(slope_kg_week, 2),
        avg_deficit=int(avg_deficit_kcal),
    )


def underreporting(slope_kg_week: float, avg_deficit: int, weeks_in_row: int) -> dict | None:
    """Вес падает медленнее, чем должен, 2 недели подряд → гипотеза о данных."""
    if weeks_in_row < 2:
        return None
    expected = -(avg_deficit * 7) / 7700
    if slope_kg_week > expected + 0.25:
        return {"expected_kg_week": round(expected, 2),
                "actual_kg_week": round(slope_kg_week, 2),
                "weeks": weeks_in_row}
    return None


# -------------------------------------------------------------- приверженность

def adherence(days_logged: int, days_total: int, kcal_by_day: dict[str, int],
              target: int) -> dict:
    """Четыре честные метрики вместо «индекса здоровья»."""
    coverage = {d: k / target for d, k in kcal_by_day.items() if target > 0}
    good_days = [d for d, c in coverage.items() if 0.8 <= c <= 1.2]
    skipped_meal_days = [d for d, k in kcal_by_day.items() if k < 0.25 * target]
    return {
        "days_logged": days_logged,
        "days_total": days_total,
        "log_rate": round(days_logged / days_total, 2) if days_total else 0.0,
        "days_in_range": len(good_days),
        "avg_coverage": round(sum(coverage.values()) / len(coverage), 2) if coverage else 0.0,
        "skipped_days": len(skipped_meal_days),
    }


def format_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    return f"{minutes // 60} ч {minutes % 60:02d} мин"
