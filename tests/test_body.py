"""Состав тела с весов: разбор, команда /body, фразы и реплики вместо «не увидел еды»."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.db import connect, migrate
from app.domain import body, intents
from app.domain.nutrition import MealUnderstanding
from app.services import assistant
from app.services import food as foodsvc
from app.storage import repo
from tests.test_routers_smoke import build, onboard, send

LATEST = {"weight_kg": 112.6, "bmi": 36.9, "body_fat_pct": 36.9, "muscle_mass_pct": 59.7,
          "visceral_fat": 18, "body_water_pct": 45.6, "bmr_kcal": 2067, "metabolic_age": 35,
          "body_score": 2.3, "resting_hr": 95}
PREVIOUS = {"weight_kg": 114.2, "body_fat_pct": 37.5, "muscle_mass_pct": 59.0,
            "visceral_fat": 18, "body_water_pct": 45.0}


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def test_categories():
    assert body.bmi_category(36.9) == "ожирение II степени" and body.bmi_category(22) == "норма"
    assert body.body_fat_category(36.9, "male") == "высокий"
    assert body.body_fat_category(18, "male") == "в пределах нормы"
    assert body.body_fat_category(30, "female") == "в пределах нормы"
    assert body.visceral_category(8) == "в пределах нормы" and body.visceral_category(18) == "высокий"


def test_deltas_show_only_stable_metrics_without_judgement():
    d = body.deltas(LATEST, PREVIOUS)
    assert "Вес: -1.6 кг" in d and "Жир: -0.6 %" in d and "Мышцы: +0.7 %" in d
    assert "Висцеральный жир: без изменений" in d
    assert not any("пульс" in x.lower() for x in d)
    assert body.deltas(LATEST, None) == []


def test_summary_is_supportive_and_honest_about_accuracy():
    text = "\n".join(body.summary(LATEST, PREVIOUS, "male", 30))
    assert "36.9" in text and "динамику за 4 недели" in text and "3–5 %" in text
    assert "не медицинский показатель" in text            # «Оценка» 2.3 не приговор
    assert "Пульс с весов измеряется грубо" in text
    assert "врачу" in text                                   # ИМТ ≥ 35: обычный чек-ап
    for bad in ("ужас", "плохо", "позор", "жирдяй"):
        assert bad not in text.lower()


def test_intents_for_body_phrases():
    for phrase in ("мои показатели", "покажи состав тела", "какой у меня процент жира",
                   "результаты взвешивания", "что там по висцеральному жиру",
                   "показатели с весов"):
        got = intents.detect(phrase)
        assert got is not None and got.name == "body", phrase
    assert intents.detect("показатель плохой") is None        # реплика → ассистенту


def _measure(db, uid, date, **extra):
    metrics = {"weight_kg": 112.6, "bmi": 36.9, "body_fat_pct": 36.9, "muscle_mass_pct": 59.7,
               "visceral_fat": 18, "body_water_pct": 45.6, "fasting": 0}
    metrics.update(extra)
    repo.save_body_measurement(db, uid, date, metrics, "screenshot", 0.95)


@pytest.mark.asyncio
async def test_body_command_and_care_mode(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "/body")
    assert any("Пока нет замеров" in t for t in session.texts)

    _measure(db, uid, "2026-10-29", weight_kg=114.2, body_fat_pct=37.5)
    _measure(db, uid, "2026-10-30")
    session.texts.clear()
    await send(dp, bot, "/body")
    text = " ".join(session.texts)
    assert "Состав тела" in text and "112.6" in text and "Δ" in text
    assert "не натощак" in text                                # fasting=0 пояснён

    repo.update_user(db, uid, care_mode=1)
    session.texts.clear()
    await send(dp, bot, "/body")
    assert "не показываю цифры" in " ".join(session.texts)


@pytest.mark.asyncio
async def test_remark_goes_to_assistant_not_food_error(db, monkeypatch):
    async def no_items(text, user_id, conn):
        return MealUnderstanding(items=[], overall_confidence=0.1)
    monkeypatch.setattr(foodsvc, "parse_text", no_items)
    seen = {}

    async def fake_llm(purpose, messages, **kw):
        seen["ctx"] = messages[-1]["content"]
        return {"reply": "Понимаю, цифры расстраивают. Один замер не приговор: смотрим на динамику."}, "m"
    monkeypatch.setattr(assistant.llm, "chat_json", fake_llm)

    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    _measure(db, uid, "2026-10-30")
    session.texts.clear()
    await send(dp, bot, "ну показатель плохой")
    assert any("Один замер не приговор" in t for t in session.texts)
    assert not any("Не увидел еды" in t for t in session.texts)
    assert "body_composition" in seen["ctx"] and "36.9" in seen["ctx"]


@pytest.mark.asyncio
async def test_assistant_hides_body_data_in_care_mode(db, monkeypatch):
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="male", onboarded=1, care_mode=1)
    _measure(db, uid, "2026-10-30")
    ctx = assistant.snapshot(db, repo.get_user_by_tg(db, 1))
    assert ctx["body_composition"] is None
