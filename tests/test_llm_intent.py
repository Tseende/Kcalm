"""Намерение, которое определяет модель при разборе текста (один вызов вместо двух)."""
from __future__ import annotations

import os
import tempfile

import pytest

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


def _meal() -> IngredientEstimate:
    return IngredientEstimate(name="buckwheat", name_ru="гречка", grams_min=135, grams_max=165,
                              explicit_grams=150, confidence="high")


def _fake_parse(intent: str, arg: str = "", items: list | None = None):
    async def fake(text, user_id, conn):  # noqa: ANN001
        return MealUnderstanding(items=items or [], intent=intent, intent_arg=arg,
                                 overall_confidence=0.9)
    return fake


async def _bot(db, monkeypatch, parse):
    monkeypatch.setattr(foodsvc, "parse_text", parse)
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    return bot, dp, session, uid


# ------------------------------------------------------------------ разбор ответа

def test_understanding_reads_intent_and_ignores_unknown():
    u = foodsvc.understanding_from_json({"intent": "week", "intent_arg": " x ", "items": []})
    assert (u.intent, u.intent_arg) == ("week", "x")
    assert foodsvc.understanding_from_json({"intent": "format_disk"}).intent == "food"
    assert foodsvc.understanding_from_json({"items": []}).intent == "food"


@pytest.mark.parametrize("name, arg, expected", [
    ("week", "", ("week", "")),
    ("undo", "что угодно", ("undo", "")),
    ("weighin", "07:30 пн ср", ("weighin", "07:30 пн ср")),
    ("weighin", "off", ("weighin", "off")),
    ("weighin", "7.30", ("weighin", "7:30")),
    ("weighin", "удали всё", None),
    ("plan_add", "если скучно, то выйду погулять", ("plan_add", "если скучно, то выйду погулять")),
    ("plan_add", "бросить сладкое", None),
    ("food_info", "гречка", ("food_info", "гречка")),
    ("food_info", "", None),
    ("question", "", None),
    ("craving", "", None),
    ("drop_table", "", None),
])
def test_from_llm_validates_arguments(name, arg, expected):
    got = intents.from_llm(name, arg)
    assert (None if got is None else (got.name, got.arg)) == expected


# ------------------------------------------------------------------ в боте целиком

@pytest.mark.asyncio
async def test_week_by_model_intent(db, monkeypatch):
    bot, dp, session, uid = await _bot(db, monkeypatch, _fake_parse("week"))
    session.texts.clear()
    await send(dp, bot, "ну чё там по итогам семи дней")
    assert any("риложени" in t for t in session.texts), session.texts      # отчёт теперь в Mini App
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 0


@pytest.mark.asyncio
async def test_undo_by_model_intent_even_when_food_named(db, monkeypatch):
    bot, dp, session, uid = await _bot(db, monkeypatch, _fake_parse("food", items=[_meal()]))
    await send(dp, bot, "гречка 150 г")
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=? AND deleted_at IS NULL",
                      (uid,)).fetchone()["c"] == 1
    monkeypatch.setattr(foodsvc, "parse_text", _fake_parse("undo", items=[_meal()]))
    await send(dp, bot, "убери ту гречку из дневника")
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=? AND deleted_at IS NULL",
                      (uid,)).fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 1


@pytest.mark.asyncio
async def test_other_intent_with_food_items_stays_food(db, monkeypatch):
    bot, dp, session, uid = await _bot(db, monkeypatch, _fake_parse("week", items=[_meal()]))
    await send(dp, bot, "гречка 150 г на этой неделе постоянно")
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 1


@pytest.mark.asyncio
async def test_weighin_and_rule_from_model(db, monkeypatch):
    bot, dp, session, uid = await _bot(db, monkeypatch, _fake_parse("weighin", "06:40 пн ср пт"))
    await send(dp, bot, "будь добр, подталкивай меня на весы по утрам")
    row = repo.due_reminders(db, uid, "weigh")
    assert row["local_time"] == "06:40"

    monkeypatch.setattr(foodsvc, "parse_text",
                        _fake_parse("plan_add", "если устал, то лягу спать"))
    await send(dp, bot, "пусть у меня будет такая страховка: при усталости ложиться спать")
    assert [r["then_text"] for r in repo.list_rules(db, uid)] == ["лягу спать"]


@pytest.mark.asyncio
async def test_bad_argument_goes_to_assistant_not_action(db, monkeypatch):
    async def fake_llm(purpose, messages, **kw):
        return {"reply": "Не понял настройку. Давай проще: «напомни взвеситься в 7:00»."}, "m"
    monkeypatch.setattr(assistant.llm, "chat_json", fake_llm)
    bot, dp, session, uid = await _bot(db, monkeypatch, _fake_parse("plan_add", "бросить сладкое"))
    await send(dp, bot, "запомни это на будущее пожалуйста")
    assert repo.list_rules(db, uid) == []
    assert any("Не понял настройку" in t for t in session.texts)


@pytest.mark.asyncio
async def test_craving_by_model_intent_starts_support(db, monkeypatch):
    bot, dp, session, uid = await _bot(db, monkeypatch, _fake_parse("craving"))
    await send(dp, bot, "ощущение что сейчас всё сметём подчистую")
    assert db.execute("SELECT COUNT(*) c FROM craving_events WHERE user_id=?",
                      (uid,)).fetchone()["c"] == 1


@pytest.mark.asyncio
async def test_question_and_chat_go_to_assistant(db, monkeypatch):
    calls = []

    async def fake_llm(purpose, messages, **kw):
        calls.append(purpose)
        return {"reply": "Понял тебя, всё хорошо идёт."}, "m"
    monkeypatch.setattr(assistant.llm, "chat_json", fake_llm)
    bot, dp, session, uid = await _bot(db, monkeypatch, _fake_parse("chat"))
    session.texts.clear()
    await send(dp, bot, "вот такие дела")
    assert calls == ["assistant"]
    assert any("Понял тебя" in t for t in session.texts)
