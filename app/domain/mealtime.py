"""Время приёма пищи из текста («в обед», «вчера вечером», «в 13:30», «час назад») и из EXIF.

Чистые функции без сети и БД: «сейчас» передаётся снаружи, поэтому всё проверяется тестами.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# время по умолчанию для слов «завтрак/обед/…»
SLOT_DEFAULTS = {
    "breakfast": "08:00", "lunch": "13:00", "snack": "16:00", "dinner": "19:00",
}

# кнопки «🕐 Другое время»: ключ → (подпись, смещение дня, время, тип приёма)
SLOTS: dict[str, tuple[str, int, str, str]] = {
    "b": ("Завтрак 08:00", 0, "08:00", "breakfast"),
    "l": ("Обед 13:00", 0, "13:00", "lunch"),
    "s": ("Перекус 16:00", 0, "16:00", "snack"),
    "d": ("Ужин 19:00", 0, "19:00", "dinner"),
    "y": ("Вчера ужин", -1, "19:00", "dinner"),
}

_NUMBER_WORDS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
                 "пять": 5, "шесть": 6, "семь": 7}


@dataclass(frozen=True)
class MealTime:
    local_date: str                  # YYYY-MM-DD
    local_time: str                  # HH:MM
    meal_type: str | None = None     # None — вывести из времени (guess_meal_type)


def slot_time(key: str, today: date) -> MealTime | None:
    """Время для кнопки быстрого выбора."""
    slot = SLOTS.get(key)
    if slot is None:
        return None
    _, offset, hhmm, meal_type = slot
    return MealTime((today + timedelta(days=offset)).isoformat(), hhmm, meal_type)


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


_REL_HALF = re.compile(r"\bполчаса назад\b")
_REL = re.compile(r"\b(?:(\d{1,2}|один|одну|два|две|три|четыре|пять|шесть|семь)\s+)?"
                  r"(час(?:а|ов)?|мин(?:ут[уы]?)?)\s+назад\b")
_CLOCK_COLON = re.compile(r"(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_CLOCK_DOT = re.compile(r"\b(?:в|около|к)\s+([01]?\d|2[0-3])\.([0-5]\d)(?![\d.])")
_CLOCK_PART = re.compile(r"\bв\s+(\d{1,2})\s*(?:час(?:а|ов)?\s+)?(утра|дня|вечера|ночи)\b")
_CLOCK_HOURS = re.compile(r"\bв\s+(\d{1,2})\s*час(?:а|ов)?\b")

_DAY_WORDS = (("позавчера", -2), ("вчера", -1), ("сегодня", 0))

# (регулярка, тип приёма, время по умолчанию)
_SLOT_WORDS = (
    (re.compile(r"\bзавтрак\w*|\bутром\b|\bс утра\b"), "breakfast", "08:00"),
    (re.compile(r"\bперекус\w*|\bполдник\w*"), "snack", "16:00"),
    (re.compile(r"\bобед\w*|\bднем\b"), "lunch", "13:00"),
    (re.compile(r"\bужин\w*"), "dinner", "19:00"),
    (re.compile(r"\bвечером\b"), "dinner", "20:00"),
    (re.compile(r"\bночью\b"), "snack", "23:30"),
)


def _hour_with_part(hour: int, part: str) -> int | None:
    if not 1 <= hour <= 12:
        return None
    if part == "утра":
        return 0 if hour == 12 else hour
    if part == "дня":
        return 12 if hour == 12 else hour + 12
    if part == "вечера":
        return 12 if hour == 12 else hour + 12
    if part == "ночи":                      # «в 12 ночи» = 00, «в 11 ночи» = 23, «в 2 ночи» = 02
        return 0 if hour == 12 else (hour if hour < 6 else hour + 12)
    return None


def _explicit_clock(c: str) -> tuple[int, int] | None:
    m = _CLOCK_PART.search(c)
    if m:
        h = _hour_with_part(int(m.group(1)), m.group(2))
        if h is not None:
            return h, 0
    for rx in (_CLOCK_COLON, _CLOCK_DOT):
        m = rx.search(c)
        if m:
            return int(m.group(1)), int(m.group(2))
    m = _CLOCK_HOURS.search(c)
    if m and int(m.group(1)) <= 23:
        return int(m.group(1)), 0
    return None


def parse_mealtime(text: str | None, now: datetime) -> MealTime | None:
    """Найти в тексте, когда была еда. None — указаний времени нет (значит «сейчас»).

    ``now`` — локальное время пользователя (tzinfo игнорируется). Правила:
    «N минут/часов назад» считаются от now; явное «13:30» и слова «обед/утром/вечером»
    дают время, «вчера/позавчера» сдвигают дату. Будущее время без указания дня
    не допускается: часы и минуты уходят на вчера, а слово («на ужин» днём) прижимается к now.
    """
    if not text:
        return None
    c = _norm(text)
    now = now.replace(tzinfo=None, second=0, microsecond=0)

    if _REL_HALF.search(c):
        t = now - timedelta(minutes=30)
        return MealTime(t.date().isoformat(), t.strftime("%H:%M"))
    m = _REL.search(c)
    if m:
        raw, unit = m.group(1), m.group(2)
        n = int(raw) if raw and raw.isdigit() else _NUMBER_WORDS.get(raw or "", 1)
        delta = timedelta(hours=n) if unit.startswith("час") else timedelta(minutes=n)
        t = now - delta
        return MealTime(t.date().isoformat(), t.strftime("%H:%M"))

    day_offset: int | None = None
    for word, off in _DAY_WORDS:
        if re.search(rf"\b{word}\b", c):
            day_offset = off
            break
    clock = _explicit_clock(c)
    slot_type = slot_time_default = None
    for rx, mtype, default in _SLOT_WORDS:
        if rx.search(c):
            slot_type, slot_time_default = mtype, default
            break

    if clock is None and slot_type is None and day_offset is None:
        return None
    if clock is None and slot_type is None:         # просто «вчера»/«сегодня»: время не меняем
        if day_offset == 0:
            return None
        d = now.date() + timedelta(days=day_offset)
        return MealTime(d.isoformat(), now.strftime("%H:%M"))

    if clock is not None:
        hh, mm = clock
    else:
        hh, mm = (int(x) for x in slot_time_default.split(":"))
    d = now.date() + timedelta(days=day_offset or 0)
    moment = datetime(d.year, d.month, d.day, hh, mm)
    if (day_offset in (None, 0)) and moment > now + timedelta(minutes=5):
        if clock is not None and day_offset is None:
            moment -= timedelta(days=1)             # «в 23:30» в 00:10 — это вчера
        elif clock is None:
            moment = now                            # «на ужин» днём — записываем на сейчас
    return MealTime(moment.date().isoformat(), moment.strftime("%H:%M"), slot_type)


# ------------------------------------------------------------------ EXIF

def exif_datetime(data: bytes) -> datetime | None:
    """DateTimeOriginal из EXIF JPEG без внешних библиотек. None — нет или не разобрать."""
    try:
        return _exif_datetime(data)
    except (struct.error, ValueError, IndexError):
        return None


def _exif_datetime(data: bytes) -> datetime | None:
    if data[:2] != b"\xff\xd8":
        return None
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xDA:                           # начало изображения: EXIF уже не будет
            return None
        size = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\x00\x00":
            return _read_tiff(data[pos + 10:pos + 2 + size])
        pos += 2 + size
    return None


def _read_tiff(tiff: bytes) -> datetime | None:
    endian = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if endian is None:
        return None
    u16 = lambda off: struct.unpack(endian + "H", tiff[off:off + 2])[0]    # noqa: E731
    u32 = lambda off: struct.unpack(endian + "I", tiff[off:off + 4])[0]    # noqa: E731

    def ifd(offset: int) -> dict[int, tuple[int, int, int]]:
        out = {}
        for i in range(u16(offset)):
            e = offset + 2 + i * 12
            out[u16(e)] = (u16(e + 2), u32(e + 4), e + 8)     # тип, число, позиция значения
        return out

    def ascii_value(entry: tuple[int, int, int]) -> str:
        _, count, vpos = entry
        start = u32(vpos) if count > 4 else vpos
        return tiff[start:start + count].split(b"\x00")[0].decode("ascii", "ignore")

    root = ifd(u32(4))
    raw = None
    if 0x8769 in root:
        exif = ifd(u32(root[0x8769][2]))
        for tag in (0x9003, 0x9004):                 # DateTimeOriginal, DateTimeDigitized
            if tag in exif:
                raw = ascii_value(exif[tag])
                break
    if raw is None and 0x0132 in root:               # DateTime
        raw = ascii_value(root[0x0132])
    if not raw:
        return None
    return datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S")
