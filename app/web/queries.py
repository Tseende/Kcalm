"""SQL веб-API: чтение для экранов «День» и «Статистика», выгрузка CSV.

Всё, что уже есть в repo, берём оттуда; здесь только то, чего там нет.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta
from itertools import groupby

from app.db import read_batch
from app.domain import analytics
from app.domain import tracking as trk
from app.domain.weekgoal import STEPS_GOAL
from app.domain.weighin import pick_trend_rows
from app.domain.targets import ewma_dated, value_at_or_before
from app.services import water as watersvc
from app.storage import repo
from app.web.serializers import DAY_ITEM_SQL, DAY_PHOTO_SQL, day_meals_json, target_values

RANGE_DAYS = {"week": 7, "month": 30, "90d": 90}
CSV_KINDS = ("meals", "body", "water", "sleep", "activity")
KCAL_BAND = (0.8, 1.2)        # «день в цели»: как analytics.adherence


# ------------------------------------------------------------------ владение

def owned_item(db: sqlite3.Connection, user_id: int, item_id: int) -> sqlite3.Row | None:
    """Позиция чужого или удалённого приёма не видна."""
    return db.execute(
        """SELECT mi.* FROM meal_items mi JOIN meals m ON m.id = mi.meal_id
           WHERE mi.id=? AND m.user_id=? AND m.deleted_at IS NULL""",
        (item_id, user_id)).fetchone()


def live_meal(db: sqlite3.Connection, user_id: int, meal_id: int) -> sqlite3.Row | None:
    meal = repo.get_meal(db, user_id, meal_id)
    return meal if meal and not meal["deleted_at"] else None


def owned_photo(db: sqlite3.Connection, user_id: int, photo_id: int) -> sqlite3.Row | None:
    return db.execute(
        """SELECT p.* FROM meal_photos p JOIN meals m ON m.id = p.meal_id
           WHERE p.id=? AND m.user_id=? AND m.deleted_at IS NULL""",
        (photo_id, user_id)).fetchone()


# ------------------------------------------------------------------ вес

def weight_summary(db: sqlite3.Connection, user: sqlite3.Row, today: str) -> dict:
    """latest — последний замер (или стартовый вес), trend — EWMA по подходящим замерам."""
    return {"latest": repo.latest_weight_kg(db, user["id"]),
            "trend": weight_trend(db, user["id"], today),
            "start": user["start_weight_kg"]}


def _trend_points(db: sqlite3.Connection, user_id: int, to_date: str) -> list[tuple[int, float]]:
    rows, _ = repo.trend_weight_series(db, user_id, "1900-01-01", to_date)
    points = [(date.fromisoformat(r["local_date"]).toordinal(), r["weight_kg"]) for r in rows]
    return ewma_dated(points)


def weight_trend(db: sqlite3.Connection, user_id: int, to_date: str) -> float | None:
    smooth = _trend_points(db, user_id, to_date)
    return round(smooth[-1][1], 1) if smooth else None


# ------------------------------------------------------------------ день

def day_weight(db: sqlite3.Connection, user_id: int, local_date: str) -> float | None:
    rows = repo.weight_series(db, user_id, local_date, local_date)
    return float(rows[-1]["weight_kg"]) if rows else None


def _sleep_values(row) -> dict | None:
    if row and (row["duration_min"] is not None or row["score"] is not None):
        return {"duration_min": row["duration_min"], "score": row["score"]}
    return None


def _activity_hint_values(rows, workouts: int, current: int) -> dict | None:
    steps = [r["steps"] for r in rows if r["steps"] is not None]
    if not steps:
        return None
    avg = sum(steps) / len(steps)
    suggested = trk.activity_hint(avg, len(steps), int(workouts or 0), current)
    if suggested is None:
        return None
    return {"avg_steps": int(round(avg, -2)), "days": len(steps), "current": current, "suggested": suggested}


def _activity_values(row) -> dict | None:
    if row and (row["steps"] is not None or row["active_kcal"] is not None):
        return {"steps": row["steps"], "active_kcal": row["active_kcal"]}
    return None


def _day_totals_values(t) -> dict:
    return {"kcal": int(t["kcal"]), "protein": round(float(t["protein"]), 1),
            "fat": round(float(t["fat"]), 1), "carbs": round(float(t["carbs"]), 1),
            "unresolved_items": t["unresolved_items"]}


def day_totals(db: sqlite3.Connection, user_id: int, local_date: str) -> dict:
    return _day_totals_values(repo.day_totals(db, user_id, local_date))


def day_snapshot(db: sqlite3.Connection, user, day: date, activity_level: int | None) -> dict:
    """Load the whole diary screen in one database round trip."""
    uid, key = user["id"], day.isoformat()
    start = (day - timedelta(days=trk.HINT_WINDOW_DAYS - 1)).isoformat() if activity_level is not None else key
    meals, items, photos, totals_rows, target_rows, water_rows, weight_rows, latest_rows, sleep_rows, activity_rows, workouts = read_batch(
        db,
        (repo.DAY_MEALS_SQL, (uid, key)),
        (DAY_ITEM_SQL, (uid, key)),
        (DAY_PHOTO_SQL, (uid, key)),
        (repo.DAY_TOTALS_SQL, (uid, key)),
        (repo.CURRENT_TARGET_SQL, (uid,)),
        (repo.DAY_WATER_SQL, (uid, key)),
        ("""SELECT local_date, weight_kg FROM body_measurements
            WHERE user_id=? AND local_date<=? AND weight_kg IS NOT NULL
            ORDER BY local_date DESC, id DESC LIMIT 1""", (uid, key)),
        (repo.LATEST_WEIGHT_SQL, (uid,)),
        (repo.SLEEP_SERIES_SQL, (uid, key, key)),
        (repo.ACTIVITY_SERIES_SQL, (uid, start, key)),
        ("""SELECT COUNT(*) FROM activity_daily WHERE user_id=? AND local_date BETWEEN ? AND ?
            AND workouts_json IS NOT NULL AND CAST(workouts_json AS TEXT) NOT IN ('', '[]')""",
         (uid, start, key)),
    )
    target = next(iter(target_rows), None)
    latest = latest_rows[0]["weight_kg"] if latest_rows else user["start_weight_kg"]
    targets = target_values(user, target, watersvc.water_goal_ml(latest))
    totals = _day_totals_values(totals_rows[0])
    weight = weight_rows[0] if weight_rows else None
    sleep = next(iter(repo.merge_daily_sources(sleep_rows, "wake_date")), None)
    active = repo.merge_daily_sources(activity_rows, "local_date")
    today_active = next((row for row in active if row["local_date"] == key), None)
    hint = _activity_hint_values(active, workouts[0][0], activity_level) if activity_level is not None else None
    return {
        "date": key, "targets": targets, "totals": totals,
        "remaining_kcal": None if totals["unresolved_items"] or not targets["kcal"] else targets["kcal"] - totals["kcal"],
        "water_ml": int(water_rows[0]["ml"]),
        "meals": day_meals_json(db, meals, items, photos),
        "sleep": _sleep_values(sleep),
        "activity": _activity_values(today_active),
        "steps_goal": STEPS_GOAL,
        "activity_hint": hint,
        "weight": float(weight["weight_kg"]) if weight and weight["local_date"] == key else None,
        "weight_last": {"kg": float(weight["weight_kg"]), "date": weight["local_date"]} if weight else None,
    }


# ------------------------------------------------------------------ статистика

def _avg(values: list, ndigits: int | None = 1):
    values = [v for v in values if v is not None]
    if not values:
        return None
    mean = sum(values) / len(values)
    return round(mean) if ndigits is None else round(mean, ndigits)


def series(db: sqlite3.Connection, user: sqlite3.Row, start: date, n: int) -> list[dict]:
    """Ряды по дням: n дней начиная со start (еда, вода, вес, сон, шаги, поздняя еда)."""
    uid = user["id"]
    end = start + timedelta(days=n - 1)
    f, t = start.isoformat(), end.isoformat()

    meal_rows, water_rows, weight_rows, sleep_rows, activity_rows, targets = read_batch(
        db,
        (repo.RANGE_MEALS_SQL, (uid, f, t)),
        (repo.WATER_BY_DAY_SQL, (uid, f, t)),
        (repo.TREND_WEIGHT_SQL, (uid, "1900-01-01", t)),
        (repo.SLEEP_SERIES_SQL, (uid, f, t)),
        (repo.ACTIVITY_SERIES_SQL, (uid, f, t)),
        ("SELECT valid_from, kcal_target FROM targets_history WHERE user_id=? ORDER BY valid_from", (uid,)),
    )
    meals: dict[str, dict] = {}
    incomplete_days = set()
    for m in meal_rows:
        if m["incomplete"]:
            incomplete_days.add(m["local_date"])
        d = meals.setdefault(m["local_date"], {"kcal": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "late": 0})
        d["kcal"] += m["kcal_total"]
        d["protein"] += m["protein_g"]
        d["fat"] += m["fat_g"]
        d["carbs"] += m["carbs_g"]
        if analytics.is_late(m["local_time"], user["eating_cutoff"] or "21:00"):
            d["late"] += m["kcal_total"]
    water = {r["local_date"]: int(r["ml"]) for r in water_rows}
    weights = {r["local_date"]: float(r["weight_kg"]) for r in weight_rows if r["local_date"] >= f}
    trend_rows, _ = pick_trend_rows(weight_rows)
    smooth = ewma_dated([(date.fromisoformat(r["local_date"]).toordinal(), r["weight_kg"]) for r in trend_rows])
    sleep: dict[str, int] = {}
    for r in repo.merge_daily_sources(sleep_rows, "wake_date"):
        if r["duration_min"] and r["wake_date"] not in sleep:
            sleep[r["wake_date"]] = r["duration_min"]
    steps: dict[str, int] = {}
    for r in repo.merge_daily_sources(activity_rows, "local_date"):
        if r["steps"] is not None and r["local_date"] not in steps:
            steps[r["local_date"]] = r["steps"]

    def target_on(day: str) -> int | None:
        found = None
        for row in targets:
            if row["valid_from"] <= day:
                found = row["kcal_target"]
        return found if found is not None else (targets[0]["kcal_target"] if targets else None)

    days = []
    for i in range(n):
        d = start + timedelta(days=i)
        key = d.isoformat()
        eaten = meals.get(key) if key not in incomplete_days else None
        trend = value_at_or_before(smooth, d.toordinal())
        days.append({
            "date": key,
            "kcal": int(eaten["kcal"]) if eaten else None,
            "protein": round(eaten["protein"], 1) if eaten else None,
            "fat": round(eaten["fat"], 1) if eaten else None,
            "carbs": round(eaten["carbs"], 1) if eaten else None,
            "late_kcal": int(eaten["late"]) if eaten else 0,
            "target_kcal": target_on(key),
            "water_ml": water.get(key),
            "weight": weights.get(key),
            "weight_trend": trend,
            "sleep_min": sleep.get(key),
            "steps": steps.get(key),
        })
    return days


def summarize(days: list[dict]) -> dict:
    logged = [d for d in days if d["kcal"] is not None]
    on_target = sum(1 for d in logged if d["target_kcal"]
                    and KCAL_BAND[0] <= d["kcal"] / d["target_kcal"] <= KCAL_BAND[1])
    measured = [d["weight"] for d in days if d["weight"] is not None]
    return {
        "avg_kcal": _avg([d["kcal"] for d in logged], None),
        "avg_protein": _avg([d["protein"] for d in logged]),
        "avg_water": _avg([d["water_ml"] for d in days], None),
        "days_logged": len(logged),
        "days_on_target": on_target,
        "weight_change": round(measured[-1] - measured[0], 1) if len(measured) >= 2 else None,
        "avg_sleep_min": _avg([d["sleep_min"] for d in days], None),
        "avg_steps": _avg([d["steps"] for d in days], None),
    }


MEAL_ORDER = ("breakfast", "lunch", "dinner", "snack")
TOP_FOODS = 5


KCALS_BY_MEAL_TYPE_SQL = """SELECT meal_type, SUM(kcal_total) FROM meals
WHERE user_id=? AND local_date BETWEEN ? AND ? AND deleted_at IS NULL GROUP BY meal_type"""
KCALS_BY_ITEM_SQL = """SELECT i.name_ru, i.kcal FROM meal_items i JOIN meals m ON m.id = i.meal_id
WHERE m.user_id=? AND m.local_date BETWEEN ? AND ? AND m.deleted_at IS NULL"""


def kcal_sources(meal_types, items) -> dict:
    """Откуда калории за период: доли по приёмам и продукты, давшие больше всего ккал."""
    by_type = {key: 0 for key in MEAL_ORDER}
    for row in meal_types:
        if row[0] in by_type:
            by_type[row[0]] = int(row[1] or 0)
    total = sum(by_type.values())
    foods: dict[str, dict] = {}
    for row in items:
        name = (row[0] or "").strip()
        if not name:
            continue
        item = foods.setdefault(name.lower().replace("ё", "е"), {"name": name, "kcal": 0, "times": 0})
        item["kcal"] += int(row[1] or 0)
        item["times"] += 1
    items_total = sum(x["kcal"] for x in foods.values())
    top = sorted((x for x in foods.values() if x["kcal"] > 0), key=lambda x: -x["kcal"])[:TOP_FOODS]

    def share(v: int, whole: int) -> int:
        return round(100 * v / whole) if whole else 0

    return {"total": total,
            "by_type": [{"type": k, "kcal": v, "share": share(v, total)} for k, v in by_type.items() if v],
            "top": [{**x, "share": share(x["kcal"], items_total)} for x in top]}


def comparison_series(db: sqlite3.Connection, user: sqlite3.Row, today: date, days: int):
    """Read both periods together. Only the current period displays smoothed weight."""
    combined = series(db, user, today - timedelta(days=2 * days - 1), 2 * days)
    return combined[days:], combined[:days]


def stats(db: sqlite3.Connection, user: sqlite3.Row, range_name: str, today: date) -> dict:
    """Ряды по дням за range_name (последние N дней включая сегодня), сводка и сводка прошлого периода."""
    n = RANGE_DAYS[range_name]
    days, prev = comparison_series(db, user, today, n)
    uid = user["id"]
    dates = (uid, (today - timedelta(days=n - 1)).isoformat(), today.isoformat())
    targets, weights, meal_types, items = read_batch(
        db,
        (repo.CURRENT_TARGET_SQL, (uid,)),
        (repo.LATEST_WEIGHT_SQL, (uid,)),
        (KCALS_BY_MEAL_TYPE_SQL, dates),
        (KCALS_BY_ITEM_SQL, dates),
    )
    tgt = targets[0] if targets else None
    weight = weights[0]["weight_kg"] if weights else user["start_weight_kg"]
    return {"range": range_name, "days": days, "summary": summarize(days), "prev_summary": summarize(prev),
            "sources": kcal_sources(meal_types, items),
            "goals": {"protein_g": int(tgt["protein_g"]) if tgt and tgt["protein_g"] else None,
                      "water_ml": watersvc.water_goal_ml(weight), "steps": STEPS_GOAL, "sleep_min": 420}}


# ------------------------------------------------------------------ CSV

def export_csv(db: sqlite3.Connection, user_id: int, kind: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "meals":
        w.writerow(["id", "date", "time", "type", "kcal", "protein", "fat", "carbs",
                    "source", "method", "items"])
        rows = db.execute("""
            SELECT m.id, m.local_date, m.local_time, m.meal_type, m.kcal_total,
                   m.protein_g, m.fat_g, m.carbs_g, m.source, m.method,
                   i.name_ru AS item_name, i.grams AS item_grams
            FROM meals AS m LEFT JOIN meal_items AS i ON i.meal_id = m.id
            WHERE m.user_id = ? AND m.deleted_at IS NULL
            ORDER BY m.local_date, m.local_time, m.id, i.id
            """, (user_id,))
        for _, group in groupby(rows, key=lambda row: row["id"]):
            meal_rows = list(group)
            m = meal_rows[0]
            items = "; ".join(f"{i['item_name']} {i['item_grams']:g} г"
                              for i in meal_rows if i["item_name"] is not None)
            w.writerow([m["id"], m["local_date"], m["local_time"], m["meal_type"], m["kcal_total"],
                        m["protein_g"], m["fat_g"], m["carbs_g"], m["source"], m["method"], items])
    elif kind == "body":
        w.writerow(["date", "weight_kg", "body_fat_pct", "muscle_mass_kg", "bmr_kcal", "source"])
        for b in db.execute("SELECT * FROM body_measurements WHERE user_id=? ORDER BY local_date",
                            (user_id,)):
            w.writerow([b["local_date"], b["weight_kg"], b["body_fat_pct"],
                        b["muscle_mass_kg"], b["bmr_kcal"], b["source"]])
    elif kind == "water":
        w.writerow(["date", "time", "ml", "source"])
        for r in db.execute("SELECT * FROM water_log WHERE user_id=? ORDER BY id", (user_id,)):
            w.writerow([r["local_date"], r["local_time"], r["ml"], r["source"]])
    elif kind == "sleep":
        w.writerow(["wake_date", "duration_min", "deep_min", "light_min", "rem_min", "score"])
        for s in db.execute("SELECT * FROM sleep_sessions WHERE user_id=? ORDER BY wake_date",
                            (user_id,)):
            w.writerow([s["wake_date"], s["duration_min"], s["deep_min"], s["light_min"],
                        s["rem_min"], s["score"]])
    elif kind == "activity":
        w.writerow(["date", "steps", "distance_km", "active_kcal", "resting_hr", "avg_hr",
                    "active_minutes"])
        for a in db.execute("SELECT * FROM activity_daily WHERE user_id=? ORDER BY local_date",
                            (user_id,)):
            w.writerow([a["local_date"], a["steps"], a["distance_km"], a["active_kcal"],
                        a["resting_hr"], a["avg_hr"], a["active_minutes"]])
    else:
        raise ValueError(kind)
    return buf.getvalue()

