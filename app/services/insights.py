"""Факты дня и недели и советы по ним: чистый Python, без LLM.

Советы считаются правилами по реальным цифрам, поэтому всегда привязаны к данным и бесплатны.
Тон мягкий: без стыда, без «отработки» съеденного, без советов недоедать. В осторожном режиме
(care_mode) калории и дефицит в советах не упоминаются.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain import analytics
from app.services import water as watersvc
from app.storage import repo

STEPS_GOAL = 8000
SLEEP_GOAL_MIN = 7 * 60
SLEEP_LOW_MIN = 6 * 60

# белок в типичных порциях (г белка), чтобы совет был «что именно съесть»
PROTEIN_IDEAS = (
    (15, "2 яйца или 100 г творога"),
    (30, "150 г творога или 100–150 г курицы/рыбы"),
    (999, "порцию курицы/рыбы (около 150 г) и творог или йогурт на перекус"),
)


TIP_TITLES = {"🥩": "Белок", "💧": "Вода", "🚶": "Движение", "😴": "Сон", "🍽": "Калории",
              "🌙": "Поздняя еда", "📆": "Выходные", "📝": "Дневник", "🍚": "Энергия", "🧭": "Тяга", "🧮": "Расход",
              "⏱": "Паузы"}


@dataclass
class Tip:
    icon: str
    text: str
    prio: int = 5             # меньше — важнее

    @property
    def title(self) -> str:
        return TIP_TITLES.get(self.icon, "Совет")

    @property
    def html(self) -> str:
        """Пункт списка: жирный заголовок и текст рядом."""
        from app.textutil import esc
        return f"<b>{self.icon} {self.title}.</b> {esc(self.text)}"


def days_word(n: int) -> str:
    """1 день, 2 дня, 5 дней."""
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def _num(value) -> str:
    return f"{value:,.0f}".replace(",", " ")


# --------------------------------------------------------------------------- день

def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _cravings_of_day(db: sqlite3.Connection, uid: int, day: str) -> list[dict]:
    """Эпизоды тяги «дня»: ночные после полуночи (до 04:00) относятся к прожитому дню."""
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    rows = db.execute(
        """SELECT * FROM craving_events WHERE user_id=? AND mode='craving'
           AND (local_date=? OR (local_date=? AND local_time < '04:00')) ORDER BY id""",
        (uid, day, nxt)).fetchall()
    return [{"time": r["local_time"], "category": r["category"], "outcome": r["outcome"],
             "kcal_so_far": r["kcal_so_far"], "kcal_target": r["kcal_target"]} for r in rows]


def _meal_gap(meals) -> tuple[float, str, str] | None:
    """Самая длинная пауза между приёмами пищи (не меньше 50 ккал) в окне 07:00–22:00."""
    times = sorted(m["local_time"] for m in meals if m["kcal_total"] >= 50
                   and "07:00" <= m["local_time"] <= "22:00")
    if len(times) < 2:
        return None
    best = max(zip(times, times[1:]), key=lambda ab: _minutes(ab[1]) - _minutes(ab[0]))
    hours = (_minutes(best[1]) - _minutes(best[0])) / 60
    return round(hours, 1), best[0], best[1]


def day_facts(db: sqlite3.Connection, user, day: str) -> dict:
    uid = user["id"]
    totals = repo.day_totals(db, uid, day)
    meals = repo.day_meals(db, uid, day)
    tgt = repo.current_target(db, uid)
    act = db.execute("SELECT * FROM activity_daily WHERE user_id=? AND local_date=?",
                     (uid, day)).fetchone()
    sleep = db.execute("SELECT * FROM sleep_sessions WHERE user_id=? AND wake_date=?",
                       (uid, day)).fetchone()
    late = sum(m["kcal_total"] for m in meals
               if analytics.is_late(m["local_time"], user["eating_cutoff"]))
    gap = _meal_gap(meals)

    def col(row, name):
        return row[name] if row and row[name] is not None else None

    return {
        "date": day,
        "kcal": int(totals["kcal"]), "meals": int(totals["meals"]),
        "kcal_low": int(sum(m["kcal_low"] or 0 for m in meals)),
        "kcal_high": int(sum(m["kcal_high"] or 0 for m in meals)),
        "protein": round(float(totals["protein"])),
        "fat": round(float(totals["fat"])), "carbs": round(float(totals["carbs"])),
        "kcal_target": int(tgt["kcal_target"]) if tgt else 2100,
        "protein_target": int(tgt["protein_g"] or 120) if tgt else 120,
        "water": repo.day_water_ml(db, uid, day),
        "water_goal": watersvc.goal_for(db, user),
        "steps": col(act, "steps"),
        "active_kcal": col(act, "active_kcal"), "active_min": col(act, "active_minutes"),
        "avg_hr": col(act, "avg_hr"), "resting_hr": col(act, "resting_hr"),
        "sleep_min": sleep["duration_min"] if sleep and sleep["duration_min"] else None,
        "sleep_score": col(sleep, "score"), "sleep_awake_min": col(sleep, "awake_min"),
        "sleep_deep_min": col(sleep, "deep_min"),
        "late_kcal": int(late), "cutoff": user["eating_cutoff"],
        "first_meal": meals[0]["local_time"] if meals else None,
        "last_meal": meals[-1]["local_time"] if meals else None,
        "longest_gap_h": gap[0] if gap else None,
        "gap_from": gap[1] if gap else None, "gap_to": gap[2] if gap else None,
        "cravings": _cravings_of_day(db, uid, day),
        "care": bool(user["care_mode"]),
    }


def maybe_incomplete(f: dict, now_hhmm: str | None = None) -> bool:
    """Записано подозрительно мало: вероятно, не вся еда внесена в дневник.

    Для идущего дня (now_hhmm) до 18:00 недобор нормален, поэтому молчим."""
    if now_hhmm is not None and now_hhmm < "18:00":
        return False
    meals, kcal, target = f.get("meals", 0), f.get("kcal", 0), f.get("kcal_target", 0)
    if not target:
        return False
    return meals == 0 or kcal < 0.5 * target or (meals <= 2 and kcal < 0.7 * target)


def llm_facts(f: dict, now_hhmm: str | None = None) -> dict:
    """Факты дня для модели: всё, что нужно для совета, и готовые разности, чтобы она не считала.

    В осторожном режиме калории и их разности не передаются."""
    out = {
        "date": f["date"], "meals": f["meals"], "protein_g": f["protein"],
        "protein_target_g": f["protein_target"],
        "protein_left_g": max(0, f["protein_target"] - f["protein"]),
        "water_ml": f["water"], "water_goal_ml": f["water_goal"],
        "water_left_ml": max(0, f["water_goal"] - f["water"]),
        "steps": f["steps"], "active_kcal": f.get("active_kcal"),
        "active_min": f.get("active_min"), "resting_hr": f.get("resting_hr"),
        "sleep_min": f["sleep_min"], "sleep_score": f.get("sleep_score"),
        "sleep_awake_min": f.get("sleep_awake_min"),
        "first_meal": f.get("first_meal"), "last_meal": f.get("last_meal"),
        "longest_gap_h": f.get("longest_gap_h"),
        "cravings": [{"time": c["time"], "category": c["category"], "outcome": c["outcome"]}
                     for c in f.get("cravings", [])],
        "diary_maybe_incomplete": maybe_incomplete(f, now_hhmm),
    }
    if not f["care"]:
        out.update({
            "kcal": f["kcal"], "kcal_target": f["kcal_target"],
            "kcal_left": f["kcal_target"] - f["kcal"],
            "kcal_range": [f.get("kcal_low", 0), f.get("kcal_high", 0)],
            "late_kcal": f["late_kcal"], "eating_cutoff": f["cutoff"],
        })
    return out


def _status(ratio: float, good: float = 0.9, ok: float = 0.7) -> str:
    return "✅" if ratio >= good else "🟡" if ratio >= ok else "⚠️"


def scorecard_rows(f: dict) -> list[list[str]]:
    """Строки таблицы «показатель, факт, ориентир, статус»."""
    rows: list[list[str]] = []
    if not f["care"]:
        k, t = f["kcal"], f["kcal_target"]
        status = "—" if not f["meals"] else "✅" if 0.85 <= k / t <= 1.1 else \
            "🟡" if 0.7 <= k / t <= 1.2 else "⚠️"
        rows.append(["Калории", f"{_num(k)} ккал", f"{_num(t)}", status])
    p, pt = f["protein"], f["protein_target"]
    rows.append(["Белок", f"{p} г", f"{pt} г", _status(p / pt) if f["meals"] else "—"])
    w, wg = f["water"], f["water_goal"]
    rows.append(["Вода", f"{_num(w)} мл", f"{_num(wg)} мл", _status(w / wg, 0.9, 0.5)])
    steps = f["steps"]
    rows.append(["Шаги", _num(steps) if steps is not None else "—", _num(STEPS_GOAL),
                 _status(steps / STEPS_GOAL, 1.0, 0.6) if steps is not None else "—"])
    sleep = f["sleep_min"]
    rows.append(["Сон", analytics.format_minutes(sleep) if sleep else "—", "7+ ч",
                 _status(sleep / SLEEP_GOAL_MIN, 0.95, 0.85) if sleep else "—"])
    return rows


def _protein_idea(need: int) -> str:
    return next(text for limit, text in PROTEIN_IDEAS if need <= limit)


def _times(n: int) -> str:
    return f"{n} {'раза' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'раз'}"


def _craving_tip(f: dict, incomplete: bool) -> Tip | None:
    """Совет по тяге дня: что было записано к её началу и что из этого следует."""
    cravings = f.get("cravings") or []
    if not cravings:
        return None
    first = cravings[0]
    overate = sum(1 for c in cravings if c["outcome"] == "overate")
    head = (f"Тяга была в {first['time']}" if len(cravings) == 1
            else f"Тяга была {_times(len(cravings))}, первый раз в {first['time']}")
    ks, kt = first.get("kcal_so_far"), first.get("kcal_target")
    prio = 1 if overate else 2
    if f["care"] or ks is None or not kt:
        return Tip("🧭", f"{head}. Это волна на 15–30 минут. Придумай заранее замену на это время: "
                         "чай, душ или короткая прогулка.", prio)
    if ks < 0.6 * kt:
        maybe = " (если еда записана полностью)" if incomplete else ""
        return Tip("🧭", f"{head}: к этому времени записано {ks} из {kt} ккал{maybe}. Скорее "
                         "голод, чем срыв. Завтра сдвинь часть еды на день и добавь белок "
                         "в обед и полдник.", prio)
    return Tip("🧭", f"{head}, при этом записано уже {ks} из {kt} ккал. Скорее усталость, скука или "
                     "привычка, чем голод. Заранее реши, чем заменишь это время: чай, душ, прогулка.",
               prio)


def day_tips(f: dict, closed: bool = True) -> tuple[list[str], list[Tip]]:
    """(что получилось, советы). closed=False: день ещё идёт, недобор не ругаем.

    Если записано подозрительно мало (maybe_incomplete), не объявляем недоедание и не считаем
    «дефицит белка» по неполным данным: сначала просим дописать дневник."""
    good: list[str] = []
    tips: list[Tip] = []
    kcal, target, meals = f["kcal"], f["kcal_target"], f["meals"]
    steps, sleep = f["steps"], f["sleep_min"]
    protein, ptarget = f["protein"], f["protein_target"]
    active_kcal = f.get("active_kcal")
    active = (steps is not None and steps >= 10000) or (active_kcal is not None
                                                        and active_kcal >= 700)
    incomplete = closed and maybe_incomplete(f)

    if incomplete:
        if not meals:
            tips.append(Tip("📝", "Еда за день не записана. Если что-то было, допиши хотя бы "
                                  "коротко: тогда советы будут по делу.", 1))
        elif f["care"]:
            tips.append(Tip("📝", "Похоже, в дневнике записано не всё. Допиши то, что не попало: "
                                  "советы станут точнее.", 1))
        else:
            tips.append(Tip("📝", f"Записано {_num(kcal)} из {_num(target)} ккал: похоже, внесено "
                                  "не всё. Допиши то, что не попало в дневник. Если еды правда "
                                  "было мало, вечером тянет сильнее, поэтому завтра добавь "
                                  "нормальный приём пищи с белком.", 1))

    if meals >= 2 and protein < 0.75 * ptarget and closed and not incomplete:
        need = ptarget - protein
        why = ("Много движения: мышцам нужен белок, чтобы восстановиться. "
               if active else "Белок держит сытость и бережёт мышцы. ")
        tips.append(Tip("🥩", f"{why}Сегодня {protein} из {ptarget} г: добавь ещё около {need} г, "
                              f"например {_protein_idea(need)}.", 1 if active else 2))
    elif meals >= 2 and protein >= 0.9 * ptarget:
        good.append(f"белок в норме ({protein} г)")

    water, goal = f["water"], f["water_goal"]
    if closed or water:
        if water >= goal:
            good.append(f"воды достаточно ({_num(water)} мл)")
        elif water < 0.5 * goal:
            tips.append(Tip("💧", f"Воды мало: {_num(water)} из {_num(goal)} мл. Поставь стакан на "
                                  "виду и пей по паре глотков каждый час. Часто «голод» вечером "
                                  "оказывается жаждой и усталостью.", 3))
        elif water < 0.9 * goal:
            tips.append(Tip("💧", f"Воды {_num(water)} из {_num(goal)} мл: до цели ещё "
                                  f"{_num(goal - water)} мл, один-два стакана.", 6))

    if steps is not None:
        if steps >= 10000:
            good.append(f"много движения ({_num(steps)} шагов)")
        elif steps >= STEPS_GOAL:
            good.append(f"шаги в плане ({_num(steps)})")
        elif steps < 5000 and closed:
            tips.append(Tip("🚶", f"Шагов {_num(steps)}: 10–15 минут неспешной прогулки после еды "
                                  "улучшают самочувствие и аппетит. Это проще, чем кажется.", 5))
    if active and closed and not f["care"] and meals >= 2 and kcal < 0.85 * target \
            and not incomplete:
        load = (f"По данным браслета активных ккал {_num(active_kcal)}"
                if active_kcal is not None and active_kcal >= 700
                else f"{_num(steps)} шагов")
        tips.append(Tip("🍚", f"{load}: расход выше обычного. Не урезай ужин, добавь сложных "
                              "углеводов (крупа, картофель, хлеб из цельного зерна), иначе завтра "
                              "потянет на сладкое.", 2))

    if sleep:
        score, awake = f.get("sleep_score"), f.get("sleep_awake_min")
        low_quality = (score is not None and score < 70) or (awake is not None and awake >= 45)
        if sleep < SLEEP_LOW_MIN:
            tips.append(Tip("😴", f"Сон {analytics.format_minutes(sleep)}: после недосыпа сильнее "
                                  "тянет на сладкое. Завтра начни с белкового завтрака и ляг на "
                                  "30–40 минут раньше.", 2))
        elif low_quality:
            detail = (f"оценка {score} из 100" if score is not None and score < 70
                      else f"пробуждения {awake} мин")
            tips.append(Tip("😴", f"Сон {analytics.format_minutes(sleep)}, но качество невысокое "
                                  f"({detail}). Последний кофе до 15:00 и ужин за 2–3 часа до сна "
                                  "самые простые рычаги.", 3))
        elif sleep >= SLEEP_GOAL_MIN:
            good.append("сон в норме" if score is None or score < 80
                        else f"сон хороший (оценка {score})")

    crave = _craving_tip(f, incomplete)
    if crave:
        tips.append(crave)

    gap = f.get("longest_gap_h")
    if closed and not incomplete and gap is not None and gap >= 5.5:
        tips.append(Tip("⏱", f"Между приёмами в {f['gap_from']} и {f['gap_to']} прошло около "
                             f"{gap:g} ч. Длинные паузы часто оборачиваются вечерней тягой: "
                             "поставь напоминание на перекус с белком посередине.", 4))

    if not f["care"] and meals and closed:
        ratio = kcal / target
        if ratio > 1.1:
            tips.append(Tip("🍽", f"Съедено на {kcal - target} ккал больше цели: это нормально, "
                                  "один день не решает ничего. Завтра обычный режим, без голодовки "
                                  "и «отработки».", 4))
        elif ratio < 0.7 and not incomplete:
            tips.append(Tip("🍽", f"Съедено {_num(kcal)} из {_num(target)} ккал: слишком мало. "
                                  "Недоедание днём часто заканчивается вечерним срывом, поэтому "
                                  "завтра добавь нормальный приём пищи с белком.", 1))
        elif 0.85 <= ratio <= 1.1:
            good.append("калории в цели")
        if f["late_kcal"]:
            tips.append(Tip("🌙", f"После {f['cutoff']} съедено {f['late_kcal']} ккал. Если "
                                  "потянет поздно, план Б: кефир, творог или овощи на 150–200 ккал.",
                            5))
    return good, sorted(tips, key=lambda t: t.prio)


# ------------------------------------------------------------------------- неделя

def week_facts(db: sqlite3.Connection, user, today: date) -> dict:
    days = [day_facts(db, user, (today - timedelta(days=i)).isoformat()) for i in range(6, -1, -1)]
    prev = [day_facts(db, user, (today - timedelta(days=i)).isoformat()) for i in range(13, 6, -1)]
    return {"days": days, "prev": prev, "care": bool(user["care_mode"]),
            **_energy_baseline(db, user, today)}


def _energy_baseline(db: sqlite3.Connection, user, today: date) -> dict:
    """Оценка расхода из профиля (tdee_est цели) и базовый обмен: для сверки с браслетом."""
    from app.domain.targets import bmr_mifflin
    tgt = repo.current_target(db, user["id"])
    tdee = tgt["tdee_est"] if tgt and tgt["tdee_est"] else None
    w = repo.latest_weight_kg(db, user["id"])
    if not tdee or not w or not user["height_cm"] or not user["birth_date"]:
        return {"tdee_est": tdee, "bmr": None}
    age = today.year - int(str(user["birth_date"])[:4])
    return {"tdee_est": tdee, "bmr": round(bmr_mifflin(user["sex"], age, float(w),
                                                       float(user["height_cm"])))}


def _avg(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def week_summary(days: list[dict]) -> dict:
    logged = [d for d in days if d["meals"]]
    return {
        "logged": len(logged),
        "kcal": _avg([d["kcal"] for d in logged]),
        "protein": _avg([d["protein"] for d in logged]),
        "water": _avg([d["water"] for d in days if d["water"]]),
        "water_days": sum(1 for d in days if d["water"]),
        "steps": _avg([d["steps"] for d in days]),
        "sleep": _avg([d["sleep_min"] for d in days]),
        "late_days": sum(1 for d in days if d["late_kcal"]),
        "kcal_target": days[-1]["kcal_target"],
        "protein_target": days[-1]["protein_target"],
        "water_goal": days[-1]["water_goal"],
    }


def week_table_rows(days: list[dict]) -> list[list[str]]:
    rows = []
    for d in days:
        rows.append([d["date"][5:], _num(d["kcal"]) if d["meals"] else "—",
                     f"{d['protein']}" if d["meals"] else "—",
                     _num(d["water"]) if d["water"] else "—",
                     _num(d["steps"]) if d["steps"] is not None else "—",
                     analytics.format_minutes(d["sleep_min"]) if d["sleep_min"] else "—"])
    return rows


def week_scorecard(days: list[dict], care: bool) -> list[list[str]]:
    s = week_summary(days)
    rows: list[list[str]] = []
    if not care and s["kcal"] is not None:
        ratio = s["kcal"] / s["kcal_target"]
        rows.append(["Калории (среднее)", f"{_num(s['kcal'])} ккал", _num(s["kcal_target"]),
                     "✅" if 0.85 <= ratio <= 1.1 else "🟡" if 0.7 <= ratio <= 1.2 else "⚠️"])
    if s["protein"] is not None:
        rows.append(["Белок", f"{_num(s['protein'])} г", f"{s['protein_target']} г",
                     _status(s["protein"] / s["protein_target"])])
    if s["water"] is not None:
        rows.append(["Вода", f"{_num(s['water'])} мл", f"{_num(s['water_goal'])} мл",
                     _status(s["water"] / s["water_goal"], 0.9, 0.5)])
    if s["steps"] is not None:
        rows.append(["Шаги", _num(s["steps"]), _num(STEPS_GOAL),
                     _status(s["steps"] / STEPS_GOAL, 1.0, 0.6)])
    if s["sleep"] is not None:
        rows.append(["Сон", analytics.format_minutes(int(s["sleep"])), "7+ ч",
                     _status(s["sleep"] / SLEEP_GOAL_MIN, 0.95, 0.85)])
    rows.append(["Дней с едой", f"{s['logged']} из 7", "7", _status(s["logged"] / 7, 0.85, 0.55)])
    return rows


def _energy_check(wf: dict) -> Tip | None:
    """Сверка расхода: базовый обмен + активные ккал браслета против оценки в цели.

    Только информация: браслеты ошибаются на 20–30 %, цель сама пересчитывает адаптивный
    TDEE по весу и еде. Нужно не меньше 5 дней данных активности."""
    tdee, bmr = wf.get("tdee_est"), wf.get("bmr")
    act = [d["active_kcal"] for d in wf["days"] if d.get("active_kcal")]
    if wf["care"] or not tdee or not bmr or len(act) < 5:
        return None
    measured = round((bmr + sum(act) / len(act)) / 10) * 10
    diff = (measured - tdee) / tdee
    if abs(diff) < 0.15:
        return None
    side = "выше" if diff > 0 else "ниже"
    return Tip("🧮", f"По браслету расход около {_num(measured)} ккал в день: это {side} оценки "
                     f"в профиле ({_num(tdee)}). Браслеты ошибаются на 20–30 %, поэтому цель не "
                     "меняю. Если вес за 2–3 недели идёт не так, как задумано, цель "
                     "пересчитается по весу и записанной еде.", 4)


def week_tips(wf: dict) -> tuple[list[str], list[Tip]]:
    """Наблюдения и советы по неделе. Порог не достигнут: молчим."""
    days, prev, care = wf["days"], wf["prev"], wf["care"]
    s, p = week_summary(days), week_summary(prev)
    good: list[str] = []
    tips: list[Tip] = []

    if s["logged"] < 4:
        tips.append(Tip("📝", f"Еда записана за {s['logged']} {days_word(s['logged'])} из 7, поэтому выводы "
                              "по неделе пока приблизительные. Хватит и короткой записи, даже "
                              "«гречка с курицей».", 1))

    if s["protein"] is not None and s["logged"] >= 3:
        if s["protein"] < 0.75 * s["protein_target"]:
            gap = int(s["protein_target"] - s["protein"])
            tips.append(Tip("🥩", f"Белка в среднем {_num(s['protein'])} из {s['protein_target']} г: "
                                  f"не хватает около {gap} г в день. Самый простой способ: белок в "
                                  "каждый приём пищи (яйца или творог утром, курица/рыба в обед, "
                                  "кефир или йогурт вечером).", 1))
        elif s["protein"] >= 0.9 * s["protein_target"]:
            good.append("белок держится у цели")

    if s["water"] is not None and s["water"] < 0.6 * s["water_goal"]:
        tips.append(Tip("💧", f"Вода в среднем {_num(s['water'])} мл при ориентире "
                              f"{_num(s['water_goal'])}. Привяжи стакан к привычкам: после "
                              "пробуждения, перед каждой едой и в 16:00.", 2))
    elif s["water"] is not None and s["water"] >= s["water_goal"]:
        good.append("воды хватает")

    if s["steps"] is not None:
        if s["steps"] >= 10000:
            good.append(f"активная неделя ({_num(s['steps'])} шагов в среднем)")
        elif s["steps"] < 5000:
            tips.append(Tip("🚶", f"Шагов в среднем {_num(s['steps'])}. Прибавка по 1000 шагов в "
                                  "неделю (одна короткая прогулка) даёт больше, чем разовый "
                                  "рывок.", 3))
        if p["steps"] is not None and s["steps"] - p["steps"] >= 1500:
            good.append(f"шагов стало больше на {_num(s['steps'] - p['steps'])} в день")

    tip = _energy_check(wf)
    if tip:
        tips.append(tip)

    if s["sleep"] is not None and s["sleep"] < SLEEP_LOW_MIN + 30:
        tips.append(Tip("😴", f"Сон в среднем {analytics.format_minutes(int(s['sleep']))}. Недосып "
                              "повышает аппетит и тягу к сладкому, и сон — самый дешёвый рычаг: "
                              "сдвинь отбой на 30 минут раньше.", 2))

    if not care and s["kcal"] is not None and s["logged"] >= 4:
        ratio = s["kcal"] / s["kcal_target"]
        if ratio > 1.1:
            tips.append(Tip("🍽", f"В среднем на {int(s['kcal'] - s['kcal_target'])} ккал выше цели. "
                                  "Посмотри, на каком приёме пищи набегает больше всего: часто это "
                                  "перекусы и вечер.", 2))
        elif ratio < 0.7:
            tips.append(Tip("🍽", "Еды заметно меньше цели: жёсткий недобор часто ведёт к срывам "
                                  "и потере мышц. Подними приёмы пищи ближе к цели.", 1))
        elif 0.85 <= ratio <= 1.1:
            good.append("калории в целевом коридоре")
        late = s["late_days"]
        if late >= 3:
            tips.append(Tip("🌙", f"Поздняя еда была {late} {days_word(late)} из 7. Попробуй сдвинуть ужин "
                                  "на час раньше и добавить в него белок: так вечером меньше "
                                  "тянет.", 3))
        # рабочие дни против выходных
        work = [d["kcal"] for d in days if d["meals"] and date.fromisoformat(d["date"]).weekday() < 5]
        free = [d["kcal"] for d in days if d["meals"] and date.fromisoformat(d["date"]).weekday() >= 5]
        if len(work) >= 3 and len(free) >= 2:
            diff = int(sum(free) / len(free) - sum(work) / len(work))
            if diff >= 350:
                tips.append(Tip("📆", f"В выходные ешь примерно на {diff} ккал больше, чем в будни. "
                                      "Заранее решай, что будет на завтрак и ужин в субботу: план "
                                      "спасает лучше силы воли.", 4))

    logged = [d for d in days if d["meals"]]
    if logged and not care and s["logged"] >= 4:
        best = min(logged, key=lambda d: abs(d["kcal"] - d["kcal_target"]))
        good.append(f"ближе всего к цели был день {best['date'][5:]} ({_num(best['kcal'])} ккал): "
                    "повтори его меню")
    return good, sorted(tips, key=lambda t: t.prio)
