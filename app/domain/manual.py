"""Приём «как есть»: значения введены или взяты с упаковки, без LLM и без округления вверх-вниз."""
from __future__ import annotations

from app.domain.nutrition import CalculatedItem, MealCalculation


def _calc(name: str, grams: float, kcal: float, protein: float, fat: float, carbs: float) -> MealCalculation:
    grams = max(float(grams), 1.0)
    per100 = lambda v: round(v * 100 / grams, 2)      # noqa: E731
    item = CalculatedItem(
        name_ru=name, name_en=name, grams=grams, grams_min=grams, grams_max=grams, kcal=round(kcal),
        protein_g=round(protein, 1), fat_g=round(fat, 1), carbs_g=round(carbs, 1),
        kcal_low=round(kcal), kcal_high=round(kcal), source="user", confidence=1.0,
        kcal_100g=per100(kcal), protein_100g=per100(protein), fat_100g=per100(fat),
        carbs_100g=per100(carbs))
    return MealCalculation(
        items=[item], kcal_total=item.kcal, kcal_low=item.kcal, kcal_high=item.kcal,
        protein_g=item.protein_g, fat_g=item.fat_g, carbs_g=item.carbs_g, method="db", confidence=1.0)


def portion_calculation(name: str, grams: float, kcal100: float, protein100: float,
                        fat100: float, carbs100: float) -> MealCalculation:
    """Продукт с известным составом на 100 г, съеденный порцией в `grams`."""
    k = max(float(grams), 1.0) / 100.0
    return _calc(name, grams, kcal100 * k, protein100 * k, fat100 * k, carbs100 * k)


def totals_calculation(name: str, kcal: float, protein: float, fat: float, carbs: float,
                       grams: float = 100.0) -> MealCalculation:
    """Блюдо одной строкой: итоговые ккал и БЖУ порции заданы напрямую."""
    return _calc(name, grams, kcal, protein, fat, carbs)
