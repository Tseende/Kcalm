"""Цели, TDEE, тренд веса. Формулы — из research/formulas.md, переносить как есть."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

log = logging.getLogger(__name__)

KCAL_PER_KG = 7700
MIN_KCAL_MALE = 1500
MIN_KCAL_FEMALE = 1200
MAX_RATE = 0.7            # кг/нед — быстрее не предлагаем
FULL_DEFICIT_DISTANCE = 5.0   # кг до цели, ближе которых дефицит гасится
ZERO_DEFICIT_DISTANCE = 1.0
REVIEW_MIN_DAYS = 6       # адаптивный пересмотр цели — раз в неделю (с запасом в день)
INTAKE_COVERAGE = 0.8     # день учитывается в адаптивном TDEE, если записано ≥80 % цели
MIN_AGE = 18              # младше — только поддержание веса, дефицит не считаем
MIN_BMI = 18.5            # ниже — дефицит не предлагаем, цель не опускаем под этот ИМТ

_PAL_BY_PA = {1.0: 1.25, 1.12: 1.5, 1.27: 1.75, 1.54: 2.2}


def pal_from_pa(pa: float | None) -> float:
    """Обратное к pa_from_pal: сохранённый в профиле PA → PAL для compute_target."""
    if pa is None:
        return 1.5
    return _PAL_BY_PA[min(_PAL_BY_PA, key=lambda known: abs(known - pa))]


def bmi(weight_kg: float, height_cm: float) -> float:
    return weight_kg / (height_cm / 100.0) ** 2


def min_goal_weight(height_cm: float) -> float:
    """Вес, при котором ИМТ = MIN_BMI: ниже цель не ставим."""
    return round(MIN_BMI * (height_cm / 100.0) ** 2, 1)


def pa_from_pal(pal: float, sex: str = "male") -> float:
    """Категория активности → PA. Правило границ из формул §1."""
    if pal < 1.4:
        return 1.00
    if pal < 1.6:
        return 1.12 if sex == "male" else 1.14
    if pal < 1.9:
        return 1.27
    return 1.54 if sex == "male" else 1.45


def tee_iom(sex: str, age: int, weight_kg: float, height_m: float, pa: float) -> int:
    """IOM 2005, p. 204. Скобки критичны: PA умножает и вес, и рост."""
    if sex == "male":
        tee = 864 - 9.72 * age + pa * (14.2 * weight_kg + 503 * height_m)
    else:
        tee = 387 - 7.31 * age + pa * (10.9 * weight_kg + 660.7 * height_m)
    return round10_kcal(tee)


def bmr_mifflin(sex: str, age: int, weight_kg: float, height_cm: float) -> float:
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex == "male" else -161)


def crosscheck_tee(tee: int, sex: str, age: int, weight_kg: float,
                   height_cm: float, pal: float) -> None:
    """Мягкая проверка вменяемости профиля — только в лог, не пользователю.

    У IOM TEE есть собственная константа термогенеза, поэтому для сидячих
    (PAL ≤ 1.12) он законно выше BMR×PAL и сравнивать модели бессмысленно —
    раньше это давало ложные warning'и на каждом расчёте.
    """
    if pal <= 1.12:
        return
    bmr = bmr_mifflin(sex, age, weight_kg, height_cm)
    expected = bmr * pal
    if abs(tee - expected) / max(tee, 1) > 0.25:
        log.info("TEE IOM %s расходится с BMR×PAL %s >25%% — проверь профиль",
                 tee, round(expected))


def round10_kcal(x: float) -> int:
    return int(round(x / 10.0)) * 10


@dataclass
class Target:
    kcal_target: int
    protein_g: float
    fat_g: float
    carbs_g: float
    tdee_est: int
    floor_applied: bool = False
    taper_factor: float = 1.0


def compute_target(sex: str, age: int, weight_kg: float, height_cm: float,
                   pal: float, rate_kg_week: float = -0.5,
                   goal_weight_kg: float | None = None) -> Target:
    """target = TEE + adjustment, adjustment гасится taper'ом у цели."""
    pa = pa_from_pal(pal, sex)
    height_m = height_cm / 100.0
    tee = tee_iom(sex, age, weight_kg, height_m, pa)
    crosscheck_tee(tee, sex, age, weight_kg, height_cm, pal)

    rate = max(-MAX_RATE, min(MAX_RATE, rate_kg_week))
    if age < MIN_AGE or bmi(weight_kg, height_cm) < MIN_BMI:
        rate = max(rate, 0.0)                        # нет дефицита: только поддержание
    adjustment = rate * KCAL_PER_KG / 7              # rate<0 (похудение) → дефицит

    taper = 1.0
    if goal_weight_kg is not None:
        distance = abs(goal_weight_kg - weight_kg)
        if distance <= ZERO_DEFICIT_DISTANCE:
            taper = 0.0
        elif distance < FULL_DEFICIT_DISTANCE:
            taper = (distance - ZERO_DEFICIT_DISTANCE) / \
                (FULL_DEFICIT_DISTANCE - ZERO_DEFICIT_DISTANCE)
    adjustment *= taper

    kcal = kcal_floor_check(tee + adjustment, sex, age, weight_kg, height_cm)
    return _macros(kcal, tee, goal_weight_kg, taper)


def kcal_floor_check(kcal: float, sex: str, age: int, weight_kg: float,
                     height_cm: float) -> tuple[int, bool]:
    """Мягкий пол: поднятие до минимума, flag вместо блокировки. -> (kcal, raised)"""
    floor = MIN_KCAL_MALE if sex == "male" else MIN_KCAL_FEMALE
    bmr = bmr_mifflin(sex, age, weight_kg, height_cm)
    floor = max(floor, math.ceil(1.1 * bmr / 10) * 10)
    if kcal < floor:
        return floor, True
    return int(round(kcal)), False


def _macros(kcal: int | tuple[int, bool], tdee_est: int,
            goal_weight_kg: float | None, taper: float) -> Target:
    if isinstance(kcal, tuple):
        kcal_val, raised = kcal
    else:
        kcal_val, raised = int(kcal), False
    goal = goal_weight_kg or 0
    protein = round(1.6 * goal, 1)
    fat = round(0.8 * goal, 1)
    carbs = max(50.0, round((kcal_val - 4 * protein - 9 * fat) / 4, 1))
    return Target(kcal_target=kcal_val, protein_g=protein, fat_g=fat,
                  carbs_g=carbs, tdee_est=tdee_est, floor_applied=raised,
                  taper_factor=round(taper, 2))


# ---------------------------------------------------------------- тренд веса

def ewma(values: list[float], alpha: float = 0.15) -> list[float]:
    """s_t = α·w_t + (1−α)·s_{t−1}, старт — первое измерение."""
    if not values:
        return []
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def ewma_dated(points: list[tuple[int, float]], alpha: float = 0.15) -> list[tuple[int, float]]:
    """EWMA по датам: за пропуск в gap дней прошлое значение затухает как (1−α)^gap,
    поэтому редкие взвешивания не «залипают» так, как при сглаживании по номеру замера."""
    out: list[tuple[int, float]] = []
    for i, (day, value) in enumerate(points):
        if i == 0:
            out.append((day, value))
            continue
        gap = max(1, day - points[i - 1][0])
        a = 1 - (1 - alpha) ** gap
        out.append((day, a * value + (1 - a) * out[-1][1]))
    return out


def weekly_slope(points: list[tuple[int, float]], window_days: int = 14) -> float | None:
    """Наклон веса, кг/нед: OLS по датам за последние window_days дней."""
    if not points:
        return None
    last = points[-1][0]
    window = [(d, v) for d, v in points if last - d < window_days]
    slope = ols_slope([d for d, _ in window], [v for _, v in window])
    return None if slope is None else slope * 7


def value_at_or_before(points: list[tuple[int, float]], day: int) -> float | None:
    """Последнее значение ряда не позже указанного дня."""
    prior = [v for d, v in points if d <= day]
    return prior[-1] if prior else None


def ols_slope(days: list[float], values: list[float]) -> float | None:
    """Наклон OLS; None при <2 точках или нулевой дисперсии."""
    n = len(days)
    if n < 2 or n != len(values):
        return None
    mean_d = sum(days) / n
    mean_v = sum(values) / n
    cov = sum((d - mean_d) * (v - mean_v) for d, v in zip(days, values))
    var = sum((d - mean_d) ** 2 for d in days)
    if var == 0:
        return None
    return cov / var


# ------------------------------------------------------------- адаптивный TDEE

@dataclass
class AdaptiveTdee:
    tdee_est: int | None
    new_target: int | None
    reason: str
    need_more_days: int | None = None   # сколько дней еды ещё нужно
    need_more_weighs: int | None = None


def adaptive_tdee(weight_points: list[tuple[float, float]],  # (день, кг)
                  intake_by_day: dict[float, int],           # день -> ккал (дни с покрытием ≥80 %)
                  current_target: int,
                  rate_kg_week: float = -0.5,
                  goal_weight_kg: float | None = None,
                  last_review_day: float | None = None,
                  today: float | None = None) -> AdaptiveTdee:
    """Пересчёт раз в неделю (не раньше чем через 6 дней); ±150 ккал за раз;
    данных мало — не трогать цель."""
    ref_day = today if today is not None else (weight_points[-1][0] if weight_points else None)
    if last_review_day is not None and ref_day is not None and \
            ref_day - last_review_day < REVIEW_MIN_DAYS:
        return AdaptiveTdee(None, None, "пересмотр не чаще раза в неделю")

    recent = weight_points[-14:]
    if len(recent) < 7:
        return AdaptiveTdee(None, None, "недостаточно взвешиваний",
                            need_more_weighs=7 - len(recent))
    if len(intake_by_day) < 10:
        return AdaptiveTdee(None, None, "недостаточно дней еды",
                            need_more_days=10 - len(intake_by_day))

    days = [d for d, _ in recent]
    weights = [w for _, w in recent]
    slope_day = ols_slope(days, weights)
    if slope_day is None:
        return AdaptiveTdee(None, None, "недостаточно взвешиваний")

    avg_intake = sum(intake_by_day.values()) / len(intake_by_day)
    deficit = -slope_day * KCAL_PER_KG                 # trend<0 → deficit>0
    tdee_est = int(round(avg_intake + deficit))

    rate = max(-MAX_RATE, min(MAX_RATE, rate_kg_week))
    adjustment = rate * KCAL_PER_KG / 7
    if goal_weight_kg is not None:
        distance = abs(goal_weight_kg - weights[-1])
        if distance <= ZERO_DEFICIT_DISTANCE:
            adjustment = 0
        elif distance < FULL_DEFICIT_DISTANCE:
            adjustment *= (distance - ZERO_DEFICIT_DISTANCE) / \
                (FULL_DEFICIT_DISTANCE - ZERO_DEFICIT_DISTANCE)

    desired = int(round(tdee_est + adjustment))
    capped = max(current_target - 150, min(current_target + 150, desired))
    reason = "adaptive_tdee"
    if capped != desired:
        reason += "_capped_150"
    return AdaptiveTdee(tdee_est=tdee_est, new_target=capped, reason=reason)
