"""Упакованные продукты: штрихкод → Open Food Facts → локальный кэш → расчёт приёма."""
from __future__ import annotations

import re
import sqlite3

from app.adapters import off
from app.domain.nutrition import FoodRef, IngredientEstimate, MealUnderstanding
from app.storage import repo

# «4600605123456», «штрихкод 4600605123456 40», «ean: 5449000000996 330 г»
_BARCODE_MSG = re.compile(
    r"^(?:штрих\s*-?\s*код|barcode|ean)?\s*:?\s*(\d{8,14})"
    r"(?:\s+(\d{1,4}(?:[.,]\d+)?)\s*(?:г|гр|грамм\w*|g)?)?\s*$", re.IGNORECASE)
_GRAMS_MSG = re.compile(r"^(\d{1,4}(?:[.,]\d+)?)\s*(?:г|гр|грамм\w*|g)?$", re.IGNORECASE)


def parse_barcode_message(text: str) -> tuple[str, float | None] | None:
    """«штрихкод + необязательные граммы» → (код, граммы) или None, если это не штрихкод."""
    m = _BARCODE_MSG.match(text.strip())
    if not m:
        return None
    grams = float(m.group(2).replace(",", ".")) if m.group(2) else None
    return m.group(1), grams


def parse_grams_reply(text: str) -> float | None:
    m = _GRAMS_MSG.match(text.strip())
    if not m:
        return None
    grams = float(m.group(1).replace(",", "."))
    return grams if 1 <= grams <= 3000 else None


def _ref_from_row(row: sqlite3.Row) -> FoodRef:
    return FoodRef(food_id=row["id"], name_ru=row["name_ru"], name_en=row["name_en"],
                   kcal_100g=row["kcal_100g"], protein_100g=row["protein_100g"],
                   fat_100g=row["fat_100g"], carbs_100g=row["carbs_100g"],
                   source=row["source"], confidence=0.9)


async def lookup(db: sqlite3.Connection, code: str, transport=None,
                 user_id: int | None = None) -> tuple[FoodRef, float | None] | None:
    """Продукт по штрихкоду: сначала локальный кэш, затем OFF. Возвращает (ref, порция в г)."""
    cached = repo.food_by_barcode(db, code, user_id)
    if cached:
        return _ref_from_row(cached), None
    product = await off.fetch_barcode(code, transport=transport)
    if not product:
        return None
    label = f"{product['name']} ({product['brand']})" if product["brand"] else product["name"]
    food_id = repo.save_off_food(db, code, label, product["name"], product["kcal_100g"],
                                 product["protein_100g"], product["fat_100g"],
                                 product["carbs_100g"])
    row = db.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone()
    return _ref_from_row(row), product["serving_g"]


def understanding_for(ref: FoodRef, grams: float) -> MealUnderstanding:
    """Порция названа явно: узкий диапазон ±5 %, уверенность высокая."""
    return MealUnderstanding(
        items=[IngredientEstimate(name=ref.name_en, name_ru=ref.name_ru,
                                  grams_min=grams * 0.95, grams_max=grams * 1.05,
                                  explicit_grams=grams, confidence="high")],
        confidence="high", overall_confidence=0.9)
