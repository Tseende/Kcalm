"""Работа с локальным временем пользователя и валидация настроек времени."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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
    hour, minute = map(int, validate_hhmm(local_time).split(":"))
    hour, minute = divmod((hour * 60 + minute - minutes) % (24 * 60), 60)
    return f"{hour:02d}:{minute:02d}"


def reminder_minutes(now: datetime) -> range:
    """Минуты расписания для этого тика, включая пропущенные при переводе часов вперёд."""
    if now.fold:
        return range(0)
    current = now.hour * 60 + now.minute
    if now.tzinfo is None:
        return range(current, current + 1)
    previous = (now.astimezone(timezone.utc) - timedelta(minutes=1)).astimezone(now.tzinfo)
    if previous.date() != now.date():
        # При обычной полуночи current=0. При смене смещения может быть уже позже.
        start = 0 if previous.utcoffset() != now.utcoffset() else current
    else:
        start = min(current, previous.hour * 60 + previous.minute + 1)
    return range(start, current + 1)
