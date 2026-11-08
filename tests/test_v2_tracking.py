"""Фаза 4 v2: сон, шаги и тренировки. Фразы словами, скриншоты любых приложений (generic_*),
сервис tracking без aiogram, дедупликация источников в отчётах."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.db import connect, migrate
from app.domain import intents
from app.domain import tracking as trk
from app.routers import intake
from app.services import food as foodsvc
from app.services import screens
from app.services import tracking as tracksvc
from app.storage import repo
from tests.test_photo_flow import NOW, _feed, _food, _photo, build  # noqa: F401
from tests.test_photo_flow import db  # noqa: F401  (фикстура)
from tests.test_photo_flow import _fast  # noqa: F401  (autouse: время и справочник)
from tests.test_routers_smoke import onboard, send

TODAY = NOW.date()                       # 2026-05-20, «сейчас» зафиксировано фикстурой _fast
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    """Дата в сервисах и роутерах — та же, что у еды (фикстура _fast), а не часы прогона."""
    from app.services import tracking as svc
    from app.routers import tracking as router
    monkeypatch.setattr(svc, "local_today", lambda tz: TODAY)
    monkeypatch.setattr(router, "local_today", lambda tz: TODAY)


@pytest.fixture()
def plain_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def _user(conn, tg=1):
    repo.create_user(conn, tg)
    return repo.get_user_by_tg(conn, tg)


# ------------------------------------------------------------------ парсеры

@pytest.mark.parametrize("text,minutes", [
    ("спал 7 часов", 420), ("Спал 7 часов", 420), ("спал 6ч 40м", 400),
    ("спала 6 ч 40 мин", 400), ("сон 7.5", 450), ("сон 7,5 часа", 450),
    ("спал семь часов", 420), ("спал 450 минут", 450), ("сон: 6:45", 405),
    ("поспал 5 часов", 300), ("спал всего 5 часов", 300), ("спал примерно 6.5 часа", 390),
    ("сон 8ч", 480),
])
def test_parse_sleep_duration(text, minutes):
    got = trk.parse_sleep_text(text)
    assert got and got["duration_min"] == minutes and got["start"] is None, text
    assert got["yesterday"] is False


@pytest.mark.parametrize("text,start,end,minutes", [
    ("лег в 23:30 встал в 7:10", "23:30", "07:10", 460),
    ("легла в 1:15, встала в 8", "01:15", "08:00", 405),
    ("спал с 23:00 до 7:00", "23:00", "07:00", 480),
    ("лёг в 22.45 проснулся в 6.30", "22:45", "06:30", 465),
])
def test_parse_sleep_range(text, start, end, minutes):
    got = trk.parse_sleep_text(text)
    assert got and (got["start"], got["end"], got["duration_min"]) == (start, end, minutes), text


def test_parse_sleep_yesterday_flag():
    assert trk.parse_sleep_text("вчера спал 8 часов")["yesterday"] is True
    assert trk.parse_sleep_text("спал 8 часов")["yesterday"] is False


@pytest.mark.parametrize("text", [
    "", "сон", "спал плохо", "не спал всю ночь", "хочу спать 8 часов", "как я спал?",
    "сколько мне надо спать? 8 часов?", "съел 2 яйца", "спал 20 часов", "спал 5 минут",
    "спал 7 часов и съел бутерброд", "персона 5 часов", "лег в 25:00 встал в 7:00",
    "лег в 23:00 встал в 23:00", "я буду спать 8 часов",
])
def test_parse_sleep_rejects(text):
    assert trk.parse_sleep_text(text) is None, text


@pytest.mark.parametrize("text,steps", [
    ("10 тысяч шагов", 10000), ("шаги 8500", 8500), ("прошел 12000 шагов", 12000),
    ("прошёл 12 000 шагов", 12000), ("10к шагов", 10000), ("8,5 тыс шагов", 8500),
    ("шагов 7000", 7000), ("сегодня 15000 шагов", 15000), ("шагов за день 8500", 8500),
])
def test_parse_steps(text, steps):
    got = trk.parse_steps_text(text)
    assert got and got["steps"] == steps, text


@pytest.mark.parametrize("text", [
    "", "шагов 50", "10 шагов", "хочу 10000 шагов", "съел 2 яйца", "сколько шагов в день?",
    "шаги 8500 км", "500000 шагов",
])
def test_parse_steps_rejects(text):
    assert trk.parse_steps_text(text) is None, text


@pytest.mark.parametrize("text,name,minutes,km", [
    ("тренировка бег 30 минут", "бег 30 мин", 30, None),
    ("зал 1 час", "зал 60 мин", 60, None),
    ("пробежал 5 км за 30 минут", "бег 5 км 30 мин", 30, 5.0),
    ("плавание 45 мин", "плавание 45 мин", 45, None),
    ("тренировка 40 минут", "тренировка 40 мин", 40, None),
    ("йога полчаса", "йога 30 мин", 30, None),
    ("был в зале полтора часа", "зал 90 мин", 90, None),
    ("бег 5 км", "бег 5 км", None, 5.0),
])
def test_parse_workout(text, name, minutes, km):
    got = trk.parse_workout_text(text)
    assert got and (got["name"], got["minutes"], got["km"]) == (name, minutes, km), text


@pytest.mark.parametrize("text", [
    "", "зал", "тренировка", "бег", "хочу пойти в зал на час", "съел 2 яйца", "кофе 15 минут",
    "съел зал 1 час", "тренировка 3 минуты", "тренировка 20 часов", "как мне тренироваться 40 минут?",
])
def test_parse_workout_rejects(text):
    assert trk.parse_workout_text(text) is None, text


def test_tracking_intents():
    assert intents.detect("спал 7 часов") == intents.Intent("sleep_log", "спал 7 часов")
    assert intents.detect("10 тысяч шагов").name == "steps_log"
    assert intents.detect("тренировка бег 30 минут").name == "workout_log"
    # отчёты и еда остаются прежними
    assert intents.detect("как я спал?") is None or intents.detect("как я спал?").name == "sleep"
    assert intents.detect("как прошла неделя?").name == "week"
    assert intents.detect("съел 2 яйца") is None
    assert intents.detect("гречка 150 г") is None


# ------------------------------------------------------------------ сервис

def test_log_sleep_duration_and_range(plain_db):
    user = _user(plain_db)
    res = tracksvc.log_sleep(plain_db, user, duration_min=450)
    assert res["wake_date"] == TODAY.isoformat() and res["duration_min"] == 450
    row = plain_db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert (row["source"], row["duration_min"], row["confidence"]) == ("manual", 450, 1.0)
    assert row["sleep_start_utc"] is None

    res = tracksvc.log_sleep(plain_db, user, date=YESTERDAY, start="23:30", end="7:10")
    assert res["duration_min"] == 460 and res["start"] == "23:30" and res["end"] == "07:10"
    row = plain_db.execute("SELECT * FROM sleep_sessions WHERE wake_date=?",
                           (YESTERDAY.isoformat(),)).fetchone()
    tz = ZoneInfo(user["tz"])
    from datetime import datetime, timezone
    start = datetime.strptime(row["sleep_start_utc"], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc).astimezone(tz)
    end = datetime.strptime(row["sleep_end_utc"], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc).astimezone(tz)
    assert (start.date(), start.strftime("%H:%M")) == (YESTERDAY - timedelta(days=1), "23:30")
    assert (end.date(), end.strftime("%H:%M")) == (YESTERDAY, "07:10")


def test_log_sleep_after_midnight_start_is_same_date(plain_db):
    user = _user(plain_db)
    tracksvc.log_sleep(plain_db, user, start="01:00", end="08:00")
    row = plain_db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert row["duration_min"] == 420
    assert row["sleep_start_utc"][:10] in (TODAY.isoformat(),
                                           (TODAY - timedelta(days=1)).isoformat())


@pytest.mark.parametrize("kwargs", [
    dict(), dict(duration_min=0), dict(duration_min=1500), dict(start="23:00"),
    dict(duration_min=400, score=101), dict(start="25:00", end="07:00"),
    dict(start="abc", end="07:00"),
])
def test_log_sleep_validates(plain_db, kwargs):
    user = _user(plain_db)
    with pytest.raises(ValueError):
        tracksvc.log_sleep(plain_db, user, **kwargs)
    assert plain_db.execute("SELECT COUNT(*) c FROM sleep_sessions").fetchone()["c"] == 0


def test_log_sleep_merges_with_coalesce(plain_db):
    user = _user(plain_db)
    tracksvc.log_sleep(plain_db, user, duration_min=400, score=80)
    tracksvc.log_sleep(plain_db, user, duration_min=430)             # оценку не стираем
    row = plain_db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert (row["duration_min"], row["score"]) == (430, 80)
    assert plain_db.execute("SELECT COUNT(*) c FROM sleep_sessions").fetchone()["c"] == 1


def test_log_activity_merges_and_appends_workouts(plain_db):
    user = _user(plain_db)
    tracksvc.log_activity(plain_db, user, steps=8500)
    tracksvc.log_activity(plain_db, user, workout="бег 30 мин")
    res = tracksvc.log_activity(plain_db, user, workout="зал 60 мин", active_kcal=350)
    assert res["workouts"] == ["бег 30 мин", "зал 60 мин"]
    row = plain_db.execute("SELECT * FROM activity_daily").fetchone()
    assert (row["steps"], row["active_kcal"], row["source"]) == (8500, 350, "manual")
    assert json.loads(row["workouts_json"]) == ["бег 30 мин", "зал 60 мин"]
    tracksvc.log_activity(plain_db, user, steps=9000)                # тренировки не стираем
    row = plain_db.execute("SELECT * FROM activity_daily").fetchone()
    assert row["steps"] == 9000 and json.loads(row["workouts_json"])
    assert plain_db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()["c"] == 1


@pytest.mark.parametrize("kwargs", [
    dict(), dict(steps=-1), dict(steps=300000), dict(active_kcal=20000),
    dict(active_minutes=2000), dict(distance_km=500),
])
def test_log_activity_validates(plain_db, kwargs):
    user = _user(plain_db)
    with pytest.raises(ValueError):
        tracksvc.log_activity(plain_db, user, **kwargs)
    assert plain_db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()["c"] == 0


def test_series_dedupe_across_sources(plain_db):
    """Скриншот и ручная запись за один день — один день в отчёте, скриншот главнее."""
    user = _user(plain_db)
    uid = user["id"]
    day = TODAY.isoformat()
    repo.save_sleep(plain_db, uid, day, {"duration_min": 400, "score": 70}, "manual", 1.0)
    repo.save_sleep(plain_db, uid, day, {"duration_min": 430}, "screenshot", 0.9)
    repo.save_activity(plain_db, uid, day, {"steps": 8000}, "manual", 1.0)
    repo.save_activity(plain_db, uid, day, {"steps": 9100, "active_kcal": 300}, "screenshot", 0.9)
    sleep = repo.sleep_series(plain_db, uid, day, day)
    assert len(sleep) == 1 and sleep[0]["duration_min"] == 430 and sleep[0]["score"] == 70
    act = repo.activity_series(plain_db, uid, day, day)
    assert len(act) == 1 and act[0]["steps"] == 9100 and act[0]["active_kcal"] == 300
    assert tracksvc.sleep_avg_7d(plain_db, user, TODAY) == (430, 1)
    assert tracksvc.steps_avg_7d(plain_db, user, TODAY) == (9100, 1)


def test_seven_day_average_ignores_old_nights(plain_db):
    user = _user(plain_db)
    for back, minutes in ((0, 420), (1, 480), (2, 360), (20, 120)):
        tracksvc.log_sleep(plain_db, user, date=TODAY - timedelta(days=back), duration_min=minutes)
    assert tracksvc.sleep_avg_7d(plain_db, user, TODAY) == (420, 3)
    assert tracksvc.steps_avg_7d(plain_db, user, TODAY) is None


# ------------------------------------------------------------------ generic-скриншоты

def _parsed(screen, metrics, conf=0.95, day=None):
    return {"screen": screen, "date": day, "overall_confidence": conf, "raw_text": ["x"],
            "metrics": {k: {"value": v, "confidence": 0.9} for k, v in metrics.items()}}


def test_apply_parsed_generic_sleep_and_activity(plain_db):
    uid = _user(plain_db)["id"]
    day = TODAY.isoformat()
    kind = screens.apply_parsed(plain_db, uid, _parsed(
        "generic_sleep", {"sleep_duration_min": 445, "sleep_deep_min": 80, "sleep_score": 84},
        day=day), day)
    assert kind == "sleep"
    row = plain_db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert (row["duration_min"], row["deep_min"], row["score"], row["source"],
            row["wake_date"]) == (445, 80, 84, "screenshot", day)
    kind = screens.apply_parsed(plain_db, uid, _parsed(
        "generic_activity", {"steps": 8540, "active_kcal": 410, "distance_km": 6.1}, day=day), day)
    assert kind == "activity"
    row = plain_db.execute("SELECT * FROM activity_daily").fetchone()
    assert (row["steps"], row["active_kcal"], row["distance_km"], row["source"]) == \
        (8540, 410, 6.1, "screenshot")


def test_apply_parsed_generic_rejects_out_of_range_and_empty(plain_db):
    uid = _user(plain_db)["id"]
    assert screens.apply_parsed(plain_db, uid, _parsed("generic_activity", {"steps": 900000}),
                                TODAY.isoformat()) == "unknown"
    assert screens.apply_parsed(plain_db, uid, _parsed("generic_sleep", {}),
                                TODAY.isoformat()) == "unknown"
    assert plain_db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()["c"] == 0


def test_screenshot_prompt_lists_generic_types():
    prompt = foodsvc.load_prompt("screenshot")
    for word in ("generic_sleep", "generic_activity", "Apple Health", "Samsung Health",
                 "Garmin Connect", "Huawei Health", "Google Fit", "Zepp", "Fitbit"):
        assert word in prompt, word


@pytest.mark.parametrize("caption,expected", [
    ("сон", True), ("Apple Health сон", True), ("Garmin шаги", True), ("Samsung Health", True),
    ("Huawei Health", True), ("Zepp активность", True), ("Fitbit", True), ("Google Fit", True),
    ("тренировка сегодня", True), ("спал плохо", True), ("пикук", True),
    ("обед", False), ("борщ", False), ("гречка с курицей", False), ("персонал", False),
])
def test_caption_routing_keywords(caption, expected):
    assert intake._caption_says_screen(caption) is expected


# ------------------------------------------------------------------ process_screenshot

def _image(tmp_path, name="shot.jpg"):
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff\xe0fake-" + name.encode())
    return path


def _mock_parse(monkeypatch, parsed):
    async def fake(image_b64, user_id, conn):  # noqa: ANN001
        return json.loads(json.dumps(parsed))
    monkeypatch.setattr(screens, "parse_screenshot", fake)


@pytest.mark.asyncio
async def test_process_screenshot_applies_confident_sleep(plain_db, tmp_path, monkeypatch):
    user = _user(plain_db)
    _mock_parse(monkeypatch, _parsed("generic_sleep", {"sleep_duration_min": 445},
                                     day=TODAY.isoformat()))
    res = await tracksvc.process_screenshot(plain_db, user, _image(tmp_path))
    assert res["screen"] == "generic_sleep" and res["applied"] == "sleep"
    assert res["needs_confirm"] is False and res["metrics"] == {"sleep_duration_min": 445}
    assert res["date"] == TODAY.isoformat() and res["confidence"] == pytest.approx(0.95)
    assert plain_db.execute("SELECT duration_min FROM sleep_sessions").fetchone()[0] == 445
    raw = plain_db.execute("SELECT * FROM raw_observations WHERE id=?", (res["id"],)).fetchone()
    assert raw["kind"] == "generic_sleep"


@pytest.mark.asyncio
async def test_process_screenshot_low_confidence_waits_for_confirm(plain_db, tmp_path, monkeypatch):
    user = _user(plain_db)
    _mock_parse(monkeypatch, _parsed("generic_activity", {"steps": 8000}, conf=0.6,
                                     day=TODAY.isoformat()))
    res = await tracksvc.process_screenshot(plain_db, user, _image(tmp_path))
    assert res["needs_confirm"] is True and res["applied"] is None and res["reasons"]
    assert plain_db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()["c"] == 0

    assert tracksvc.confirm_screenshot(plain_db, user, res["id"], apply=True) == "activity"
    assert plain_db.execute("SELECT steps FROM activity_daily").fetchone()[0] == 8000
    assert tracksvc.confirm_screenshot(plain_db, user, res["id"], apply=True) is None  # уже решено


@pytest.mark.asyncio
async def test_process_screenshot_reject_and_bad_date(plain_db, tmp_path, monkeypatch):
    user = _user(plain_db)
    _mock_parse(monkeypatch, _parsed("generic_sleep", {"sleep_duration_min": 400},
                                     conf=0.95, day="2019-01-01"))      # дата вне диапазона
    res = await tracksvc.process_screenshot(plain_db, user, _image(tmp_path))
    assert res["needs_confirm"] is True and res["date"] == TODAY.isoformat()
    assert tracksvc.confirm_screenshot(plain_db, user, res["id"], apply=False) is None
    assert plain_db.execute("SELECT COUNT(*) c FROM sleep_sessions").fetchone()["c"] == 0
    assert repo.latest_pending_input(plain_db, user["id"], "screenshot_confirm") is None


@pytest.mark.asyncio
async def test_process_screenshot_unknown_screen(plain_db, tmp_path, monkeypatch):
    user = _user(plain_db)
    _mock_parse(monkeypatch, {"screen": "unknown", "metrics": {}, "overall_confidence": 0.2})
    res = await tracksvc.process_screenshot(plain_db, user, _image(tmp_path))
    assert res["screen"] == "unknown" and res["applied"] is None and not res["needs_confirm"]


@pytest.mark.asyncio
async def test_confirm_screenshot_of_another_user_is_ignored(plain_db, tmp_path, monkeypatch):
    user = _user(plain_db, 1)
    stranger = _user(plain_db, 2)
    _mock_parse(monkeypatch, _parsed("generic_activity", {"steps": 8000}, conf=0.5,
                                     day=TODAY.isoformat()))
    res = await tracksvc.process_screenshot(plain_db, user, _image(tmp_path))
    assert tracksvc.confirm_screenshot(plain_db, stranger, res["id"], apply=True) is None
    assert plain_db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()["c"] == 0
    assert tracksvc.confirm_screenshot(plain_db, user, res["id"], apply=True) == "activity"


# ------------------------------------------------------------------ чат: текст

def _no_food(monkeypatch):
    async def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("сон/шаги/тренировка не должны уходить в разбор еды")
    monkeypatch.setattr(foodsvc, "parse_text", boom)


def _sleep_rows(db):
    return db.execute("SELECT * FROM sleep_sessions ORDER BY wake_date").fetchall()


@pytest.mark.asyncio
async def test_chat_sleep_today_and_average(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    _no_food(monkeypatch)
    await send(dp, bot, "спал 7 часов")
    rows = _sleep_rows(db)
    assert [(r["wake_date"], r["duration_min"], r["source"]) for r in rows] == \
        [(TODAY.isoformat(), 420, "manual")]
    assert "7 ч 00 мин" in session.texts[-1] and "Среднее за 7 дней" in session.texts[-1]
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0

    await send(dp, bot, "вчера спал 6ч 40м")
    assert [(r["wake_date"], r["duration_min"]) for r in _sleep_rows(db)] == \
        [(YESTERDAY.isoformat(), 400), (TODAY.isoformat(), 420)]
    assert "6 ч 40 мин" in session.texts[-1] and "(2 ноч.)" in session.texts[-1]


@pytest.mark.asyncio
async def test_chat_sleep_range_and_decimal(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    _no_food(monkeypatch)
    await send(dp, bot, "лег в 23:30 встал в 7:10")
    row = _sleep_rows(db)[0]
    assert row["duration_min"] == 460 and row["sleep_start_utc"] and row["sleep_end_utc"]
    assert "23:30–07:10" in session.texts[-1]
    await send(dp, bot, "сон 7.5")                    # тот же день: обновляется, не двоится
    rows = _sleep_rows(db)
    assert len(rows) == 1 and rows[0]["duration_min"] == 450


@pytest.mark.asyncio
async def test_chat_steps_and_workouts(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    _no_food(monkeypatch)
    await send(dp, bot, "10 тысяч шагов")
    assert "10 000" in session.texts[-1]
    await send(dp, bot, "тренировка бег 30 минут")
    await send(dp, bot, "зал 1 час")
    assert "Тренировок за день: 2" in session.texts[-1]
    row = db.execute("SELECT * FROM activity_daily").fetchone()
    assert row["steps"] == 10000 and row["source"] == "manual"
    assert json.loads(row["workouts_json"]) == ["бег 30 мин", "зал 60 мин"]
    await send(dp, bot, "вчера 9000 шагов")
    days = {r["local_date"]: r["steps"] for r in db.execute("SELECT * FROM activity_daily")}
    assert days == {TODAY.isoformat(): 10000, YESTERDAY.isoformat(): 9000}
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0


@pytest.mark.asyncio
async def test_chat_food_phrases_still_go_to_food(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    seen = []

    async def fake_parse(text, user_id, conn):  # noqa: ANN001
        seen.append(text)
        return _food("яйцо", 110)

    monkeypatch.setattr(foodsvc, "parse_text", fake_parse)
    for text in ("съел 2 яйца", "выпил кофе 15 минут назад", "хочу спать 8 часов"):
        await send(dp, bot, text)
    assert seen[:2] == ["съел 2 яйца", "выпил кофе 15 минут назад"]
    assert _sleep_rows(db) == [] and db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()[0] >= 2


@pytest.mark.asyncio
async def test_chat_sleep_not_swallowed_as_caption_of_fresh_photo(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("салат", 200)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "food-1"))                  # открыто окно подписи
    meals = db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"]
    await send(dp, bot, "спал 7 часов")
    assert [r["duration_min"] for r in _sleep_rows(db)] == [420]
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == meals


@pytest.mark.asyncio
async def test_chat_tracking_needs_onboarding(db):
    bot, dp, session = build(db)
    await send(dp, bot, "спал 7 часов")
    assert any("/start" in t for t in session.texts)
    assert db.execute("SELECT COUNT(*) c FROM sleep_sessions").fetchone()["c"] == 0


@pytest.mark.asyncio
async def test_chat_report_commands_see_manual_data(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    _no_food(monkeypatch)
    from app.routers import commands
    monkeypatch.setattr(commands, "local_today", lambda tz: TODAY)
    await send(dp, bot, "спал 7 часов")
    await send(dp, bot, "9000 шагов")
    session.texts.clear()
    await send(dp, bot, "/sleep")
    await send(dp, bot, "/week")
    joined = "\n".join(session.texts)
    assert "7 ч 00 мин" in joined and "9 000" in joined


# ------------------------------------------------------------------ чат: скриншоты по подписи

@pytest.mark.asyncio
async def test_chat_photo_with_health_caption_goes_to_screenshot_parser(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    calls = []

    async def fake_parse(image_b64, user_id, conn):  # noqa: ANN001
        calls.append(1)
        return _parsed("generic_sleep", {"sleep_duration_min": 445},
                       day=screens.local_date_of("Europe/Minsk"))

    async def no_food(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("скриншот не должен идти в распознавание еды")

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse)
    monkeypatch.setattr(foodsvc, "parse_photo", no_food)
    monkeypatch.setattr(foodsvc, "parse_photos", no_food)
    await _feed(dp, bot, _photo(bot, "shot-1", caption="Apple Health сон"))
    assert calls == [1]
    row = db.execute("SELECT * FROM sleep_sessions WHERE user_id=?", (uid,)).fetchone()
    assert (row["duration_min"], row["source"]) == (445, "screenshot")
    assert any("Сон записал" in t for t in session.texts)


@pytest.mark.asyncio
async def test_chat_photo_with_activity_caption_and_unknown_screen_falls_back_to_food(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)

    async def fake_parse(image_b64, user_id, conn):  # noqa: ANN001
        return {"screen": "unknown", "metrics": {}, "overall_confidence": 0.1}

    async def fake_food(*a, **k):  # noqa: ANN002, ANN003
        return _food("салат", 200)

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse)
    monkeypatch.setattr(foodsvc, "parse_photo", fake_food)
    monkeypatch.setattr(foodsvc, "parse_photos", fake_food)
    await _feed(dp, bot, _photo(bot, "shot-2", caption="обед после тренировки"))
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_chat_low_confidence_generic_screenshot_confirm_buttons(db, monkeypatch):
    from aiogram.types import CallbackQuery, Update, User
    from tests.test_photo_flow import _ids
    from tests.test_routers_smoke import message
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)

    async def fake_parse(image_b64, user_id, conn):  # noqa: ANN001
        return _parsed("generic_activity", {"steps": 8000}, conf=0.5, day=TODAY.isoformat())

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse)
    await _feed(dp, bot, _photo(bot, "shot-3", caption="Garmin шаги"))
    assert db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()[0] == 0
    pending = repo.latest_pending_input(db, uid, "screenshot_confirm")
    assert pending
    cb = CallbackQuery(id=str(next(_ids)), from_user=User(id=1, is_bot=False, first_name="Т"),
                       chat_instance="x", message=message("карточка").as_(bot),
                       data=f"screen_yes:{pending['id']}").as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))
    assert db.execute("SELECT steps FROM activity_daily").fetchone()[0] == 8000
    assert any("Активность записал" in t for t in session.texts)


@pytest.mark.asyncio
async def test_unrecognized_screenshot_can_be_resent(db, monkeypatch):
    """Экран не опознан, ничего не записано: тот же файл, присланный снова, не блокируется
    ответом «уже записал»."""
    from app import config
    monkeypatch.setattr(config, "_config", config.Config(_env_file=None, chat_logs_food=False,
                                                       allow_public_bot=True))
    bot, dp, session = build(db)
    await onboard(dp, bot)
    calls = []

    async def fake_parse(image_b64, user_id, conn):  # noqa: ANN001
        calls.append(1)
        if len(calls) == 1:
            return {"screen": "unknown", "metrics": {}}
        return _parsed("generic_activity", {"steps": 8000}, conf=0.95,
                       day=screens.local_date_of("Europe/Minsk"))

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse)
    await _feed(dp, bot, _photo(bot, "shot-resend"))
    assert db.execute("SELECT COUNT(*) c FROM activity_daily").fetchone()[0] == 0
    await _feed(dp, bot, _photo(bot, "shot-resend"))
    assert len(calls) == 2
    assert db.execute("SELECT steps FROM activity_daily").fetchone()[0] == 8000
    assert not any("уже записал" in t for t in session.texts)


# ------------------------------------------------------------------ проводка

def test_tracking_router_registered_and_help_mentions_phrases():
    from app.bot import ROUTERS
    from app.routers import tracking
    from app.services import assistant
    assert tracking.router in ROUTERS
    assert "спал 7 часов" in assistant.HELP_TEXT and "шагов" in assistant.HELP_TEXT
