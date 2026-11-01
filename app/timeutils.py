"""Работа с локальным временем пользователя и валидация настроек времени."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("часовой пояс не может быть пустым")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("нужен часовой пояс IANA, например Europe/Minsk") from exc
    return name


def validate_hhmm(value: str) -> str:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError as exc:
        raise ValueError("время нужно в формате ЧЧ:ММ, например 21:00") from exc
    return parsed.strftime("%H:%M")


def local_now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(validate_timezone(tz_name)))


def local_today(tz_name: str) -> date:
    return local_now(tz_name).date()


def minutes_before(local_time: str, minutes: int) -> str:
    """Время за ``minutes`` до HH:MM с переходом через полночь."""
    normalized = validate_hhmm(local_time)
    base = datetime.strptime(normalized, "%H:%M") - timedelta(minutes=minutes)
    return base.strftime("%H:%M")
