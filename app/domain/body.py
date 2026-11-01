"""Состав тела с бытовых весов: что показывать и как об этом говорить.

Оговорки, которые бот всегда держит в голове: биоимпеданс на бытовых весах приблизителен
(±3–5 %), зависит от воды, еды и времени суток, а шкалы «норма / повышено» у производителей
разные. Поэтому цифры сравниваются с ориентирами и со ВАШИМИ прошлыми замерами, а не
выдаются как диагноз. Главное — динамика за недели, а не одно число.
"""
from __future__ import annotations

from app.rich import Doc

# (поле, название, единица)
FIELDS = [
    ("weight_kg", "Вес", "кг"),
    ("bmi", "ИМТ", ""),
    ("body_fat_pct", "Жир", "%"),
    ("muscle_mass_pct", "Мышцы", "%"),
    ("skeletal_muscle_pct", "Скелетные мышцы", "%"),
    ("visceral_fat", "Висцеральный жир", ""),
    ("body_water_pct", "Вода в теле", "%"),
    ("protein_pct", "Белок в теле", "%"),
    ("bone_mass_kg", "Кости", "кг"),
    ("bmr_kcal", "Базовый обмен (BMR)", "ккал"),
    ("metabolic_age", "Метаболический возраст", ""),
]
# динамику между замерами показываем только по этим полям, остальное слишком шумное
DELTA_FIELDS = ("weight_kg", "body_fat_pct", "muscle_mass_pct", "visceral_fat", "body_water_pct")
# для них «больше» — это плохо / хорошо (для цвета стрелок: ▲▼ без оценки пока не нужен)
LOWER_IS_BETTER = {"weight_kg", "body_fat_pct", "visceral_fat"}


def bmi_category(bmi: float) -> str:
    if bmi < 18.5:
        return "ниже нормы"
    if bmi < 25:
        return "норма"
    if bmi < 30:
        return "избыточный вес"
    if bmi < 35:
        return "ожирение I степени"
    if bmi < 40:
        return "ожирение II степени"
    return "ожирение III степени"


def body_fat_category(pct: float, sex: str) -> str:
    """Ориентир для взрослых 20–39 лет по общепринятым шкалам (Gallagher и др., 2000)."""
    if sex == "female":
        limits = (21, 33, 39)
    else:
        limits = (8, 20, 25)
    low, high, obese = limits
    if pct < low:
        return "низкий"
    if pct < high:
        return "в пределах нормы"
    if pct < obese:
        return "повышенный"
    return "высокий"


def visceral_category(level: float) -> str:
    """Шкала большинства бытовых весов: 1–9 норма, 10–14 повышен, 15+ высокий."""
    if level <= 9:
        return "в пределах нормы"
    if level <= 14:
        return "повышенный"
    return "высокий"


def _fmt(field: str, value: float) -> str:
    if field in ("bmr_kcal", "metabolic_age", "visceral_fat"):
        return f"{value:.0f}"
    return f"{value:.1f}"


def deltas(latest: dict, previous: dict | None) -> list[str]:
    """Изменения по сравнению с прошлым замером, без оценок."""
    if not previous:
        return []
    out = []
    for field, label, unit in FIELDS:
        if field not in DELTA_FIELDS:
            continue
        a, b = latest.get(field), previous.get(field)
        if a is None or b is None:
            continue
        out.append(f"{label}: {_delta_str(a - b, unit)}")
    return out


def _delta_str(diff: float, unit: str) -> str:
    if abs(diff) < 0.05:
        return "без изменений"
    return f"{diff:+.1f}{(' ' + unit) if unit else ''}"


def _notes(latest: dict, sex: str, age: int | None) -> list[str]:
    notes: list[str] = []
    bmi = latest.get("bmi")
    if bmi is not None:
        notes.append(f"ИМТ {bmi:.1f}: {bmi_category(bmi)}.")
    fat = latest.get("body_fat_pct")
    if fat is not None:
        notes.append(f"Жир {fat:.1f} %: ориентир по шкалам для взрослых, "
                     f"{body_fat_category(fat, sex)}.")
    visceral = latest.get("visceral_fat")
    if visceral is not None:
        notes.append(f"Висцеральный жир {visceral:.0f}: по шкале большинства весов "
                     f"{visceral_category(visceral)} (шкала Picooc может отличаться).")
    meta = latest.get("metabolic_age")
    if meta is not None and age:
        notes.append("Метаболический возраст — условная оценка производителя: "
                     f"{meta:.0f} при паспортных {age}.")
    hr = latest.get("resting_hr")
    if hr is not None:
        notes.append("Пульс с весов измеряется грубо, ориентироваться на него не стоит.")
    score = latest.get("body_score")
    if score is not None:
        notes.append(f"«Оценка» {score:g} — внутренний балл приложения весов, а не медицинский "
                     "показатель. Он зависит от возраста, роста и состава и со временем "
                     "растёт, когда жира становится меньше, а мышц больше.")
    return notes


_CLOSING = ("Главное: одно число — это точка отсчёта, а не приговор. Весы ошибаются "
            "на 3–5 %, поэтому смотрим на динамику за 4 недели: жир вниз, мышцы не падают. "
            "Хорошие рычаги: белок в каждом приёме пищи, силовые тренировки, шаги и сон.")
_DOCTOR = ("При ИМТ от 35 разумно один раз сходить к врачу: давление, сахар, "
           "холестерин. Это обычная проверка, а не повод волноваться.")


def summary(latest: dict, previous: dict | None, sex: str, age: int | None) -> list[str]:
    """Человекочитаемый разбор последнего замера."""
    lines: list[str] = []
    for field, label, unit in FIELDS:
        value = latest.get(field)
        if value is None:
            continue
        lines.append(f"• {label}: {_fmt(field, value)}{(' ' + unit) if unit else ''}")

    notes = _notes(latest, sex, age)
    bmi = latest.get("bmi")
    delta = deltas(latest, previous)
    out = ["🧍 Состав тела по последнему замеру:"] + lines
    if delta:
        out += ["", "Изменения с прошлого замера:"] + [f"• {d}" for d in delta]
    out += ["", "Как это читать:"] + [f"• {n}" for n in notes]
    out += ["", _CLOSING]
    if bmi is not None and bmi >= 35:
        out.append(_DOCTOR)
    return out


# ориентиры для таблицы: (низ, верх) или None, если у показателя нет общепринятой нормы
def _ranges(sex: str) -> dict[str, tuple[float, float] | None]:
    female = sex == "female"
    return {
        "bmi": (18.5, 24.9),
        "body_fat_pct": (21, 33) if female else (8, 20),
        "visceral_fat": (1, 9),
        "body_water_pct": (45, 60) if female else (50, 65),
    }


def _range_text(field: str, sex: str) -> str:
    rng = _ranges(sex).get(field)
    return f"{rng[0]:g}–{rng[1]:g}" if rng else "—"


def _status(field: str, value: float, sex: str) -> str:
    rng = _ranges(sex).get(field)
    if not rng:
        return "—"
    low, high = rng
    if low <= value <= high:
        return "✅"
    span = (high - low) or 1
    return "🟡" if (value < low and low - value <= 0.15 * span) or \
        (value > high and value - high <= 0.15 * span) else "⚠️"


def _masses(row: dict) -> tuple[float, float] | None:
    """(жировая масса, безжировая масса) в кг из веса и процента жира."""
    weight, fat = row.get("weight_kg"), row.get("body_fat_pct")
    if weight is None or fat is None:
        return None
    fat_kg = weight * fat / 100
    return fat_kg, weight - fat_kg


def advice(latest: dict, previous: dict | None, sex: str, age: int | None,
           fasting: bool = True) -> list[str]:
    """Конкретные выводы по замеру и динамике, до четырёх, самые важные первыми."""
    out: list[tuple[int, str]] = []
    now, before = _masses(latest), _masses(previous) if previous else None
    if now and before and latest.get("weight_kg") is not None:
        d_weight = latest["weight_kg"] - previous["weight_kg"]
        d_fat, d_lean = now[0] - before[0], now[1] - before[1]
        if d_weight <= -0.3:
            lost = -d_weight
            lean_share = max(0.0, -d_lean) / lost
            if lean_share >= 0.4:
                out.append((1, f"Из {lost:.1f} кг потери около {max(0.0, -d_lean):.1f} кг пришлось на "
                               "мышцы и воду: это много. Держи белок около 1,6 г на кг веса, "
                               "добавь 2 силовые тренировки в неделю и не урезай калории сильнее."))
            else:
                out.append((1, f"Вес вниз на {lost:.1f} кг, и это в основном жир "
                               f"(≈{max(0.0, -d_fat):.1f} кг). Состав меняется в нужную сторону: "
                               "ничего не ломай, продолжай."))
        elif d_weight >= 0.3:
            if d_fat >= 0.3:
                out.append((1, f"Прибавка {d_weight:.1f} кг в основном жировая (≈{d_fat:.1f} кг). "
                               "Посмотри на последние две недели: порции вечером, напитки с "
                               "сахаром, выходные."))
            else:
                out.append((2, f"Вес вырос на {d_weight:.1f} кг, но жир почти не изменился: чаще "
                               "это вода, соль или еда в кишечнике. Оценивай по трём-четырём "
                               "замерам, а не по одному."))
        elif d_fat <= -0.3 and d_lean >= -0.2:
            out.append((1, f"Вес стоит, а жира стало меньше на {-d_fat:.1f} кг при сохранённых "
                           "мышцах: это ровно то, что нужно. Не паникуй из-за стрелки весов."))
    elif previous is None:
        out.append((3, "Это точка отсчёта. Следующий замер сделай через 1–2 недели в тех же "
                       "условиях: утром, после туалета, до еды и воды."))

    visceral = latest.get("visceral_fat")
    if visceral is not None and visceral >= 10:
        out.append((2, f"Висцеральный жир {visceral:.0f}: выше нормы. Он уходит одним из первых, "
                       "если ходить 8–10 тысяч шагов, убрать сладкие напитки и алкоголь и "
                       "спать 7 часов и больше."))
    fat = latest.get("body_fat_pct")
    if fat is not None and body_fat_category(fat, sex) in ("повышенный", "высокий") and not any(
            "жир" in text for _, text in out):
        out.append((3, f"Жира {fat:.1f} %: выше ориентира. Рабочая схема: умеренный дефицит, "
                       "белок в каждом приёме пищи и силовые 2 раза в неделю."))
    water = latest.get("body_water_pct")
    rng = _ranges(sex)["body_water_pct"]
    if water is not None and rng and water < rng[0]:
        out.append((4, f"Воды в теле {water:.1f} %: ниже ориентира. Пей равномерно в течение "
                       "дня и сравнивай замеры только сделанные в одно время."))
    meta = latest.get("metabolic_age")
    if meta is not None and age and meta - age >= 5:
        out.append((5, f"Метаболический возраст {meta:.0f} при паспортных {age}: это условная "
                       "оценка весов. Снижает его то же, что и жир: мышцы, шаги, сон."))
    if not fasting:
        out.append((4, "Замер сделан не натощак, поэтому вода и еда искажают цифры. Для "
                       "сравнения бери только утренние замеры."))
    out.sort(key=lambda item: item[0])
    return [text for _, text in out[:4]]


def summary_doc(latest: dict, previous: dict | None, sex: str, age: int | None,
                when: str, note: str = "", chart: bytes | None = None) -> Doc:
    """Таблица «показатель, значение, ориентир, статус, изменение» и конкретные выводы."""
    rows = []
    for field, label, unit in FIELDS:
        value = latest.get(field)
        if value is None:
            continue
        row = [label, f"{_fmt(field, value)}{(' ' + unit) if unit else ''}",
               _range_text(field, sex), _status(field, value, sex)]
        if previous is not None:
            before = previous.get(field)
            row.append(_delta_str(value - before, unit)
                       if field in DELTA_FIELDS and before is not None else "—")
        rows.append(row)
    headers = ["Показатель", "Сейчас", "Ориентир", ""] + (["Δ"] if previous is not None else [])
    now, before = _masses(latest), _masses(previous) if previous else None
    if now:
        mass = ["Жировая масса", f"{now[0]:.1f} кг", "—", "—"]
        lean = ["Без жира", f"{now[1]:.1f} кг", "—", "—"]
        if previous is not None:
            mass.append(_delta_str(now[0] - before[0], "кг") if before else "—")
            lean.append(_delta_str(now[1] - before[1], "кг") if before else "—")
        rows[1:1] = [mass, lean] if len(rows) > 1 else []
        if len(rows) <= 1:
            rows += [mass, lean]
    doc = Doc().text(f"🧍 <b>Состав тела</b> · замер {when}")
    doc.image(chart)
    doc.table(headers, rows, align="lrrcr" if previous is not None else "lrrc")
    if note:
        doc.text(note)
    tips = advice(latest, previous, sex, age, fasting="Замер сделан не натощак" not in note)
    if tips:
        doc.heading("💡 Что это значит и что делать")
        doc.bullets(tips)
    bmi = latest.get("bmi")
    if bmi is not None and bmi >= 35:
        doc.quote(_DOCTOR)
    doc.text("<i>Весы дают ±3–5 %: смотри на динамику за 3–4 замера, а не на одно число.</i>")
    return doc
