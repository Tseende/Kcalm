"""Приём «как есть»: значения введены или взяты с упаковки, без LLM и без округления вверх-вниз."""
from __future__ import annotations

from app.domain.nutrition import CalculatedItem, MealCalculation


def derive(grams: float, kcal: float, protein: float, fat: float, carbs: float,
           per: str = "100g") -> tuple[tuple[int, float, float, float], tuple[float, float, float, float]]:
    """Введённые числа → (значения порции, значения на 100 г).

    per='100g': числа относятся к 100 г, порция = они * граммы / 100.
    per='portion': числа относятся ко всей порции, на 100 г = они * 100 / граммы.
    Что ввёл человек, то и сохраняется: ккал до целого, БЖУ до 0,1, без округления до 5/10."""
    grams = max(float(grams), 1.0)
    if per == "portion":
        absolute = (kcal, protein, fat, carbs)
        per100 = tuple(v * 100.0 / grams for v in absolute)
    else:
        per100 = (kcal, protein, fat, carbs)
        absolute = tuple(v * grams / 100.0 for v in per100)
    return ((int(round(absolute[0])), round(absolute[1], 1), round(absolute[2], 1),
             round(absolute[3], 1)),
            (round(per100[0], 2), round(per100[1], 2), round(per100[2], 2), round(per100[3], 2)))


def make_item(name: str, grams: float, kcal: float, protein: float, fat: float, carbs: float,
              per: str = "100g", *, source: str = "user", food_ref_id: int | None = None,
              edited: bool = False, name_en: str | None = None) -> CalculatedItem:
    grams = max(float(grams), 1.0)
    (a_kcal, a_p, a_f, a_c), (k100, p100, f100, c100) = derive(grams, kcal, protein, fat, carbs, per)
    return CalculatedItem(
        name_ru=name, name_en=name_en or name, grams=grams, grams_min=grams, grams_max=grams,
        kcal=a_kcal, protein_g=a_p, fat_g=a_f, carbs_g=a_c, kcal_low=a_kcal, kcal_high=a_kcal,
        food_ref_id=food_ref_id, source=source, confidence=1.0, kcal_100g=k100,
        protein_100g=p100, fat_100g=f100, carbs_100g=c100, edited_by_user=edited)


def combine(items: list[CalculatedItem]) -> MealCalculation:
    """Несколько точных позиций → приём: вилка равна итогу, уверенность по самой слабой позиции."""
    kcal = sum(i.kcal for i in items)
    return MealCalculation(
        items=items, kcal_total=kcal, kcal_low=kcal, kcal_high=kcal,
        protein_g=round(sum(i.protein_g for i in items), 1),
        fat_g=round(sum(i.fat_g for i in items), 1),
        carbs_g=round(sum(i.carbs_g for i in items), 1), method="db",
        confidence=round(min((i.confidence for i in items), default=1.0), 2))


def portion_calculation(name: str, grams: float, kcal100: float, protein100: float,
                        fat100: float, carbs100: float) -> MealCalculation:
    """Продукт с известным составом на 100 г, съеденный порцией в `grams`."""
    return combine([make_item(name, grams, kcal100, protein100, fat100, carbs100, "100g")])


def totals_calculation(name: str, kcal: float, protein: float, fat: float, carbs: float,
                       grams: float = 100.0) -> MealCalculation:
    """Блюдо одной строкой: итоговые ккал и БЖУ порции заданы напрямую."""
    return combine([make_item(name, grams, kcal, protein, fat, carbs, "portion")])
