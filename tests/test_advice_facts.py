"""Советы по данным дня: полнота записей, активность, качество сна, тяга, паузы, контексты для LLM."""
from __future__ import annotations

import json

import pytest

from app.domain.nutrition import FoodRef, calculate_meal
from app.services import assistant, coach, craving, insights
from app.storage import repo
from tests.test_refine import _items
from tests.test_routers_smoke import build, db, onboard  # noqa: F401

DAY = "2026-05-20"


def _facts(**kw):
    base = {"date": DAY, "kcal": 1900, "meals": 3, "protein": 130, "fat": 60, "carbs": 200,
            "kcal_target": 2100, "protein_target": 120, "water": 2300, "water_goal": 2200,
            "steps": 8000, "sleep_min": 450, "late_kcal": 0, "cutoff": "21:00", "care": False}
    return {**base, **kw}


def _icons(f, closed=True):
    return [t.icon for t in insights.day_tips(f, closed)[1]]


# ------------------------------------------------------------------ полнота дня

def test_maybe_incomplete_rules():
    assert not insights.maybe_incomplete(_facts(kcal=300, meals=1), "14:00"), "днём рано судить"
    assert insights.maybe_incomplete(_facts(kcal=300, meals=1), "21:30")
    assert insights.maybe_incomplete(_facts(meals=0, kcal=0), "21:30")
    assert insights.maybe_incomplete(_facts(kcal=1300, meals=2), "21:30")
    assert not insights.maybe_incomplete(_facts(kcal=1900, meals=3), "21:30")


def test_partial_logging_asks_to_complete_instead_of_scolding():
    f = _facts(kcal=700, meals=1, protein=20)
    good, tips = insights.day_tips(f, closed=True)
    icons = [t.icon for t in tips]
    assert "📝" in icons
    assert "🍽" not in icons and "🥩" not in icons, "по неполным данным нельзя ругать"
    assert "похоже" in tips[0].text.lower()


def test_empty_closed_day_asks_to_log():
    _, tips = insights.day_tips(_facts(meals=0, kcal=0, protein=0), closed=True)
    assert [t.icon for t in tips][0] == "📝"


def test_care_mode_incomplete_hint_has_no_calories():
    _, tips = insights.day_tips(_facts(kcal=500, meals=1, care=True), closed=True)
    note = next(t for t in tips if t.icon == "📝")
    assert "ккал" not in note.text


# ------------------------------------------------------------------ новые правила

def test_high_activity_suggests_carbs_not_cutting():
    f = _facts(steps=14000, active_kcal=1180, kcal=1700)
    assert "🍚" in _icons(f)
    assert "🍚" not in _icons(_facts(steps=14000, active_kcal=1180, kcal=2050))


def test_low_sleep_quality_tip_with_normal_duration():
    f = _facts(sleep_min=450, sleep_score=60, sleep_awake_min=10)
    tip = next(t for t in insights.day_tips(f)[1] if t.icon == "😴")
    assert "оценка 60" in tip.text
    good_sleep = insights.day_tips(_facts(sleep_min=450, sleep_score=85))[0]
    assert any("сон хороший" in g for g in good_sleep)


def test_craving_before_enough_food_reads_as_hunger():
    cravings = [{"time": "17:30", "category": "hunger", "outcome": "resisted",
                 "kcal_so_far": 700, "kcal_target": 2100}]
    tip = next(t for t in insights.day_tips(_facts(cravings=cravings, kcal=1900))[1]
               if t.icon == "🧭")
    assert "17:30" in tip.text and "700 из 2100" in tip.text and "голод" in tip.text


def test_craving_after_enough_food_points_to_habit_and_care_mode_hides_kcal():
    cravings = [{"time": "22:10", "category": "habit", "outcome": "ate_planned",
                 "kcal_so_far": 1900, "kcal_target": 2100}]
    tip = next(t for t in insights.day_tips(_facts(cravings=cravings))[1] if t.icon == "🧭")
    assert "привычка" in tip.text
    care = next(t for t in insights.day_tips(_facts(cravings=cravings, care=True))[1]
                if t.icon == "🧭")
    assert "ккал" not in care.text


def test_long_gap_between_meals_gets_tip():
    f = _facts(longest_gap_h=7.5, gap_from="08:00", gap_to="15:30")
    tip = next(t for t in insights.day_tips(f)[1] if t.icon == "⏱")
    assert "08:00" in tip.text and "15:30" in tip.text
    assert "⏱" not in _icons(_facts(longest_gap_h=4.0, gap_from="08:00", gap_to="12:00"))


def test_protein_gap_tip_mentions_activity_when_active():
    f = _facts(protein=60, steps=13000)
    text = next(t.text for t in insights.day_tips(f)[1] if t.icon == "🥩")
    assert "Много движения" in text and "около 60 г" in text


# ------------------------------------------------------------------ факты из БД

def _save(db, uid, hhmm, name, grams, day=DAY):
    u = _items((name, grams))
    ref = lambda i: FoodRef(name_ru=i.name_ru, name_en=i.name, kcal_100g=150,  # noqa: E731
                            protein_100g=10, fat_100g=5, carbs_100g=15, source="seed")
    calc = calculate_meal([(i, ref(i)) for i in u.items])
    return repo.save_meal(db, uid, f"{day} {hhmm}:00", day, hhmm, "lunch", "text", calc)


@pytest.mark.asyncio
async def test_day_facts_collects_activity_sleep_gap_and_cravings(db):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    user = repo.get_user(db, uid) if hasattr(repo, "get_user") else \
        db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    _save(db, uid, "08:00", "овсянка", 250)
    _save(db, uid, "15:30", "курица", 200)
    db.execute("""INSERT INTO activity_daily(user_id, local_date, steps, active_kcal, resting_hr,
                  avg_hr, active_minutes, source) VALUES (?,?,?,?,?,?,?, 'test')""",
               (uid, DAY, 12768, 1182, 95, 110, 70))
    db.execute("""INSERT INTO sleep_sessions(user_id, wake_date, duration_min, deep_min, awake_min,
                  score, source) VALUES (?,?,?,?,?,?, 'test')""", (uid, DAY, 429, 60, 3, 73))
    db.execute("""INSERT INTO craving_events(user_id, started_utc, local_date, local_time,
                  category, outcome, kcal_so_far, kcal_target, mode)
                  VALUES (?,?,?,?,?,?,?,?, 'craving')""",
               (uid, "2026-05-20T14:00:00", DAY, "14:00", "hunger", "resisted", 450, 2100))
    db.execute("""INSERT INTO craving_events(user_id, started_utc, local_date, local_time,
                  category, outcome, kcal_so_far, kcal_target, mode)
                  VALUES (?,?,?,?,?,?,?,?, 'craving')""",
               (uid, "2026-05-20T22:00:00", "2026-05-21", "01:10", "habit", "overate", 1900, 2100))
    db.execute("""INSERT INTO craving_events(user_id, started_utc, local_date, local_time,
                  category, outcome, mode) VALUES (?,?,?,?,?,?, 'talk')""",
               (uid, "2026-05-20T18:00:00", DAY, "18:00", "unknown", "open"))
    db.commit()

    f = insights.day_facts(db, user, DAY)

    assert (f["active_kcal"], f["steps"], f["resting_hr"], f["sleep_score"]) == (1182, 12768, 95, 73)
    assert (f["first_meal"], f["last_meal"]) == ("08:00", "15:30")
    assert f["longest_gap_h"] == 7.5 and (f["gap_from"], f["gap_to"]) == ("08:00", "15:30")
    assert [c["time"] for c in f["cravings"]] == ["14:00", "01:10"], "ночная тяга — к прожитому дню, разговор — нет"
    assert f["cravings"][0]["kcal_so_far"] == 450


@pytest.mark.asyncio
async def test_contexts_for_llm_carry_today_facts_and_pass_number_check(db):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    _save(db, uid, "09:00", "омлет", 200, day=insights_today(user))
    ctx = coach.build_context(db, user)
    tf = ctx["today_facts"]
    assert tf["protein_left_g"] >= 0 and "water_left_ml" in tf and "diary_maybe_incomplete" in tf
    assert "today_facts" in assistant.snapshot(db, user)
    # число из today_facts проходит проверку «цифры только из контекста»
    assert not coach.check_numbers(f"Белка не хватает {tf['protein_left_g']} г", ctx)


def insights_today(user) -> str:
    from app.timeutils import local_today
    return local_today(user["tz"]).isoformat()


def test_llm_facts_hides_calories_in_care_mode():
    out = insights.llm_facts(_facts(care=True), "21:30")
    assert "kcal" not in out and "kcal_left" not in out and "kcal_range" not in out
    full = insights.llm_facts(_facts(), "21:30")
    assert full["kcal_left"] == 200 and json.dumps(full)  # сериализуется для промпта


@pytest.mark.asyncio
async def test_morning_summary_shows_yesterday_food_with_night_entries(db):
    from datetime import date, timedelta

    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    today = date(2026, 5, 21)
    y = (today - timedelta(days=1)).isoformat()
    _save(db, uid, "09:00", "омлет", 300, day=y)
    _save(db, uid, "00:30", "хлеб", 100, day=today.isoformat())
    text = await coach.morning_summary(db, user, today=today)
    assert "🍽 Вчера:" in text and "Ночью после полуночи" in text
    empty = await coach.morning_summary(db, user, today=date(2026, 6, 1))
    assert "Вчера еда не записана" in empty


def _week(active, tdee=3040, bmr=2100, care=False):
    days = [_facts(date=f"2026-10-{24 + i}", active_kcal=a) for i, a in enumerate(active)]
    return {"days": days, "prev": days, "care": care, "tdee_est": tdee, "bmr": bmr}


def test_energy_check_flags_big_gap_but_stays_quiet_otherwise():
    low = insights._energy_check(_week([300] * 7))          # 2400 против 3040: ниже на 21 %
    assert low and "ниже" in low.text and "цель не меняю" in low.text
    assert insights._energy_check(_week([900] * 7)) is None  # 3000 ≈ 3040
    assert insights._energy_check(_week([300] * 3 + [None] * 4)) is None, "мало данных"
    assert insights._energy_check(_week([300] * 7, care=True)) is None
    assert insights._energy_check(_week([300] * 7, tdee=None)) is None


@pytest.mark.asyncio
async def test_week_facts_carries_energy_baseline(db):
    from datetime import date

    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    wf = insights.week_facts(db, user, date(2026, 5, 21))
    assert "tdee_est" in wf and "bmr" in wf
