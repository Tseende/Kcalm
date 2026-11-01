"""Скриншоты Mi Fitness / PICOOC → ScreenshotParse → строки БД.

Правило: только то, что видно на экране; нет поля — null + missing.
"""
from __future__ import annotations

import base64
import logging
import math
import re
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_config
from app.llm import client as llm
from app.services.food import load_prompt
from app.storage import repo

log = logging.getLogger(__name__)

PICOOC_FIELDS = ["weight_kg", "bmi", "body_fat_pct", "fat_mass_kg", "muscle_mass_kg",
                 "muscle_mass_pct", "skeletal_muscle_pct", "body_water_pct",
                 "bone_mass_kg", "protein_pct", "visceral_fat", "bmr_kcal",
                 "metabolic_age", "body_tone", "body_score", "resting_hr"]
ACTIVITY_FIELDS = ["steps", "distance_km", "active_kcal", "avg_hr", "resting_hr",
                   "active_minutes"]
SLEEP_FIELDS = ["sleep_duration_min", "sleep_deep_min", "sleep_light_min",
                "sleep_rem_min", "sleep_awake_min", "sleep_score"]

# Сон и активность из любого приложения (Apple Health, Samsung Health, Garmin Connect, Huawei
# Health, Google Fit, Zepp, Fitbit) попадают в те же таблицы, что и Mi Fitness.
SLEEP_SCREENS = ("mifitness_sleep", "generic_sleep")
ACTIVITY_SCREENS = ("mifitness_activity", "generic_activity")
CONFIRM_BELOW = 0.85

# Защита от галлюцинаций/OCR-ошибок: за границы данные не записываются.
METRIC_RANGES = {
    "weight_kg": (20, 500), "bmi": (10, 80), "body_fat_pct": (1, 75),
    "fat_mass_kg": (0, 300), "muscle_mass_kg": (1, 250),
    "skeletal_muscle_pct": (1, 80), "body_water_pct": (20, 80),
    "bone_mass_kg": (0.1, 15), "protein_pct": (1, 35), "visceral_fat": (1, 60),
    "bmr_kcal": (500, 5000), "metabolic_age": (5, 120),
    "muscle_mass_pct": (1, 80), "body_tone": (0, 100), "body_score": (0, 10),
    "steps": (0, 200000), "distance_km": (0, 250), "active_kcal": (0, 10000),
    "avg_hr": (20, 250), "resting_hr": (20, 180), "active_minutes": (0, 1440),
    "sleep_duration_min": (1, 1440), "sleep_deep_min": (0, 1000),
    "sleep_light_min": (0, 1000), "sleep_rem_min": (0, 1000),
    "sleep_awake_min": (0, 1000), "sleep_score": (0, 100),
}


async def parse_screenshot(image_b64: str, user_id: int | None,
                           db: sqlite3.Connection) -> dict:
    """Vision (temperature=0) → контракт ScreenshotParse."""
    prompt = load_prompt("screenshot")
    content = [{"type": "text", "text": prompt},
               {"type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    data, used_model = await llm.chat_json(
        "screenshot_parse",
        [{"role": "user", "content": content}],
        model=get_config().model_vision,
        temperature=0.0, user_id=user_id, db=db,
    )
    parsed = clean_parsed(data)
    if isinstance(parsed, dict):
        parsed["model"] = used_model          # попадает в raw_observations.model
    return parsed


def clean_parsed(data: dict) -> dict:
    """Детерминированная чистка ответа модели.

    Модель регулярно дублирует найденные метрики в `missing` (и наоборот правило
    соблюдает через раз). На запись это не влияет, но путает при разборе логов,
    поэтому приводим список в согласованный вид.
    """
    if not isinstance(data, dict):
        return {}
    metrics = data.get("metrics") or {}
    missing = data.get("missing")
    if isinstance(missing, list):
        data["missing"] = [m for m in missing if m not in metrics]
    return data


def metrics_value(parsed: dict, key: str) -> float | None:
    entry = (parsed.get("metrics") or {}).get(key)
    if not entry or entry.get("value") is None:
        return None
    try:
        value = float(entry["value"])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    lower, upper = METRIC_RANGES.get(key, (-float("inf"), float("inf")))
    return value if lower <= value <= upper else None


_TIME_BEFORE_DATE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\s*[,;]?\s*\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
_TIME_AFTER_DATE = re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\s*[,;]?\s*([01]?\d|2[0-3]):([0-5]\d)\b")


def time_from_raw(parsed: dict) -> str | None:
    """Время замера из распознанных подписей экрана («21:04 10/30/2026»). Только так: поле time
    от модели однажды превратило 21:04 в 07:04 и вечерний замер стал «утренним»."""
    for line in parsed.get("raw_text") or []:
        for pattern in (_TIME_BEFORE_DATE, _TIME_AFTER_DATE):
            m = pattern.search(str(line))
            if m:
                return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def fasting_for(conn: sqlite3.Connection, user, parsed: dict, local_date: str,
                now_local: datetime | None = None) -> int | None:
    """Замер натощак? Время берём из подписей скриншота; если его нет, а скриншот прислан утром
    в день замера, считаем, что взвесились только что. Позже ничего не утверждаем."""
    from app.domain import weighin
    measured = time_from_raw(parsed)
    if measured is None:
        now_local = now_local or datetime.now(ZoneInfo(user["tz"]))
        same_day = local_date == now_local.strftime("%Y-%m-%d")
        sent = now_local.strftime("%H:%M")
        if same_day and weighin.FASTING_FROM <= sent < weighin.FASTING_UNTIL:
            measured = sent
    first_meal = repo.first_meal_time(conn, user["id"], local_date)
    return weighin.classify_fasting(measured, first_meal)


def apply_parsed(conn: sqlite3.Connection, user_id: int, parsed: dict,
                 local_date: str | None, raw_id: int | None = None,
                 fasting: int | None = None) -> str:
    """Маппинг контракта в таблицы. Возвращает 'sleep'|'activity'|'body'|'unknown'."""
    screen = parsed.get("screen", "unknown")
    if screen == "unknown" or not parsed.get("metrics"):
        return "unknown"

    if screen in SLEEP_SCREENS:
        wake_date = parsed.get("date") or local_date
        if not wake_date:
            return "unknown"
        m = {
            "duration_min": metrics_value(parsed, "sleep_duration_min"),
            "deep_min": metrics_value(parsed, "sleep_deep_min"),
            "light_min": metrics_value(parsed, "sleep_light_min"),
            "rem_min": metrics_value(parsed, "sleep_rem_min"),
            "awake_min": metrics_value(parsed, "sleep_awake_min"),
            "score": metrics_value(parsed, "sleep_score"),
        }
        if all(v is None for v in m.values()):
            return "unknown"
        repo.save_sleep(conn, user_id, wake_date, m, "screenshot",
                        float(parsed.get("overall_confidence", 0.5)), raw_id)
        return "sleep"

    if screen in ACTIVITY_SCREENS:
        date = parsed.get("date") or local_date
        if not date:
            return "unknown"
        m = {k: metrics_value(parsed, k) for k in ACTIVITY_FIELDS}
        if all(v is None for v in m.values()):
            return "unknown"
        repo.save_activity(conn, user_id, date, m, "screenshot",
                           float(parsed.get("overall_confidence", 0.5)), raw_id)
        return "activity"

    if screen == "picooc_measure":
        date = parsed.get("date") or local_date
        if not date:
            return "unknown"
        m = {k: metrics_value(parsed, k) for k in PICOOC_FIELDS}
        if all(v is None for v in m.values()):
            return "unknown"
        m["fasting"] = fasting
        repo.save_body_measurement(conn, user_id, date, m, "screenshot",
                                   float(parsed.get("overall_confidence", 0.5)),
                                   raw_id=raw_id)
        return "body"
    return "unknown"


def assess_parsed(conn: sqlite3.Connection, user_id: int, parsed: dict,
                  today: date) -> dict:
    """Можно ли писать разобранный скриншот без вопроса.

    → {local_date, date_ok, outlier, confidence, reasons, needs_confirm}. Дата, которой нельзя
    верить, в parsed заменяется на сегодняшнюю."""
    kind = parsed.get("screen", "unknown")
    try:
        conf = float(parsed.get("overall_confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    local_date, date_ok = sane_date(parsed.get("date"), today)
    if not date_ok:
        parsed["date"] = local_date          # OCR даты не доверяем: пишем сегодняшнюю
    weight = metrics_value(parsed, "weight_kg") if kind == "picooc_measure" else None
    outlier = weight is not None and weight_outlier(conn, user_id, local_date, weight)
    reasons = []
    if outlier:
        reasons.append("вес сильно отличается от тренда")
    if not date_ok:
        reasons.append("дата не распознана — ставлю сегодняшнюю")
    if conf < CONFIRM_BELOW:
        reasons.append(f"уверенность {conf:.0%}")
    return {"local_date": local_date, "date_ok": date_ok, "outlier": outlier,
            "confidence": conf, "reasons": reasons, "needs_confirm": bool(reasons)}


def apply_for_user(conn: sqlite3.Connection, user, parsed: dict, local_date: str,
                   raw_id: int | None) -> tuple[str, int | None]:
    """Записать скриншот в таблицы. → (вид: sleep|activity|body|unknown, fasting для веса)."""
    fasting = None
    if parsed.get("screen") == "picooc_measure":
        fasting = fasting_for(conn, user, parsed, parsed.get("date") or local_date)
    kind = apply_parsed(conn, user["id"], parsed, local_date, raw_id, fasting=fasting)
    return kind, fasting


def weight_outlier(conn: sqlite3.Connection, user_id: int, local_date: str,
                   weight_kg: float, sigma: float = 3.0) -> bool:
    """Расхождение с трендом > 3σ → спросить подтверждение."""
    rows = repo.weight_series(conn, user_id, "1900-01-01", local_date)
    vals = [r["weight_kg"] for r in rows[-14:] if r["weight_kg"]]
    if len(vals) < 5:
        return False
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    sd = var ** 0.5
    return sd > 0 and abs(weight_kg - mean) > sigma * sd


def local_date_of(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def sane_date(parsed_date: str | None, today: date, max_age_days: int = 90) -> tuple[str, bool]:
    """Дата со скриншота, если ей можно верить (иначе — сегодня).

    Vision регулярно путает год (2026 → 2024) и тащит дату экспорта. Замер,
    уехавший на два года назад, ломает тренд веса молча, поэтому доверяем только
    диапазону [today − max_age_days; today + 1 день]. Возвращает (дата, доверяем ли).
    """
    if not parsed_date:
        return today.isoformat(), False
    try:
        parsed = date.fromisoformat(str(parsed_date)[:10])
    except ValueError:
        return today.isoformat(), False
    if parsed > today + timedelta(days=1) or parsed < today - timedelta(days=max_age_days):
        return today.isoformat(), False
    return parsed.isoformat(), True
