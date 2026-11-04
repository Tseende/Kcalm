"""Свои продукты: этикетка → черновик, текст → черновик, сохранение, копия из приёма или OFF.

Без aiogram: сервис используют и бот, и web-API. Все числа продукта — на 100 г.
Пересчёт «на порцию» и кДж → ккал, проверки границ делает код, а не LLM.
"""
from __future__ import annotations

import logging
import re
import sqlite3

from app.adapters import off
from app.config import get_config
from app.domain import products as dom
from app.domain.matching import normalize
from app.domain.products import (check_values, complete_draft, draft_missing,  # noqa: F401
                                 parse_product_text)
from app.llm import client as llm
from app.services.food import load_prompt
from app.storage import repo

log = logging.getLogger(__name__)

DRAFT_KEYS = ("name", "brand", "kcal", "protein", "fat", "carbs", "default_grams",
              "unit_name", "unit_grams", "barcode")


def _num(value, lo: float, hi: float) -> float | None:
    """Число из ответа LLM в границах [lo, hi]; всё остальное — None."""
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number == number and lo <= number <= hi else None


def _text(value, limit: int = 80) -> str | None:
    value = str(value).strip() if value not in (None, "") else ""
    return value[:limit] or None


# ------------------------------------------------------------------ этикетка

def label_draft(data: dict) -> tuple[dict, float, list[str]]:
    """Ответ LLM по этикетке → (черновик на 100 г, уверенность, заметки). Без сети: тестируется."""
    notes: list[str] = []
    if not isinstance(data, dict):
        return _empty_draft(), 0.0, ["не удалось разобрать ответ"]
    per100 = data.get("per_100g") if isinstance(data.get("per_100g"), dict) else {}
    per_serv = data.get("per_serving") if isinstance(data.get("per_serving"), dict) else {}
    serving_g = _num(data.get("serving_g"), 1, 5000)
    package_g = _num(data.get("package_g"), 1, 20000)
    limits = {"kcal": (0, 10_000), "protein": (0, 1000), "fat": (0, 1000), "carbs": (0, 1000)}
    values = {k: _num(per100.get(k), *limits[k]) for k in limits}

    kj = _num(data.get("kj_100g"), 0, dom.MAX_KCAL_100G * dom.KJ_PER_KCAL * 1.2)
    if values["kcal"] is None and kj is not None:
        values["kcal"] = round(kj / dom.KJ_PER_KCAL)
        notes.append("калории пересчитал из кДж")

    if serving_g:
        scale = 100.0 / serving_g
        scaled = []
        for key in limits:
            raw = _num(per_serv.get(key), *limits[key])
            if values[key] is None and raw is not None:
                values[key] = round(raw * scale) if key == "kcal" else round(raw * scale, 1)
                scaled.append(key)
        if scaled:
            notes.append(f"значения на порцию {serving_g:g} г пересчитал на 100 г")

    for key, value in list(values.items()):
        if value is None:
            continue
        cap = dom.MAX_KCAL_100G if key == "kcal" else dom.MAX_MACRO_100G
        if value > cap:
            notes.append(f"{dom.FIELD_RU[key]} {value:g} вне допустимых границ, убрал")
            values[key] = None
    values = {k: (round(v) if k == "kcal" and v is not None else v) for k, v in values.items()}

    unit_name = unit_grams = None
    if package_g:
        unit_name, unit_grams = "упаковка", package_g
    elif serving_g:
        unit_name, unit_grams = "порция", serving_g
    barcode = re.sub(r"\D", "", str(data.get("barcode") or ""))
    if barcode and not off.valid_barcode(barcode):
        notes.append("штрихкод прочитан с ошибкой, проверьте цифры")
        barcode = ""
    draft = {"name": _text(data.get("name")), "brand": _text(data.get("brand"), 40),
             **values, "default_grams": serving_g, "unit_name": unit_name,
             "unit_grams": unit_grams, "barcode": barcode or None}
    llm_note = _text(data.get("notes"), 200)
    if llm_note:
        notes.append(llm_note)
    conf = _num(data.get("confidence"), 0, 1) or 0.0
    draft, derived = complete_draft(draft)
    if derived:
        notes.append("калории посчитал по БЖУ")
    return draft, conf, notes


def _empty_draft() -> dict:
    return {k: None for k in DRAFT_KEYS}


async def parse_label(db: sqlite3.Connection, user, image_b64: str | list[str]
                      ) -> tuple[dict, float, list[str]]:
    """Фото этикетки (одно или несколько фото одного товара) → (черновик, уверенность 0..1, заметки).
    Ничего не сохраняет."""
    images = [image_b64] if isinstance(image_b64, str) else list(image_b64)
    content = [{"type": "text", "text": load_prompt("label")},
               *({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                 for b64 in images)]
    data, _model = await llm.chat_json(
        "label_parse", [{"role": "user", "content": content}],
        model=get_config().model_vision, temperature=0.0,
        user_id=user["id"] if user else None, db=db)
    return label_draft(data)


# ------------------------------------------------------------------ Open Food Facts

_NUTRIENTS = ("kcal", "protein", "fat", "carbs")


def _off_draft(found: dict, code: str) -> dict:
    """Продукт OFF → черновик на 100 г (как у этикетки)."""
    serving = found.get("serving_g")
    return {"name": _text(found.get("name")), "brand": _text(found.get("brand"), 40),
            "kcal": round(found["kcal_100g"]), "protein": found["protein_100g"],
            "fat": found["fat_100g"], "carbs": found["carbs_100g"],
            "default_grams": serving, "unit_name": "порция" if serving else None,
            "unit_grams": serving, "barcode": code}


async def enrich_with_off(draft: dict, notes: list[str], transport=None) -> tuple[dict, list[str]]:
    """Штрихкод с фото → Open Food Facts: дополняет пробелы и сверяет калории.

    Числа с упаковки главнее: база краудсорсная. OFF берём целиком только когда на фото
    КБЖУ прочиталось не полностью. Сбой сети ничего не ломает."""
    code = draft.get("barcode")
    if not code:
        return draft, notes
    found = await off.fetch_barcode(code, transport=transport)
    if not found:
        return draft, notes
    ref = _off_draft(found, code)
    out, notes = dict(draft), list(notes)
    if any(out.get(k) is None for k in _NUTRIENTS):
        for k in _NUTRIENTS:
            out[k] = ref[k]
        notes.append("КБЖУ не читалось с фото, взял из Open Food Facts: проверьте")
    elif ref["kcal"] and abs(out["kcal"] - ref["kcal"]) / ref["kcal"] > 0.2:
        notes.append(f"На фото {out['kcal']} ккал, в Open Food Facts {ref['kcal']}: проверьте упаковку")
    for k in ("name", "brand", "default_grams", "unit_name", "unit_grams"):
        if not out.get(k) and ref.get(k):
            out[k] = ref[k]
    return out, notes


async def barcode_draft(db: sqlite3.Connection, user, code: str, transport=None
                        ) -> tuple[dict, int | None] | None:
    """Штрихкод → (черновик, id своего продукта с этим кодом | None) или None, если нигде нет.

    Сначала Open Food Facts; при сбое сети берём локальный кэш прошлых находок."""
    found = await off.fetch_barcode(code, transport=transport)
    if found:
        draft = _off_draft(found, code)
    else:
        cached = repo.food_by_barcode(db, code, user["id"] if user else None)
        if cached is None:
            return None
        draft = {"name": _text(cached["name_ru"]), "brand": _text(cached["brand"], 40),
                 "kcal": round(cached["kcal_100g"]), "protein": cached["protein_100g"],
                 "fat": cached["fat_100g"], "carbs": cached["carbs_100g"],
                 "default_grams": cached["default_grams"], "unit_name": cached["unit_name"],
                 "unit_grams": cached["unit_grams"], "barcode": code}
    own = db.execute("SELECT id FROM foods WHERE barcode=? AND owner_user_id=? AND deleted_at IS NULL",
                     (code, user["id"])).fetchone()
    return draft, (own["id"] if own else None)


# ------------------------------------------------------------------ текст

_TEXT_PROMPT = (
    "Из сообщения пользователя вытащи продукт питания и его КБЖУ НА 100 г. Верни СТРОГО JSON: "
    '{"name": строка|null, "brand": строка|null, "kcal": число|null, "protein": число|null, '
    '"fat": число|null, "carbs": число|null}. Только то, что написано в сообщении; '
    "ничего не придумывай. Если числа даны на порцию или на другой вес, пересчитай на 100 г.")


async def parse_text(db: sqlite3.Connection, user, text: str) -> dict | None:
    """«добавь продукт ...» → черновик. Сначала регулярки; LLM только если чего-то не хватает."""
    draft = parse_product_text(text)
    if draft is not None:
        filled, _ = complete_draft(draft)
        if not draft_missing(filled):
            return draft
    try:
        data, _model = await llm.chat_json(
            "product_text",
            [{"role": "system", "content": _TEXT_PROMPT}, {"role": "user", "content": text}],
            model=get_config().model_text, temperature=0.0,
            user_id=user["id"] if user else None, db=db, max_tokens=300)
    except Exception:                                          # noqa: BLE001
        log.warning("product_text: LLM недоступен", exc_info=True)
        return draft
    if not isinstance(data, dict):
        return draft
    from_llm = {"name": _text(data.get("name")), "brand": _text(data.get("brand"), 40),
                "kcal": _num(data.get("kcal"), 0, dom.MAX_KCAL_100G),
                "protein": _num(data.get("protein"), 0, dom.MAX_MACRO_100G),
                "fat": _num(data.get("fat"), 0, dom.MAX_MACRO_100G),
                "carbs": _num(data.get("carbs"), 0, dom.MAX_MACRO_100G)}
    if all(v is None for k, v in from_llm.items() if k not in ("name", "brand")):
        return draft
    if draft is None:
        return from_llm
    return dom.merge_draft(from_llm, draft)             # числа, найденные регулярками, главнее


# ------------------------------------------------------------------ сохранение

def _same_product(db: sqlite3.Connection, user_id: int, name: str, brand: str | None,
                  barcode: str | None = None) -> sqlite3.Row | None:
    key = normalize(f"{name} {brand or ''}")
    for row in repo.list_products(db, user_id, limit=1000):
        if barcode and row["barcode"] == barcode:
            return row
        if normalize(f"{row['name_ru']} {row['brand'] or ''}") == key:
            return row
    return None


def save_product(db: sqlite3.Connection, user, draft: dict,
                 barcode: str | None = None) -> tuple[int, list[str]]:
    """Черновик → свой продукт. Продукт с тем же названием и брендом обновляется, а не двоится.

    → (id, предупреждения). ValueError — не хватает полей или значения вне границ."""
    uid = user["id"]
    draft, _derived = complete_draft(draft)
    missing = draft_missing(draft)
    if missing:
        raise ValueError("Не хватает: " + ", ".join(missing) + ".")
    warnings = check_values(draft["kcal"], draft["protein"], draft["fat"], draft["carbs"])
    fields = {k: draft.get(k) for k in repo.PRODUCT_FIELDS if draft.get(k) is not None}
    barcode = barcode or draft.get("barcode")
    if barcode:
        fields["barcode"] = barcode
    existing = _same_product(db, uid, draft["name"], draft.get("brand"), barcode)
    if existing:
        repo.update_product(db, uid, existing["id"], **fields)
        return existing["id"], warnings
    name = fields.pop("name")
    pid = repo.create_product(db, uid, name, fields.pop("kcal"), fields.pop("protein"),
                              fields.pop("fat"), fields.pop("carbs"), **fields)
    return pid, warnings


def product_from_item(db: sqlite3.Connection, user, item_id: int,
                      name: str | None = None) -> tuple[int, list[str]]:
    """Позиция из записи еды → свой продукт (КБЖУ на 100 г берём из позиции).

    ValueError, если позиции нет или по ней нельзя вывести КБЖУ."""
    item = repo.owned_meal_item(db, user["id"], item_id)
    if item is None:
        raise ValueError("Позиция не найдена.")
    grams = item["grams"] or 0

    def per100(col100: str, col_abs: str) -> float | None:
        if item[col100] is not None:
            return float(item[col100])
        if grams > 0 and item[col_abs] is not None:
            return float(item[col_abs]) * 100.0 / grams
        return None

    kcal, p, f, c = (per100("kcal_100g", "kcal"), per100("protein_100g", "protein_g"),
                     per100("fat_100g", "fat_g"), per100("carbs_100g", "carbs_g"))
    if None in (kcal, p, f, c):
        raise ValueError("В этой позиции нет КБЖУ на 100 г.")
    draft = {"name": _text(name or item["name_ru"] or item["name_en"]),
             "kcal": round(kcal), "protein": round(p, 1), "fat": round(f, 1),
             "carbs": round(c, 1), "default_grams": round(grams) if grams > 0 else None}
    if not draft["name"]:
        raise ValueError("Нужно название продукта.")
    return save_product(db, user, draft)


_NAME_BRAND = re.compile(r"^(?P<name>.+?)\s*\((?P<brand>[^()]+)\)\s*$")


def product_from_food(db: sqlite3.Connection, user, food_id: int
                      ) -> tuple[int, list[str]]:
    """Общий продукт (например, найденный по штрихкоду в OFF) → копия в «мои продукты»."""
    row = db.execute("SELECT * FROM foods WHERE id=? AND deleted_at IS NULL "
                     "AND (owner_user_id IS NULL OR owner_user_id=?)",
                     (food_id, user["id"])).fetchone()
    if row is None:
        raise ValueError("Продукт не найден.")
    if row["owner_user_id"] == user["id"]:
        return row["id"], []
    label = row["name_ru"]
    m = _NAME_BRAND.match(label)
    name, brand = (m.group("name"), m.group("brand")) if m else (label, None)
    draft = {"name": name, "brand": brand, "kcal": round(row["kcal_100g"]),
             "protein": row["protein_100g"], "fat": row["fat_100g"],
             "carbs": row["carbs_100g"]}
    return save_product(db, user, draft, barcode=row["barcode"])


# ------------------------------------------------------------------ форма API

def product_dict(row) -> dict:
    """Строка foods → Product из docs/V2_API.md."""
    def get(key):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None
    return {"id": row["id"], "name": row["name_ru"], "brand": get("brand"),
            "kcal": row["kcal_100g"], "protein": row["protein_100g"], "fat": row["fat_100g"],
            "carbs": row["carbs_100g"], "default_grams": get("default_grams"),
            "unit_name": get("unit_name"), "unit_grams": get("unit_grams"),
            "barcode": get("barcode"), "times_used": get("times_used") or 0}
