"""Безопасность цели, экранирование HTML, сбои LLM и доступ к Picooc."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

from app.config import Config, get_config, set_config
from app.db import connect, migrate
from app.domain.nutrition import (IngredientEstimate, MealCalculation, MealUnderstanding,
                                  calculate_meal, FoodRef)
from app.llm.client import LLMError
from app.services import food as foodsvc
from app.storage import repo


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


@pytest.fixture
def restore_config():
    old = get_config()
    yield
    set_config(old)


def _understanding(name: str, name_ru: str | None = None) -> MealUnderstanding:
    return MealUnderstanding(items=[IngredientEstimate(
        name=name, name_ru=name_ru or name, grams_min=100, grams_max=200)])


# ------------------------------------------------------------------ HTML

def test_card_escapes_llm_text():
    ref = FoodRef(name_ru="x", name_en="x", kcal_100g=100, protein_100g=1,
                  fat_100g=1, carbs_100g=10, source="seed")
    item = IngredientEstimate(name="soup", name_ru="Суп <b>&", grams_min=100, grams_max=100)
    calc = calculate_meal([(item, ref)])
    card = foodsvc.format_card("12:00", "Обед", calc, 100, 2000, 1, 100,
                               late=False, late_kcal=0)
    for rendered in (card.rich(), card.plain()):
        assert "Суп &lt;b&gt;&amp;" in rendered
        assert "<b>&" not in rendered


# ------------------------------------------------------------ сбои LLM

@pytest.mark.asyncio
async def test_llm_error_is_flagged_not_silent(db, monkeypatch):
    async def boom(*a, **k):
        raise LLMError("нет сети")
    monkeypatch.setattr(foodsvc.llm, "chat_json", boom)
    uid = repo.create_user(db, 1)
    calc = await foodsvc.build_calc(_understanding("zzqq", "Зюзя"), db, uid)
    assert calc.kcal_total == 0
    assert any("Зюзя" in f for f in calc.quality_flags)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"kcal_100g": None, "protein_100g": 1, "fat_100g": 1, "carbs_100g": 1},
    {"kcal_100g": "много", "protein_100g": 1, "fat_100g": 1, "carbs_100g": 1},
    {"kcal_100g": 5000, "protein_100g": 1, "fat_100g": 1, "carbs_100g": 1},
    {"kcal_100g": 100, "protein_100g": 90, "fat_100g": 90, "carbs_100g": 90},
])
async def test_garbage_nutrition_from_llm_does_not_crash(db, monkeypatch, payload):
    async def fake(*a, **k):
        return payload, "model"
    monkeypatch.setattr(foodsvc.llm, "chat_json", fake)
    uid = repo.create_user(db, 1)
    calc = await foodsvc.build_calc(_understanding("zzqq", "Зюзя"), db, uid)
    assert calc.kcal_total == 0 and calc.quality_flags


@pytest.mark.asyncio
async def test_zero_kcal_drinks_are_not_flagged(db, monkeypatch):
    async def boom(*a, **k):
        raise LLMError("нет сети")
    monkeypatch.setattr(foodsvc.llm, "chat_json", boom)
    uid = repo.create_user(db, 1)
    # «вода» может найтись в базе; проверяем заглушку напрямую
    ref = foodsvc._empty_ref(IngredientEstimate(
        name="water", name_ru="вода", grams_min=200, grams_max=300))
    assert ref.unresolved is False


# ------------------------------------------------------------- Picooc

def test_picooc_owner_resolution(restore_config):
    set_config(Config(_env_file=None, allowed_tg_ids="7"))
    assert get_config().picooc_owner_id == 7
    set_config(Config(_env_file=None, allowed_tg_ids="7,8"))
    assert get_config().picooc_owner_id is None
    set_config(Config(_env_file=None, allowed_tg_ids="7,8", picooc_owner_tg_id=8))
    assert get_config().picooc_owner_id == 8
    set_config(Config(_env_file=None, allow_public_bot=True))
    assert get_config().picooc_owner_id is None


@pytest.mark.asyncio
async def test_stranger_cannot_sync_owners_scale(db, restore_config):
    from app.adapters.picooc import PicoocError
    from app.services.scale import sync_picooc
    set_config(Config(_env_file=None, allowed_tg_ids="1,2", picooc_owner_tg_id=1,
                      picooc_user="u", picooc_password="p"))
    repo.create_user(db, 2, tz="Europe/Minsk")
    stranger = repo.get_user_by_tg(db, 2)
    with pytest.raises(PicoocError, match="владельца"):
        await sync_picooc(db, stranger)


# --------------------------------------------------- цель и онбординг

def _adaptive_user(db, weight=85.0, height=180, birth="1990-01-01", sex="male"):
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    repo.update_user(db, uid, sex=sex, birth_date=birth, height_cm=height,
                     start_weight_kg=weight, goal_weight_kg=weight - 10,
                     target_rate_kg_week=-0.5, activity_pa=1.12, onboarded=1)
    repo.insert_target(db, uid, "2026-10-01", 1500, 136.0, 68.0, 100.0, 2000, "onboarding")
    return uid


def _fill_flat_weight_and_light_meals(db, uid, today: date, kcal_per_day=1400):
    half = kcal_per_day // 2
    calc = MealCalculation(items=[], kcal_total=half, kcal_low=half, kcal_high=half,
                           protein_g=10, fat_g=10, carbs_g=10, method="db", confidence=0.9)
    for i in range(13):
        day = (today - timedelta(days=i)).isoformat()
        repo.save_body_measurement(db, uid, day, {"weight_kg": 85.0}, "manual", 0.9)
        for t in ("09:00", "19:00"):
            repo.save_meal(db, uid, f"{day} {t}:00", day, t, "lunch", "text", calc)


def test_adaptive_target_never_drops_below_floor(db):
    from app.routers.commands import weekly_report
    uid = _adaptive_user(db)
    today = date(2026, 10, 25)
    _fill_flat_weight_and_light_meals(db, uid, today)
    text = weekly_report(db, uid, today=today)
    row = repo.current_target(db, uid)
    # BMR мужчины 36 лет, 85 кг, 180 см ≈ 1800 → пол 1.1×BMR ≈ 1980
    assert row["kcal_target"] >= 1500 * 1.3, (row["kcal_target"], text)
    assert row["reason"].endswith("_floor")
    assert "безопасного минимума" in text


def test_onboarding_underweight_gets_no_deficit(db):
    from app.routers.onboarding import _finish
    data = {"sex": "ж", "age": 30, "height": 170, "weight": 48, "rate": "0.5",
            "cutoff": "21:00", "activity": "2", "tz": "Europe/Minsk"}
    uid, kcal, note = _finish(db, 5, data)
    user = repo.get_user_by_tg(db, 5)
    assert user["target_rate_kg_week"] == 0
    assert user["goal_weight_kg"] == 48
    assert "ИМТ" in note


def test_onboarding_goal_weight_not_below_bmi_185(db):
    from app.routers.onboarding import _finish
    data = {"sex": "ж", "age": 30, "height": 170, "weight": 56, "rate": "0.7",
            "cutoff": "21:00", "activity": "2", "tz": "Europe/Minsk"}
    _finish(db, 6, data)
    user = repo.get_user_by_tg(db, 6)
    assert user["goal_weight_kg"] == 53.5
