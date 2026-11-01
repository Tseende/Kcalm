"""Юнит-тесты домена (без сети, без ключей). Запуск: .venv/bin/pytest -q"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app.domain import analytics, matching, targets
from app.domain.nutrition import (FoodRef, IngredientEstimate, calculate_item,
                                  calculate_meal)
from app.db import connect, migrate
from app.llm.client import LLMError, extract_json
from app.storage import repo


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ---------------------------------------------------------------- парсер JSON

def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```')["a"] == 1


def test_extract_json_dirty():
    assert extract_json('Ответ: {"a": {"b": "}"}} всё')["a"]["b"] == "}"


def test_extract_json_truncated():
    assert extract_json('{"a": 1, "b": [1, 2')["a"] == 1
    assert extract_json('{"x": "обрезан') == {"x": "обрезан"}


def test_extract_json_no_json():
    with pytest.raises(LLMError):
        extract_json("просто текст")


# --------------------------------------------------------------- калькулятор

def test_range_golden():
    ref = FoodRef(name_ru="x", name_en="x", kcal_100g=200, protein_100g=10,
                  fat_100g=5, carbs_100g=20, source="seed")
    ci = calculate_item(IngredientEstimate(name="x", grams_min=120, grams_max=180), ref)
    assert (ci.kcal_low, ci.kcal_high) == (240, 360)


def test_validator_fixes_reversed_range():
    it = IngredientEstimate(name="x", grams_min=300, grams_max=100)
    assert it.grams_min < it.grams_max


def test_zero_range_rejected():
    with pytest.raises(Exception):
        IngredientEstimate(name="x", grams_min=0, grams_max=0)


def test_sanitary_macro_density():
    items = []
    for i in range(2):
        it = IngredientEstimate(name=f"f{i}", grams_min=100, grams_max=100)
        ref = FoodRef(name_ru="жир", name_en="fat", kcal_100g=900, protein_100g=1,
                      fat_100g=99, carbs_100g=0, source="seed")
        items.append((it, ref))
    meal = calculate_meal(items)
    assert any("завышен" in f for f in meal.quality_flags)


def test_llm_prior_method():
    it = IngredientEstimate(name="x", grams_min=100, grams_max=200)
    ref = FoodRef(name_ru="x", name_en="x", kcal_100g=250, protein_100g=10,
                  fat_100g=15, carbs_100g=20, source="llm")
    meal = calculate_meal([(it, ref)])
    assert meal.method == "llm_prior"


def test_recalculate_from_snapshot():
    k, p, f, c = __import__("app.domain.nutrition", fromlist=["recalculate_from_snapshot"]) \
        .recalculate_from_snapshot([
            {"grams": 150, "kcal_100g": 110, "protein_100g": 4, "fat_100g": 1, "carbs_100g": 21}
        ])
    assert k in (160, 170) and p == 6.0   # банковское округление 165 → 160


# ------------------------------------------------------------------- цели

def test_tee_iom_golden():
    assert targets.tee_iom("male", 35, 92.4, 1.78, 1.27) == 3330
    assert targets.tee_iom("female", 35, 70.0, 1.65, 1.27) > 0


def test_deficit_and_taper():
    full = targets.compute_target("male", 35, 92.4, 178, 1.75, -0.5, goal_weight_kg=80)
    assert full.taper_factor == 1.0
    mid = targets.compute_target("male", 35, 88, 178, 1.75, -0.5, goal_weight_kg=85)
    assert mid.taper_factor == 0.5
    zero = targets.compute_target("male", 35, 85.5, 178, 1.75, -0.5, goal_weight_kg=85)
    assert zero.taper_factor == 0.0
    below = targets.compute_target("male", 35, 84, 178, 1.75, -0.5, goal_weight_kg=85)
    assert below.taper_factor == 0.0


def test_floor_male_1500():
    t = targets.compute_target("male", 35, 60, 170, 1.0, -0.7)
    assert t.kcal_target >= 1500 and t.floor_applied


def test_no_deficit_for_minors():
    kept = targets.compute_target("male", 16, 80, 175, 1.5, -0.7)
    maintenance = targets.compute_target("male", 16, 80, 175, 1.5, 0.0)
    assert kept.kcal_target == maintenance.kcal_target


def test_no_deficit_when_bmi_below_normal():
    low = targets.compute_target("female", 30, 48, 170, 1.5, -0.5)   # ИМТ 16.6
    maintenance = targets.compute_target("female", 30, 48, 170, 1.5, 0.0)
    assert low.kcal_target == maintenance.kcal_target


def test_pal_from_pa_roundtrip():
    for pal in (1.25, 1.5, 1.75, 2.2):
        pa = targets.pa_from_pal(pal)
        assert targets.pal_from_pa(pa) == pal
    assert targets.pal_from_pa(None) == 1.5


def test_min_goal_weight():
    assert targets.min_goal_weight(170) == 53.5


def test_macros_on_goal_weight():
    t = targets.compute_target("male", 35, 90, 178, 1.75, -0.5, goal_weight_kg=80)
    assert t.protein_g == 128.0 and t.fat_g >= 64.0


def test_adaptive_tdee_synthetic():
    days = [float(i) for i in range(14)]
    weights = [100 - 0.06 * d for d in days]
    intake = {d: 2100 for d in days}
    res = targets.adaptive_tdee(list(zip(days, weights)), intake, 2100)
    assert res.tdee_est and abs(res.tdee_est - 2560) <= 130


def test_adaptive_tdee_cap_150():
    days = [float(i) for i in range(14)]
    weights = [100 - 0.06 * d for d in days]
    intake = {d: 2100 for d in days}
    res = targets.adaptive_tdee(list(zip(days, weights)), intake, 1500)
    assert abs(res.new_target - 1500) <= 150


def test_adaptive_tdee_not_enough_data():
    res = targets.adaptive_tdee([(0, 100), (1, 99)], {}, 2100)
    assert res.new_target is None and res.need_more_weighs


def test_ewma_and_ols_gaps():
    s = targets.ewma([100, 101, 100.5])
    assert abs(s[-1] - 100.2) < 0.05
    assert targets.ols_slope([0, 3, 7, 12], [100, 99.5, 98.8, 98.0]) < 0


# ---------------------------------------------------------------- матчинг

def test_matching_exact_and_synonym(db):
    ref = matching.find_food(db, "buckwheat", "гречка")
    assert ref and "Гречка" in ref.name_ru
    ref2 = matching.find_food(db, "chicken breast", "куриная грудка жареная")
    assert ref2 and ref2.kcal_100g == 165


def test_matching_user_food_priority(db):
    uid = repo.create_user(db, 1)
    fid = repo.upsert_food(db, "мой творог", "my cottage cheese", 99, 18, 5, 3,
                           "user", "u:custom1")
    repo.add_alias(db, uid, fid, "мой творог")
    ref = matching.find_food(db, "my cottage cheese", "мой творог", user_id=uid)
    assert ref and ref.source == "user" and ref.kcal_100g == 99


def test_matching_miss_gives_none(db):
    assert matching.find_food(db, "chalice", "чаша грааля") is None


def test_matching_scores_not_first_result(db):
    # «котлета куриная» должна дать куриную котлету, а не свино-говяжью
    ref = matching.find_food(db, "chicken cutlet", "котлета куриная")
    assert ref and "куриная" in ref.name_ru.lower()


# ---------------------------------------------------------------- аналитика

def test_is_late_boundary():
    assert not analytics.is_late("21:00", "21:00")
    assert analytics.is_late("21:01", "21:00")


def test_sleep_food_thresholds():
    pairs = [analytics.SleepFoodPair(f"d{i}", 6.0, 2200, 0) for i in range(10)] + \
            [analytics.SleepFoodPair(f"e{i}", 7.5, 2000, 0) for i in range(10)]
    assert analytics.sleep_food_insight(pairs)["diff_kcal"] == 200
    assert analytics.sleep_food_insight(pairs[:18]) is None
    small = [analytics.SleepFoodPair(f"d{i}", 6.0, 2000, 0) for i in range(10)] + \
            [analytics.SleepFoodPair(f"e{i}", 7.5, 1920, 0) for i in range(10)]
    assert analytics.sleep_food_insight(small) is None


def test_plateau_threshold():
    assert analytics.plateau_check(0.05, 300).is_plateau
    assert not analytics.plateau_check(0.15, 300).is_plateau
    assert not analytics.plateau_check(0.05, 150).is_plateau


def test_compare_late_weeks_threshold():
    w = lambda late: {"late_evenings": late, "avg_late_kcal": 300, "late_share": 0.1}
    assert analytics.compare_late_weeks([w(0)] * 2 + [w(5)] * 3) is None
    assert analytics.compare_late_weeks([w(0)] * 3 + [w(5)] * 3) is not None


# ------------------------------------------------------------- скриншоты

def test_screenshot_mapping_nulls(db):
    from app.services import screens
    uid = repo.create_user(db, 5)
    parsed = {"screen": "picooc_measure", "date": "2026-10-28",
              "metrics": {"weight_kg": {"value": 90.0}, "bmi": {"value": 28.1}},
              "missing": ["body_fat_pct"], "overall_confidence": 0.95}
    assert screens.apply_parsed(db, uid, parsed, "2026-10-28") == "body"
    row = db.execute("SELECT * FROM body_measurements").fetchone()
    assert row["weight_kg"] == 90.0 and row["body_fat_pct"] is None


def test_sane_date_guards_ocr_year():
    """Год, перепутанный vision-моделью, не должен утащить замер в прошлое."""
    from datetime import date

    from app.services.screens import sane_date
    today = date(2026, 10, 28)
    assert sane_date("2026-10-28", today) == ("2026-10-28", True)
    assert sane_date("2026-10-20", today) == ("2026-10-20", True)
    assert sane_date("2024-10-28", today) == ("2026-10-28", False), "год перепутан"
    assert sane_date("2027-03-01", today) == ("2026-10-28", False), "будущее"
    assert sane_date("10/28/2026", today) == ("2026-10-28", False), "не ISO"
    assert sane_date(None, today) == ("2026-10-28", False)
    assert sane_date("2026-08-01", today, max_age_days=90) == ("2026-08-01", True)
    assert sane_date("2026-06-01", today, max_age_days=90) == ("2026-10-28", False), \
        "старше 90 дней — не доверяем, это скорее OCR, чем старый отчёт"


def test_clean_parsed_removes_missing_duplicates():
    """Модель дублирует найденные метрики в missing — приводим к согласию."""
    from app.services.screens import clean_parsed
    data = {"metrics": {"weight_kg": {"value": 111.0}, "bmi": {"value": 36.2}},
            "missing": ["weight_kg", "bmi", "fat_mass_kg"]}
    cleaned = clean_parsed(data)
    assert cleaned["missing"] == ["fat_mass_kg"]
    assert cleaned["metrics"]["weight_kg"]["value"] == 111.0
    assert clean_parsed({}) == {}


def test_picooc_report_real_layout(db):
    """Реальный отчёт PICOOC: «Мышцы» — проценты, кг мышц на экране нет.

    Фикстура повторяет наш скриншот от 2026-10-28 почти дословно.
    """
    from app.services import screens
    uid = repo.create_user(db, 8)
    parsed = {
        "screen": "picooc_measure", "date": "2026-10-28",
        "metrics": {
            "weight_kg": {"value": 111.0}, "bmi": {"value": 36.2},
            "body_fat_pct": {"value": 35.9}, "muscle_mass_pct": {"value": 60.7},
            "skeletal_muscle_pct": {"value": 36.2}, "visceral_fat": {"value": 18},
            "bmr_kcal": {"value": 2049}, "protein_pct": {"value": 14.1},
            "body_water_pct": {"value": 46.6}, "bone_mass_kg": {"value": 3.8},
            "metabolic_age": {"value": 36}, "body_tone": {"value": 7.8},
            "body_score": {"value": 2.3}, "resting_hr": {"value": 91},
        },
        "missing": ["fat_mass_kg", "muscle_mass_kg", "subcutaneous_fat_pct"],
        "overall_confidence": 0.95,
    }
    assert screens.apply_parsed(db, uid, parsed, "2026-10-28") == "body"
    row = db.execute("SELECT * FROM body_measurements").fetchone()

    assert row["weight_kg"] == 111.0 and row["bmi"] == 36.2
    assert row["body_fat_pct"] == 35.9
    assert row["muscle_mass_pct"] == 60.7, "«Мышцы 60.7 %» — это доля"
    assert row["muscle_mass_kg"] is None, "проценты нельзя писать в поле кг"
    assert row["skeletal_muscle_pct"] == 36.2
    assert row["visceral_fat"] == 18 and row["bmr_kcal"] == 2049
    assert row["protein_pct"] == 14.1 and row["body_water_pct"] == 46.6
    assert row["bone_mass_kg"] == 3.8 and row["metabolic_age"] == 36
    assert row["body_tone"] == 7.8 and row["body_score"] == 2.3
    assert row["resting_hr"] == 91
    assert row["fat_mass_kg"] is None and row["subcutaneous_fat_pct"] is None


def test_sleep_wake_date_key(db):
    from app.services import screens
    uid = repo.create_user(db, 6)
    parsed = {"screen": "mifitness_sleep", "date": "2026-10-27",
              "metrics": {"sleep_duration_min": {"value": 420}},
              "missing": [], "overall_confidence": 0.9}
    assert screens.apply_parsed(db, uid, parsed, "2026-10-28") == "sleep"
    row = db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert row["wake_date"] == "2026-10-27"


def test_unknown_screen_is_never_saved(db):
    from app.services import screens
    uid = repo.create_user(db, 7)
    assert screens.apply_parsed(db, uid, {"screen": "unknown", "metrics": {}}, "d") == "unknown"
    assert db.execute("SELECT COUNT(*) c FROM body_measurements").fetchone()["c"] == 0


# -------------------------------------------------------------- еда end-to-end

def test_meal_save_edit_undo(db):
    uid = repo.create_user(db, 11)
    ref = FoodRef(name_ru="гречка", name_en="buckwheat", kcal_100g=110,
                  protein_100g=4.2, fat_100g=1.1, carbs_100g=21.3, source="seed")
    item = IngredientEstimate(name="buckwheat", grams_min=200, grams_max=200)
    calc = calculate_meal([(item, ref)])
    mid = repo.save_meal(db, uid, "2026-10-28 13:00:00", "2026-10-28", "13:00",
                         "lunch", "text", calc)
    assert repo.day_totals(db, uid, "2026-10-28")["kcal"] == calc.kcal_total
    items = repo.get_meal_items(db, mid)
    repo.edit_item_grams(db, items[0]["id"], 100.0)
    assert repo.day_totals(db, uid, "2026-10-28")["kcal"] == 110
    edits = db.execute("SELECT COUNT(*) c FROM meal_edits").fetchone()["c"]
    assert edits == 1
    assert repo.soft_delete_meal(db, uid, mid)
    assert repo.day_totals(db, uid, "2026-10-28")["kcal"] == 0


def test_migration_idempotent(db):
    assert migrate(db) == migrate(db)


def test_message_card_length_bound():
    from app.services.food import format_card
    items = [(IngredientEstimate(name=f"d{i}", grams_min=100, grams_max=200),
              FoodRef(name_ru=f"блюдо {i}", name_en=f"d{i}", kcal_100g=200,
                      protein_100g=10, fat_100g=10, carbs_100g=20, source="seed"))
             for i in range(60)]
    card = format_card("20:40", "Ужин", calculate_meal(items), 0, 2000, 0, 100,
                       late=False, late_kcal=0)
    assert len(card.plain()) <= 4096
