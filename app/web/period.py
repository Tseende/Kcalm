"""Анализ за неделю и месяц: сводка по всем данным сразу (еда, вес, шаги, сон, вода).

Только правила по реальным цифрам, без LLM. Сравнивает с прошлым таким же периодом, считает
прогноз по весу и ищет связи между показателями (например, сон и аппетит)."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app.domain import analytics
from app.services.insights import SLEEP_GOAL_MIN, STEPS_GOAL, days_word
from app.storage import repo
from app.web import queries as q

PERIODS = {"week": 7, "month": 30}
KCAL_KG = 7700            # ккал на 1 кг массы тела: грубая оценка для сверки с весом
WEEKDAYS = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]


def _avg(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _n(v: float) -> str:
    return f"{round(v):,}".replace(",", " ")


def _status(ratio: float, good: float, ok: float) -> str:
    return "good" if ratio >= good else "ok" if ratio >= ok else "bad"


def _delta(cur, prev, unit: str, better: str, fmt=_n, min_rel: float = 0.03) -> dict | None:
    """Изменение к прошлому периоду: better = up|down|none (что считать улучшением)."""
    if cur is None or prev is None or prev == 0:
        return None
    diff = cur - prev
    if abs(diff) / abs(prev) < min_rel:
        return {"text": "как раньше", "dir": "flat"}
    sign = "+" if diff > 0 else "−"
    direction = "flat" if better == "none" else ("good" if (diff > 0) == (better == "up") else "bad")
    return {"text": f"{sign}{fmt(abs(diff))}{unit} к прошлому", "dir": direction}


def _weight_block(days: list[dict], user, n: int, care: bool, tdee: int | None, avg_kcal, logged: int) -> tuple[dict | None, list[dict]]:
    weights = [(d["date"], d["weight"]) for d in days if d["weight"] is not None]
    trends = [(d["date"], d["weight_trend"]) for d in days if d["weight_trend"] is not None]
    if care or len(weights) < 2 or len(trends) < 2:
        return ({"weighins": len(weights)} if weights and not care else None), []
    (d0, t0), (d1, t1) = trends[0], trends[-1]
    span = max(1, (date.fromisoformat(d1) - date.fromisoformat(d0)).days)
    change = round(t1 - t0, 1)
    per_week = round((t1 - t0) / span * 7, 2)
    goal = float(user["goal_weight_kg"]) if user["goal_weight_kg"] else None
    block = {"start": t0, "now": t1, "change": change, "per_week": per_week, "weighins": len(weights),
             "goal": goal, "remaining": None, "eta_weeks": None,
             "points": [{"date": d, "v": v} for d, v in trends]}
    tips: list[dict] = []
    if goal is not None:
        rem = round(t1 - goal, 1)
        block["remaining"] = rem
        toward = (rem > 0 and per_week < -0.05) or (rem < 0 and per_week > 0.05)
        if toward and abs(rem) > 0.1:
            block["eta_weeks"] = min(104, max(1, round(abs(rem) / abs(per_week))))
    # безопасный темп: быстрее ~1 % массы тела в неделю — это уже много
    if per_week < 0 and abs(per_week) > max(0.8, t1 * 0.01) and span >= 6:
        tips.append({"icon": "⚖️", "prio": 1, "title": "Вес уходит быстро",
                     "text": f"Минус {abs(per_week):.1f} кг в неделю — быстрее комфортного темпа (до 0,5–1 кг). "
                             "Такой темп чаще забирает мышцы и воду. Добавь к обедам и ужинам по порции "
                             "гарнира, чтобы дефицит стал мягче."})
    # сверка с записанной едой
    if tdee and avg_kcal and logged >= max(4, int(n * 0.6)) and span >= 6:
        expected = round((avg_kcal - tdee) * span / KCAL_KG, 1)
        gap = round(change - expected, 1)
        block["expected_change"] = expected
        if gap >= 0.6 and change > -0.3:
            tips.append({"icon": "🧮", "prio": 2, "title": "Вес не идёт вниз",
                         "text": f"По записанной еде ожидалось около {expected:+.1f} кг, а факт {change:+.1f}. "
                                 "Чаще всего не записаны масло, соусы, напитки и перекусы «на ходу». "
                                 "Дня три подряд записывай всё, даже мелочи: так станет видно, куда уходят калории."})
        elif gap <= -0.8:
            tips.append({"icon": "🧮", "prio": 3, "title": "Вес падает быстрее плана",
                         "text": f"По еде ожидалось около {expected:+.1f} кг, а факт {change:+.1f}. "
                                 "Возможно, ты ешь больше, чем записываешь, или расход выше оценки: это нормально, "
                                 "просто не урезай калории дальше."})
    return block, tips


def build(db: sqlite3.Connection, user, today: date, period: str) -> dict:
    n = PERIODS[period]
    start = today - timedelta(days=n - 1)
    days = q.series(db, user, start, n)
    prev = q.series(db, user, start - timedelta(days=n), n)
    care = bool(user["care_mode"])
    tgt = repo.current_target(db, user["id"])
    kcal_t = int(tgt["kcal_target"]) if tgt else 2100
    prot_t = int(tgt["protein_g"] or 120) if tgt else 120
    tdee = int(tgt["tdee_est"]) if tgt and tgt["tdee_est"] else None
    water_t = q.watersvc.goal_for(db, user)

    logged = [d for d in days if d["kcal"] is not None]
    plogged = [d for d in prev if d["kcal"] is not None]
    kcal = _avg([d["kcal"] for d in logged])
    pkcal = _avg([d["kcal"] for d in plogged])
    protein = _avg([d["protein"] for d in logged])
    pprotein = _avg([d["protein"] for d in plogged])
    water = _avg([d["water_ml"] for d in days])
    pwater = _avg([d["water_ml"] for d in prev])
    steps = _avg([d["steps"] for d in days])
    psteps = _avg([d["steps"] for d in prev])
    sleep = _avg([d["sleep_min"] for d in days])
    psleep = _avg([d["sleep_min"] for d in prev])

    metrics: list[dict] = []
    if not care and kcal is not None:
        ratio = kcal / kcal_t
        on = sum(1 for d in logged if 0.8 <= d["kcal"] / kcal_t <= 1.2)
        dk = None
        if pkcal:
            closer = abs(kcal - kcal_t) < abs(pkcal - kcal_t)
            diff = kcal - pkcal
            dk = ({"text": "как раньше", "dir": "flat"} if abs(diff) < 50 else
                  {"text": f"{'+' if diff > 0 else '−'}{_n(abs(diff))} к прошлому", "dir": "good" if closer else "bad"})
        metrics.append({"key": "kcal", "label": "Калории", "value": f"{_n(kcal)} ккал", "goal": f"норма {_n(kcal_t)}",
                        "status": "good" if 0.85 <= ratio <= 1.1 else "ok" if 0.7 <= ratio <= 1.2 else "bad",
                        "note": f"в норме {on} из {len(logged)} дн.", "delta": dk})
    if protein is not None:
        hit = sum(1 for d in logged if d["protein"] >= 0.9 * prot_t)
        metrics.append({"key": "protein", "label": "Белок", "value": f"{_n(protein)} г", "goal": f"цель {prot_t} г",
                        "status": _status(protein / prot_t, 0.9, 0.7), "note": f"цель достигнута {hit} из {len(logged)} дн.",
                        "delta": _delta(protein, pprotein, " г", "up")})
    if water is not None:
        hit = sum(1 for d in days if (d["water_ml"] or 0) >= 0.9 * water_t)
        metrics.append({"key": "water", "label": "Вода", "value": f"{_n(water)} мл", "goal": f"цель {_n(water_t)}",
                        "status": _status(water / water_t, 0.9, 0.5), "note": f"цель достигнута {hit} из {n} дн.",
                        "delta": _delta(water, pwater, " мл", "up")})
    if steps is not None:
        hit = sum(1 for d in days if (d["steps"] or 0) >= STEPS_GOAL)
        metrics.append({"key": "steps", "label": "Шаги", "value": _n(steps), "goal": f"цель {_n(STEPS_GOAL)}",
                        "status": _status(steps / STEPS_GOAL, 1.0, 0.6), "note": f"{hit} дн. с целью",
                        "delta": _delta(steps, psteps, "", "up")})
    if sleep is not None:
        short = sum(1 for d in days if d["sleep_min"] and d["sleep_min"] < 360)
        metrics.append({"key": "sleep", "label": "Сон", "value": analytics.format_minutes(int(sleep)).replace(" мин", " м"),
                        "goal": "цель 7+ ч", "status": _status(sleep / SLEEP_GOAL_MIN, 0.95, 0.85),
                        "note": f"меньше 6 ч: {short} {days_word(short)}" if short else "недосыпа нет",
                        "delta": _delta(sleep, psleep, " мин", "up")})
    metrics.append({"key": "logged", "label": "Дней с едой", "value": f"{len(logged)} из {n}", "goal": "",
                    "status": _status(len(logged) / n, 0.85, 0.55), "note": "", "delta": None})

    weight, wtips = _weight_block(days, user, n, care, tdee, kcal, len(logged))

    good: list[str] = []
    tips: list[dict] = list(wtips)
    patterns: list[dict] = []

    def tip(prio, icon, title, text):
        tips.append({"icon": icon, "prio": prio, "title": title, "text": text})

    if len(logged) < max(3, n // 2):
        tip(1, "📝", "Мало записей", f"Еда записана за {len(logged)} {days_word(len(logged))} из {n}, поэтому выводы приблизительные. "
                                       "Хватит короткой записи, даже «гречка с курицей».")
    if protein is not None and len(logged) >= 3:
        if protein < 0.75 * prot_t:
            tip(1, "🥩", "Белок", f"В среднем {_n(protein)} из {prot_t} г, не хватает около {int(prot_t - protein)} г в день. "
                                 "Добавь белок в каждый приём: яйца или творог утром, курица/рыба в обед, кефир или йогурт вечером.")
        elif protein >= 0.9 * prot_t:
            good.append("белок держится у цели")
    if water is not None:
        if water < 0.6 * water_t:
            tip(2, "💧", "Вода", f"В среднем {_n(water)} мл при цели {_n(water_t)}. Стакан после пробуждения, перед каждой едой и в 16:00 закроют разрыв.")
        elif water >= water_t * 0.95:
            good.append("воды хватает")
    if steps is not None:
        if steps >= 10000:
            good.append(f"активный период ({_n(steps)} шагов в среднем)")
        elif steps < 5000:
            tip(3, "🚶", "Движение", f"Шагов в среднем {_n(steps)}. Прибавляй по 1000 шагов в неделю (одна короткая прогулка): это надёжнее разового рывка.")
        if psteps and steps - psteps >= 1500:
            good.append(f"шагов стало больше на {_n(steps - psteps)} в день")
    if sleep is not None and sleep < SLEEP_GOAL_MIN - 30:
        tip(2, "😴", "Сон", f"Сон в среднем {analytics.format_minutes(int(sleep))}. Недосып повышает аппетит и тягу к сладкому: сдвинь отбой на 30 минут раньше.")
    elif sleep is not None and sleep >= SLEEP_GOAL_MIN:
        good.append("сон в норме")

    if not care and kcal is not None and len(logged) >= 3:
        ratio = kcal / kcal_t
        if ratio > 1.1:
            tip(2, "🍽", "Калории", f"В среднем на {int(kcal - kcal_t)} ккал выше нормы. Посмотри, на каком приёме набегает больше всего: чаще это перекусы и вечер.")
        elif ratio < 0.7:
            tip(1, "🍽", "Калории", "Еды заметно меньше нормы: жёсткий недобор часто ведёт к срывам и потере мышц. Подними приёмы пищи ближе к норме.")
        elif 0.85 <= ratio <= 1.1:
            good.append("калории в целевом коридоре")
        late_days = sum(1 for d in logged if d["late_kcal"])
        if late_days >= max(3, n // 4):
            tip(3, "🌙", "Поздняя еда", f"Поздняя еда была {late_days} {days_word(late_days)} из {len(logged)}. Сдвинь ужин на час раньше и добавь в него белок: вечером меньше тянет.")
        best = min(logged, key=lambda d: abs(d["kcal"] - kcal_t))
        good.append(f"ближе всего к норме был день {best['date'][8:]}.{best['date'][5:7]} ({_n(best['kcal'])} ккал): повтори его меню")

        # --- закономерности: связи между показателями ---
        short = [d["kcal"] for d in logged if d["sleep_min"] and d["sleep_min"] < 390]
        full = [d["kcal"] for d in logged if d["sleep_min"] and d["sleep_min"] >= 450]
        if len(short) >= 3 and len(full) >= 3:
            diff = int(sum(short) / len(short) - sum(full) / len(full))
            if diff >= 150:
                patterns.append({"icon": "😴", "title": "Сон и аппетит",
                                 "text": f"После коротких ночей (меньше 6,5 ч) ты ешь примерно на {diff} ккал больше, чем после полноценных. "
                                         "Недосып усиливает голод: ранний отбой сработает лучше силы воли."})
        work = [d["kcal"] for d in logged if date.fromisoformat(d["date"]).weekday() < 5]
        free = [d["kcal"] for d in logged if date.fromisoformat(d["date"]).weekday() >= 5]
        if len(work) >= 3 and len(free) >= 2:
            diff = int(sum(free) / len(free) - sum(work) / len(work))
            if diff >= 300:
                patterns.append({"icon": "📆", "title": "Выходные",
                                 "text": f"В выходные ты ешь примерно на {diff} ккал больше, чем в будни. Заранее реши, что будет на завтрак и ужин в субботу: план помогает лучше силы воли."})
        by_wd: dict[int, list[int]] = {}
        for d in logged:
            by_wd.setdefault(date.fromisoformat(d["date"]).weekday(), []).append(d["kcal"])
        peak = [(wd, sum(v) / len(v)) for wd, v in by_wd.items() if len(v) >= 2]
        if len(peak) >= 4:
            wd, val = max(peak, key=lambda x: x[1])
            if val >= kcal_t * 1.15 and val >= kcal * 1.12:
                patterns.append({"icon": "📈", "title": "Тяжёлый день недели",
                                 "text": f"По {WEEKDAYS[wd]} калорий больше всего: в среднем {_n(val)} при норме {_n(kcal_t)}. Подготовь на этот день простой план приёмов пищи."})
        if weight and weight.get("per_week") is not None and steps is not None and psteps and steps - psteps >= 1500 and weight["per_week"] < 0:
            patterns.append({"icon": "🚶", "title": "Шаги работают",
                             "text": f"Шагов стало больше на {_n(steps - psteps)} в день, и тренд веса идёт вниз ({weight['change']:+.1f} кг). Продолжай в том же духе."})
    if weight and weight.get("change") is not None and not care:
        goalw = user["goal_weight_kg"]
        if goalw and weight["per_week"] < -0.05 and weight["change"] <= -0.3:
            good.append(f"тренд веса {weight['change']:+.1f} кг за период")
        elif not goalw and weight["change"] <= -0.3:
            good.append(f"тренд веса {weight['change']:+.1f} кг за период")

    tips.sort(key=lambda t: t["prio"])
    # заголовок и главный фокус
    if not logged:
        headline, focus = "Данных пока мало", None
    else:
        urgent = [t for t in tips if t["prio"] <= 2]
        headline = ("Есть что поправить" if urgent else "Хороший период" if len(good) >= 4 else "В целом неплохо")
        focus = ({"title": tips[0]["title"], "text": tips[0]["text"], "icon": tips[0]["icon"]} if tips
                 else {"title": "Держи темп", "text": "Критичных проблем не видно. Следи, чтобы белок и сон не просели.", "icon": "✅"})
    for t in tips:
        t.pop("prio", None)
    return {
        "period": period, "from": start.isoformat(), "to": today.isoformat(), "days": n,
        "headline": headline, "focus": focus, "metrics": metrics, "weight": weight,
        "good": [g[:1].upper() + g[1:] for g in good], "patterns": patterns, "tips": tips,
        "logged_days": len(logged), "care": care,
    }
