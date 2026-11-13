"""Сон, шаги и тренировки текстом: «спал 7 часов», «10 тысяч шагов», «зал 1 час».

Чистые функции, без БД и сети. Парсеры осторожные: фраза про еду, вопрос или намерение
(«хочу спать 8 часов») ничего не распознают, и сообщение идёт дальше как раньше.
"""
from __future__ import annotations

import re

MAX_TEXT_LEN = 100
SLEEP_MIN, SLEEP_MAX = 30, 960            # минут: короче получаса и дольше 16 ч — не ночной сон
STEPS_MIN, STEPS_MAX = 100, 200_000
WORKOUT_MIN, WORKOUT_MAX = 5, 600         # минут

_FOOD_LOG = re.compile(r"\b(?:съел|съела|поел|поела|скушал|скушала|выпил|выпила|заказал|заказала)\b")
_WISH = re.compile(r"\b(?:хочу|хочется|надо|нужно|собираюсь|планирую|буду|хотел|хотела|цель|"
                   r"стоит|должен|должна|как)\b")
_YESTERDAY = re.compile(r"\bвчера\b")

_NUM = r"\d+(?:[.,]\d+)?"
_WORDS = {"один": 1, "одну": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
          "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
          "двенадцать": 12}
_WORD_RE = "|".join(_WORDS)
_FILLER = r"(?:\s+(?:всего|только|около|примерно|где-то|почти|ровно|сегодня|ночью|вчера|реально))*"
_FILLER_TRAIL = rf"{_FILLER}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def _f(value: str) -> float:
    return float(value.replace(",", "."))


def _guard(t: str) -> bool:
    """False — фраза не похожа на отчёт о сделанном."""
    return bool(t) and len(t) <= MAX_TEXT_LEN and not t.endswith("?") \
        and not _FOOD_LOG.search(t) and not _WISH.search(t)


def wants_yesterday(text: str) -> bool:
    return bool(_YESTERDAY.search(_norm(text)))


# ------------------------------------------------------------------ сон

_HM = rf"(?P<h>{_NUM}|{_WORD_RE})\s*(?:ч|час\w*)(?:\s*(?P<m>\d{{1,2}})\s*(?:м|мин\w*)?)?"
_COLON = r"(?P<ch>\d{1,2}):(?P<cm>\d{2})"
_MIN_ONLY = r"(?P<mo>\d{2,4})\s*мин\w*"
_SLEEP_VERB = r"(?<![а-я])(?<!не\s)(?:спал[аи]?|поспал[аи]?|выспал[а-я]*|сон)"
_SLEEP_DUR = re.compile(rf"{_SLEEP_VERB}{_FILLER}\s*[:\-—]?\s*(?:{_HM}|{_COLON}|{_MIN_ONLY})")
_SLEEP_BARE = re.compile(
    rf"^(?:вчера\s+)?сон\s*[:\-—]?\s*(?P<h>{_NUM})(?:\s*(?:ч|час\w*))?"
    rf"(?:\s*(?P<m>\d{{1,2}})\s*(?:м|мин\w*))?$")
_CLOCK = r"(\d{1,2})(?:[:.](\d{2}))?"
_SLEEP_RANGE = re.compile(
    rf"(?:лег(?:ла)?|ложил\w*|засн\w*|заснул\w*)\s+(?:спать\s+)?(?:в\s+)?{_CLOCK}"
    rf".{{0,30}}?(?:встал[аи]?|проснул\w*|вста\w*|подъем)\s+(?:в\s+)?{_CLOCK}")
_SLEEP_FROM_TO = re.compile(rf"спал[аи]?\s+с\s+{_CLOCK}\s+до\s+{_CLOCK}")


def _hhmm(hour: str, minute: str | None) -> str | None:
    h, m = int(hour), int(minute or 0)
    return f"{h:02d}:{m:02d}" if 0 <= h <= 23 and 0 <= m <= 59 else None


def _range_minutes(start: str, end: str) -> int:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    diff = (eh * 60 + em) - (sh * 60 + sm)
    return diff if diff > 0 else diff + 24 * 60


def parse_sleep_text(text: str) -> dict | None:
    """→ {duration_min, start, end, yesterday} (start/end «HH:MM» или None) либо None."""
    t = _norm(text)
    if not _guard(t):
        return None
    yesterday = wants_yesterday(t)
    for pat in (_SLEEP_RANGE, _SLEEP_FROM_TO):
        m = pat.search(t)
        if m:
            start, end = _hhmm(m.group(1), m.group(2)), _hhmm(m.group(3), m.group(4))
            if not start or not end or start == end:
                return None
            minutes = _range_minutes(start, end)
            if not SLEEP_MIN <= minutes <= SLEEP_MAX:
                return None
            return {"duration_min": minutes, "start": start, "end": end, "yesterday": yesterday}
    minutes = None
    m = _SLEEP_DUR.search(t)
    if m:
        d = m.groupdict()
        if d.get("h") is not None:
            raw = d["h"]
            hours = _WORDS[raw] if raw in _WORDS else _f(raw)
            minutes = round(hours * 60) + int(d["m"] or 0)
        elif d.get("ch") is not None:
            minutes = int(d["ch"]) * 60 + int(d["cm"])
        elif d.get("mo") is not None:
            minutes = int(d["mo"])
    else:
        m = _SLEEP_BARE.match(t)
        if m:
            value = _f(m.group("h"))
            if m.group("m") is None and value > 24 and "." not in m.group("h") \
                    and "," not in m.group("h"):
                minutes = round(value)                      # «сон 450» — минуты
            else:
                minutes = round(value * 60) + int(m.group("m") or 0)
    if minutes is None or not SLEEP_MIN <= minutes <= SLEEP_MAX:
        return None
    return {"duration_min": minutes, "start": None, "end": None, "yesterday": yesterday}


# ------------------------------------------------------------------ шаги

_THOUSANDS = rf"\d{{1,3}}(?:[  ]\d{{3}})+|{_NUM}"
_MULT = r"(?:\s*(?P<k>тысяч\w*|тыс\b\.?|к|k)(?![а-яa-z]))?"
_STEPS_A = re.compile(rf"(?P<n>{_THOUSANDS}){_MULT}\s*шаг\w*")
_STEPS_B = re.compile(
    rf"шаг\w*\s*[:\-—=]?\s*(?:за\s+(?:день|сегодня)\s*)?(?P<n>{_THOUSANDS}){_MULT}"
    rf"(?![\d.,]*\s*(?:км|мин|ккал))")


def parse_steps_text(text: str) -> dict | None:
    """→ {steps, yesterday} либо None."""
    t = _norm(text)
    if not _guard(t):
        return None
    for pat in (_STEPS_A, _STEPS_B):
        for m in pat.finditer(t):
            value = _f(re.sub(r"[  ]", "", m.group("n")))
            if m.group("k"):
                value *= 1000
            steps = round(value)
            if STEPS_MIN <= steps <= STEPS_MAX:
                return {"steps": steps, "yesterday": wants_yesterday(t)}
    return None


# ------------------------------------------------------------------ тренировки

_SPORTS = [
    ("бег", r"бег\w*|пробеж\w*|побегал\w*"),
    ("зал", r"зал(?:е|а|у|ом)?"),
    ("плавание", r"плаван\w*|плавал\w*|бассейн\w*"),
    ("йога", r"йог\w*"),
    ("велосипед", r"велосипед\w*|велотренаж\w*|катал\w*\s+на\s+велик\w*"),
    ("ходьба", r"ходьб\w*"),
    ("прогулка", r"прогулк\w*|гулял\w*"),
    ("силовая", r"силов\w*"),
    ("кардио", r"кардио"),
    ("пилатес", r"пилатес\w*"),
    ("растяжка", r"растяжк\w*|стретчинг\w*"),
    ("футбол", r"футбол\w*"),
    ("теннис", r"теннис\w*"),
    ("танцы", r"танц\w*"),
    ("скакалка", r"скакалк\w*"),
    ("эллипс", r"эллипс\w*"),
    ("гребля", r"гребл\w*|гребн\w*"),
]
_SPORT_RES = [(name, re.compile(rf"(?<![а-яa-z]){pat}(?![а-яa-z])")) for name, pat in _SPORTS]
_TRAINING = re.compile(r"(?<![а-яa-z])тренировк\w*|(?<![а-яa-z])тренил\w*|(?<![а-яa-z])тренировал\w*")
_WK_MIN = re.compile(rf"(?P<n>{_NUM})\s*мин\w*")
_WK_HOUR = re.compile(rf"(?P<n>{_NUM})\s*(?:ч\b|час\w*)")
_WK_HALF = re.compile(r"полчаса|пол\s*часа")
_WK_ONE_AND_HALF = re.compile(r"полтора\s+час\w*")
_WK_BARE_HOUR = re.compile(r"(?<![\d.,]\s)(?<![\d.,])\bчас\b(?!\s*назад)")
_WK_KM = re.compile(rf"(?P<n>{_NUM})\s*км(?![а-яa-z])")


def _workout_minutes(t: str) -> int | None:
    total = 0.0
    found = False
    m = _WK_ONE_AND_HALF.search(t)
    if m:
        total, found = 90, True
    elif _WK_HALF.search(t):
        total, found = 30, True
    else:
        h = _WK_HOUR.search(t)
        if h:
            total, found = total + _f(h.group("n")) * 60, True
        elif _WK_BARE_HOUR.search(t):
            total, found = total + 60, True
    mi = _WK_MIN.search(t)
    if mi:
        total, found = total + _f(mi.group("n")), True
    return round(total) if found else None


def parse_workout_text(text: str) -> dict | None:
    """«тренировка бег 30 минут» → {name: «бег 30 мин», minutes, km, yesterday} либо None.

    Нужны вид занятия и длительность (или километры)."""
    t = _norm(text)
    if not _guard(t):
        return None
    sport = next((name for name, pat in _SPORT_RES if pat.search(t)), None)
    generic = bool(_TRAINING.search(t))
    if sport is None and not generic:
        return None
    minutes = _workout_minutes(t)
    km_match = _WK_KM.search(t)
    km = _f(km_match.group("n")) if km_match else None
    if minutes is not None and not WORKOUT_MIN <= minutes <= WORKOUT_MAX:
        return None
    if km is not None and not 0.1 <= km <= 100:
        km = None
    if minutes is None and km is None:
        return None
    parts = [sport or "тренировка"]
    if km is not None:
        parts.append(f"{km:g} км")
    if minutes is not None:
        parts.append(f"{minutes} мин")
    return {"name": " ".join(parts), "minutes": minutes, "km": km,
            "yesterday": wants_yesterday(t)}


# ---- подсказка про уровень активности в профиле
HINT_WINDOW_DAYS = 21      # смотрим последние три недели
HINT_MIN_DAYS = 10         # и только если шаги записаны хотя бы в 10 из них
_STEP_LEVELS = ((5000, 1), (10000, 2))   # < 5000 сидячая, < 10000 лёгкая, дальше средняя


def steps_level(avg_steps: float) -> int:
    """Уровень активности профиля (1..3), которому отвечают средние шаги. Высокую (4) по одним шагам не ставим."""
    for limit, level in _STEP_LEVELS:
        if avg_steps < limit:
            return level
    return 3


def activity_hint(avg_steps: float | None, days: int, workouts: int, current: int) -> int | None:
    """Какой уровень активности предложить вместо current, либо None.

    Норма калорий от шагов не пересчитывается: человеку лишь предлагается поправить профиль.
    Вверх предлагаем по шагам. Вниз только на одну ступень и только если в окне нет тренировок:
    шаги не видят зал и бассейн.
    """
    if avg_steps is None or days < HINT_MIN_DAYS:
        return None
    level = steps_level(avg_steps)
    if level > current:
        return level
    if level < current and workouts == 0:
        return current - 1
    return None
