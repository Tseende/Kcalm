"""Планировщик: напоминание про воду, продолжение эпизода тяги, параллельная рассылка."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import scheduler
from app.db import connect, migrate
from app.services import craving
from app.storage import repo


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


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
async def test_one_broken_user_does_not_block_others(db):
    _user(db, tg=10)
    uid2, _ = _user(db, tg=12)
    db.execute("UPDATE users SET tz='Not/AZone' WHERE id=?", (uid2,))
    db.commit()
    bot = FakeBot()
    await scheduler.dispatch_due_reminders(db, bot, now_utc=_utc_for_local(16, 0))
    assert [chat for chat, _ in bot.sent] == [10]
