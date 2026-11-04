"""Ассистент: ответы на вопросы своими словами по данным пользователя.

Контекст собирает Python (никаких выдуманных цифр), LLM лишь формулирует ответ. Ответ
проходит те же проверки, что у коуча: числа только из контекста, никакого стыда и компенсации.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import date, timedelta

from app.config import get_config
from app.domain import craving as craving_dom
from app.domain import safety as safety_dom
from app.domain.matching import normalize
from app.domain.targets import ewma_dated, weekly_slope
from app.llm import client as llm
from app.services import food as foodsvc
from app.services import safety as safetysvc
from app.services import water as watersvc
from app.services.coach import check_numbers
from app.storage import repo
from app.textutil import esc

log = logging.getLogger(__name__)

ACTION_NUMBERS = [5, 10, 15, 20, 30, 60]
HISTORY_LIMIT = 10             # сколько последних реплик чата уходит в модель
HISTORY_CHARS = 600

HELP_TEXT = (
    "Я твой личный дневник питания. Пиши или говори как удобно:\n"
    "• еда: «гречка 150 г и котлета», фото, голосовое, цифры штрихкода;\n"
    "• вода: «выпил 2 стакана воды»;\n"
    "• вес, сон и шаги: скриншоты Picooc, Mi Fitness, Apple Health, Garmin и других или словами: "
    "«спал 7 часов», «10 тысяч шагов», «тренировка бег 30 минут»;\n"
    "• тянет переесть или хочется поговорить: просто напиши, как есть, я отвечу и подскажу;\n"
    "• «отмени последнее», «напомни взвеситься в 7:00».\n"
    "Итоги дня и недели, графики, посуда, свои продукты и настройки в приложении: /app."
)

GREETING = "Привет! Я на связи. Напиши, что съел, или спроси что-нибудь. Что я умею: «что ты умеешь»."


def greeting(user) -> str:
    """Приветствие в нужном роде: пол известен из профиля, «съел(а)» не нужно."""
    verb = "съела" if user["sex"] == "female" else "съел"
    return GREETING.replace("съел,", verb + ",")
THANKS = "Всегда пожалуйста 💛"


# ------------------------------------------------------------------- вопрос про продукт

def food_info(db: sqlite3.Connection, query: str) -> str:
    """Калорийность продукта из базы: без LLM, с учётом падежей (по основам слов)."""
    words = [w for w in normalize(query).split()
             if len(w) > 2 and not w.isdigit() and w not in ("гр",) and not w.startswith("грамм")]
    stems = [w[:-2] if len(w) > 5 else w[:-1] if len(w) > 3 else w for w in words]
    if not stems:
        return "Напиши продукт, например: сколько калорий в гречке?"
    best = None
    asks_raw = any(w.startswith(("сух", "сыр", "жарен", "запечен")) for w in words)
    for row in db.execute("SELECT * FROM foods WHERE owner_user_id IS NULL AND deleted_at IS NULL"):
        name = normalize(row["name_ru"])
        hits = sum(1 for s in stems if s in name)
        if hits == len(stems):
            cooked = 0 if asks_raw else int("варен" in name or "готов" in name)
            key = (hits, cooked, -len(name))
            if best is None or key > best[0]:
                best = (key, row)
    if best is None:
        return (f"Не нашёл «{esc(query)}» в своей базе. Можно прислать штрихкод цифрами, "
                "или просто записать продукт: я оценю по описанию.")
    r = best[1]
    return (f"{esc(r['name_ru'])}: {r['kcal_100g']:g} ккал на 100 г "
            f"(Б {r['protein_100g']:g} / Ж {r['fat_100g']:g} / У {r['carbs_100g']:g}).")


# ---------------------------------------------------------------------- контекст

def snapshot(db: sqlite3.Connection, user, today: date | None = None) -> dict:
    uid = user["id"]
    now_local = foodsvc.local_now(user["tz"])
    today = today or now_local.date()
    tgt = repo.current_target(db, uid)
    kcal_target = int(tgt["kcal_target"]) if tgt else 0
    protein_target = int(tgt["protein_g"] or 0) if tgt else 0
    days = []
    for offset in range(6, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        totals = repo.day_totals(db, uid, d)
        act = db.execute("SELECT steps FROM activity_daily WHERE user_id=? AND local_date=?",
                         (uid, d)).fetchone()
        sleep = db.execute("SELECT duration_min FROM sleep_sessions WHERE user_id=? AND wake_date=?",
                           (uid, d)).fetchone()
        days.append({
            "date": d, "kcal": int(totals["kcal"]), "protein_g": round(float(totals["protein"])),
            "meals": int(totals["meals"]), "water_ml": repo.day_water_ml(db, uid, d),
            "steps": act["steps"] if act and act["steps"] is not None else None,
            "sleep_min": sleep["duration_min"] if sleep and sleep["duration_min"] else None,
        })
    today_row = days[-1]
    rows, _ = repo.trend_weight_series(db, uid, (today - timedelta(days=13)).isoformat(),
                                       today.isoformat())
    points = [(date.fromisoformat(r["local_date"]).toordinal(), r["weight_kg"]) for r in rows]
    weight: dict = {"points_14d": len(points), "goal_kg": user["goal_weight_kg"]}
    if len(points) >= 2:
        weight["trend_kg"] = round(ewma_dated(points)[-1][1], 1)
        slope = weekly_slope(points)
        weight["kg_per_week"] = None if slope is None else round(slope, 1)
    foods = db.execute(
        """SELECT f.name_ru, uf.default_grams FROM user_foods uf JOIN foods f ON f.id=uf.food_id
           WHERE uf.user_id=? AND f.deleted_at IS NULL
           ORDER BY uf.times_used DESC LIMIT 6""", (uid,)).fetchall()
    cravings = repo.cravings_range(db, uid, (today - timedelta(days=13)).isoformat(),
                                   today.isoformat())
    flags = safetysvc.assess(db, user, today)
    body_rows = repo.latest_body_rows(db, uid, 2)
    body_ctx = None
    if body_rows and not user["care_mode"]:
        def pack(row):
            return {k: row[k] for k in ("local_date", "weight_kg", "bmi", "body_fat_pct",
                                        "muscle_mass_pct", "visceral_fat", "body_water_pct",
                                        "bmr_kcal", "metabolic_age", "body_score", "fasting")
                    if row[k] is not None}
        body_ctx = {"latest": pack(body_rows[0]),
                    "previous": pack(body_rows[1]) if len(body_rows) > 1 else None}
    from app.services import insights
    today_facts = insights.llm_facts(insights.day_facts(db, user, today.isoformat()),
                                     now_local.strftime("%H:%M"))
    return {
        "body_composition": body_ctx,
        "today_facts": today_facts,
        "time_now": now_local.strftime("%H:%M"),
        "today": {**today_row, "kcal_target": kcal_target, "kcal_left": kcal_target - today_row["kcal"],
                  "protein_target_g": protein_target,
                  "water_goal_ml": watersvc.goal_for(db, user)},
        "last_7_days": days,
        "weight": weight,
        "frequent_foods": [{"name": r["name_ru"], "usual_grams": r["default_grams"]} for r in foods],
        "my_rules": [f"если {r['if_text']}, то {r['then_text']}" for r in repo.list_rules(db, uid)],
        "cravings_14d": {"count": len(cravings),
                         "overate": sum(1 for c in cravings if c["outcome"] == "overate"),
                         "resisted": sum(1 for c in cravings if c["outcome"] == "resisted")},
        "care_mode": bool(user["care_mode"]),
        "flags": [f.code for f in flags],
        "allowed_extra": ACTION_NUMBERS,
    }


FALLBACK_SOFT = ("Сейчас не получается ответить на это точно, спроси чуть иначе: «что я сегодня ел?», "
                 "«как вес?», «как прошла неделя?». Или просто записывай еду.")
FALLBACK = ("Сейчас не получается ответить на это умно: нет связи с моделью. Базовое работает: "
            "«что я сегодня ел?», «как вес?», «как прошла неделя?». Или просто записывай еду.")


def _history_messages(history: list[dict] | None) -> list[dict]:
    """Последние реплики диалога как сообщения LLM. Свои прошлые ответы ассистент видит в том
    же JSON, который должен вернуть: иначе подхватывает простой текст и перестаёт отвечать JSON."""
    out = []
    for m in (history or [])[-HISTORY_LIMIT:]:
        text = str(m.get("text") or "").strip()[:HISTORY_CHARS]
        if not text:
            continue
        if m.get("role") == "assistant":
            out.append({"role": "assistant",
                        "content": json.dumps({"reply": text}, ensure_ascii=False)})
        else:
            out.append({"role": "user", "content": text})
    return out


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

# Бот не передаёт историю чата, поэтому «почему» без контекста не понять. Держим последние реплики
# в памяти процесса (на перезапуске теряются, это только контекст диалога).
_RECENT: dict[int, list[tuple[float, dict]]] = {}
RECENT_TTL_S = 30 * 60
RECENT_KEEP = 6


def _recent(user_id: int) -> list[dict]:
    now = time.monotonic()
    items = [(t, m) for t, m in _RECENT.get(user_id, []) if now - t < RECENT_TTL_S]
    _RECENT[user_id] = items
    return [m for _, m in items]


def _remember(user_id: int, question: str, reply: str) -> None:
    items = _RECENT.setdefault(user_id, [])
    now = time.monotonic()
    items += [(now, {"role": "user", "text": question}), (now, {"role": "assistant", "text": reply})]
    del items[:-RECENT_KEEP]


async def answer(db: sqlite3.Connection, user, question: str, focus: str | None = None,
                 history: list[dict] | None = None) -> str:
    """Ответ на вопрос по данным. Всегда возвращает безопасный текст (HTML-экранированный).

    focus: «today»/«week»/… (отчёт) или описание конкретного приёма пищи; history: последние
    реплики чата [{role, text}] (только Mini App; бот зовёт без них)."""
    level = craving_dom.risk_level(question)
    if level == "self_harm":
        return esc(craving_dom.SELF_HARM_TEXT)
    if level == "purging":
        return esc(craving_dom.PURGING_TEXT)

    ctx = snapshot(db, user)
    remembered = history is None
    if remembered:
        history = _recent(user["id"])
    # числа из фокуса (приём пищи) и из уже показанных реплик тоже законны в ответе
    known = {**ctx, "extra": [float(n.replace(",", ".")) for n in _NUM_RE.findall(
        " ".join([focus or ""] + [str(m.get("text") or "") for m in history or []]))]}
    # статичный префикс (system) первым, затем история, свежие данные и вопрос в конце: так
    # кэш промпта у провайдера продолжает работать
    base = [
        {"role": "system", "content": foodsvc.load_prompt("assistant")},
        *_history_messages(history),
        {"role": "user", "content": json.dumps(
            {"data": ctx, **({"focus": focus} if focus else {}), "question": question},
            ensure_ascii=False)},
    ]
    reply = ""
    for attempt in (1, 2):
        messages = base if attempt == 1 else base + [
            {"role": "assistant", "content": json.dumps({"reply": reply}, ensure_ascii=False)},
            {"role": "user", "content": "Ответ не прошёл проверку: убери все числа, которых нет в data, "
                                        "и всё, что похоже на оценку тела или компенсацию. Ответь короче."}]
        try:
            data, _ = await llm.chat_json("assistant", messages, temperature=0.4,
                                          model=get_config().model_dialog or None,
                                          user_id=user["id"], db=db)
        except llm.LLMError:
            log.warning("assistant: LLM недоступен", exc_info=True)
            return esc(FALLBACK)
        reply = str(data.get("reply") or "").strip()
        if reply and len(reply) <= 1500 and not craving_dom.violates_policy(reply) \
                and not check_numbers(reply, known):
            break
        log.warning("assistant: ответ модели отклонён проверкой (попытка %s)", attempt)
        reply = reply or "…"
    else:
        return esc(FALLBACK_SOFT)
    if any(f in ctx["flags"] for f in ("risk_language", "very_low_intake", "repeated_overate")):
        reply += "\n\n" + safety_dom.CARE_PROFESSIONAL
    if remembered:
        _remember(user["id"], question, reply)
    return esc(reply)
