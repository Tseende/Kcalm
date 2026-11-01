"""Вода: разбор фраз вида «выпил 2 стакана воды» без LLM и дневной ориентир.

Разбор детерминированный (регулярки): так вода не тратит запрос к модели и не
может превратиться в «еду» с выдуманной калорийностью. Голосовые приходят сюда уже
расшифрованным текстом.
"""
from __future__ import annotations

import re

MIN_ML = 10
MAX_ML = 3000                 # за одну запись; больше — скорее всего ошибка ввода

UNIT_ML = {"стакан": 250, "чашк": 200, "кружк": 300, "бутылк": 500, "бутылочк": 500}

_NUM_WORDS = {
    "полтора": 1.5, "полторы": 1.5, "пол": 0.5, "один": 1, "одну": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10,
}

_NUM = r"\d+(?:\.\d+)?|полтор[аы]|пол|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять"
_UNIT = r"мл|ml|миллилитр\w*|литр\w*|стакан\w*|чашк\w*|кружк\w*|бутылочк\w*|бутылк\w*|л|l"
_QTY = rf"(?:(?P<num>{_NUM})[\s-]*)?(?P<unit>{_UNIT})\b"
_WATER = r"(?:воды|воду|вода|водой|водичк\w*|water)"
_ADJ = (r"(?:(?:холодной|теплой|горячей|чистой|питьевой|минеральной|обычной|простой|"
        r"газированной|негазированной|кипяченой|фильтрованной)\s+)?")
_SEP = r"\s*[:=\-—]?\s*"

# «2 стакана (холодной) воды», «0.5 л воды», «стакан воды»
_QTY_BEFORE = re.compile(rf"(?<![\w.]){_QTY}\s+{_ADJ}{_WATER}\b")
# «вода 500 мл», «воды 2 стакана»
_QTY_AFTER = re.compile(rf"{_WATER}{_SEP}{_QTY}")
# «вода 500», «500 воды» — число без единицы считаем миллилитрами
_BARE_AFTER = re.compile(rf"{_WATER}{_SEP}(?P<n>\d{{2,4}})(?![\d.,]|\s*(?:кг|ккал|г\b|%))")
_BARE_BEFORE = re.compile(rf"(?<![\w.])(?P<n>\d{{3,4}})\s+{_WATER}\b")

_FILLER = {
    "выпил", "выпила", "выпили", "попил", "попила", "пью", "выпить", "ещё", "еще", "и", "плюс",
    "добавь", "добавить", "запиши", "записать", "записал", "сегодня", "утром", "днем", "днём",
    "вечером", "тоже", "около", "примерно", "где", "то", "ну", "в", "за", "на", "с", "был",
    "была", "было", "мне", "я",
}


def _to_ml(num: str | None, unit: str) -> int | None:
    unit = unit.lower()
    value = 1.0
    if num:
        value = _NUM_WORDS.get(num) or float(num)
    if unit in ("мл", "ml") or unit.startswith("миллилитр"):
        if not num:
            return None
        return round(value)
    if unit in ("л", "l") or unit.startswith("литр"):
        if not num and unit in ("л", "l"):
            return None
        return round(value * 1000)
    for stem, ml in UNIT_ML.items():
        if unit.startswith(stem):
            return round(value * ml)
    return None


def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return re.sub(r"\s+", " ", text)


def parse_amount(text: str) -> int | None:
    """Объём из короткого ввода команды: «300», «300 мл», «2 стакана», «0.5 л»."""
    text = _normalize(text).strip()
    if re.fullmatch(r"\d{2,4}", text):
        return int(text)
    m = re.fullmatch(_QTY, text)
    if not m:
        return None
    return _to_ml(m.group("num"), m.group("unit"))


def extract_water(text: str) -> tuple[int, str]:
    """Найти воду в тексте. Возвращает (мл, остаток без фраз про воду); 0 — воды нет.

    Если остаток не содержит ничего, кроме служебных слов («выпил», «ещё»),
    сообщение было только про воду.
    """
    norm = _normalize(text)
    total = 0
    for pattern in (_QTY_BEFORE, _QTY_AFTER, _BARE_AFTER, _BARE_BEFORE):
        def take(m: re.Match) -> str:
            nonlocal total
            if "n" in m.groupdict() and m.groupdict().get("n"):
                ml: int | None = int(m.group("n"))
            else:
                ml = _to_ml(m.group("num"), m.group("unit"))
            if ml is None or not MIN_ML <= ml <= MAX_ML:
                return m.group(0)                    # не похоже на воду — не трогаем
            total += ml
            return " "
        norm = pattern.sub(take, norm)
    return total, norm.strip()


def is_water_only(remainder: str) -> bool:
    """Остаток после extract_water не содержит еды."""
    words = re.findall(r"[a-zа-я]+", remainder.lower().replace("ё", "е"))
    return all(w in _FILLER or len(w) < 3 for w in words)


# чай и кофе (в том числе с молоком или сахаром) засчитываются в питьевой объём;
# сок, газировка, какао и алкоголь — нет, они идут только в калории
_HYDRATING = re.compile(
    r"(?<![а-яa-z])(?:чай|чая|чаю|чаем|кофе|капучино|латте|американо|эспрессо|раф|мокко|"
    r"флэт\s*уайт|tea|coffee|cappuccino|latte|americano|espresso)(?![а-яa-z])")
_NOT_A_DRINK = re.compile(r"чайн\w+\s+ложк|зерн|молот|растворим\w*\s+порош|сухо|конфет|торт|пирожн")


def hydration_ml(name_ru: str, name_en: str = "", grams: float = 0) -> int:
    """Сколько мл напитка из позиции приёма пищи идёт в воду (0 — не чай и не кофе).

    Масса напитка в граммах ≈ объёму в мл. Меньше MIN_ML (ложка кофе) не считаем."""
    name = f"{name_ru} {name_en}".lower().replace("ё", "е")
    if not _HYDRATING.search(name) or _NOT_A_DRINK.search(name):
        return 0
    ml = round(grams or 0)
    return ml if MIN_ML <= ml <= MAX_ML else 0


def water_goal_ml(weight_kg: float | None) -> int:
    """Ориентир ≈30 мл на кг веса, в коридоре 1500–3000 мл, кратно 100.
    Это общее правило, а не медицинская норма: при болезнях почек, сердца и
    ограничениях жидкости цель задаёт врач."""
    if not weight_kg:
        return 2000
    return max(1500, min(3000, round(weight_kg * 30 / 100) * 100))
