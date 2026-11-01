"""SQL веб-API: чтение для экранов «День» и «Статистика», выгрузка CSV, история чата.

Всё, что уже есть в repo, берём оттуда; здесь только то, чего там нет. Единственная запись —
таблица chat_messages (миграция 018).
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta

from app.domain.targets import ewma_dated, value_at_or_before
from app.storage import repo

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


def day_sleep(db: sqlite3.Connection, user_id: int, wake_date: str) -> dict | None:
    for row in repo.sleep_series(db, user_id, wake_date, wake_date):
        if row["duration_min"] is not None or row["score"] is not None:
            return {"duration_min": row["duration_min"], "score": row["score"]}
    return None


def day_activity(db: sqlite3.Connection, user_id: int, local_date: str) -> dict | None:
    for row in repo.activity_series(db, user_id, local_date, local_date):
        if row["steps"] is not None or row["active_kcal"] is not None:
            return {"steps": row["steps"], "active_kcal": row["active_kcal"]}
    return None


def day_totals(db: sqlite3.Connection, user_id: int, local_date: str) -> dict:
    t = repo.day_totals(db, user_id, local_date)
    return {"kcal": int(t["kcal"]), "protein": round(float(t["protein"]), 1),
            "fat": round(float(t["fat"]), 1), "carbs": round(float(t["carbs"]), 1)}


# ------------------------------------------------------------------ статистика

def _avg(values: list, ndigits: int | None = 1):
    values = [v for v in values if v is not None]
    if not values:
        return None
    mean = sum(values) / len(values)
    return round(mean) if ndigits is None else round(mean, ndigits)


def stats(db: sqlite3.Connection, user: sqlite3.Row, range_name: str, today: date) -> dict:
    """Ряды по дням за range_name (последние N дней включая сегодня) и сводка."""
    n = RANGE_DAYS[range_name]
    uid = user["id"]
    start = today - timedelta(days=n - 1)
    f, t = start.isoformat(), today.isoformat()

    meals: dict[str, dict] = {}
    for m in repo.range_meals(db, uid, f, t):
        d = meals.setdefault(m["local_date"], {"kcal": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0})
        d["kcal"] += m["kcal_total"]
        d["protein"] += m["protein_g"]
        d["fat"] += m["fat_g"]
        d["carbs"] += m["carbs_g"]
    water = repo.water_by_day(db, uid, f, t)
    weights = {r["local_date"]: float(r["weight_kg"]) for r in repo.weight_series(db, uid, f, t)}
    smooth = _trend_points(db, uid, t)
    sleep: dict[str, int] = {}
    for r in repo.sleep_series(db, uid, f, t):
        if r["duration_min"] and r["wake_date"] not in sleep:
            sleep[r["wake_date"]] = r["duration_min"]
    steps: dict[str, int] = {}
    for r in repo.activity_series(db, uid, f, t):
        if r["steps"] is not None and r["local_date"] not in steps:
            steps[r["local_date"]] = r["steps"]
    targets = db.execute(
        "SELECT valid_from, kcal_target FROM targets_history WHERE user_id=? ORDER BY valid_from",
        (uid,)).fetchall()

    def target_on(day: str) -> int | None:
        found = None
        for row in targets:
            if row["valid_from"] <= day:
                found = row["kcal_target"]
        return found if found is not None else (targets[0]["kcal_target"] if targets else None)

    days = []
    for i in range(n):
        d = (start + timedelta(days=i))
        key = d.isoformat()
        eaten = meals.get(key)
        trend = value_at_or_before(smooth, d.toordinal())
        days.append({
            "date": key,
            "kcal": int(eaten["kcal"]) if eaten else None,
            "protein": round(eaten["protein"], 1) if eaten else None,
            "fat": round(eaten["fat"], 1) if eaten else None,
            "carbs": round(eaten["carbs"], 1) if eaten else None,
            "target_kcal": target_on(key),
            "water_ml": water.get(key),
            "weight": weights.get(key),
            "weight_trend": None if trend is None else round(trend, 1),
            "sleep_min": sleep.get(key),
            "steps": steps.get(key),
        })

    logged = [d for d in days if d["kcal"] is not None]
    on_target = sum(1 for d in logged if d["target_kcal"]
                    and KCAL_BAND[0] <= d["kcal"] / d["target_kcal"] <= KCAL_BAND[1])
    measured = [d["weight"] for d in days if d["weight"] is not None]
    summary = {
        "avg_kcal": _avg([d["kcal"] for d in logged], None),
        "avg_protein": _avg([d["protein"] for d in logged]),
        "days_logged": len(logged),
        "days_on_target": on_target,
        "weight_change": round(measured[-1] - measured[0], 1) if len(measured) >= 2 else None,
        "avg_sleep_min": _avg([d["sleep_min"] for d in days], None),
        "avg_steps": _avg([d["steps"] for d in days], None),
    }
    return {"range": range_name, "days": days, "summary": summary}


# ------------------------------------------------------------------ CSV

def export_csv(db: sqlite3.Connection, user_id: int, kind: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "meals":
        w.writerow(["id", "date", "time", "type", "kcal", "protein", "fat", "carbs",
                    "source", "method", "items"])
        for m in db.execute("SELECT * FROM meals WHERE user_id=? AND deleted_at IS NULL "
                            "ORDER BY local_date, local_time", (user_id,)):
            items = "; ".join(f"{i['name_ru']} {i['grams']:g} г" for i in repo.get_meal_items(db, m["id"]))
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


# ------------------------------------------------------------------ чат

def add_chat_message(db: sqlite3.Connection, user_id: int, role: str, text: str,
                     meal_id: int | None = None) -> dict:
    cur = db.execute("INSERT INTO chat_messages (user_id, role, text, meal_id) VALUES (?,?,?,?)",
                     (user_id, role, text, meal_id))
    db.commit()
    row = db.execute("SELECT * FROM chat_messages WHERE id=?", (cur.lastrowid,)).fetchone()
    return chat_json(row)


def chat_history(db: sqlite3.Connection, user_id: int, limit: int) -> list[dict]:
    rows = db.execute("SELECT * FROM chat_messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                      (user_id, limit)).fetchall()
    return [chat_json(r) for r in reversed(rows)]


def chat_recent(db: sqlite3.Connection, user_id: int, limit: int = 10) -> list[dict]:
    """Последние реплики для памяти ассистента: [{role, text}], от старых к новым."""
    rows = db.execute("SELECT role, text FROM chat_messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                      (user_id, limit)).fetchall()
    return [{"role": r["role"], "text": r["text"]} for r in reversed(rows)]


def chat_json(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "role": row["role"], "text": row["text"],
            "created_at": row["created_at"].replace(" ", "T") + "Z"}
