"""Посуда: описание для модели и разбор фразы «добавь посуду: глубокая тарелка 20 см 500 мл».

Чистые функции, без БД, LLM и сети.
"""
from __future__ import annotations

import re

KIND_RU = {
    "plate": "тарелка", "deep_plate": "глубокая тарелка", "bowl": "миска", "mug": "кружка",
    "cup": "чашка", "glass": "стакан", "pan": "сковорода", "container": "контейнер",
    "other": "посуда",
}

# порядок важен: «глубокая тарелка» раньше «тарелка»
_KIND_PATTERNS = [
    ("deep_plate", r"глубок\w*\s+тарелк\w*|суповая\s+тарелк\w*|тарелк\w*\s+для\s+супа"),
    ("plate", r"тарелк\w*"),
    ("bowl", r"миск\w*|пиал\w*|салатник\w*|боул\w*"),
    ("mug", r"кружк\w*"),
    ("cup", r"чашк\w*"),
    ("glass", r"стакан\w*|бокал\w*"),
    ("pan", r"сковород\w*"),
    ("container", r"контейнер\w*|ланч-?бокс\w*"),
]

_NUM = r"(\d+(?:[.,]\d+)?)"
_PREFIX = re.compile(
    r"^\s*(?:(?:добавь|добавить|запомни|создай|сохрани|новая)\s+(?:мне\s+)?(?:мою\s+|новую\s+)?)?"
    r"посуд\w*\s*[:\-—]?\s*", re.IGNORECASE)
_QUOTED = re.compile(r"[«\"“„]([^»\"”“]{1,40})[»\"”“]")
_DEPTH = [re.compile(rf"глубин\w*\s*{_NUM}\s*(см|мм)", re.IGNORECASE),
          re.compile(rf"{_NUM}\s*(см|мм)\s*глубин\w*", re.IGNORECASE)]
_VOLUME = re.compile(rf"{_NUM}\s*(мл|миллилитр\w*|л|литр\w*)(?![а-яa-z])", re.IGNORECASE)
_DIAMETER = [re.compile(rf"(?:диаметр\w*|ø|⌀)\s*{_NUM}\s*(см|мм)?", re.IGNORECASE),
             re.compile(rf"{_NUM}\s*(см|мм)(?![а-яa-z])", re.IGNORECASE)]
_DEFAULT = re.compile(r"по\s+умолчанию|основн\w+|главн\w+", re.IGNORECASE)

LIMITS = {"diameter_cm": (3.0, 60.0), "depth_cm": (0.5, 40.0), "volume_ml": (20.0, 5000.0)}


def _f(value) -> float:
    return float(str(value).replace(",", "."))


def _get(dish, key: str):
    try:
        return dish[key]
    except (KeyError, IndexError, TypeError):
        return getattr(dish, key, None)


def _num(value) -> str:
    return f"{float(value):g}"


def describe_dish(dish) -> str:
    """«глубокая тарелка «Белая», Ø20 см, глубина 5 см, объём 500 мл» (dict или sqlite3.Row)."""
    kind = KIND_RU.get(_get(dish, "kind") or "other", "посуда")
    name = (_get(dish, "name") or "").strip()
    head = kind if not name or name.lower() == kind else f"{kind} «{name}»"
    parts = [head]
    if _get(dish, "diameter_cm"):
        parts.append(f"Ø{_num(_get(dish, 'diameter_cm'))} см")
    if _get(dish, "depth_cm"):
        parts.append(f"глубина {_num(_get(dish, 'depth_cm'))} см")
    if _get(dish, "volume_ml"):
        parts.append(f"объём {_num(_get(dish, 'volume_ml'))} мл")
    return ", ".join(parts)


def _pull(patterns, text: str, scale):
    """Первое совпадение: (значение, текст без него)."""
    for pat in patterns:
        m = pat.search(text)
        if m:
            unit = (m.group(2) or "см").lower() if m.lastindex and m.lastindex >= 2 else "см"
            return scale(_f(m.group(1)), unit), text[:m.start()] + " " + text[m.end():]
    return None, text


def parse_dish_text(text: str) -> dict | None:
    """Фраза про посуду → {name, kind, diameter_cm, depth_cm, volume_ml, is_default}.

    None — ничего похожего на посуду (нет ни типа, ни размеров) или размеры нереальные.
    Принимает и полную фразу с «добавь посуду:», и только описание."""
    t = _PREFIX.sub("", text or "", count=1).strip()
    if not t:
        return None
    name = None
    quoted = _QUOTED.search(t)
    if quoted:
        name = quoted.group(1).strip()
        t = t[:quoted.start()] + " " + t[quoted.end():]
    is_default = bool(_DEFAULT.search(t))
    t = _DEFAULT.sub(" ", t)

    depth, t = _pull(_DEPTH, t, lambda v, u: v / 10 if u == "мм" else v)
    volume = None
    m = _VOLUME.search(t)
    if m:
        unit = m.group(2).lower()
        volume = _f(m.group(1)) * (1 if unit.startswith("мл") or unit.startswith("милли") else 1000)
        t = t[:m.start()] + " " + t[m.end():]
    diameter, t = _pull(_DIAMETER, t, lambda v, u: v / 10 if u == "мм" else v)

    kind = next((k for k, pat in _KIND_PATTERNS if re.search(pat, t, re.IGNORECASE)), None)
    if kind is None and diameter is None and depth is None and volume is None:
        return None
    for key, value in (("diameter_cm", diameter), ("depth_cm", depth), ("volume_ml", volume)):
        if value is not None and not LIMITS[key][0] <= value <= LIMITS[key][1]:
            return None
    kind = kind or "other"
    return {"name": name or KIND_RU[kind].capitalize(),
            "kind": kind, "diameter_cm": diameter, "depth_cm": depth,
            "volume_ml": volume, "is_default": is_default}


DISH_ADD_RE = re.compile(
    r"^\s*(?:добавь|добавить|запомни|создай|сохрани|новая)\s+(?:мне\s+)?(?:мою\s+|новую\s+)?"
    r"посуд\w*\s*[:\-—]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
DISH_SHORT_RE = re.compile(r"^\s*посуда\s*[:\-—]?\s*(.+)$", re.IGNORECASE | re.DOTALL)
DISH_LIST_RE = re.compile(
    r"^\s*(?:моя посуда|мои посуды|покажи (?:мою )?посуду|список посуды|посуда)\W*$",
    re.IGNORECASE)

HELP_TEXT = (
    "Как добавить посуду: напиши, например,\n"
    "• добавь посуду: глубокая тарелка 20 см 500 мл\n"
    "• посуда кружка 350 мл\n"
    "• добавь посуду «Белая» тарелка Ø26 см по умолчанию\n"
    "Тип: тарелка, глубокая тарелка, миска, кружка, чашка, стакан, сковорода, контейнер. "
    "Размеры: см или мм, мл или л, глубина: «глубина 5 см».")
