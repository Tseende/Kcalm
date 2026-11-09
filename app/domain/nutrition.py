"""Пищевые схемы и ДЕТЕРМИНИРОВАННЫЙ расчёт. LLM сюда не входит: только Python."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Confidence = Literal["low", "medium", "high"]
MealType = Literal["breakfast", "lunch", "dinner", "snack"]
Preparation = Literal["fried", "boiled", "baked", "raw", "unknown"]


class IngredientEstimate(BaseModel):
    """Диапазон граммов обязателен: одно число из LLM — это ложная точность."""

    model_config = ConfigDict(allow_inf_nan=False)

    name: str = Field(..., min_length=1, max_length=240)
    name_ru: str = ""
    grams_min: float = Field(..., ge=0)
    grams_max: float = Field(..., ge=0)
    preparation: str = "unknown"
    brand: str | None = None
    explicit_grams: float | None = None  # масса, названная пользователем — главнее оценки
    confidence: Confidence = "medium"

    @model_validator(mode="after")
    def _fix_range(self) -> "IngredientEstimate":
        if self.grams_max < self.grams_min:            # модели путают порядок
            self.grams_min, self.grams_max = self.grams_max, self.grams_min
        if self.grams_min == 0 and self.grams_max == 0:
            raise ValueError("empty gram range")
        return self

    @property
    def grams_mid(self) -> float:
        return (self.grams_min + self.grams_max) / 2


class MealUnderstanding(BaseModel):
    """Контракт FoodExtraction после нормализации к диапазонам."""

    model_config = ConfigDict(allow_inf_nan=False)

    items: list[IngredientEstimate] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=48)
    confidence: Confidence = "medium"
    overall_confidence: float = Field(0.5, ge=0, le=1)
    clarification_question: str | None = None
    clarification_options: list[str] = Field(default_factory=list, max_length=3)
    # что хочет человек: food или имя команды (определяет та же модель, что разбирает еду)
    intent: str = "food"
    intent_arg: str = ""
    photo_kind: str = "meal"      # meal | label (фото этикетки) | other


class FoodRef(BaseModel):
    """Сопоставленная запись пищевой базы (на 100 г)."""

    food_id: int | None = None
    name_ru: str
    name_en: str
    kcal_100g: float
    protein_100g: float
    fat_100g: float
    carbs_100g: float
    source: Literal["off", "usda", "user", "seed", "llm"]
    confidence: float = 0.5
    unresolved: bool = False      # КБЖУ не определены: в сумме 0 ккал, это надо показать


class CalculatedItem(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    name_ru: str
    name_en: str
    grams: float
    grams_min: float
    grams_max: float
    kcal: int
    protein_g: float
    fat_g: float
    carbs_g: float
    kcal_low: int
    kcal_high: int
    food_ref_id: int | None = None
    source: str = "seed"
    confidence: float = 0.5
    quality_flags: list[str] = Field(default_factory=list)
    # база на 100 г, по которой считали: нужна, чтобы пересчитывать граммы без потерь
    kcal_100g: float | None = None
    protein_100g: float | None = None
    fat_100g: float | None = None
    carbs_100g: float | None = None
    # позиция введена или подтверждена человеком: «Уточнить» через модель её не трогает
    edited_by_user: bool = False


class MealCalculation(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    items: list[CalculatedItem]
    kcal_total: int
    kcal_low: int
    kcal_high: int
    protein_g: float
    fat_g: float
    carbs_g: float
    method: Literal["db", "mixed", "llm_prior"]
    confidence: float
    quality_flags: list[str] = Field(default_factory=list)


def round5(x: float) -> int:
    return int(round(x / 5.0)) * 5


def round10(x: float) -> int:
    return int(round(x / 10.0)) * 10


def calculate_item(item: IngredientEstimate, ref: FoodRef) -> CalculatedItem:
    grams = item.explicit_grams if item.explicit_grams else item.grams_mid
    grams = max(0.0, min(grams, 3000.0))
    scale_lo = item.grams_min / 100.0
    scale_hi = item.grams_max / 100.0
    scale = grams / 100.0
    flags: list[str] = []
    if ref.unresolved:
        flags.append(f"не нашёл калорийность «{item.name_ru or item.name}» — "
                     "в сумме 0 ккал, внеси вручную")
    kcal_mid = ref.kcal_100g * scale

    # проверка 4/9/4: заявленная энергия против энергии из макросов, допуск 25 %
    # (сравниваем НА 100 Г, масштаб граммов одинаковый по обе стороны)
    macro_kcal_100 = 4 * ref.protein_100g + 9 * ref.fat_100g + 4 * ref.carbs_100g
    if macro_kcal_100 > 0 and abs(ref.kcal_100g - macro_kcal_100) / max(ref.kcal_100g, macro_kcal_100, 1) > 0.25:
        flags.append("источник противоречив: ккал не сходятся с БЖУ")

    kcal = round5(kcal_mid)
    return CalculatedItem(
        name_ru=item.name_ru or ref.name_ru,
        name_en=item.name or ref.name_en,
        grams=round(grams),
        grams_min=round(item.grams_min),
        grams_max=round(item.grams_max),
        kcal=kcal,
        protein_g=round(ref.protein_100g * scale, 1),
        fat_g=round(ref.fat_100g * scale, 1),
        carbs_g=round(ref.carbs_100g * scale, 1),
        kcal_low=round5(ref.kcal_100g * scale_lo),
        kcal_high=round5(ref.kcal_100g * scale_hi),
        food_ref_id=ref.food_id,
        source=ref.source,
        confidence=ref.confidence,
        quality_flags=flags,
        kcal_100g=ref.kcal_100g,
        protein_100g=ref.protein_100g,
        fat_100g=ref.fat_100g,
        carbs_100g=ref.carbs_100g,
    )


def calculate_meal(items: list[tuple[IngredientEstimate, FoodRef]]) -> MealCalculation:
    """Сумма позиций. Пересчёт всегда с нуля, не инкрементом."""
    calculated = [calculate_item(i, r) for i, r in items]
    kcal_total = round10(sum(c.kcal for c in calculated))
    kcal_low = round10(sum(c.kcal_low for c in calculated))
    kcal_high = round10(sum(c.kcal_high for c in calculated))
    flags = [f for c in calculated for f in c.quality_flags]

    # проверка плотности макросов: составное блюдо ≥ 180 г не может быть чистым жиром
    if len(calculated) >= 2:
        grams_mid = sum(c.grams for c in calculated)
        if grams_mid >= 180:
            prot = sum(c.protein_g for c in calculated)
            fat = sum(c.fat_g for c in calculated)
            carb = sum(c.carbs_g for c in calculated)
            if (prot / grams_mid * 100 > 32 or fat / grams_mid * 100 > 55
                    or carb / grams_mid * 100 > 90
                    or (prot + fat + carb) / grams_mid * 100 > 95):
                flags.append("порции выглядят завышенными")

    if any(c.source == "llm" for c in calculated):
        method: Literal["db", "mixed", "llm_prior"] = (
            "mixed" if any(c.source != "llm" for c in calculated) else "llm_prior"
        )
    else:
        method = "db"

    confidences = [c.confidence for c in calculated] or [0.5]
    return MealCalculation(
        items=calculated,
        kcal_total=kcal_total,
        kcal_low=kcal_low,
        kcal_high=kcal_high,
        protein_g=round(sum(c.protein_g for c in calculated), 1),
        fat_g=round(sum(c.fat_g for c in calculated), 1),
        carbs_g=round(sum(c.carbs_g for c in calculated), 1),
        method=method,
        confidence=round(min(confidences), 2),
        quality_flags=flags,
    )


def recalculate_from_snapshot(items: list[dict]) -> tuple[int, float, float, float]:
    """Пересчёт итогов при правке граммов — из снимка КБЖУ, не из справочника.

    items: dicts с keys grams, kcal_100g, protein_100g, fat_100g, carbs_100g
    (либо уже посчитанные kcal/protein/... и grams_old для пропорции).
    """
    kcal = sum(float(it["kcal_100g"]) * float(it["grams"]) / 100 for it in items)
    prot = sum(float(it["protein_100g"]) * float(it["grams"]) / 100 for it in items)
    fat = sum(float(it["fat_100g"]) * float(it["grams"]) / 100 for it in items)
    carb = sum(float(it["carbs_100g"]) * float(it["grams"]) / 100 for it in items)
    return round10(kcal), round(prot, 1), round(fat, 1), round(carb, 1)
