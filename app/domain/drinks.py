"""Напитки с калориями: быстрый ввод из приложения без LLM.

Белки, жиры и углеводы даны на 100 мл как у типичных напитков, ккал считаются из них (4/9/4).
Это ориентиры, а не данные производителя: у одной и той же колы или фанты состав
отличается по странам и годам. Точные значения с этикетки лучше добавить продуктом.
Чай и кофе потом сами засчитываются в воду (domain.water.hydration_ml по названию).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.nutrition import CalculatedItem, MealCalculation
from app.domain.water import hydration_ml

SUGAR_TSP_G = 5.0                 # чайная ложка сахара без горки
MAX_SUGAR_TSP = 6
MIN_ML = 30
MAX_ML = 1500
RANGE = 0.10                      # ±10 % вокруг оценки


@dataclass(frozen=True)
class Drink:
    id: str
    name: str
    name_en: str
    emoji: str
    color: str                    # ключ цвета плитки в интерфейсе (ui.TILE)
    protein: float                # г на 100 мл
    fat: float
    carbs: float
    sizes: tuple[int, ...]        # быстрые объёмы, мл
    sugar: bool = False           # можно добавить ложки сахара

    @property
    def kcal_100(self) -> int:
        return round(4 * self.protein + 9 * self.fat + 4 * self.carbs)


DRINKS: tuple[Drink, ...] = (
    Drink("cola_zero", "Кола без сахара", "cola zero", "🥤", "gray", 0, 0, 0, (330, 500)),
    Drink("cola", "Кола", "cola", "🥤", "red", 0, 0, 10.6, (330, 500)),
    Drink("fanta", "Фанта", "fanta orange soda", "🍊", "orange", 0, 0, 10, (330, 500)),
    Drink("tea", "Чай", "tea", "🍵", "green", 0, 0, 0, (200, 300, 400), True),
    Drink("americano", "Американо", "americano", "☕", "indigo", 0.1, 0, 0, (200, 300), True),
    Drink("cappuccino", "Капучино", "cappuccino", "☕", "orange", 1.8, 2.2, 3.0, (180, 250, 350), True),
    Drink("flat_white", "Флэт уайт", "flat white", "☕", "purple", 2.6, 3.0, 3.8, (160, 200), True),
    Drink("latte", "Латте", "latte", "🥛", "blue", 2.0, 2.4, 3.2, (250, 350), True),
    Drink("juice", "Сок", "juice", "🧃", "yellow", 0.5, 0.1, 10.5, (200, 250)),
)
BY_ID = {d.id: d for d in DRINKS}


def get(drink_id: str) -> Drink | None:
    return BY_ID.get(drink_id)


def catalog_json() -> list[dict]:
    return [{"id": d.id, "name": d.name, "emoji": d.emoji, "color": d.color,
             "kcal_100": round(4 * d.protein + 9 * d.fat + 4 * d.carbs, 1),
             "sizes": list(d.sizes), "sugar": d.sugar,
             "hydrates": hydration_ml(d.name, d.name_en, 100) > 0}      # чай и кофе идут в воду
            for d in DRINKS]


def item_name(drink: Drink, sugar_tsp: int) -> str:
    return drink.name if not sugar_tsp else f"{drink.name}, {sugar_tsp} ч. л. сахара"


def calculate(drink: Drink, ml: float, sugar_tsp: int = 0) -> MealCalculation:
    """Один напиток одной порцией. Сахар (только у горячих напитков) добавляется к тому же пункту."""
    ml = float(ml)
    if not MIN_ML <= ml <= MAX_ML:
        raise ValueError(f"объём напитка {MIN_ML}-{MAX_ML} мл")
    sugar_tsp = int(sugar_tsp) if drink.sugar else 0
    if not 0 <= sugar_tsp <= MAX_SUGAR_TSP:
        raise ValueError(f"сахара от 0 до {MAX_SUGAR_TSP} ч. л.")
    sugar_g = sugar_tsp * SUGAR_TSP_G
    protein = drink.protein * ml / 100
    fat = drink.fat * ml / 100
    carbs = drink.carbs * ml / 100 + sugar_g
    kcal = round(4 * protein + 9 * fat + 4 * carbs)
    low, high = round(kcal * (1 - RANGE)), round(kcal * (1 + RANGE))
    per100 = lambda v: round(v * 100 / ml, 2)           # noqa: E731
    item = CalculatedItem(
        name_ru=item_name(drink, sugar_tsp), name_en=drink.name_en, grams=ml,
        grams_min=ml, grams_max=ml, kcal=kcal, protein_g=round(protein, 1), fat_g=round(fat, 1),
        carbs_g=round(carbs, 1), kcal_low=low, kcal_high=high, source="seed", confidence=0.9,
        kcal_100g=per100(kcal), protein_100g=per100(protein), fat_100g=per100(fat),
        carbs_100g=per100(carbs))
    return MealCalculation(
        items=[item], kcal_total=kcal, kcal_low=low, kcal_high=high, protein_g=item.protein_g,
        fat_g=item.fat_g, carbs_g=item.carbs_g, method="db", confidence=0.9)
