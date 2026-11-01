"""Агентный слой: команды словами, вопросы ассистенту, меню команд."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.bot import BOT_COMMANDS
from app.db import connect, migrate
from app.domain import intents
from app.domain.nutrition import IngredientEstimate, MealUnderstanding
from app.services import assistant
from app.services import food as foodsvc
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


# ------------------------------------------------------------------ распознавание

@pytest.mark.parametrize("text, name, arg", [
    ("что я сегодня ел?", "today", ""),
    ("что по статистике", "today", ""),
    ("а статистика потребления за день", "today", ""),
    ("статистика за неделю", "week", ""),
    ("статистика сна", "sleep", ""),
    ("статистика по весу", "weight", ""),
    ("сколько мне надо есть в день", "target", ""),
    ("какой у меня лимит на день", "target", ""),
    ("сколько жидкости за день", "water_status", ""),
    ("сон за две недели покажи", "sleep", ""),
    ("сколько калорий сегодня", "today", ""),
    ("как прошла неделя", "week", ""),
    ("как вес?", "weight", ""),
    ("как я спал", "sleep", ""),
    ("какая моя цель", "target", ""),
    ("сколько мне можно калорий", "target", ""),
    ("отмени последнее", "undo", ""),
    ("сколько воды я выпил", "water_status", ""),
    ("мои правила", "plan_list", ""),
    ("мои частые продукты", "foods", ""),
    ("включи осторожный режим", "care_on", ""),
    ("выключи осторожный режим", "care_off", ""),
    ("напомни взвеситься в 7:00", "weighin", "07:00"),
    ("не напоминай про вес", "weighin", "off"),
    ("напоминай взвешиваться по пн ср пт в 7", "weighin", "07:00 пн ср пт"),
    ("добавь правило: если скучно, то прогуляюсь", "plan_add", "если скучно, то прогуляюсь"),
    ("что ты умеешь", "help", ""),
    ("привет", "greeting", ""),
])
def test_detect_intents(text, name, arg):
    got = intents.detect(text)
    assert got is not None and got.name == name and got.arg == arg


@pytest.mark.parametrize("text", [
    "борщ и котлета", "съел борщ сегодня", "гречка 150 г", "две котлеты и гречка",
    "почему вес стоит?", "что съесть на ужин?", "как мне не срываться вечером?",
    "выпил 2 стакана воды",
])
def test_food_and_open_questions_are_not_commands(text):
    assert intents.detect(text) is None


def test_question_detection():
    assert intents.is_question("что съесть на ужин?")
    assert intents.is_question("почему вес стоит")
    assert not intents.is_question("борщ и котлета")
    assert not intents.is_question("съел котлету, нормально?")      # запись еды важнее «?»


# ---------------------------------------------------------------- в боте целиком

async def _setup(db, monkeypatch, fake_meal=True):
    async def fake_parse(text, user_id, conn):
        return MealUnderstanding(items=[IngredientEstimate(
            name="buckwheat", name_ru="гречка", grams_min=135, grams_max=165,
            explicit_grams=150, confidence="high")], overall_confidence=0.9)
    monkeypatch.setattr(foodsvc, "parse_text", fake_parse)
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    return bot, dp, session, uid


@pytest.mark.asyncio
async def test_commands_work_without_slash(db, monkeypatch):
    async def no_llm(*a, **k):
        raise AssertionError("команды словами не должны идти в LLM")
    monkeypatch.setattr(assistant.llm, "chat_json", no_llm)
    bot, dp, session, uid = await _setup(db, monkeypatch)

    await send(dp, bot, "гречка 150 г")
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 1

    session.texts.clear()
    await send(dp, bot, "что я сегодня ел?")
    assert any("Сегодня" in t and "ккал" in t for t in session.texts + session.rich)

    await send(dp, bot, "напомни взвеситься в 6:45")
    assert repo.due_reminders(db, uid, "weigh")["local_time"] == "06:45"

    await send(dp, bot, "добавь правило: если скучно, то прогуляюсь")
    assert [r["then_text"] for r in repo.list_rules(db, uid)] == ["прогуляюсь"]

    await send(dp, bot, "включи осторожный режим")
    assert repo.get_user_by_tg(db, 1)["care_mode"] == 1

    session.texts.clear()
    await send(dp, bot, "сколько калорий в гречке?")
    assert any("110 ккал" in t for t in session.texts), (session.texts, session.names()[-6:])

    await send(dp, bot, "отмени последнее")
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=? AND deleted_at IS NULL",
                      (uid,)).fetchone()["c"] == 0


@pytest.mark.asyncio
async def test_food_text_still_logged_and_greeting_not_food(db, monkeypatch):
    bot, dp, session, uid = await _setup(db, monkeypatch)
    await send(dp, bot, "борщ и котлета")
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 1
    await send(dp, bot, "привет")
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 1


@pytest.mark.asyncio
async def test_question_goes_to_assistant_with_real_context(db, monkeypatch):
    seen = {}

    async def fake_llm(purpose, messages, **kw):
        seen["purpose"] = purpose
        seen["ctx"] = messages[-1]["content"]
        return {"reply": "Сегодня у тебя ещё есть запас: возьми творог и воды. Подожди 15 минут."}, "m"
    monkeypatch.setattr(assistant.llm, "chat_json", fake_llm)
    bot, dp, session, uid = await _setup(db, monkeypatch)
    await send(dp, bot, "гречка 150 г")
    session.texts.clear()
    await send(dp, bot, "что съесть на ужин?")
    assert seen["purpose"] == "assistant"
    assert "kcal_left" in seen["ctx"] and "гречка" in seen["ctx"].lower()
    assert any("творог" in t for t in session.texts)
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 1


@pytest.mark.asyncio
async def test_assistant_rejects_invented_numbers_and_shame(db):
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="male", onboarded=1)
    user = repo.get_user_by_tg(db, 1)

    async def fake(reply):
        async def _f(*a, **k):
            return {"reply": reply}, "m"
        return _f
    for bad in ("В авокадо 9999 ккал", "Ты слабак, завтра пропусти завтрак"):
        assistant.llm.chat_json, orig = await fake(bad), assistant.llm.chat_json
        try:
            text = await assistant.answer(db, user, "что съесть?")
        finally:
            assistant.llm.chat_json = orig
        assert "9999" not in text and "слабак" not in text and "не получается" in text


@pytest.mark.asyncio
async def test_risky_question_gets_safety_text_without_llm(db, monkeypatch):
    async def no_llm(*a, **k):
        raise AssertionError("в опасной теме LLM не вызываем")
    monkeypatch.setattr(assistant.llm, "chat_json", no_llm)
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="male", onboarded=1)
    user = repo.get_user_by_tg(db, 1)
    text = await assistant.answer(db, user, "как мне вызвать рвоту после еды?")
    assert "врач" in text


@pytest.mark.asyncio
async def test_assistant_adds_specialist_note_when_flags(db, monkeypatch):
    from datetime import date
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="female", onboarded=1)
    eid = repo.create_craving(db, uid, date.today().isoformat(), "23:00", "x", 0, 1500, risk_flag=1)
    repo.close_craving(db, eid, "dismissed")

    async def ok(*a, **k):
        return {"reply": "Давай по шагам: вода и 10 минут отдыха."}, "m"
    monkeypatch.setattr(assistant.llm, "chat_json", ok)
    user = repo.get_user_by_tg(db, 1)
    assert "врачом или психологом" in await assistant.answer(db, user, "как мне держаться?")


def test_assistant_fallback_when_llm_down(db, monkeypatch):
    import asyncio
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="male", onboarded=1)
    user = repo.get_user_by_tg(db, 1)

    async def down(*a, **k):
        raise assistant.llm.LLMError("нет сети")
    monkeypatch.setattr(assistant.llm, "chat_json", down)
    assert "не получается" in asyncio.run(assistant.answer(db, user, "что съесть?"))


def test_food_info_handles_cases_and_missing(db):
    assert "110 ккал" in assistant.food_info(db, "гречке")
    assert "Не нашёл" in assistant.food_info(db, "драконьем фрукте")
    assert "110 ккал" in assistant.food_info(db, "гречке 150 г")


# ------------------------------------------------------------------------ меню

def test_command_menu_is_valid():
    names = [c.command for c in BOT_COMMANDS]
    assert len(names) == len(set(names)) <= 100
    for c in BOT_COMMANDS:
        assert c.command == c.command.lower() and 1 <= len(c.command) <= 32
        assert 3 <= len(c.description) <= 256
    for must in ("today", "week", "weight", "water", "craving", "plan", "weighin", "care", "undo"):
        assert must in names


@pytest.mark.asyncio
async def test_rate_limit_counts_each_message_once(db, monkeypatch):
    """Сообщение, «провалившееся» через craving-роутер в intake, не должно считаться дважды."""
    bot, dp, session, uid = await _setup(db, monkeypatch)
    middleware = dp.message.outer_middleware._middlewares[0]
    before = len(middleware._hits[1])
    for _ in range(5):
        await send(dp, bot, "гречка 150 г")
    assert len(middleware._hits[1]) - before == 5
