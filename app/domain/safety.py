"""Забота о здоровье: признаки, при которых бот должен смягчиться, а не давить на результат.

Бот не ставит диагнозов. Он считает простые сигналы из записей и в ответ делает три вещи:
перестаёт снижать цель, реже просит взвешиваться и честно предлагает поговорить со специалистом.
"""
from __future__ import annotations

from dataclasses import dataclass

VERY_LOW_KCAL = 1000            # день с ≥2 записанными приёмами и меньше этого — тревожно мало
FAST_LOSS_PCT_WEEK = 1.0        # быстрее ≈1 % веса в неделю — выше безопасного темпа
BMI_SOFT_FLOOR = 19.5           # рядом с нижней границей нормы дефицит уже не нужен
MIN_BMI_FOR_DEFICIT = 18.5


@dataclass
class SafetyInputs:
    very_low_days: int = 0          # за 14 дней
    logged_days: int = 0
    overate_14d: int = 0
    risk_events_30d: int = 0        # эпизоды с упоминанием рвоты/слабительных/голодовок/самоповреждения
    busy_weigh_days: int = 0        # дней за 14, когда скриншотов весов было 3 и больше
    bmi: float | None = None
    target_rate: float = 0.0        # кг/нед, отрицательный = похудение
    loss_pct_week: float | None = None   # положительное число = вес падает
    care_mode: bool = False


@dataclass
class Flag:
    code: str
    text: str
    strong: bool = False


def assess(i: SafetyInputs) -> list[Flag]:
    flags: list[Flag] = []
    if i.risk_events_30d >= 1:
        flags.append(Flag(
            "risk_language",
            "В твоих сообщениях были слова про рвоту, слабительные, голодовку или желание "
            "навредить себе. Это важнее любых цифр.", strong=True))
    if i.very_low_days >= 4:
        flags.append(Flag(
            "very_low_intake",
            f"В {i.very_low_days} днях за две недели записано меньше {VERY_LOW_KCAL} ккал. "
            "Либо часть еды не записана, либо ты правда ешь слишком мало, а это опасно.",
            strong=i.very_low_days >= 8))
    if i.overate_14d >= 3:
        flags.append(Flag(
            "repeated_overate",
            f"За две недели было {i.overate_14d} эпизода переедания. Чаще всего это следствие "
            "жёстких ограничений, а не слабой воли."))
    if i.busy_weigh_days >= 2:
        flags.append(Flag(
            "weighing_often",
            "Ты взвешиваешься по несколько раз в день. Вес колеблется на килограмм из-за воды "
            "и соли, и такие цифры чаще расстраивают, чем помогают."))
    if i.bmi is not None and i.bmi < BMI_SOFT_FLOOR and i.target_rate < 0:
        flags.append(Flag(
            "near_bmi_floor",
            f"Твой ИМТ {i.bmi:.1f} уже рядом с нижней границей нормы (18.5). "
            "Пора переходить с похудения на поддержание веса."))
    if i.loss_pct_week is not None and i.loss_pct_week >= FAST_LOSS_PCT_WEEK:
        flags.append(Flag(
            "fast_loss",
            f"Вес снижается примерно на {i.loss_pct_week:.1f} % в неделю, это быстрее "
            "безопасного темпа около 1 %."))
    return flags


def needs_professional(flags: list[Flag]) -> bool:
    return any(f.strong for f in flags) or len(flags) >= 2


def freeze_target_decrease(flags: list[Flag], care_mode: bool) -> bool:
    """Нельзя снижать калорийность цели автоматически."""
    return care_mode or any(f.code in ("very_low_intake", "fast_loss", "near_bmi_floor",
                                       "risk_language", "repeated_overate") for f in flags)


def reduce_weighing(flags: list[Flag], care_mode: bool) -> bool:
    return care_mode or any(f.code in ("weighing_often", "risk_language", "repeated_overate")
                            for f in flags)


CARE_PROFESSIONAL = (
    "Мне важно сказать прямо: стоит обсудить это с врачом или психологом, лучше с тем, кто "
    "работает с питанием и расстройствами пищевого поведения. Это не слабость и не диагноз, "
    "а нормальный способ получить поддержку. Я помогаю с записями и планом, но не заменяю "
    "специалиста."
)
