"""Вечерний коуч (21:30): Python собирает контекст с цифрами, LLM только формулирует.

Проверка честности: каждое число в ответе обязано присутствовать в контексте.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, timedelta

from app.domain import analytics
from app.llm import client as llm
from app.services.food import load_prompt, meal_type_ru
from app.storage import repo
from app.textutil import esc

log = logging.getLogger(__name__)


def _now_hhmm(user, today: date) -> str | None:
    """Текущее местное время, если считаем «сегодня»; для прошлых дней день уже закрыт (None)."""
    from app.timeutils import local_today
    if today != local_today(user["tz"]):
        return "23:59"
    from app.services.food import local_now
    return local_now(user["tz"]).strftime("%H:%M")


def build_context(db: sqlite3.Connection, user, today: date | None = None) -> dict:
    uid = user["id"]
    if today is None:
        from app.timeutils import local_today
        today = local_today(user["tz"])
    days = [ (today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1) ]
    day_rows = []
    for d in days:
        day = repo.day_totals(db, uid, d)
        meals = repo.day_meals(db, uid, d)
        late = sum(m["kcal_total"] for m in meals
                   if analytics.is_late(m["local_time"], user["eating_cutoff"]))
        sl = db.execute("SELECT duration_min FROM sleep_sessions WHERE user_id=? AND wake_date=?",
                        (uid, d)).fetchone()
        act = db.execute("SELECT steps FROM activity_daily WHERE user_id=? AND local_date=?",
                         (uid, d)).fetchone()
        day_rows.append({
            "date": d, "kcal": day["kcal"], "meals": day["meals"], "late_kcal": late,
            "protein": day["protein"], "sleep_min": sl["duration_min"] if sl else None,
            "steps": act["steps"] if act and act["steps"] is not None else None,
        })
    tgt = repo.current_target(db, uid)
    target = tgt["kcal_target"] if tgt else 2100
    weights, _ = repo.trend_weight_series(db, uid, (today - timedelta(days=13)).strftime("%Y-%m-%d"),
                                          today.strftime("%Y-%m-%d"))
    week_kcal = [r["kcal"] for r in day_rows if r["meals"]]
    avg = int(sum(week_kcal) / len(week_kcal)) if week_kcal else 0
    from app.services import insights
    facts = insights.day_facts(db, user, today.isoformat())
    return {
        "today_facts": insights.llm_facts(facts, _now_hhmm(user, today)),
        "target_kcal": target,
        "eating_cutoff": user["eating_cutoff"],
        "days": day_rows,
        "week_avg_kcal": avg,
        "week_late_evenings": sum(1 for r in day_rows if r["late_kcal"] > 0),
        "weight_trend_14d": [r["weight_kg"] for r in weights],
        "show_disclaimer": today.day % 7 == 0,   # не чаще раза в неделю
    }


def check_numbers(text: str, context: dict) -> list[str]:
    """Каждое число в тексте обязано быть в контексте (или быть служебным)."""
    allowed = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)):
            allowed.add(int(o))
            allowed.add(str(o))

    walk(context)
    # время («19:30») и даты — не статистика; малые числа 0–7 — счёт дней («4 из 7»)
    text = re.sub(r"\b\d{1,2}:\d{2}\b|\b\d{4}-\d{2}-\d{2}\b", " ", text)
    allowed.update(range(0, 8))
    suspects = []
    for num in re.findall(r"\d+(?:[.,]\d+)?", text):
        n = num.replace(",", ".")
        try:
            iv = int(float(n))
        except ValueError:
            continue
        if iv not in allowed and num not in allowed and n not in allowed:
            suspects.append(num)
    return suspects


def format_review(ctx: dict, out: dict) -> str:
    """4 блока, ≤ 1200 символов. Числа — из Python-контекста."""
    today = ctx["days"][-1]
    from app.domain.targets import round10_kcal
    left = ctx["target_kcal"] - today["kcal"]
    sleep = analytics.format_minutes(today["sleep_min"]) if today["sleep_min"] else "—"
    lines = [
        "📊 Итог дня",
        f"{today['kcal']} / {ctx['target_kcal']} ккал · осталось {left} · "
        f"шаги {today['steps'] if today['steps'] is not None else '—'} · сон {sleep}",
    ]
    if today["late_kcal"]:
        lines.append(f"🍽 Поздняя еда: {today['late_kcal']} ккал")
    if out.get("what_worked"):
        lines += ["", "✅ Что получилось"] + [f"— {esc(s)}" for s in out["what_worked"][:3]]
    if out.get("what_hurts"):
        lines += ["", "⚠️ Что мешает"] + [f"— {esc(s)}" for s in out["what_hurts"][:3]]
    if out.get("one_action"):
        lines += ["", f"🎯 {esc(out['one_action'])}"]
    if out.get("ask_user"):
        lines.append(f"❓ {esc(out['ask_user'])}")
    if ctx.get("show_disclaimer") or out.get("disclaimer"):
        lines += ["", "<i>Оценки приблизительные, это не медицинский сервис.</i>"]
    return "\n".join(lines)[:1200]


FALLBACK = "Недостаточно данных для разбора: нужно ещё дней с едой и сном. Кидай скриншоты и еду."


async def evening_review(db: sqlite3.Connection, user, today: date | None = None) -> str:
    uid = user["id"]
    ctx = build_context(db, user, today=today)
    if not any(r["meals"] for r in ctx["days"]):
        return FALLBACK
    prompt = load_prompt("coach")
    try:
        out, _ = await llm.chat_json(
            "coach_evening",
            [{"role": "system", "content": prompt},
             {"role": "user",
              "content": json.dumps(ctx, ensure_ascii=False) +
                         "\n\nСделай разбор дня на этих фактах."}],
            temperature=0.4, user_id=uid, db=db,
        )
    except llm.LLMError:
        return FALLBACK
    suspects = check_numbers(
        json.dumps({k: out.get(k) for k in
                    ("facts", "what_worked", "what_hurts", "one_action", "ask_user")},
                   ensure_ascii=False),
        ctx,
    )
    if suspects:
        log.warning("коуч использовал числа не из контекста: %s", suspects[:5])
        out = {"facts": [], "what_worked": [], "what_hurts": [], "one_action": None}
        return FALLBACK
    return format_review(ctx, out)


async def evening_doc(db: sqlite3.Connection, user, today: date | None = None):
    """Вечерний итог: таблицы и советы считает Python, LLM добавляет «одно действие на завтра»."""
    from app.services import report
    from app.timeutils import local_today
    today = today or local_today(user["tz"])
    ctx = build_context(db, user, today=today)
    if not any(r["meals"] for r in ctx["days"]):
        return FALLBACK
    out: dict | None = None
    try:
        data, _ = await llm.chat_json(
            "coach_evening",
            [{"role": "system", "content": load_prompt("coach")},
             {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}],
            temperature=0.4, user_id=user["id"], db=db)
        if check_numbers(json.dumps({k: data.get(k) for k in ("one_action", "ask_user")},
                                    ensure_ascii=False), ctx):
            log.warning("коуч использовал числа не из контекста")
        else:
            out = data
    except llm.LLMError:
        log.warning("вечерний коуч: LLM недоступен, шлю итог без него")
    chart = await report.day_chart_bytes(db, user, today.isoformat())
    return report.day_doc(db, user, today.isoformat(), coach=out, closed=True,
                          title="Итог дня", chart=chart)


def _yesterday_food_lines(db: sqlite3.Connection, user, yesterday: str, today_s: str) -> list[str]:
    """Вчерашняя еда одной-двумя строками. Ночные записи после полуночи (до 04:00) лежат в
    сегодняшней дате, но относятся к вчерашнему вечеру, поэтому добавляем их к итогу."""
    from app.services import insights
    f = insights.day_facts(db, user, yesterday)
    night = [m for m in repo.day_meals(db, user["id"], today_s) if m["local_time"] < "04:00"]
    night_kcal = int(sum(m["kcal_total"] for m in night))
    night_protein = round(sum(m["protein_g"] or 0 for m in night))
    if not f["meals"] and not night:
        return ["🍽 Вчера еда не записана."]
    kcal, protein = f["kcal"] + night_kcal, f["protein"] + night_protein
    parts = [] if f["care"] else [f"{kcal:,} из {f['kcal_target']:,} ккал".replace(",", " ")]
    parts.append(f"белок {protein} из {f['protein_target']} г")
    line = "🍽 Вчера: " + ", ".join(parts)
    out = [line]
    if night:
        out.append(f"🌙 Ночью после полуночи записано приёмов: {len(night)}"
                   + ("" if f["care"] else f" ({night_kcal} ккал), они уже в сумме выше") + ".")
    if insights.maybe_incomplete({**f, "kcal": kcal, "meals": f["meals"] + len(night)}):
        out.append("Похоже, записано не всё. Если что-то не внёс, допиши, это поправит статистику.")
    return out


async def morning_summary(db: sqlite3.Connection, user, today: date | None = None) -> str:
    uid = user["id"]
    if today is None:
        from app.timeutils import local_today
        today = local_today(user["tz"])
    today_s = today.isoformat()
    lines = ["🌅 Доброе утро!"]
    sl = db.execute("SELECT duration_min FROM sleep_sessions WHERE user_id=? AND wake_date=?",
                    (uid, today_s)).fetchone()
    if sl and sl["duration_min"]:
        lines.append(f"😴 Сон: {analytics.format_minutes(sl['duration_min'])}")
    yesterday = (today - timedelta(days=1)).isoformat()
    act = db.execute("SELECT steps FROM activity_daily WHERE user_id=? AND local_date=?",
                     (uid, yesterday)).fetchone()
    if act and act["steps"] is not None:
        lines.append(f"👟 Вчера шагов: {act['steps']:g}")
    lines += _yesterday_food_lines(db, user, yesterday, today_s)
    weights, _ = repo.trend_weight_series(db, uid, (today - timedelta(days=14)).strftime("%Y-%m-%d"),
                                          today_s)
    if len(weights) >= 4:
        from app.domain.targets import ewma_dated
        s = ewma_dated([(date.fromisoformat(r["local_date"]).toordinal(), r["weight_kg"])
                        for r in weights])
        lines.append(f"⚖️ Тренд веса (сглаженный): {s[-1][1]:.1f} кг")
    tgt = repo.current_target(db, uid)
    lines.append(f"🎯 Цель на день: {tgt['kcal_target'] if tgt else 2100} ккал")
    from app.services.craving import morning_note
    note = morning_note(db, user, today)
    if note:
        lines += ["", note]
    return "\n".join(lines)
