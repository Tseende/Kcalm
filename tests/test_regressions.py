"""Регрессии: тренд по датам, плато/недоучёт, пересмотр цели раз в неделю, коуч, матчинг,
скриншоты, пресеты."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

from app.db import connect, migrate
from app.domain import analytics, matching, targets
from app.domain.nutrition import MealCalculation
from app.services import coach, screens
from app.storage import repo

TODAY = date(2026, 10, 25)


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ----------------------------------------------------------------- тренд по датам

def test_slope_uses_dates_not_indices():
    # вес падает на 0.1 кг в день, но замеры идут с пропусками
    points = [(0, 90.0), (1, 89.9), (5, 89.5), (6, 89.4), (13, 88.7)]
    assert targets.weekly_slope(points, window_days=14) == pytest.approx(-0.7, abs=0.01)
    # по номеру замера тот же ряд дал бы совсем другой наклон
    by_index = targets.ols_slope(list(range(5)), [v for _, v in points])
    assert by_index * 7 != pytest.approx(-0.7, abs=0.1)


def test_ewma_dated_decays_over_gaps():
    flat = targets.ewma_dated([(0, 100.0), (1, 100.0)])
    assert flat[-1][1] == 100.0
    jump_after_gap = targets.ewma_dated([(0, 100.0), (10, 90.0)])[-1][1]
    jump_next_day = targets.ewma_dated([(0, 100.0), (1, 90.0)])[-1][1]
    assert jump_after_gap < jump_next_day           # после долгого пропуска новому замеру верим больше


def test_value_at_or_before():
    pts = [(1, 10.0), (5, 12.0), (9, 14.0)]
    assert targets.value_at_or_before(pts, 6) == 12.0
    assert targets.value_at_or_before(pts, 0) is None


def test_adaptive_review_not_more_often_than_weekly():
    days = list(range(100, 114))
    weights = [(d, 90.0 - 0.05 * (d - 100)) for d in days]
    intake = {d: 1800 for d in days[:12]}
    fresh = targets.adaptive_tdee(weights, intake, 1900, last_review_day=days[-1] - 3,
                                  today=days[-1])
    assert fresh.new_target is None and "раза в неделю" in fresh.reason
    due = targets.adaptive_tdee(weights, intake, 1900, last_review_day=days[-1] - 7,
                                today=days[-1])
    assert due.new_target is not None


# ---------------------------------------------------------- плато и недоучёт

def _profile(db):
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="male", birth_date="1990-01-01", height_cm=180,
                     start_weight_kg=85, goal_weight_kg=75, target_rate_kg_week=-0.5,
                     activity_pa=1.12, onboarded=1, eating_cutoff="21:00")
    repo.insert_target(db, uid, "2026-09-01", 1900, 136, 68, 200, 2400, "onboarding")
    return uid


def _days(db, uid, weights_by_offset: dict[int, float], kcal_per_day=1800):
    half = kcal_per_day // 2
    calc = MealCalculation(items=[], kcal_total=half, kcal_low=half, kcal_high=half,
                           protein_g=10, fat_g=10, carbs_g=10, method="db", confidence=0.9)
    for off in range(14):
        day = (TODAY - timedelta(days=off)).isoformat()
        for t in ("09:00", "19:00"):
            repo.save_meal(db, uid, f"{day} {t}:00", day, t, "lunch", "text", calc)
        if off in weights_by_offset:
            repo.save_body_measurement(db, uid, day, {"weight_kg": weights_by_offset[off]},
                                       "manual", 0.9)


def test_underreporting_after_two_flat_weeks(db):
    from app.routers.commands import weekly_report
    uid = _profile(db)
    _days(db, uid, {off: 85.0 for off in range(14)})
    text = weekly_report(db, uid, today=TODAY)
    assert "медленнее, чем должен" in text and "Калории резать не нужно" in text


def test_plateau_note_when_only_this_week_is_flat(db):
    from app.routers.commands import weekly_report
    uid = _profile(db)
    weights = {}
    for off in range(14):
        weights[off] = 85.0 if off < 7 else 85.0 + (off - 6) * 0.0786   # прошлая неделя шла вниз
    _days(db, uid, weights)
    text = weekly_report(db, uid, today=TODAY)
    assert "Похоже на плато" in text and "урезать еду ещё сильнее не стоит" in text


def test_no_notes_without_enough_data(db):
    from app.routers.commands import weekly_report
    uid = _profile(db)
    text = weekly_report(db, uid, today=TODAY)
    assert "плато" not in text and "медленнее" not in text


# --------------------------------------------------------------------- коуч

def test_coach_number_check_allows_times_and_day_counts():
    ctx = {"target_kcal": 1900, "days": [{"kcal": 1750}]}
    ok = "Ужин был в 19:30, а не 22:00. Записано 4 из 7 дней, 1750 ккал при цели 1900."
    assert coach.check_numbers(ok, ctx) == []
    assert coach.check_numbers("Съедено 2345 ккал", ctx) == ["2345"]


# ----------------------------------------------------------------- матчинг

@pytest.mark.parametrize("query, expected", [
    ("ржаной хлеб", "Хлеб чёрный"),
    ("гречневая каша", "Гречка варёная"),
    ("творог", "Творог 5%"),
    ("хлеб", "Хлеб белый"),
])
def test_matching_modifiers(db, query, expected):
    assert matching.find_food(db, "", query).name_ru == expected


def test_modifier_swap_is_not_matched(db):
    # «котлета куриная» есть в базе; «котлета бараньная» не должна уйти в другое мясо
    ref = matching.find_food(db, "", "котлета бараньная")
    assert ref is None or "барань" in ref.name_ru.lower()


# ------------------------------------------------------------ скриншоты и пресеты

def test_screens_do_not_report_success_when_everything_filtered(db):
    uid = repo.create_user(db, 1)
    parsed = {"screen": "mifitness_sleep", "date": "2026-10-25", "overall_confidence": 0.9,
              "metrics": {"sleep_duration_min": {"value": 99999}}}      # вне границ
    assert screens.apply_parsed(db, uid, parsed, "2026-10-25") == "unknown"
    ok = {"screen": "mifitness_sleep", "date": "2026-10-25", "overall_confidence": 0.9,
          "metrics": {"sleep_duration_min": {"value": 420}}}
    assert screens.apply_parsed(db, uid, ok, "2026-10-25") == "sleep"


def test_pending_input_expires(db):
    uid = repo.create_user(db, 1)
    pid = repo.save_pending_input(db, uid, "screenshot_confirm", {"x": 1})
    assert repo.latest_pending_input(db, uid, "screenshot_confirm", max_age_min=30)
    db.execute("UPDATE pending_inputs SET created_at=datetime('now','-2 hours') WHERE id=?", (pid,))
    db.commit()
    assert repo.latest_pending_input(db, uid, "screenshot_confirm", max_age_min=30) is None
    assert repo.latest_pending_input(db, uid, "screenshot_confirm")        # без TTL как раньше


def test_forget_file_allows_retry(db):
    uid = repo.create_user(db, 1)
    repo.remember_file(db, uid, "abc")
    assert repo.file_seen(db, uid, "abc")
    repo.forget_file(db, uid, "abc")
    assert not repo.file_seen(db, uid, "abc")


@pytest.mark.asyncio
async def test_presets_learned_from_grams_and_confirm(db, monkeypatch):
    from tests.test_routers_smoke import build, onboard, send
    from app.services import food as foodsvc
    from app.domain.nutrition import IngredientEstimate, MealUnderstanding

    async def fake_parse(text, user_id, conn):
        return MealUnderstanding(items=[IngredientEstimate(
            name="buckwheat", name_ru="гречка", grams_min=135, grams_max=165,
            explicit_grams=150, confidence="high")], overall_confidence=0.9)
    monkeypatch.setattr(foodsvc, "parse_text", fake_parse)

    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "гречка 150 г")
    row = db.execute("SELECT * FROM user_foods WHERE user_id=?", (uid,)).fetchone()
    assert row and row["default_grams"] == 150 and row["times_used"] == 1
    await send(dp, bot, "гречка 150 г")
    assert db.execute("SELECT times_used FROM user_foods WHERE user_id=?",
                      (uid,)).fetchone()["times_used"] == 2
    await send(dp, bot, "/foods")                                   # команда отвечает
    assert "SendMessage" in session.names()
