"""Психологическая поддержка: правила «если-то», разбор после срыва, защита и /care."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import scheduler
from app.db import connect, migrate
from app.domain import safety as sdom
from app.domain.nutrition import MealCalculation
from app.services import craving, plan, safety
from app.storage import repo
from tests.test_routers_smoke import build, onboard, send
from tests.test_scheduler import FakeBot

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


def _user(db, tg=1):
    uid = repo.create_user(db, tg, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="female", onboarded=1, eating_cutoff="21:00", height_cm=168,
                     start_weight_kg=72, birth_date="1990-01-01", target_rate_kg_week=-0.5,
                     goal_weight_kg=65, activity_pa=1.12)
    repo.insert_target(db, uid, "2026-10-01", 1600, 115, 58, 150, 2000, "onboarding")
    return uid, repo.get_user_by_tg(db, tg)


# ---------------------------------------------------------------- правила «если-то»

def test_parse_rule_variants():
    assert plan.parse_rule("если тянет сладкого, то чай и 15 минут") == (
        "тянет сладкого", "чай и 15 минут", None)
    assert plan.parse_rule("в 21:30 если поужинала то почищу зубы") == (
        "поужинала", "почищу зубы", "21:30")
    assert isinstance(plan.parse_rule("просто текст"), str)
    assert isinstance(plan.parse_rule("если а, то б"), str)                 # слишком коротко


def test_rule_with_starvation_is_rejected():
    msg = plan.parse_rule("если сорвалась, то завтра пропусти завтрак и поголодай")
    assert isinstance(msg, str) and "не сохраню" in msg


def test_rule_limit_duplicates_and_ownership(db):
    uid, _ = _user(db)
    other, _ = _user(db, tg=2)
    assert "Добавил" in plan.add_user_rule(db, uid, "если скучно, то прогуляюсь")
    assert "уже есть" in plan.add_user_rule(db, uid, "если скучно, то прогуляюсь")
    for i in range(6):
        plan.add_user_rule(db, uid, f"если ситуация {i}, то действие {i}")
    assert len(repo.list_rules(db, uid)) == repo.MAX_RULES
    assert "уже" in plan.add_user_rule(db, uid, "если ещё одно, то ещё действие")
    rule = repo.list_rules(db, uid)[0]
    assert repo.delete_rule(db, other, rule["id"]) is False                # чужое не удалить
    assert plan.delete_by_number(db, uid, 1) is True


def test_presets_and_html_escaped(db):
    uid, _ = _user(db)
    assert "Добавил" in plan.add_preset(db, uid, 0)
    kb = plan.presets_keyboard(db, uid)
    assert kb and len(kb.inline_keyboard) == len(plan.PRESETS) - 1          # добавленное не предлагаем
    plan.add_user_rule(db, uid, "если <b>больно</b>, то вода")
    assert "&lt;b&gt;" in plan.overview(db, uid)


def test_rules_shown_in_craving_plan_by_category(db):
    uid, user = _user(db)
    plan.add_preset(db, uid, 1)                                              # emotion
    eid, _, _ = craving.start_episode(db, user, "хочу есть")
    event = repo.get_craving(db, uid, eid)
    text = craving.category_plan(db, user, event, "emotion")
    assert "Твои правила" in text and "тянет есть от стресса" in text
    other = craving.category_plan(db, user, repo.get_craving(db, uid, eid), "habit")
    assert "тянет есть от стресса" not in other


@pytest.mark.asyncio
async def test_rule_reminder_sent_once_per_day(db):
    uid, user = _user(db)
    repo.add_rule(db, uid, "поужинала", "почищу зубы", None, "21:00")
    bot = FakeBot()
    now = datetime(2026, 10, 26, 21, 0, tzinfo=ZoneInfo("Europe/Minsk")).astimezone(timezone.utc)
    await scheduler.dispatch_due_reminders(db, bot, now_utc=now)
    await scheduler.dispatch_due_reminders(db, bot, now_utc=now)
    texts = [t for _, t in bot.sent if "правило" in t]
    assert len(texts) == 1 and "почищу зубы" in texts[0]


# --------------------------------------------------------------- разбор после срыва

@pytest.mark.asyncio
async def test_debrief_flow_through_router(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "/craving")
    event = db.execute("SELECT * FROM craving_events WHERE user_id=?", (uid,)).fetchone()
    repo.update_craving(db, event["id"], category="emotion")
    # урок выбираем так же, как нажатием кнопки
    lesson = plan.lesson_by_index("emotion", 0)
    assert lesson and repo.set_craving_lesson(db, uid, event["id"], lesson)
    created = plan.rule_from_lesson(db, uid, repo.get_craving(db, uid, event["id"]))
    assert "Добавил правило" in created
    rule = repo.list_rules(db, uid)[0]
    assert rule["then_text"] == lesson and rule["category"] == "emotion"


@pytest.mark.asyncio
async def test_own_lesson_text_is_saved_and_unsafe_one_is_not(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "/craving")
    event = db.execute("SELECT * FROM craving_events WHERE user_id=?", (uid,)).fetchone()

    repo.save_pending_input(db, uid, "craving_lesson", {"event_id": event["id"]})
    await send(dp, bot, "завтра пропусти завтрак и поголодай")             # отклоняется
    assert repo.get_craving(db, uid, event["id"])["lesson"] is None
    assert repo.latest_pending_input(db, uid, "craving_lesson")             # можно ответить ещё раз

    await send(dp, bot, "выпью воды и подожду 15 минут")
    assert repo.get_craving(db, uid, event["id"])["lesson"] == "выпью воды и подожду 15 минут"
    assert repo.latest_pending_input(db, uid, "craving_lesson") is None


# ------------------------------------------------------------------ защита и /care

def test_assess_flags_and_policy():
    assert sdom.assess(sdom.SafetyInputs()) == []
    flags = sdom.assess(sdom.SafetyInputs(very_low_days=5, overate_14d=3, bmi=19.0,
                                          target_rate=-0.5, loss_pct_week=1.3))
    codes = {f.code for f in flags}
    assert codes == {"very_low_intake", "repeated_overate", "near_bmi_floor", "fast_loss"}
    assert sdom.needs_professional(flags)
    assert sdom.freeze_target_decrease(flags, False)
    risk = sdom.assess(sdom.SafetyInputs(risk_events_30d=1))
    assert risk[0].strong and sdom.needs_professional(risk)
    mild = sdom.assess(sdom.SafetyInputs(busy_weigh_days=2))
    assert not sdom.needs_professional(mild) and sdom.reduce_weighing(mild, False)
    assert sdom.freeze_target_decrease([], True) and sdom.reduce_weighing([], True)


def _eat(db, uid, kcal, days=14):
    half = kcal // 2
    calc = MealCalculation(items=[], kcal_total=half, kcal_low=half, kcal_high=half,
                           protein_g=5, fat_g=5, carbs_g=5, method="db", confidence=0.9)
    for off in range(days):
        day = (TODAY - timedelta(days=off)).isoformat()
        for t in ("09:00", "19:00"):
            repo.save_meal(db, uid, f"{day} {t}:00", day, t, "lunch", "text", calc)


def test_gather_detects_very_low_intake(db):
    uid, user = _user(db)
    _eat(db, uid, 800)
    inputs = safety.gather(db, user, TODAY)
    assert inputs.very_low_days == 14 and inputs.logged_days == 14
    flags = safety.assess(db, user, TODAY)
    assert any(f.code == "very_low_intake" and f.strong for f in flags)


def test_weekly_report_freezes_target_and_mentions_specialist(db):
    from app.routers.commands import weekly_report
    uid, user = _user(db)
    _eat(db, uid, 800)
    for off in range(14):
        repo.save_body_measurement(db, uid, (TODAY - timedelta(days=off)).isoformat(),
                                   {"weight_kg": 72.0}, "manual", 0.9)
    before = repo.current_target(db, uid)["kcal_target"]
    text = weekly_report(db, uid, today=TODAY)
    assert "Хочу сказать по-доброму" in text and "врачом или психологом" in text
    assert "не буду снижать цель" in text
    assert repo.current_target(db, uid)["kcal_target"] >= before            # не опустилась


def test_weighing_reminder_reduced_once_and_care_mode(db):
    uid, user = _user(db)
    repo.set_reminder(db, uid, "weigh", "07:30", days="0123456")
    lines = safety.weekly_lines(db, user, TODAY, [sdom.Flag("weighing_often", "x")])
    assert any("понедельникам и четвергам" in l for l in lines)
    assert repo.due_reminders(db, uid, "weigh")["days"] == "03"
    again = safety.weekly_lines(db, user, TODAY, [sdom.Flag("weighing_often", "x")])
    assert not any("понедельникам" in l for l in again)                      # второй раз молчим


def test_care_mode_effects(db):
    uid, user = _user(db)
    repo.set_reminder(db, uid, "weigh", "07:30")
    text = safety.set_care(db, user, True)
    fresh = repo.get_user_by_tg(db, 1)
    assert fresh["care_mode"] == 1 and fresh["target_rate_kg_week"] == -0.25
    assert repo.due_reminders(db, uid, "weigh")["enabled"] == 0 and "Осторожный" in text
    assert "/weighin on" in safety.set_care(db, fresh, False)


@pytest.mark.asyncio
async def test_care_mode_stops_weigh_reminder(db):
    uid, user = _user(db)
    repo.set_reminder(db, uid, "weigh", "07:30")
    repo.update_user(db, uid, care_mode=1)
    bot = FakeBot()
    now = datetime(2026, 10, 26, 7, 30, tzinfo=ZoneInfo("Europe/Minsk")).astimezone(timezone.utc)
    await scheduler.dispatch_due_reminders(db, bot, now_utc=now)
    assert not any("взвешивание" in t for _, t in bot.sent)


@pytest.mark.asyncio
async def test_plan_and_care_commands(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "/plan если тянет сладкого, то чай и 15 минут")
    assert len(repo.list_rules(db, uid)) == 1
    await send(dp, bot, "/plan")
    await send(dp, bot, "/plan del 1")
    assert repo.list_rules(db, uid) == []
    await send(dp, bot, "/care on")
    assert repo.get_user_by_tg(db, 1)["care_mode"] == 1
    await send(dp, bot, "/care off")
    assert repo.get_user_by_tg(db, 1)["care_mode"] == 0


async def _press(dp, bot, data: str, uid: int = 1):
    import itertools
    from aiogram.types import CallbackQuery, Update, User
    from tests.test_routers_smoke import _ids, message
    cb = CallbackQuery(id=f"cb{next(_ids)}", from_user=User(id=uid, is_bot=False, first_name="Т"),
                       chat_instance="ci", data=data, message=message("x").as_(bot))
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))


@pytest.mark.asyncio
async def test_full_debrief_through_buttons(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "/craving")
    event = db.execute("SELECT * FROM craving_events WHERE user_id=?", (uid,)).fetchone()
    eid = event["id"]

    await _press(dp, bot, f"cr:out:{eid}:overate")                  # итог → кнопка разбора
    assert repo.get_craving(db, uid, eid)["outcome"] == "overate"
    await _press(dp, bot, f"cr:deb:{eid}")                          # категория неизвестна → спрашивает
    await _press(dp, bot, f"cr:dcat:{eid}:emotion")
    assert repo.get_craving(db, uid, eid)["category"] == "emotion"
    await _press(dp, bot, f"cr:less:{eid}:0")                       # готовый урок
    lesson = repo.get_craving(db, uid, eid)["lesson"]
    assert lesson == plan.lesson_by_index("emotion", 0)
    await _press(dp, bot, f"cr:rule:{eid}")                         # урок → правило «если-то»
    rules = repo.list_rules(db, uid)
    assert len(rules) == 1 and rules[0]["then_text"] == lesson

    await _press(dp, bot, f"cr:less:{eid}:own")                     # «напишу своё» ставит ожидание
    assert repo.latest_pending_input(db, uid, "craving_lesson")
    await _press(dp, bot, "pl:add:0")                               # готовое правило из /plan
    assert len(repo.list_rules(db, uid)) == 2
