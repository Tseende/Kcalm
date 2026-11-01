"""Ночная поддержка при тяге к еде: распознавание, безопасность, эпизоды, итоги."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db import connect, migrate
from app.domain import craving as dom
from app.services import craving as svc
from app.storage import repo
from tests.test_routers_smoke import build, onboard, send


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ----------------------------------------------------------------- распознавание

@pytest.mark.parametrize("text, t, expected", [
    ("хочу есть", "23:30", True),                  # слабая фраза ночью
    ("хочу есть", "14:00", False),                 # днём это обычная жизнь
    ("тянет на сладкое", "01:10", True),
    ("срываюсь, не могу остановиться", "15:00", True),   # сильная — в любое время
    ("переел", "22:00", True),
    ("съел 2 яйца и хочу есть", "23:30", False),    # это запись еды
    ("гречка 150 г", "23:30", False),
    ("выпил стакан воды", "23:50", False),
])
def test_detect_craving(text, t, expected):
    assert dom.detect_craving(text, t, "21:00") is expected


def test_night_window_crosses_midnight():
    assert dom.is_night("21:30", "21:00") and dom.is_night("03:00", "21:00")
    assert not dom.is_night("12:00", "21:00")


def test_risk_levels():
    assert dom.risk_level("вызвала рвоту после еды") == "purging"
    assert dom.risk_level("принимаю слабительные") == "purging"
    assert dom.risk_level("не хочу жить") == "self_harm"
    assert dom.risk_level("хочу сладкого") == "none"


def test_policy_blocks_shame_and_compensation():
    assert dom.violates_policy("Завтра пропусти завтрак, надо отработать")
    assert dom.violates_policy("Это слабоволие")
    assert not dom.violates_policy("Это нормально. Выпей воды и ложись спать.")


def test_insights_use_data_only():
    f = dom.Facts(local_time="23:00", kcal_so_far=900, kcal_target=1900, protein_so_far=30,
                  protein_target=130, meals_today=2, hours_since_last_meal=5.0, sleep_min=320,
                  water_ml=300, water_goal_ml=2500, craving_count_7d=0, overate_count_14d=0)
    text = " ".join(dom.insights(f))
    assert "900 из 1900" in text and "5 ч" in text and "5 ч 20 мин" in text and "300 из 2500" in text
    empty = dom.Facts(local_time="23:00", kcal_so_far=0, kcal_target=1900, protein_so_far=0,
                      protein_target=130, meals_today=0, hours_since_last_meal=None,
                      sleep_min=None, water_ml=2000, water_goal_ml=2500, craving_count_7d=0,
                      overate_count_14d=0)
    notes = dom.insights(empty)
    assert len(notes) == 1 and "пусто" in notes[0]


def test_budget_line_never_shames_when_over_plan():
    f = dom.Facts(local_time="23:00", kcal_so_far=2000, kcal_target=1900, protein_so_far=100,
                  protein_target=130, meals_today=3, hours_since_last_meal=3.0, sleep_min=None,
                  water_ml=2000, water_goal_ml=2500, craving_count_7d=0, overate_count_14d=0)
    line = dom.budget_line(f)
    assert "100–200 ккал" in line and "не ломает" in line


# --------------------------------------------------------------------- эпизоды

def _user(db, tg=1):
    uid = repo.create_user(db, tg, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="male", onboarded=1, eating_cutoff="21:00",
                     start_weight_kg=85, height_cm=180)
    repo.insert_target(db, uid, "2026-10-01", 1900, 130, 65, 200, 2400, "onboarding")
    return uid, repo.get_user_by_tg(db, tg)


def test_start_episode_has_triage_and_facts(db):
    uid, user = _user(db)
    event_id, text, kb = svc.start_episode(db, user, "хочу есть")
    assert "Я рядом" in text and kb is not None
    event = repo.get_craving(db, uid, event_id)
    assert event["outcome"] == "open" and event["trigger_text"] == "хочу есть"


def test_self_harm_text_has_no_diet_advice(db):
    uid, user = _user(db)
    event_id, text, kb = svc.start_episode(db, user, "не хочу жить")
    assert kb is None and "112" in text
    assert repo.get_craving(db, uid, event_id)["risk_flag"] == 1


def test_purging_text_sends_to_specialist(db):
    uid, user = _user(db)
    _, text, kb = svc.start_episode(db, user, "после еды вызвала рвоту")
    assert kb is None and "врач" in text


def test_category_plans_and_finish(db):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "тянет на сладкое")
    event = repo.get_craving(db, uid, event_id)
    plan = svc.category_plan(db, user, event, "hunger")
    assert "ккал" in plan and "белк" in plan          # перекусы из базы с калорийностью
    assert not dom.violates_policy(plan)
    event = repo.get_craving(db, uid, event_id)
    assert event["category"] == "hunger"
    done = svc.finish_episode(db, user, event, "overate")
    assert "наказ" in done.lower() and "без" in done
    assert repo.get_craving(db, uid, event_id)["outcome"] == "overate"


def test_every_deterministic_text_passes_policy(db):
    uid, user = _user(db)
    event_id, start, _ = svc.start_episode(db, user, "хочу есть")
    event = repo.get_craving(db, uid, event_id)
    texts = [start] + [svc.category_plan(db, user, event, c) for c in dom.CATEGORIES]
    texts += [svc.finish_episode(db, user, event, o) for o in ("resisted", "ate_planned", "overate")]
    for t in texts:
        assert not dom.violates_policy(t), t


@pytest.mark.asyncio
async def test_dialog_rejects_bad_model_reply(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    event = repo.get_craving(db, uid, event_id)

    async def bad(*a, **k):
        return {"reply": "Ты слабак, завтра пропусти завтрак и сожги 900 калорий", "risk": False}, "m"
    monkeypatch.setattr(svc.llm, "chat_json", bad)
    reply, _ = await svc.dialog_reply(db, user, event, "не могу остановиться")
    assert "слабак" not in reply and "пропусти" not in reply

    async def invented(*a, **k):
        return {"reply": "В этой булке 4321 ккал, лучше не надо", "risk": False}, "m"
    monkeypatch.setattr(svc.llm, "chat_json", invented)
    reply, _ = await svc.dialog_reply(db, user, event, "хочу булку")
    assert "4321" not in reply


@pytest.mark.asyncio
async def test_dialog_good_reply_passes_and_is_escaped(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    event = repo.get_craving(db, uid, event_id)

    async def good(*a, **k):
        return {"reply": "Понимаю <тебя>. Выпей воды и подожди 15 минут.", "risk": False}, "m"
    monkeypatch.setattr(svc.llm, "chat_json", good)
    reply, _ = await svc.dialog_reply(db, user, event, "хочу есть")
    assert "&lt;тебя&gt;" in reply and "15 минут" in reply


@pytest.mark.asyncio
async def test_dialog_falls_back_when_llm_down(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    event = repo.get_craving(db, uid, event_id)

    async def down(*a, **k):
        raise svc.llm.LLMError("нет сети")
    monkeypatch.setattr(svc.llm, "chat_json", down)
    reply, _ = await svc.dialog_reply(db, user, event, "плохо")
    assert "стакан воды" in reply


def test_week_lines_and_professional_note(db):
    uid, user = _user(db)
    today = date.today().isoformat()
    for _ in range(3):
        eid = repo.create_craving(db, uid, today, "23:10", "переел", 800, 1900)
        repo.close_craving(db, eid, "overate")
    lines = svc.week_lines(db, uid, today, today)
    assert "3 раз" in lines[0] and any("врачом или психологом" in l for l in lines)


def test_followup_flow(db):
    uid, user = _user(db)
    eid, _, _ = svc.start_episode(db, user, "хочу есть")
    svc.schedule_followup(db, eid, minutes=-1)           # срок уже наступил
    due = repo.due_craving_followups(db)
    assert [r["id"] for r in due] == [eid]
    repo.update_craving(db, eid, followup_sent=1)
    assert repo.due_craving_followups(db) == []


def test_stale_episodes_dismissed(db):
    uid, user = _user(db)
    eid, _, _ = svc.start_episode(db, user, "хочу есть")
    old = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
    repo.update_craving(db, eid, started_utc=old)
    repo.dismiss_stale_cravings(db)
    # окно считается от последнего сообщения: свежая переписка держит эпизод открытым
    assert repo.get_craving(db, uid, eid)["outcome"] == "open"
    db.execute("UPDATE craving_messages SET created_utc=? WHERE event_id=?", (old, eid))
    repo.dismiss_stale_cravings(db)
    assert repo.get_craving(db, uid, eid)["outcome"] == "dismissed"


# ----------------------------------------------------------------- через роутеры

@pytest.mark.asyncio
async def test_router_command_and_buttons(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    before = len(session.names())
    await send(dp, bot, "/craving")
    assert len(session.names()) > before
    event = db.execute("SELECT * FROM craving_events WHERE user_id=?", (uid,)).fetchone()
    assert event and event["outcome"] == "open"


@pytest.mark.asyncio
async def test_router_strong_phrase_starts_episode_and_food_log_still_works(db, monkeypatch):
    from app.services import food as foodsvc
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "не могу остановиться, срываюсь")
    event = db.execute("SELECT * FROM craving_events WHERE user_id=?", (uid,)).fetchone()
    assert event and event["trigger_text"].startswith("не могу")

    # внутри открытого эпизода вода записывается как обычно, а не как «разговор»
    await send(dp, bot, "выпил стакан воды")
    assert repo.day_water_ml(db, uid, _today(db, uid)) == 250


def _today(db, uid):
    from app.timeutils import local_today
    tz = db.execute("SELECT tz FROM users WHERE id=?", (uid,)).fetchone()["tz"]
    return local_today(tz).isoformat()


@pytest.mark.asyncio
async def test_commands_pass_through_open_episode(db):
    """Пока открыт эпизод тяги, явные команды словами не должны уходить в диалог поддержки."""
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    user = repo.get_user_by_tg(db, 1)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    session.texts.clear()
    session.rich.clear()
    await send(dp, bot, "что по статистике")
    assert session.rich and "Сегодня" in session.rich[-1]            # итоги дня таблицей
    assert repo.open_craving(db, uid, dom.EPISODE_WINDOW_MIN)["id"] == event_id   # эпизод жив
