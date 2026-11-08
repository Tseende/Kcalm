"""Анализ: режим питания, лестница по шагам, личные закономерности, главный шаг «Завтра: …»."""
from __future__ import annotations

import datetime as dt

import pytest

from app.services import insights, rhythm
from tests.test_advice_facts import _facts
from tests.test_web_api import (H, TODAY, _fixed_now, cfg, client, db, make_client,  # noqa: F401
                                user_id)

# понедельник: 2026-05-18 … воскресенье 2026-05-24
MON = dt.date(2026, 5, 18)


def _day(i, meals=3, first="08:30", kcal=1800, evening=500, gap=3.0, steps=8000, sleep_min=450):
    d = (MON + dt.timedelta(days=i)).isoformat()
    return {"date": d, "meals": meals, "first_meal": first if meals else None, "kcal": kcal,
            "evening_kcal": evening, "longest_gap_h": gap, "steps": steps, "sleep_min": sleep_min}


def _week(**kw):
    return [_day(i, **kw) for i in range(7)]


# ------------------------------------------------------------------ вспомогательные

def test_walk_minutes_rounds_up_and_is_clamped():
    assert rhythm.walk_minutes(100) == 10          # меньше минимума
    assert rhythm.walk_minutes(2000) == 20
    assert rhythm.walk_minutes(2300) == 25         # округление вверх до 5 минут
    assert rhythm.walk_minutes(20000) == 60        # потолок


def test_days_word():
    assert [rhythm.days_word(n) for n in (1, 2, 5, 11, 21)] == ["день", "дня", "дней", "дней", "день"]


# ------------------------------------------------------------------ режим питания

def test_meal_tips_stay_quiet_below_four_logged_days():
    days = [_day(i) for i in range(3)] + [_day(i, meals=0) for i in range(3, 7)]
    assert rhythm.meal_summary(days) is None
    assert rhythm.meal_tips(days, care=False) == ([], [])


def test_stable_regime_is_praised_not_scolded():
    good, tips = rhythm.meal_tips(_week(), care=False)
    assert any("режим питания стабильный" in g for g in good)
    assert tips == []
    assert rhythm.meal_status(rhythm.meal_summary(_week())) == "good"


def test_few_meals_get_tip_with_action():
    days = _week(meals=2, kcal=1500, evening=300)
    _, tips = rhythm.meal_tips(days, care=False)
    t = next(t for t in tips if "приёма пищи в день" in t["text"])
    assert t["action"].startswith("Завтра:") and "2,0" in t["text"]
    assert rhythm.meal_status(rhythm.meal_summary(days)) in ("ok", "bad")


def test_floating_breakfast_time_is_called_out():
    days = [_day(i, first="07:30" if i % 2 else "11:45") for i in range(7)]
    _, tips = rhythm.meal_tips(days, care=False)
    t = next(t for t in tips if "Первый приём пищи то в" in t["text"])
    assert "07:30" in t["text"] and "11:45" in t["text"]
    assert "около" in t["action"]


def test_evening_heavy_days_are_flagged_but_hidden_in_care_mode():
    days = _week(kcal=1800, evening=1200)
    _, tips = rhythm.meal_tips(days, care=False)
    assert any("вечер" in t["text"] for t in tips)
    _, care_tips = rhythm.meal_tips(days, care=True)
    assert not any("вечер" in t["text"] or "ккал" in t["text"] for t in care_tips)


def test_long_gaps_need_a_repeat_before_a_tip():
    one = _week()
    one[2]["longest_gap_h"] = 7.0
    assert not any(t["icon"] == "⏱" for t in rhythm.meal_tips(one, False)[1])
    many = [_day(i, gap=6.5) for i in range(7)]
    assert any(t["icon"] == "⏱" for t in rhythm.meal_tips(many, False)[1])


# ------------------------------------------------------------------ шаги

def test_steps_ladder_between_five_and_eight_thousand():
    _, tips = rhythm.steps_tips(6200, None)
    assert len(tips) == 1
    assert "1 800" in tips[0]["text"] and "20 минут" in tips[0]["text"]
    assert tips[0]["action"] == "Завтра: 20 минут ходьбы после ужина"


def test_steps_ladder_silent_on_goal_and_on_no_data():
    assert rhythm.steps_tips(8500, None) == ([], [])
    assert rhythm.steps_tips(None, 7000) == ([], [])


def test_steps_drop_vs_previous_period():
    _, tips = rhythm.steps_tips(8200, 10000)
    assert len(tips) == 1 and "1 800" in tips[0]["text"]


# ------------------------------------------------------------------ закономерности

def test_late_breakfast_pattern():
    days = [_day(i, first="12:00", evening=1100) if i < 3 else _day(i, first="08:00", evening=500)
            for i in range(7)]
    out = rhythm.patterns(days, days, care=False)
    p = next(p for p in out if p["title"] == "Завтрак и вечер")
    assert "600 ккал" in p["text"]
    assert not any(p["title"] == "Завтрак и вечер" for p in rhythm.patterns(days, days, care=True))


def test_weekend_steps_pattern_both_directions():
    more = [_day(i, steps=11000 if i >= 5 else 6000) for i in range(7)]
    assert any(p["title"] == "Шаги в будни" for p in rhythm.patterns(more, more, False))
    fewer = [_day(i, steps=4000 if i >= 5 else 9000) for i in range(7)]
    assert any(p["title"] == "Шаги в выходные" for p in rhythm.patterns(fewer, fewer, False))
    flat = [_day(i, steps=8000) for i in range(7)]
    assert rhythm.patterns(flat, flat, False) == []


def test_activity_sleep_pattern_needs_three_days_each_side():
    # шаги в день i → сон в ночь на день i+1
    steps = [9000, 3000, 9500, 3500, 9200, 3200, 9100]
    sleep = [450, 450, 450, 450, 450, 450, 450]
    days = [_day(i, steps=steps[i], sleep_min=sleep[i]) for i in range(7)]
    # после активного дня (чётные i) сон на следующий день длиннее
    for i in range(1, 7):
        days[i]["sleep_min"] = 500 if i % 2 else 400
    out = rhythm.patterns(days, days, False)
    p = next(p for p in out if p["title"] == "Движение и сон")
    assert "100 минут" in p["text"]
    few = [_day(i, steps=9000 if i < 2 else 3000, sleep_min=450) for i in range(7)]
    assert not any(p["title"] == "Движение и сон" for p in rhythm.patterns(few, few, False))


# ------------------------------------------------------------------ советы дня

def test_day_steps_ladder_has_minutes_and_action():
    tip = next(t for t in insights.day_tips(_facts(steps=6000), closed=True)[1] if t.icon == "🚶")
    assert "20 минут" in tip.text and tip.action == "Завтра: 20 минут ходьбы после ужина"
    low = next(t for t in insights.day_tips(_facts(steps=3000), closed=True)[1] if t.icon == "🚶")
    assert low.prio < tip.prio and "минут" in low.text      # меньше 5 000 важнее, чем 5–8 тысяч


def test_open_day_never_nags_about_steps():
    assert "🚶" not in [t.icon for t in insights.day_tips(_facts(steps=2000), closed=False)[1]]


def test_one_or_two_meals_in_a_closed_day():
    tips = insights.day_tips(_facts(meals=2, kcal=2000), closed=True)[1]
    t = next(t for t in tips if "приёма пищи" in t.text)
    assert t.action.startswith("Завтра:")
    one = next(t for t in insights.day_tips(_facts(meals=1, kcal=2000), closed=True)[1]
               if "приём пищи" in t.text)
    assert "1 приём" in one.text


def test_evening_heavy_day_tip_and_care_mode():
    f = _facts(kcal=2000, evening_kcal=1300)
    assert any("После 18:00" in t.text for t in insights.day_tips(f, closed=True)[1])
    assert not any("После 18:00" in t.text for t in insights.day_tips(dict(f, care=True), closed=True)[1])
    # старые словари без нового ключа не ломаются
    assert insights.day_tips(_facts(), closed=True)


def test_every_regime_tip_carries_an_action_and_is_sorted_by_prio():
    tips = insights.day_tips(_facts(meals=2, kcal=2000, steps=5500, evening_kcal=1300), closed=True)[1]
    prios = [t.prio for t in tips]
    assert prios == sorted(prios)
    assert all(t.action for t in tips if t.icon in ("🕐", "🚶"))


# ------------------------------------------------------------------ данные из БД и API

def _seed_meals(db, user_id, rows):
    for d, t, kcal in rows:
        db.execute(
            """INSERT INTO meals (user_id, eaten_at_utc, local_date, local_time, meal_type, source,
               kcal_total, protein_g, fat_g, carbs_g, kcal_low, kcal_high, confidence, method)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, d + "T10:00:00+00:00", d, t, "lunch", "text", kcal, 40, 30, 100, kcal, kcal, 0.8, "mixed"))
    db.commit()


@pytest.mark.asyncio
async def test_days_from_db_skips_tea_and_measures_evening_and_gap(client, db, user_id):
    day = "2026-05-18"
    _seed_meals(db, user_id, [(day, "09:00", 400), (day, "14:30", 600), (day, "19:30", 800)])
    db.execute(
        """INSERT INTO meals (user_id, eaten_at_utc, local_date, local_time, meal_type, source,
           kcal_total, protein_g, fat_g, carbs_g, kcal_low, kcal_high, confidence, method)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, day + "T05:00:00+00:00", day, "06:30", "snack", "manual", 5, 0, 0, 1, 5, 5, 0.8, "mixed"))
    db.commit()
    user = {"id": user_id}
    out = rhythm.days_from_db(db, user, dt.date(2026, 5, 18), 2)
    d0, d1 = out
    assert d0["meals"] == 3 and d0["first_meal"] == "09:00"      # чай в 06:30 не считается едой
    assert d0["evening_kcal"] == 800
    assert d0["longest_gap_h"] == 5.5
    assert d1["meals"] == 0 and d1["first_meal"] is None


@pytest.mark.asyncio
async def test_period_api_has_rhythm_tile_and_focus_action(client, db, user_id):
    today = dt.date.fromisoformat(TODAY)
    rows = []
    for i in range(7):
        d = (today - dt.timedelta(days=i)).isoformat()
        rows += [(d, "13:00", 600), (d, "20:00", 1400)]            # 2 приёма, вечер тяжёлый
    _seed_meals(db, user_id, rows)
    a = await (await client.get("/api/analysis?period=week", headers=H)).json()
    tile = next(m for m in a["metrics"] if m["key"] == "rhythm")
    assert tile["label"] == "Режим" and tile["status"] in ("ok", "bad")
    assert any("приёма пищи в день" in t["text"] for t in a["tips"])
    assert a["focus"] and "action" in a["focus"]
    assert a["focus"]["action"]
