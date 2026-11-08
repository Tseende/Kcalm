"""Режим питания, шаги и личные закономерности: правила по реальным цифрам, без LLM и сети.

Работает с «днями» в виде словарей: `day_facts` (советы дня и недели в чате) и `days_from_db`
(месячный и недельный разбор в приложении) отдают одни и те же ключи, поэтому правила общие.
Модуль возвращает простые словари; в `Tip` их оборачивает `insights`.

Тон мягкий: «еда сдвигается по времени», а не «беспорядочно». Совет по режиму молчит, пока записано
меньше четырёх дней: по трём точкам закономерность не видна. В осторожном режиме (care) калории
и вечерняя доля калорий не упоминаются."""
from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import date, timedelta

from app.storage import repo

EVENING_FROM = "18:00"
LATE_BREAKFAST = 11 * 60          # первый приём позже 11:00
MIN_LOGGED = 4                    # меньше записанных дней: режим не оцениваем
STEPS_GOAL = 8000
STEPS_PER_MIN = 100               # неспешная ходьба
ACTIVE_STEPS, CALM_STEPS = 8000, 6000


def days_word(n: int) -> str:
    """1 день, 2 дня, 5 дней."""
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def _min(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _hhmm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def _num(value) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _comma(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def walk_minutes(steps_left: int) -> int:
    """Сколько минут неспешной ходьбы нужно, чтобы добрать шаги (с шагом 5 минут, 10–60)."""
    return min(60, max(10, math.ceil(steps_left / STEPS_PER_MIN / 5) * 5))


# --------------------------------------------------------------------------- данные дня

def eaten(meals) -> list:
    """Чай, американо и кола без сахара записаны приёмом (source=manual), но едой не считаются."""
    return [m for m in meals if m["source"] != "manual" or m["kcal_total"] >= 50]


def meal_gap(meals) -> tuple[float, str, str] | None:
    """Самая длинная пауза между приёмами пищи (не меньше 50 ккал) в окне 07:00–22:00."""
    times = sorted(m["local_time"] for m in meals if m["kcal_total"] >= 50
                   and "07:00" <= m["local_time"] <= "22:00")
    if len(times) < 2:
        return None
    best = max(zip(times, times[1:]), key=lambda ab: _min(ab[1]) - _min(ab[0]))
    return round((_min(best[1]) - _min(best[0])) / 60, 1), best[0], best[1]


def evening_kcal(meals) -> int:
    return int(sum(m["kcal_total"] for m in eaten(meals) if m["local_time"] >= EVENING_FROM))


def days_from_db(db: sqlite3.Connection, user, start: date, n: int) -> list[dict]:
    """Дни `start … start+n-1` с полями режима питания (один запрос по приёмам)."""
    f, t = start.isoformat(), (start + timedelta(days=n - 1)).isoformat()
    by_day: dict[str, list] = {}
    for m in repo.range_meals(db, user["id"], f, t):
        by_day.setdefault(m["local_date"], []).append(m)
    out = []
    for i in range(n):
        key = (start + timedelta(days=i)).isoformat()
        meals = by_day.get(key, [])
        el = eaten(meals)
        gap = meal_gap(meals)
        out.append({"date": key, "meals": len(el), "kcal": int(sum(m["kcal_total"] for m in meals)),
                    "first_meal": el[0]["local_time"] if el else None,
                    "evening_kcal": evening_kcal(meals),
                    "longest_gap_h": gap[0] if gap else None})
    return out


# --------------------------------------------------------------------------- режим питания

def meal_summary(days: list[dict]) -> dict | None:
    """Сводка режима по дням с едой; None, если записано меньше MIN_LOGGED дней."""
    logged = [d for d in days if d.get("meals")]
    if len(logged) < MIN_LOGGED:
        return None
    counts = [d["meals"] for d in logged]
    firsts = [_min(d["first_meal"]) for d in logged if d.get("first_meal")]
    heavy = [d for d in logged if (d.get("kcal") or 0) >= 1000 and d.get("evening_kcal") is not None]
    shares = [d["evening_kcal"] / d["kcal"] for d in heavy]
    return {
        "logged": len(logged),
        "avg_meals": sum(counts) / len(counts),
        "few_days": sum(1 for c in counts if c <= 2),
        "first_min": min(firsts) if firsts else None,
        "first_max": max(firsts) if firsts else None,
        "first_median": statistics.median(firsts) if firsts else None,
        "spread": (max(firsts) - min(firsts)) if firsts else 0,
        "evening_share": sum(shares) / len(shares) if len(shares) >= MIN_LOGGED else None,
        "gap_days": sum(1 for d in logged if (d.get("longest_gap_h") or 0) >= 5.5),
    }


def meal_status(s: dict) -> str:
    """good | ok | bad для плитки «Режим»."""
    if s["avg_meals"] < 2 or s["spread"] >= 240:
        return "bad"
    if s["avg_meals"] >= 3 and s["spread"] <= 120 and s["few_days"] <= max(1, s["logged"] // 5):
        return "good"
    return "ok"


def _round15(minutes: float) -> str:
    return _hhmm(round(minutes / 15) * 15)


def meal_tips(days: list[dict], care: bool) -> tuple[list[str], list[dict]]:
    """(что хорошо, советы) по режиму питания за неделю или месяц."""
    s = meal_summary(days)
    if s is None:
        return [], []
    good: list[str] = []
    tips: list[dict] = []
    logged = s["logged"]
    share_days = max(3, math.ceil(0.4 * logged))

    if s["avg_meals"] < 2.5 or s["few_days"] >= share_days:
        tips.append({"icon": "🕐", "prio": 3,
                     "text": f"В среднем {_comma(s['avg_meals'])} приёма пищи в день, в {s['few_days']} "
                             f"{days_word(s['few_days'])} из {logged} было всего 1–2. Крупные порции и "
                             "длинные паузы хуже держат сытость и чаще кончаются вечерней тягой. Если "
                             "что-то не записываешь, допиши: тогда вывод станет точнее.",
                     "action": "Завтра: три приёма пищи, в каждом белок"})
    if s["first_median"] is not None and s["spread"] >= 180 and logged >= 5:
        tips.append({"icon": "🕐", "prio": 4,
                     "text": f"Первый приём пищи то в {_hhmm(s['first_min'])}, то в {_hhmm(s['first_max'])}. "
                             "Когда время плавает, аппетит скачет: привяжи первую еду к одному часу, "
                             f"например к {_round15(s['first_median'])}.",
                     "action": f"Завтра: первый приём пищи около {_round15(s['first_median'])}"})
    if not care and s["evening_share"] is not None and s["evening_share"] >= 0.5:
        tips.append({"icon": "🕐", "prio": 3,
                     "text": f"В среднем {round(s['evening_share'] * 100)}% калорий приходится на вечер "
                             f"(после {EVENING_FROM}). К вечеру голод копится: сдвинь часть еды на день, "
                             "белок в обед и в полдник заметно снижает вечернюю тягу.",
                     "action": "Завтра: белок в обед и перекус с белком в полдник"})
    if s["gap_days"] >= share_days:
        tips.append({"icon": "⏱", "prio": 4,
                     "text": f"Паузы между приёмами больше 5,5 часа были в {s['gap_days']} "
                             f"{days_word(s['gap_days'])} из {logged}. Поставь напоминание на перекус "
                             "с белком посередине длинной паузы.",
                     "action": "Завтра: перекус с белком в середине длинной паузы"})
    if (s["avg_meals"] >= 3 and s["spread"] <= 120 and s["few_days"] <= max(1, logged // 5)
            and s["first_median"] is not None):
        good.append(f"режим питания стабильный (первый приём около {_round15(s['first_median'])})")
    return good, tips


# --------------------------------------------------------------------------- шаги

def steps_tips(avg: float | None, prev_avg: float | None) -> tuple[list[str], list[dict]]:
    """Ступени по шагам за неделю или месяц: 5 000–8 000 тоже разбираем, а не молчим."""
    if avg is None:
        return [], []
    tips: list[dict] = []
    if 5000 <= avg < STEPS_GOAL:
        left = int(STEPS_GOAL - avg)
        mins = walk_minutes(left)
        tips.append({"icon": "🚶", "prio": 4,
                     "text": f"Шагов в среднем {_num(avg)}, до цели {_num(left)} в день: это около "
                             f"{mins} минут неспешной ходьбы. Одна прогулка после ужина закрывает "
                             "почти весь разрыв.",
                     "action": f"Завтра: {mins} минут ходьбы после ужина"})
    if prev_avg and avg - prev_avg <= -1500:
        tips.append({"icon": "🚶", "prio": 4,
                     "text": f"Шагов стало меньше на {_num(prev_avg - avg)} в день по сравнению с прошлым "
                             "периодом. Вернуть привычку проще всего коротким маршрутом в одно и то же "
                             "время.",
                     "action": "Завтра: 15 минут ходьбы в привычное время"})
    return [], tips


# --------------------------------------------------------------------------- закономерности

def patterns(rdays: list[dict], sdays: list[dict], care: bool) -> list[dict]:
    """Личные связи между показателями: нужны хотя бы 3 дня в каждой группе.

    rdays: дни с полями режима (days_from_db); sdays: дни `queries.series` (шаги, сон)."""
    out: list[dict] = []

    # ранний или поздний завтрак → вечерняя еда
    if not care:
        late = [d["evening_kcal"] for d in rdays
                if d.get("meals") and d.get("first_meal") and _min(d["first_meal"]) >= LATE_BREAKFAST]
        early = [d["evening_kcal"] for d in rdays
                 if d.get("meals") and d.get("first_meal") and _min(d["first_meal"]) < LATE_BREAKFAST]
        if len(late) >= 3 and len(early) >= 3:
            diff = int(sum(late) / len(late) - sum(early) / len(early))
            if diff >= 200:
                out.append({"icon": "🕐", "title": "Завтрак и вечер",
                            "text": f"В дни, когда первая еда позже 11:00, вечером (после {EVENING_FROM}) "
                                    f"ты съедаешь на {diff} ккал больше. Ранний завтрак с белком заранее "
                                    "снимает вечерний голод."})

    # шаги: будни и выходные
    work = [d["steps"] for d in sdays if d.get("steps") is not None
            and date.fromisoformat(d["date"]).weekday() < 5]
    free = [d["steps"] for d in sdays if d.get("steps") is not None
            and date.fromisoformat(d["date"]).weekday() >= 5]
    if len(work) >= 3 and len(free) >= 2:
        diff = int(sum(free) / len(free) - sum(work) / len(work))
        if diff >= 2000:
            out.append({"icon": "🚶", "title": "Шаги в будни",
                        "text": f"В выходные ты проходишь на {_num(diff)} шагов больше, чем в будни. "
                                "Добавь в будни короткую прогулку после обеда или ужина: 15 минут это "
                                "около 1 500 шагов."})
        elif diff <= -2000:
            out.append({"icon": "🚶", "title": "Шаги в выходные",
                        "text": f"В выходные шагов на {_num(-diff)} меньше, чем в будни. Запланируй "
                                "прогулку заранее: без плана выходной проходит сидя."})

    # движение → сон следующей ночи
    sleep_on = {d["date"]: d["sleep_min"] for d in sdays if d.get("sleep_min")}
    active, calm = [], []
    for d in sdays:
        if d.get("steps") is None:
            continue
        nxt = sleep_on.get((date.fromisoformat(d["date"]) + timedelta(days=1)).isoformat())
        if nxt is None:
            continue
        if d["steps"] >= ACTIVE_STEPS:
            active.append(nxt)
        elif d["steps"] < CALM_STEPS:
            calm.append(nxt)
    if len(active) >= 3 and len(calm) >= 3:
        diff = int(sum(active) / len(active) - sum(calm) / len(calm))
        if diff >= 25:
            out.append({"icon": "😴", "title": "Движение и сон",
                        "text": f"После дней с {_num(ACTIVE_STEPS)}+ шагов ты спишь на {diff} минут дольше, "
                                f"чем после спокойных (меньше {_num(CALM_STEPS)}). Вечерняя прогулка "
                                "один из простых способов выспаться."})
    return out
