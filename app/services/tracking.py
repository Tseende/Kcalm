"""Сон, шаги и тренировки: запись вручную и разбор скриншотов приложений здоровья.

Без aiogram: сервис используют и бот, и web-API. Ручные записи лежат с source='manual'
рядом со скриншотными; в отчётах день берётся один раз (скриншот важнее ручной записи,
см. repo._dedupe_by_date).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from base64 import b64encode
from datetime import date, datetime, timedelta
from pathlib import Path

from app.services import meals as meals_svc
from app.services import screens
from app.storage import repo
from app.timeutils import local_today

log = logging.getLogger(__name__)

MANUAL = "manual"


def _day(user, day: date | str | None) -> str:
    if day is None:
        return local_today(user["tz"]).isoformat()
    return day.isoformat() if isinstance(day, date) else str(day)[:10]


def _clock(value) -> str | None:
    """'7:10' / '07:10' / datetime.time → 'HH:MM'; мусор → ValueError."""
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    try:
        hh, mm = str(value).strip().split(":")
        hour, minute = int(hh), int(mm)
    except ValueError as exc:
        raise ValueError("Время нужно в формате ЧЧ:ММ.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Время нужно в формате ЧЧ:ММ.")
    return f"{hour:02d}:{minute:02d}"


# ------------------------------------------------------------------ сон

def log_sleep(db: sqlite3.Connection, user, date: date | str | None = None,
              duration_min: int | None = None, start=None, end=None,
              score: int | None = None) -> dict:
    """Записать сон за ночь, после которой наступила дата ``date`` (по умолчанию сегодня).

    Либо длительность, либо начало и конец («23:30» и «07:10»): тогда длительность считается
    сама, а время пересчитывается в UTC по часовому поясу пользователя. ValueError — вне границ.
    → {wake_date, duration_min, start, end, score}."""
    wake = _day(user, date)
    start_s, end_s = _clock(start), _clock(end)
    if (start_s is None) != (end_s is None):
        raise ValueError("Укажи и время засыпания, и время подъёма.")
    start_utc = end_utc = None
    if start_s and end_s:
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
        diff = (eh * 60 + em) - (sh * 60 + sm)
        computed = diff if diff > 0 else diff + 24 * 60
        if duration_min is None:
            duration_min = computed
        same_day = (sh * 60 + sm) < (eh * 60 + em)       # лёг после полуночи: та же дата
        start_date = wake if same_day else (datetime.fromisoformat(wake)
                                            - timedelta(days=1)).strftime("%Y-%m-%d")
        start_utc = meals_svc.utc_of(user["tz"], start_date, start_s)
        end_utc = meals_svc.utc_of(user["tz"], wake, end_s)
    if duration_min is None:
        raise ValueError("Нужна длительность сна или время засыпания и подъёма.")
    duration_min = int(round(duration_min))
    if not 1 <= duration_min <= 1440:
        raise ValueError("Длительность сна должна быть от 1 минуты до 24 часов.")
    if score is not None and not 0 <= int(score) <= 100:
        raise ValueError("Оценка сна от 0 до 100.")
    repo.save_sleep(db, user["id"], wake,
                    {"duration_min": duration_min, "sleep_start_utc": start_utc,
                     "sleep_end_utc": end_utc, "score": None if score is None else int(score)},
                    MANUAL, 1.0)
    return {"wake_date": wake, "duration_min": duration_min, "start": start_s, "end": end_s,
            "score": score}


# ------------------------------------------------------------------ активность

def _manual_workouts(db: sqlite3.Connection, user_id: int, day: str) -> list[str]:
    row = db.execute(
        "SELECT workouts_json FROM activity_daily WHERE user_id=? AND local_date=? AND source=?",
        (user_id, day, MANUAL)).fetchone()
    if not row or not row["workouts_json"]:
        return []
    try:
        data = json.loads(row["workouts_json"])
    except json.JSONDecodeError:
        return []
    return [str(w) for w in data] if isinstance(data, list) else []


def log_activity(db: sqlite3.Connection, user, date: date | str | None = None,
                 steps: int | None = None, active_kcal: int | None = None,
                 active_minutes: int | None = None, workout: str | None = None,
                 distance_km: float | None = None) -> dict:
    """Записать активность за день. Пустые поля ничего не стирают; ``workout`` (строка вроде
    «бег 30 мин») добавляется к списку тренировок дня. ValueError — вне границ.
    → {local_date, steps, active_kcal, active_minutes, workouts}."""
    day = _day(user, date)
    for label, value, lo, hi in (("Шаги", steps, 0, 200_000),
                                 ("Активные калории", active_kcal, 0, 10_000),
                                 ("Активные минуты", active_minutes, 0, 1440),
                                 ("Дистанция", distance_km, 0, 250)):
        if value is not None and not lo <= value <= hi:
            raise ValueError(f"{label}: от {lo} до {hi}.")
    metrics: dict = {}
    if steps is not None:
        metrics["steps"] = int(steps)
    if active_kcal is not None:
        metrics["active_kcal"] = int(active_kcal)
    if active_minutes is not None:
        metrics["active_minutes"] = int(active_minutes)
    if distance_km is not None:
        metrics["distance_km"] = float(distance_km)
    workouts = _manual_workouts(db, user["id"], day)
    if workout:
        workouts.append(str(workout).strip()[:80])
        metrics["workouts_json"] = json.dumps(workouts, ensure_ascii=False)
    if not metrics:
        raise ValueError("Нечего записывать: нужны шаги, калории, минуты или тренировка.")
    repo.save_activity(db, user["id"], day, metrics, MANUAL, 1.0)
    return {"local_date": day, "steps": steps, "active_kcal": active_kcal,
            "active_minutes": active_minutes, "workouts": workouts}


# ------------------------------------------------------------------ средние за 7 дней

def sleep_avg_7d(db: sqlite3.Connection, user, today: date | None = None
                 ) -> tuple[int, int] | None:
    """(среднее минут сна, число ночей) за 7 дней включая сегодня; None — данных нет."""
    today = today or local_today(user["tz"])
    rows = repo.sleep_series(db, user["id"], (today - timedelta(days=6)).isoformat(),
                             today.isoformat())
    values = [r["duration_min"] for r in rows if r["duration_min"]]
    return (round(sum(values) / len(values)), len(values)) if values else None


def steps_avg_7d(db: sqlite3.Connection, user, today: date | None = None
                 ) -> tuple[int, int] | None:
    """(среднее шагов, число дней с данными) за 7 дней включая сегодня."""
    today = today or local_today(user["tz"])
    rows = repo.activity_series(db, user["id"], (today - timedelta(days=6)).isoformat(),
                                today.isoformat())
    values = [r["steps"] for r in rows if r["steps"] is not None]
    return (round(sum(values) / len(values)), len(values)) if values else None


# ------------------------------------------------------------------ скриншоты

def _flat_metrics(parsed: dict) -> dict:
    out = {}
    for key, entry in (parsed.get("metrics") or {}).items():
        if isinstance(entry, dict) and entry.get("value") is not None:
            out[key] = entry["value"]
    return out


async def process_screenshot(db: sqlite3.Connection, user, image_path: str | Path) -> dict:
    """Скриншот приложения здоровья (Mi Fitness, PICOOC, Apple Health, Garmin и т. д.) →
    разбор, и запись, если данным можно верить.

    → {id (raw_observation), screen, date, metrics, confidence, needs_confirm, reasons, applied}.
    applied — 'sleep'|'activity'|'body' или None. needs_confirm: данные ждут решения, их
    подтверждает или отклоняет confirm_screenshot(id). screen == 'unknown' — это не экран метрик.
    Ошибка модели пробрасывается (LLMError): вызывающий решает, что ответить."""
    data = Path(image_path).read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    parsed = await screens.parse_screenshot(b64encode(data).decode(), user["id"], db)
    screen = parsed.get("screen", "unknown")
    raw_id = repo.save_raw_observation(db, user["id"], screen, str(image_path), file_hash,
                                       parsed, parsed.get("model"))
    result = {"id": raw_id, "screen": screen, "date": parsed.get("date"),
              "metrics": _flat_metrics(parsed),
              "confidence": float(parsed.get("overall_confidence") or 0),
              "needs_confirm": False, "reasons": [], "applied": None}
    if screen == "unknown" or not parsed.get("metrics"):
        return result
    verdict = screens.assess_parsed(db, user["id"], parsed, local_today(user["tz"]))
    result["date"] = verdict["local_date"]
    result["reasons"] = verdict["reasons"]
    if verdict["needs_confirm"]:
        repo.save_pending_input(db, user["id"], "screenshot_confirm",
                                {"raw_id": raw_id, "parsed": parsed})
        result["needs_confirm"] = True
        return result
    kind, _fasting = screens.apply_for_user(db, user, parsed, verdict["local_date"], raw_id)
    result["applied"] = None if kind == "unknown" else kind
    return result


def confirm_screenshot(db: sqlite3.Connection, user, raw_id: int, apply: bool) -> str | None:
    """Решение по скриншоту, который ждал подтверждения. apply=True записывает данные,
    False отбрасывает. → вид записанного ('sleep'|'activity'|'body') или None
    (отклонено, нечего подтверждать или данные не прошли проверку границ)."""
    row = None
    for candidate in db.execute(
            "SELECT * FROM pending_inputs WHERE user_id=? AND kind='screenshot_confirm' "
            "ORDER BY id DESC", (user["id"],)).fetchall():
        payload = json.loads(candidate["payload_json"])
        if payload.get("raw_id") == raw_id:
            row, parsed = candidate, payload["parsed"]
            break
    if row is None:
        return None
    repo.delete_pending_input(db, row["id"], user["id"])
    if not apply:
        return None
    local_date = parsed.get("date") or local_today(user["tz"]).isoformat()
    kind, _fasting = screens.apply_for_user(db, user, parsed, local_date, raw_id)
    return None if kind == "unknown" else kind
