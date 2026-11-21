"""Планировщик: напоминание про воду, продолжение эпизода тяги, параллельная рассылка."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage, SendPhoto

from app import scheduler
from app.db import connect, migrate
from app.services import craving
from app.services.reminders import TEMPLATES
from app.storage import repo


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.photos: list[str] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))

    async def send_photo(self, chat_id, photo, caption, **kwargs):
        await self.send_message(chat_id, caption, **kwargs)
        self.photos.append(photo.filename)


def test_reminder_images_cover_scheduled_notifications():
    kinds = set(TEMPLATES) | {"morning", "midday", "kitchen_close", "evening", "weekly", "custom", "rule"}
    assert all((scheduler.REMINDER_IMAGES / f"{kind}.jpg").is_file() for kind in kinds)


@pytest.mark.asyncio
async def test_reminder_image_rejection_falls_back_to_text():
    class RejectPhotoBot(FakeBot):
        async def send_photo(self, chat_id, photo, caption, **kwargs):
            raise TelegramBadRequest(SendPhoto(chat_id=chat_id, photo=photo), "PHOTO_INVALID")

    bot = RejectPhotoBot()
    await scheduler._send_notification(bot, 10, "Время завтрака", "breakfast")
    assert bot.sent == [(10, "Время завтрака")]


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def _user(db, tg=10, tz="Europe/Minsk"):
    uid = repo.create_user(db, tg, tz=tz)
    repo.update_user(db, uid, sex="female", onboarded=1, start_weight_kg=70, height_cm=165,
                     eating_cutoff="21:00")
    repo.set_reminder(db, uid, "water", "16:00")
    return uid, repo.get_user_by_tg(db, tg)


def _utc_for_local(hh: int, mm: int, tz="Europe/Minsk") -> datetime:
    local = datetime(2026, 10, 30, hh, mm, tzinfo=ZoneInfo(tz))
    return local.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_water_reminder_only_when_low(db):
    uid, user = _user(db)
    bot = FakeBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_utc_for_local(16, 0))
    assert len(bot.sent) == 1 and "💧" in bot.sent[0][1]
    assert bot.photos == ["water.jpg"]

    # достаточно воды: напоминания нет (и отметка отправки не мешает)
    uid2, user2 = _user(db, tg=11)
    repo.add_water(db, uid2, "2026-10-30", "12:00", 1500, "text")
    bot2 = FakeBot()
    await scheduler.dispatch_due_reminders(db, bot2, now_utc=_utc_for_local(16, 0))
    assert all(chat != 11 for chat, _ in bot2.sent)


@pytest.mark.asyncio
async def test_reminders_not_duplicated_within_same_minute(db):
    _user(db)
    bot = FakeBot()
    now = _utc_for_local(16, 0)
    await scheduler.dispatch_due_reminders(db, bot, now_utc=now)
    await scheduler.dispatch_due_reminders(db, bot, now_utc=now)
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_craving_followup_sent_once(db):
    uid, user = _user(db)
    eid, _, _ = craving.start_episode(db, user, "хочу есть")
    craving.schedule_followup(db, eid, minutes=-1)
    bot = FakeBot()
    await scheduler.dispatch_craving_followups(db, bot)
    await scheduler.dispatch_craving_followups(db, bot)
    assert len(bot.sent) == 1 and "10 минут" in bot.sent[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    RuntimeError("temporary network failure"),
    TelegramRetryAfter(SendMessage(chat_id=10, text="test"), "flood control", retry_after=180),
    TelegramForbiddenError(SendMessage(chat_id=10, text="test"), "bot was blocked"),
])
async def test_followup_records_delivery_only_after_success(db, monkeypatch, error):
    uid, user = _user(db)
    eid, _, _ = craving.start_episode(db, user, "хочу есть")
    craving.schedule_followup(db, eid, minutes=-1)
    history = repo.craving_messages(db, eid)
    monkeypatch.setattr(craving, "followup_message", AsyncMock(return_value="Как ты &amp; всё ли хорошо?"))
    bot = FakeBot()
    transport = AsyncMock(side_effect=error)
    bot.send_message = transport

    await scheduler.dispatch_craving_followups(db, bot)
    event = repo.get_craving(db, uid, eid)
    assert event["followup_sent"] == 0
    assert repo.craving_messages(db, eid) == history
    assert repo.due_craving_followups(db) == []
    if isinstance(error, TelegramForbiddenError):
        assert event["followup_at_utc"] is None
        return
    delay = (datetime.fromisoformat(event["followup_at_utc"]).replace(tzinfo=timezone.utc)
             - datetime.now(timezone.utc)).total_seconds()
    assert delay > (170 if isinstance(error, TelegramRetryAfter) else 50)

    craving.schedule_followup(db, eid, minutes=-1)
    transport.side_effect = None
    await scheduler.dispatch_craving_followups(db, bot)
    assert repo.get_craving(db, uid, eid)["followup_sent"] == 1
    assert repo.craving_messages(db, eid)[-1]["text"] == "Как ты & всё ли хорошо?"


@pytest.mark.asyncio
async def test_one_broken_user_does_not_block_others(db):
    _user(db, tg=10)
    uid2, _ = _user(db, tg=12)
    db.execute("UPDATE users SET tz='Not/AZone' WHERE id=?", (uid2,))
    db.commit()
    bot = FakeBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_utc_for_local(16, 0))
    assert [chat for chat, _ in bot.sent] == [10]


class _CloudProxy:
    """Имитация облачного соединения: cloud=True и spawn() даёт независимое соединение к тому же файлу."""
    cloud = True

    def __init__(self, conn, path):
        self._conn, self._path = conn, path
        self.spawned = 0

    def spawn(self):
        self.spawned += 1
        return connect(self._path)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def cloud_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield _CloudProxy(conn, path), conn
    conn.close()
    os.unlink(path)


@pytest.mark.asyncio
async def test_cloud_tick_idle_skips_shared_connection(cloud_db):
    proxy, conn = cloud_db
    _user(conn)
    bot = FakeBot()
    # 12:00 — ничего не наступило: общее соединение не используется, сообщений нет
    await scheduler.dispatch_due_reminders(proxy, bot, now_utc=_utc_for_local(12, 0))
    assert proxy.spawned == 1 and bot.sent == []


@pytest.mark.asyncio
async def test_cloud_tick_sends_when_due(cloud_db):
    proxy, conn = cloud_db
    _user(conn)
    bot = FakeBot()
    await scheduler.dispatch_due_reminders(proxy, bot, now_utc=_utc_for_local(16, 0))
    assert proxy.spawned == 1 and len(bot.sent) == 1 and "💧" in bot.sent[0][1]


@pytest.mark.asyncio
async def test_cloud_precheck_failure_falls_back(cloud_db, monkeypatch):
    proxy, conn = cloud_db
    _user(conn)
    monkeypatch.setattr(scheduler, "_has_due_work", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    bot = FakeBot()
    await scheduler.dispatch_due_reminders(proxy, bot, now_utc=_utc_for_local(16, 0))
    assert len(bot.sent) == 1


def test_keepalive_window_slides_with_activity(monkeypatch):
    """Окно keep-alive считается от последней активности: час после заходов, потом сон."""
    monkeypatch.setattr(scheduler, "_last_activity", None)
    assert not scheduler.recently_active(1)                  # никто не заходил, не пингуем
    scheduler.touch_activity()
    t0 = scheduler._last_activity
    assert scheduler.recently_active(1, now=t0 + 59 * 60)
    assert not scheduler.recently_active(1, now=t0 + 61 * 60)
    assert scheduler.recently_active(5, now=t0 + 4 * 3600)   # другая длина окна
    assert not scheduler.recently_active(0, now=t0)          # 0 = функция выключена


@pytest.mark.asyncio
async def test_keepalive_ping_only_when_active(monkeypatch):
    import httpx
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): calls.append(url)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    cfg = scheduler.get_config().model_copy(update={"keepalive_hours": 1.0, "keepalive_window": "",
                                                    "webhook_base_url": "https://x.onrender.com"})
    monkeypatch.setattr(scheduler, "get_config", lambda: cfg)
    monkeypatch.setattr(scheduler, "_last_activity", None)
    await scheduler.keepalive_ping()
    assert calls == []                                       # нет активности: молчим, сервис уснёт
    scheduler.touch_activity()
    await scheduler.keepalive_ping()
    assert calls == ["https://x.onrender.com/healthz"]


def test_keepalive_hours_default_depends_on_render(monkeypatch):
    from app.config import Config
    for var in ("RENDER", "RENDER_EXTERNAL_URL", "KEEPALIVE_HOURS"):
        monkeypatch.delenv(var, raising=False)
    assert Config(_env_file=None).keepalive_hours is None
    assert Config(_env_file=None).keepalive_hours_effective == 0          # локально выкл.
    monkeypatch.setenv("RENDER", "true")
    assert Config(_env_file=None).keepalive_hours_effective == 1.0         # на Render по умолчанию 1 ч
    monkeypatch.delenv("RENDER")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://x.onrender.com")
    assert Config(_env_file=None).keepalive_hours_effective == 1.0
    monkeypatch.setenv("KEEPALIVE_HOURS", "0")
    assert Config(_env_file=None).keepalive_hours_effective == 0           # явный 0 выключает
    monkeypatch.setenv("KEEPALIVE_HOURS", "3")
    assert Config(_env_file=None).keepalive_hours_effective == 3.0


def test_build_scheduler_registers_keepalive_by_default_on_render(monkeypatch):
    from app.config import Config
    for var in ("RENDER", "RENDER_EXTERNAL_URL", "KEEPALIVE_HOURS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://x.onrender.com")
    monkeypatch.setattr(scheduler, "get_config", lambda: Config(_env_file=None))
    assert scheduler.build_scheduler(None, None).get_job("keepalive") is not None
    monkeypatch.setenv("KEEPALIVE_HOURS", "0")
    assert scheduler.build_scheduler(None, None).get_job("keepalive") is None
