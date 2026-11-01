"""Свои продукты: проверка КБЖУ, разбор фразы «добавь продукт: ...», подпись к фото этикетки.

Чистые функции, без БД, LLM и сети. Все числа продукта — на 100 г.
"""
from __future__ import annotations

import re

MAX_KCAL_100G = 900
MAX_MACRO_100G = 100
KJ_PER_KCAL = 4.184

FIELD_RU = {"name": "название", "kcal": "ккал", "protein": "Б", "fat": "Ж", "carbs": "У"}
REQUIRED = ("name", "kcal", "protein", "fat", "carbs")


def kcal_from_macros(protein: float, fat: float, carbs: float) -> float:
    return protein * 4 + fat * 9 + carbs * 4


def check_values(kcal: float, protein: float, fat: float, carbs: float) -> list[str]:
    """Границы КБЖУ на 100 г. Невозможное → ValueError, подозрительное → список предупреждений."""
    if not 0 <= kcal <= MAX_KCAL_100G:
        raise ValueError(f"Калорийность на 100 г должна быть от 0 до {MAX_KCAL_100G} ккал.")
    for label, value in (("Белки", protein), ("Жиры", fat), ("Углеводы", carbs)):
        if not 0 <= value <= MAX_MACRO_100G:
            raise ValueError(f"{label} на 100 г должны быть от 0 до {MAX_MACRO_100G} г.")
    warnings: list[str] = []
    if protein + fat + carbs > MAX_MACRO_100G + 2:
        warnings.append("Б + Ж + У вместе больше 100 г: так не бывает, проверь числа.")
    expected = kcal_from_macros(protein, fat, carbs)
    if abs(kcal - expected) > max(30.0, 0.25 * expected):
        warnings.append(f"Калории не сходятся с БЖУ: по Б/Ж/У выходит около {round(expected)} ккал, "
                        f"а указано {round(kcal)}. Проверь числа.")
    return warnings


def draft_missing(draft: dict) -> list[str]:
    """Чего не хватает до сохранения (русские подписи полей)."""
    return [FIELD_RU[k] for k in REQUIRED if draft.get(k) in (None, "")]


def complete_draft(draft: dict) -> tuple[dict, bool]:
    """Нет калорий, но есть все БЖУ: считаем по формуле Этуотера. (draft, посчитано ли)."""
    out = dict(draft)
    if out.get("kcal") is None and all(out.get(k) is not None for k in ("protein", "fat", "carbs")):
        out["kcal"] = round(kcal_from_macros(out["protein"], out["fat"], out["carbs"]))
        return out, True
    return out, False


def merge_draft(old: dict, new: dict) -> dict:
    """Исправление поверх черновика: непустые значения нового перекрывают старые."""
    merged = dict(old)
    for key, value in new.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def fmt_num(value: float) -> str:
    return f"{float(value):g}"


def describe_per100(draft_or_row) -> str:
    """«121 ккал · Б 17 / Ж 5 / У 1.8» (dict или sqlite3.Row с полями draft или таблицы foods)."""
    def get(*keys):
        for k in keys:
            try:
                value = draft_or_row[k]
            except (KeyError, IndexError, TypeError):
                continue
            if value is not None:
                return value
        return None
    kcal, p, f, c = get("kcal", "kcal_100g"), get("protein", "protein_100g"), \
        get("fat", "fat_100g"), get("carbs", "carbs_100g")
    parts = []
    if kcal is not None:
        parts.append(f"{round(float(kcal))} ккал")
    macros = [("Б", p), ("Ж", f), ("У", c)]
    if any(v is not None for _, v in macros):
        parts.append(" / ".join(f"{k} {fmt_num(v) if v is not None else '?'}" for k, v in macros))
    return " · ".join(parts)


# ------------------------------------------------------------------ разбор текста

_NUM = r"(\d+(?:[.,]\d+)?)"
_NOT_LETTER_AFTER = r"(?![а-яёa-z])"
_NOT_LETTER_BEFORE = r"(?<![а-яёa-z\d])"

PRODUCT_ADD_RE = re.compile(
    r"^\s*(?:добавь|добавить|запомни|создай|сохрани|запиши|новый)\s+(?:мне\s+)?"
    r"(?:мой\s+|новый\s+|свой\s+)?продукт\w*\s*[:\-—]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
PRODUCT_LIST_RE = re.compile(
    r"^\s*(?:мои продукты|мои свои продукты|мои сохран[её]нные продукты|"
    r"(?:покажи|открой) (?:мои |свои )?продукты|список (?:моих |своих )?продуктов)\W*$",
    re.IGNORECASE)

_KCAL = [
    re.compile(rf"{_NUM}\s*(?:ккал|kcal|калор\w*|кал{_NOT_LETTER_AFTER})", re.IGNORECASE),
    re.compile(rf"(?:ккал|kcal|калор\w*|калорийность|энергетическая ценность|энергия)"
               rf"\s*[:=\-—]?\s*{_NUM}(?!\s*(?:кдж|kj))", re.IGNORECASE),
]
_KJ = re.compile(rf"{_NUM}\s*(?:кдж|kj)", re.IGNORECASE)
_MACRO_WORDS = {
    "protein": r"(?:белк[иао]в?|белок|протеин\w*|protein|б)",
    "fat": r"(?:жир(?:ы|а|ов)?|fat|ж)",
    "carbs": r"(?:углевод(?:ы|а|ов)?|carbs?|у)",
}
_MACRO_BEFORE = {k: re.compile(
    rf"{_NOT_LETTER_BEFORE}{w}{_NOT_LETTER_AFTER}\s*[:=\-—]?\s*{_NUM}", re.IGNORECASE)
    for k, w in _MACRO_WORDS.items()}
_MACRO_LONG = {
    "protein": r"(?:белк[иао]в?|белок|протеин\w*)",
    "fat": r"(?:жир(?:ы|а|ов)?)",
    "carbs": r"(?:углевод(?:ы|а|ов)?)",
}
_MACRO_AFTER = {k: re.compile(
    rf"{_NUM}\s*(?:г|гр|g)?\s*{w}{_NOT_LETTER_AFTER}", re.IGNORECASE)
    for k, w in _MACRO_LONG.items()}
_TRIPLE = re.compile(
    rf"(?:кбжу|бжу|б\s*/\s*ж\s*/\s*у){_NOT_LETTER_AFTER}\s*[:\-—=]?\s*"
    rf"{_NUM}(?:\s*[/|;,\-]\s*|\s+){_NUM}(?:\s*[/|;,\-]\s*|\s+){_NUM}", re.IGNORECASE)
_BASIS = re.compile(rf"(?:на|в|за|per)\s*{_NUM}\s*(?:г|гр|грамм\w*|g){_NOT_LETTER_AFTER}",
                    re.IGNORECASE)
_BRAND = re.compile(r"(?:бренд|марка|производитель)\s*[:\-—]?\s*([^,;]+)", re.IGNORECASE)
_PREFIX = re.compile(
    r"^\s*(?:(?:добавь|добавить|запомни|создай|сохрани|запиши|новый)\s+(?:мне\s+)?"
    r"(?:мой\s+|новый\s+|свой\s+)?продукт\w*\s*[:\-—]?\s*)", re.IGNORECASE)


def _f(value) -> float:
    return float(str(value).replace(",", "."))


def _blank(text: str, span: tuple[int, int]) -> str:
    return text[:span[0]] + " , " + text[span[1]:]


def parse_product_text(text: str) -> dict | None:
    """«творог 5% Савушкин, 121 ккал, Б 17, Ж 5, У 1.8» → черновик продукта на 100 г.

    Принимает полную фразу с «добавь продукт:» и короткое исправление («белки 17 жиры 5»).
    Любое поле может быть None. None целиком — в тексте нет ни калорий, ни БЖУ.
    Числа «на N г» (N не 100) пересчитываются на 100 г."""
    t = _PREFIX.sub("", text or "", count=1).strip()
    if not t:
        return None
    found: dict[str, float] = {}

    m = _TRIPLE.search(t)
    if m:
        found["protein"], found["fat"], found["carbs"] = (_f(m.group(i)) for i in (1, 2, 3))
        t = _blank(t, m.span())
    for key in ("protein", "fat", "carbs"):
        if key in found:
            continue
        for pat in (_MACRO_BEFORE[key], _MACRO_AFTER[key]):
            m = pat.search(t)
            if m:
                found[key] = _f(m.group(1))
                t = _blank(t, m.span())
                break
    for pat in _KCAL:
        m = pat.search(t)
        if m:
            found["kcal"] = _f(m.group(1))
            t = _blank(t, m.span())
            break
    else:
        m = _KJ.search(t)
        if m:
            found["kcal"] = round(_f(m.group(1)) / KJ_PER_KCAL)
            t = _blank(t, m.span())
    if not found:
        return None

    basis_g = None
    m = _BASIS.search(t)
    if m:
        basis = _f(m.group(1))
        t = _blank(t, m.span())
        if basis != 100 and 5 <= basis <= 2000:
            basis_g = basis
            scale = 100.0 / basis
            for key, value in list(found.items()):
                found[key] = round(value * scale) if key == "kcal" else round(value * scale, 1)

    brand = None
    m = _BRAND.search(t)
    if m:
        brand = m.group(1).strip()
        t = _blank(t, m.span())
    parts = [p.strip(" \t\n.:;—-") for p in re.split(r";|,(?!\d)", t)]
    parts = [p for p in parts if p]
    name = parts[0][:80] if parts else None
    if brand is None and len(parts) > 1 and len(parts[1]) <= 30 and not re.search(r"\d", parts[1]):
        brand = parts[1]
    draft = {"name": name, "brand": brand[:40] if brand else None,
             "kcal": found.get("kcal"), "protein": found.get("protein"),
             "fat": found.get("fat"), "carbs": found.get("carbs")}
    if basis_g:
        draft["basis_g"] = basis_g
    return draft


# ------------------------------------------------------------------ подпись к фото

LABEL_CAPTION_RE = re.compile(
    r"этикетк|\bсостав\b|\bпродукт\b|кбжу|пищев\w+\s+ценност|nutrition", re.IGNORECASE)
_BODY_CAPTION_RE = re.compile(r"состав\w*\s+тела|весы|пикук|picooc|mi\s*fitness", re.IGNORECASE)


def caption_says_label(caption: str | None) -> bool:
    """Подпись к фото просит прочитать этикетку (а не посчитать еду)."""
    c = (caption or "").strip()
    return bool(c) and bool(LABEL_CAPTION_RE.search(c)) and not _BODY_CAPTION_RE.search(c)


HELP_TEXT = (
    "Свои продукты: бот запомнит КБЖУ на 100 г и будет брать их в расчёт. Четыре способа:\n"
    "• фото этикетки с подписью «этикетка»: я прочитаю таблицу пищевой ценности;\n"
    "• текстом: «добавь продукт: творог 5% Савушкин, 121 ккал, Б 17, Ж 5, У 1.8»;\n"
    "• из записи еды: «✏️ Изменить» → позиция → «💾 В мои продукты»;\n"
    "• по штрихкоду: кнопка «💾 Сохранить себе» под найденным продуктом.")
