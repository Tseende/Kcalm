"""Свои напоминания: API (CRUD, проверки, чужие id, лимит), расчёт времени, умный пропуск,
экранирование текста, осторожный режим, утреннее сообщение и миграция 027."""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import scheduler
from app.db import connect, migrate, user_version
from app.services import reminders as remsvc
from app.storage import repo
from tests.test_scheduler import FakeBot
from tests.test_web_api import H, _fixed_now, cfg, client, db, make_client, user_id  # noqa: F401  (фикстуры)

SCRATCH = Path("/private/tmp/claude-502/-Users-Zhan-Python---------------/"
               "2ab85c70-51ef-4c37-ad0b-150a5425b8b6/scratchpad")
REAL_DB = Path(__file__).resolve().parent.parent / "data" / "health.db"


class KbBot(FakeBot):
    """FakeBot, который ещё помнит клавиатуру (кнопку приложения)."""

    def __init__(self) -> None:
        super().__init__()
        self.markup: list = []

    async def send_message(self, chat_id, text, **kwargs):
        self.markup.append(kwargs.get("reply_markup"))
        await super().send_message(chat_id, text, **kwargs)


def _now(hh: int, mm: int, day: int = 30, tz="Europe/Minsk") -> datetime:
    """UTC-момент, которому в Минске соответствует 2026-10-{day} hh:mm (30 октября 2026 — пятница)."""
    return datetime(2026, 10, day, hh, mm, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def _local(hh: int, mm: int, day: int = 30) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=ZoneInfo("Europe/Minsk"))


def _user(db, tg=10):
    uid = repo.create_user(db, tg, tz="Europe/Minsk")
    repo.update_user(db, uid, sex="female", onboarded=1, start_weight_kg=70, height_cm=165, eating_cutoff="21:00")
    return uid, repo.get_user_by_tg(db, tg)


def _add(db, user, **data):
    data.setdefault("time", "10:00")
    return remsvc.create(db, user, data)


# ------------------------------------------------------------------ API

@pytest.mark.asyncio
async def test_api_lists_custom_and_templates(client):
    body = await (await client.get("/api/reminders", headers=H)).json()
    assert body["custom"] == []
    keys = {t["key"] for t in body["templates"]}
    assert {"breakfast", "lunch", "dinner", "snack", "water", "steps", "sleep", "weigh", "walk",
            "vitamins", "bedtime", "screen_off"} <= keys
    kitchen = next(i for i in body["reminders"] if i["kind"] == "kitchen_close")
    assert kitchen["fixed_time"] is True and kitchen["time"] == "20:45"


@pytest.mark.asyncio
async def test_api_kitchen_close_toggle(client, db, user_id):
    r = await client.put("/api/reminders/kitchen_close", json={"enabled": True, "time": "03:00"}, headers=H)
    assert r.status == 200
    row = repo.due_reminders(db, user_id, "kitchen_close")
    assert row["enabled"] == 1 and row["local_time"] == "20:45"          # время считается от «Не есть после»


@pytest.mark.asyncio
async def test_api_create_from_template_and_custom_crud(client):
    r = await client.post("/api/user-reminders", json={"template": "water", "time": "09:30"}, headers=H)
    assert r.status == 201
    water = (await r.json())["reminder"]
    assert water["template"] == "water" and water["every_min"] == 120 and water["until_time"] == "20:00"
    assert water["time"] == "09:30" and water["days"] == "" and water["enabled"] is True

    r = await client.post("/api/user-reminders", json={
        "title": "Растяжка", "text": "Пять минут растяжки", "time": "07:00", "days": "01234"}, headers=H)
    assert r.status == 201
    mine = (await r.json())["reminder"]
    assert mine["template"] == "custom" and mine["days"] == "01234"

    r = await client.patch(f"/api/user-reminders/{mine['id']}", json={
        "title": "Зарядка", "days": [5, "6"], "enabled": False, "every_min": 60, "until_time": "08:00"}, headers=H)
    assert r.status == 200
    body = await r.json()
    upd = next(x for x in body["custom"] if x["id"] == mine["id"])
    assert upd["title"] == "Зарядка" and upd["days"] == "56" and upd["enabled"] is False and upd["every_min"] == 60
    assert [x["time"] for x in body["custom"]] == ["07:00", "09:30"]       # сортировка по времени

    r = await client.delete(f"/api/user-reminders/{mine['id']}", headers=H)
    assert r.status == 200 and [x["id"] for x in (await r.json())["custom"]] == [water["id"]]
    assert (await client.delete(f"/api/user-reminders/{mine['id']}", headers=H)).status == 404


@pytest.mark.asyncio
async def test_api_validation(client):
    ok_body = {"title": "Т", "text": "Привет", "time": "10:00"}
    bad_bodies = [
        {**ok_body, "title": ""}, {**ok_body, "title": "я" * 61}, {**ok_body, "text": ""},
        {**ok_body, "text": "я" * 301}, {**ok_body, "time": "25:00"}, {**ok_body, "time": "7"},
        {**ok_body, "days": "78"}, {**ok_body, "days": "00"}, {**ok_body, "days": "ab"},
        {**ok_body, "every_min": 45, "until_time": "12:00"},
        {**ok_body, "every_min": 60},                                      # нет «до»
        {**ok_body, "every_min": 60, "until_time": "10:00"},               # «до» не позже начала
        {**ok_body, "every_min": 60, "until_time": "09:00"},
        {"template": "nope", "time": "10:00"}, {"template": ["x"]},
        {"title": "Т", "time": "10:00"},                                   # нет текста
        {"title": "Т", "text": "Привет"},                                  # нет времени
        {**ok_body, "enabled": "yes"},
    ]
    for body in bad_bodies:
        r = await client.post("/api/user-reminders", json=body, headers=H)
        assert r.status == 400, body
        assert (await r.json())["error"] == "bad_field"
    assert (await client.post("/api/user-reminders", json=ok_body, headers=H)).status == 201
    assert (await client.post("/api/user-reminders", json={**ok_body, "days": "0123456"}, headers=H)).status == 201


@pytest.mark.asyncio
async def test_api_idor_and_limit(client, db, user_id):
    _, other = _user(db, tg=555)
    foreign = _add(db, other, title="Чужое", text="x")
    r = await client.patch(f"/api/user-reminders/{foreign['id']}", json={"title": "Взлом"}, headers=H)
    assert r.status == 404
    assert (await client.delete(f"/api/user-reminders/{foreign['id']}", headers=H)).status == 404
    assert db.execute("SELECT title FROM user_reminders WHERE id=?", (foreign["id"],)).fetchone()[0] == "Чужое"
    assert (await client.patch("/api/user-reminders/abc", json={}, headers=H)).status == 404
    body = await (await client.get("/api/reminders", headers=H)).json()
    assert body["custom"] == []                                            # чужие не видны

    for i in range(remsvc.MAX_PER_USER):
        assert (await client.post("/api/user-reminders", json={"template": "walk"}, headers=H)).status == 201
    r = await client.post("/api/user-reminders", json={"template": "walk"}, headers=H)
    assert r.status == 409 and (await r.json())["error"] == "limit"


@pytest.mark.asyncio
async def test_api_care_mode_hides_weigh_template(client, db, user_id):
    assert next(t for t in (await (await client.get("/api/reminders", headers=H)).json())["templates"]
                if t["key"] == "weigh")["available"] is True
    repo.update_user(db, user_id, care_mode=1)
    body = await (await client.get("/api/reminders", headers=H)).json()
    assert next(t for t in body["templates"] if t["key"] == "weigh")["available"] is False
    assert (await client.post("/api/user-reminders", json={"template": "weigh"}, headers=H)).status == 400
    assert (await client.post("/api/user-reminders", json={"template": "walk"}, headers=H)).status == 201


@pytest.mark.asyncio
async def test_templates_have_no_calories_or_gendered_words():
    for key, t in remsvc.TEMPLATES.items():
        low = t["text"].lower()
        assert "ккал" not in low and "калор" not in low, key
        for bad in ("(а)", "спал", "ел ", "пил ", "съел"):
            assert bad not in low, (key, bad)


# ------------------------------------------------------------------ когда слать

def test_is_due_exact_and_weekdays():
    assert remsvc.is_due("10:00", None, None, None, _local(10, 0))
    assert not remsvc.is_due("10:00", None, None, None, _local(10, 1))
    # 30 октября 2026 — пятница (4), 31 — суббота (5)
    assert remsvc.is_due("10:00", "01234", None, None, _local(10, 0, 30))
    assert not remsvc.is_due("10:00", "01234", None, None, _local(10, 0, 31))
    assert remsvc.is_due("10:00", "56", None, None, _local(10, 0, 31))


def test_is_due_repeat_window():
    args = ("10:00", None, 120, "20:00")
    for hh in (10, 12, 14, 16, 18, 20):
        assert remsvc.is_due(*args, _local(hh, 0)), hh
    for hh, mm in ((11, 0), (10, 1), (12, 30), (21, 0), (22, 0), (9, 0)):
        assert not remsvc.is_due(*args, _local(hh, mm)), (hh, mm)
    assert remsvc.is_due("10:00", None, 90, "13:00", _local(11, 30))
    assert remsvc.is_due("10:00", None, 90, "13:00", _local(13, 0))
    assert not remsvc.is_due("10:00", None, 90, "13:00", _local(14, 30))   # вне окна
    assert not remsvc.is_due("10:00", "5", 120, "20:00", _local(12, 0, 30))  # пятница не в днях


@pytest.mark.asyncio
async def test_dispatch_custom_repeat_count(db):
    uid, user = _user(db)
    _add(db, user, title="Вода", text="Попей воды", time="10:00", every_min=120, until_time="14:00")
    bot = KbBot()
    for hh, mm in ((9, 59), (10, 0), (10, 0), (11, 0), (12, 0), (12, 0), (14, 0), (16, 0)):
        await scheduler.dispatch_due_reminders(db, bot, now_utc=_now(hh, mm))
    assert len(bot.sent) == 3                                              # 10:00, 12:00, 14:00; повторы в минуту не шлём
    assert db.execute("SELECT last_sent_at FROM user_reminders").fetchone()[0] == "2026-10-30 14:00"


@pytest.mark.asyncio
async def test_dispatch_respects_disabled_and_days(db):
    uid, user = _user(db)
    off = _add(db, user, title="A", text="a", time="10:00", enabled=False)
    _add(db, user, title="B", text="b", time="10:00", days="0123")        # пятница не входит
    _add(db, user, title="C", text="c", time="10:00", days="4")
    bot = KbBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_now(10, 0))
    assert [t for _, t in bot.sent] == ["c"]
    assert off["enabled"] is False


@pytest.mark.asyncio
async def test_custom_text_is_html_escaped(db):
    uid, user = _user(db)
    _add(db, user, title="X", text="<b>жирно</b> & <a href='x'>ссылка</a>", time="10:00")
    bot = KbBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_now(10, 0))
    assert bot.sent[0][1] == "&lt;b&gt;жирно&lt;/b&gt; &amp; &lt;a href='x'&gt;ссылка&lt;/a&gt;"


@pytest.mark.asyncio
async def test_template_sends_app_button_when_url_configured(db, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "_config", config.Config(_env_file=None, allow_public_bot=True,
                                                       webapp_url="https://example.test/app"))
    uid, user = _user(db)
    _add(db, user, template="breakfast", time="08:30")
    _add(db, user, template="walk", time="08:30")
    bot = KbBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_now(8, 30))
    assert len(bot.sent) == 2
    kbs = [m for m in bot.markup if m is not None]
    assert len(kbs) == 1
    assert "screen=add_meal" in kbs[0].inline_keyboard[0][0].web_app.url


# ------------------------------------------------------------------ умный пропуск

async def _run(db, hh, mm):
    bot = KbBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_now(hh, mm))
    return bot


def _meal(db, uid, meal_type, hhmm):
    db.execute("INSERT INTO meals (user_id, eaten_at_utc, local_date, local_time, meal_type, source, kcal_total) "
               "VALUES (?, '2026-10-30 05:00:00', '2026-10-30', ?, ?, 'text', 300)", (uid, hhmm, meal_type))
    db.commit()


@pytest.mark.asyncio
async def test_skip_breakfast_when_meal_logged(db):
    uid, user = _user(db)
    _add(db, user, template="breakfast", time="08:30")
    assert len((await _run(db, 8, 30)).sent) == 1                          # ничего не записано
    db.execute("UPDATE user_reminders SET last_sent_at=NULL")
    _meal(db, uid, "breakfast", "08:00")
    assert (await _run(db, 8, 30)).sent == []
    # отметка ставится и при пропуске
    assert db.execute("SELECT last_sent_at FROM user_reminders").fetchone()[0] == "2026-10-30 08:30"


@pytest.mark.asyncio
async def test_skip_lunch_and_dinner_use_windows(db):
    uid, user = _user(db)
    _add(db, user, template="lunch", time="13:00")
    _add(db, user, template="dinner", time="19:00")
    _meal(db, uid, "snack", "11:30")                                       # любой приём с 11:00 считается обедом
    assert (await _run(db, 13, 0)).sent == []
    assert len((await _run(db, 19, 0)).sent) == 1                          # до 17:00 ужином не засчитывается
    db.execute("UPDATE user_reminders SET last_sent_at=NULL")
    _meal(db, uid, "dinner", "18:30")
    assert (await _run(db, 19, 0)).sent == []


@pytest.mark.asyncio
async def test_deleted_meal_does_not_skip(db):
    uid, user = _user(db)
    _add(db, user, template="breakfast", time="08:30")
    _meal(db, uid, "breakfast", "08:00")
    db.execute("UPDATE meals SET deleted_at='2026-10-30'")
    db.commit()
    assert len((await _run(db, 8, 30)).sent) == 1


@pytest.mark.asyncio
async def test_skip_steps_sleep_weigh(db):
    uid, user = _user(db)
    _add(db, user, template="steps", time="21:30")
    _add(db, user, template="sleep", time="09:00")
    _add(db, user, template="weigh", time="07:30")
    assert len((await _run(db, 21, 30)).sent) == 1
    assert len((await _run(db, 9, 0)).sent) == 1
    assert len((await _run(db, 7, 30)).sent) == 1
    db.execute("UPDATE user_reminders SET last_sent_at=NULL")
    repo.save_activity(db, uid, "2026-10-30", {"steps": 5000}, "manual", 1.0)
    repo.save_sleep(db, uid, "2026-10-30", {"duration_min": 420}, "manual", 1.0)
    db.execute("INSERT INTO body_measurements (user_id, local_date, weight_kg, source) VALUES (?, '2026-10-30', 70, 'manual')",
               (uid,))
    db.commit()
    for hh, mm in ((21, 30), (9, 0), (7, 30)):
        assert (await _run(db, hh, mm)).sent == []


@pytest.mark.asyncio
async def test_skip_water_when_goal_met_and_shows_progress(db):
    uid, user = _user(db)
    _add(db, user, template="water", time="10:00")
    bot = await _run(db, 10, 0)
    assert len(bot.sent) == 1 and "Сегодня выпито 0 из" in bot.sent[0][1]
    db.execute("UPDATE user_reminders SET last_sent_at=NULL")
    repo.add_water(db, uid, "2026-10-30", "09:00", 2100, "text")
    repo.add_water(db, uid, "2026-10-30", "09:10", 1000, "text")
    assert (await _run(db, 12, 0)).sent == []


@pytest.mark.asyncio
async def test_care_mode_never_sends_weigh_reminder(db):
    uid, user = _user(db)
    _add(db, user, template="weigh", time="07:30")
    repo.update_user(db, uid, care_mode=1)
    assert (await _run(db, 7, 30)).sent == []


@pytest.mark.asyncio
async def test_failure_of_one_reminder_does_not_block_others(db):
    uid, user = _user(db)
    a = _add(db, user, title="Первое", text="a", time="10:00")
    _add(db, user, title="Второе", text="b", time="10:00")

    class Flaky(KbBot):
        async def send_message(self, chat_id, text, **kw):
            if text == "a":
                raise RuntimeError("telegram down")
            await super().send_message(chat_id, text, **kw)
    bot = Flaky()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_now(10, 0))
    assert [t for _, t in bot.sent] == ["b"]
    assert db.execute("SELECT last_sent_at FROM user_reminders WHERE id=?", (a["id"],)).fetchone()[0] is None


def test_has_due_work_sees_custom_reminders(db):
    uid, user = _user(db)
    assert scheduler._has_due_work(db, _now(10, 0)) is False
    _add(db, user, title="X", text="x", time="10:00", every_min=60, until_time="12:00")
    assert scheduler._has_due_work(db, _now(11, 0)) is True
    assert scheduler._has_due_work(db, _now(11, 1)) is False
    db.execute("UPDATE user_reminders SET last_sent_at='2026-10-30 11:00'")
    db.commit()
    assert scheduler._has_due_work(db, _now(11, 0)) is False


# ------------------------------------------------------------------ утро

@pytest.mark.asyncio
async def test_morning_reminder_now_sends_message(db):
    uid, user = _user(db)
    repo.set_reminder(db, uid, "morning", "08:30")
    bot = await _run(db, 8, 30)
    assert len(bot.sent) == 1 and "план на день" in bot.sent[0][1].lower()
    assert "ккал" not in bot.sent[0][1].lower()
    repo.update_user(db, uid, care_mode=1)
    db.execute("UPDATE reminders SET last_sent_at=NULL")
    db.commit()
    bot2 = await _run(db, 8, 30)
    assert len(bot2.sent) == 1 and "ккал" not in bot2.sent[0][1].lower()


# ------------------------------------------------------------------ миграция 027

def test_migration_027_on_copy_of_real_db_and_rerun():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if not REAL_DB.exists():
        pytest.skip("нет data/health.db")
    copy = SCRATCH / "health_copy_027.db"
    for suffix in ("", "-wal", "-shm"):
        if (SCRATCH / f"health_copy_027.db{suffix}").exists():
            (SCRATCH / f"health_copy_027.db{suffix}").unlink()
    src = sqlite3.connect(REAL_DB)
    dst = sqlite3.connect(copy)
    src.backup(dst)                                    # согласованная копия, оригинал не меняется
    src.close()
    dst.close()
    conn = connect(str(copy))
    before = user_version(conn)
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert migrate(conn) >= 27
    assert user_version(conn) >= max(before, 27)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user_reminders)")}
    assert {"id", "user_id", "template", "title", "text", "local_time", "days", "every_min", "until_time",
            "enabled", "last_sent_at", "created_at"} <= cols
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == users
    # повторный прогон безвреден, и каскадное удаление работает
    conn.executescript(Path(remsvc.__file__).resolve().parents[1].joinpath(
        "migrations", "027_user_reminders.sql").read_text(encoding="utf-8"))
    uid = repo.create_user(conn, 987654321)
    conn.execute("INSERT INTO user_reminders (user_id, title, local_time) VALUES (?, 'x', '10:00')", (uid,))
    conn.commit()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM user_reminders").fetchone()[0] == 0
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
