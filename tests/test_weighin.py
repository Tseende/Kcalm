"""Взвешивание натощак: классификация, тренд без вечерних замеров, напоминание."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import scheduler
from app.db import connect, migrate
from app.domain import weighin
from app.services import screens
from app.storage import repo
from tests.test_routers_smoke import build, onboard, send
from tests.test_scheduler import FakeBot


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ----------------------------------------------------------------- классификация

@pytest.mark.parametrize("t, first_meal, expected", [
    ("07:10", None, 1),
    ("07:10", "08:00", 1),          # поел позже, значит взвесился натощак
    ("09:00", "08:00", 0),          # уже ел
    ("13:30", None, 0),             # слишком поздно
    ("03:30", None, 0),             # ночью после ужина
    ("23:00", "08:00", 0),
    (None, None, None),             # время неизвестно
])
def test_classify_fasting(t, first_meal, expected):
    assert weighin.classify_fasting(t, first_meal) == expected


def test_hint_overrides_time():
    assert weighin.classify_fasting("19:00", None, hint=1) == 1
    assert weighin.classify_fasting("07:00", None, hint=0) == 0
    assert weighin.caption_hint("после ужина") == 0 and weighin.caption_hint("натощак") == 1
    assert weighin.caption_hint("вес") is None


def test_normalize_time_and_days():
    assert weighin.normalize_time("7:05") == "07:05" and weighin.normalize_time("25:00") is None
    assert weighin.parse_days("пн ср пт") == "024"
    assert weighin.parse_days("ежедневно") == "0123456"
    assert weighin.parse_days("когда-нибудь") is None
    assert weighin.reminder_days_label("024") == "пн, ср, пт"


# ------------------------------------------------------------------- выбор точек

def _rows(spec):
    return [{"fasting": f, "weight_kg": 80.0, "local_date": f"2026-10-{i + 1:02d}"}
            for i, f in enumerate(spec)]


def test_trend_drops_non_fasting_when_enough_morning_points():
    rows = _rows([1, 1, 0, 1, 0, 1, None])
    picked, used_all = weighin.pick_trend_rows(rows)
    assert len(picked) == 5 and not used_all and all(r["fasting"] != 0 for r in picked)


def test_trend_falls_back_to_all_with_warning():
    rows = _rows([0, 0, 1, 0, 0])
    picked, used_all = weighin.pick_trend_rows(rows)
    assert len(picked) == 5 and used_all


def test_no_warning_when_everything_is_unknown():
    picked, used_all = weighin.pick_trend_rows(_rows([None, None, None]))
    assert len(picked) == 3 and not used_all


# ----------------------------------------------------------------------- запись

def test_evening_weight_does_not_overwrite_morning(db):
    uid = repo.create_user(db, 1)
    repo.save_body_measurement(db, uid, "2026-10-25", {"weight_kg": 85.0, "fasting": 1},
                               "manual", 0.9)
    repo.save_body_measurement(db, uid, "2026-10-25", {"weight_kg": 86.4, "fasting": 0},
                               "manual", 0.9)
    row = db.execute("SELECT weight_kg, fasting FROM body_measurements").fetchone()
    assert row["weight_kg"] == 85.0 and row["fasting"] == 1
    # а утренний перезаписывает вечерний
    repo.save_body_measurement(db, uid, "2026-10-26", {"weight_kg": 86.4, "fasting": 0},
                               "manual", 0.9)
    repo.save_body_measurement(db, uid, "2026-10-26", {"weight_kg": 85.2, "fasting": 1},
                               "manual", 0.9)
    assert db.execute("SELECT weight_kg FROM body_measurements WHERE local_date='2026-10-26'"
                      ).fetchone()["weight_kg"] == 85.2


def test_screenshot_time_and_meals_decide_fasting(db):
    uid = repo.create_user(db, 1, tz="Europe/Minsk")
    user = repo.get_user_by_tg(db, 1)
    now = datetime(2026, 10, 25, 20, 0, tzinfo=ZoneInfo("Europe/Minsk"))
    morning = {"screen": "picooc_measure", "date": "2026-10-25",
               "raw_text": ["PICOOC", "07:12 10/25/2026", "85.0кг"]}
    assert screens.fasting_for(db, user, morning, "2026-10-25", now_local=now) == 1
    evening = {"screen": "picooc_measure", "date": "2026-10-25",
               "raw_text": ["21:30 10/25/2026"], "time": "07:30"}   # поле time от модели не доверяем
    assert screens.fasting_for(db, user, evening, "2026-10-25", now_local=now) == 0
    no_time_late = {"screen": "picooc_measure", "date": "2026-10-25"}
    assert screens.fasting_for(db, user, no_time_late, "2026-10-25", now_local=now) is None
    no_time_morning = datetime(2026, 10, 25, 7, 40, tzinfo=ZoneInfo("Europe/Minsk"))
    assert screens.fasting_for(db, user, no_time_late, "2026-10-25", now_local=no_time_morning) == 1


def test_apply_parsed_stores_fasting_flag(db):
    uid = repo.create_user(db, 1)
    parsed = {"screen": "picooc_measure", "date": "2026-10-25", "overall_confidence": 0.95,
              "metrics": {"weight_kg": {"value": 85.0}}}
    assert screens.apply_parsed(db, uid, parsed, "2026-10-25", fasting=1) == "body"
    assert db.execute("SELECT fasting FROM body_measurements").fetchone()["fasting"] == 1


# ------------------------------------------------------------------ напоминание

def _utc(hh, mm, day=26):
    return datetime(2026, 10, day, hh, mm, tzinfo=ZoneInfo("Europe/Minsk")).astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_weigh_reminder_days_and_already_weighed(db):
    uid = repo.create_user(db, 5, tz="Europe/Minsk")
    repo.update_user(db, uid, onboarded=1, sex="male", eating_cutoff="21:00")
    repo.set_reminder(db, uid, "weigh", "07:30", days="024")      # пн, ср, пт
    bot = FakeBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_utc(7, 30, day=26))   # понедельник
    assert len(bot.sent) == 1 and "взвешивание" in bot.sent[0][1]

    bot2 = FakeBot()
    await scheduler.dispatch_due_reminders(db, bot2, now_utc=_utc(7, 30, day=27))  # вторник
    assert bot2.sent == []

    repo.save_body_measurement(db, uid, "2026-10-28", {"weight_kg": 85.0}, "manual", 0.9)
    bot3 = FakeBot()
    await scheduler.dispatch_due_reminders(db, bot3, now_utc=_utc(7, 30, day=28))  # среда, уже взвесился
    assert bot3.sent == []


@pytest.mark.asyncio
async def test_weighin_command(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    assert repo.due_reminders(db, uid, "weigh")["local_time"] == "07:30"
    await send(dp, bot, "/weighin 06:45 пн ср пт")
    row = repo.due_reminders(db, uid, "weigh")
    assert row["local_time"] == "06:45" and row["days"] == "024" and row["enabled"] == 1
    await send(dp, bot, "/weighin off")
    assert repo.due_reminders(db, uid, "weigh")["enabled"] == 0
    await send(dp, bot, "/weighin on")
    assert repo.due_reminders(db, uid, "weigh")["enabled"] == 1
    await send(dp, bot, "/weighin 99:99")
    assert repo.due_reminders(db, uid, "weigh")["local_time"] == "06:45"      # не изменилось


def test_time_from_raw_text_variants():
    assert screens.time_from_raw({"raw_text": ["PICOOC", "21:04 10/30/2026"]}) == "21:04"
    assert screens.time_from_raw({"raw_text": ["10/30/2026 7:05"]}) == "07:05"
    assert screens.time_from_raw({"raw_text": ["112.6кг", "36.9%"]}) is None
    assert screens.time_from_raw({}) is None
