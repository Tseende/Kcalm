"""Учёт воды: разбор фраз, ориентир, команда /water и свободный текст."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.db import connect, migrate, user_version
from app.domain.water import extract_water, is_water_only, parse_amount, water_goal_ml
from app.services import food as foodsvc
from app.storage import repo
from tests.test_routers_smoke import build, onboard, send


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


@pytest.mark.parametrize("text, ml", [
    ("выпил 2 стакана воды", 500),
    ("Выпила полтора литра воды", 1500),
    ("вода 500 мл", 500),
    ("воды 0,5 л", 500),
    ("стакан воды", 250),
    ("вода 500", 500),
    ("500 воды", 500),
    ("полстакана воды", 125),
    ("пол-литра воды", 500),
    ("бутылка воды", 500),
    ("три стакана холодной воды", 750),
])
def test_extract_water(text, ml):
    got, rest = extract_water(text)
    assert got == ml
    assert is_water_only(rest)


@pytest.mark.parametrize("text", [
    "съел курицу 200 г", "чашка чая с водой", "вода 2 кг", "выпил 10 литров воды",
    "гречка 150 г",
])
def test_not_water(text):
    assert extract_water(text)[0] == 0


def test_mixed_message_keeps_food_part():
    ml, rest = extract_water("выпил стакан воды и съел кашу 200 г")
    assert ml == 250
    assert not is_water_only(rest) and "каш" in rest


def test_parse_amount():
    assert parse_amount("300") == 300
    assert parse_amount("2 стакана") == 500
    assert parse_amount("0,5л") == 500
    assert parse_amount("абв") is None
    assert parse_amount("л") is None


def test_goal_is_clamped():
    assert water_goal_ml(85) == 2600
    assert water_goal_ml(40) == 1500
    assert water_goal_ml(150) == 3000
    assert water_goal_ml(None) == 2000


def test_migration_creates_water_table(db):
    assert user_version(db) >= 5
    uid = repo.create_user(db, 1)
    repo.add_water(db, uid, "2026-10-30", "10:00", 250, "text", "стакан воды")
    with pytest.raises(Exception):
        repo.add_water(db, uid, "2026-10-30", "10:01", 5, "text")       # меньше 10 мл
    assert repo.day_water_ml(db, uid, "2026-10-30") == 250


def test_delete_water_only_own_entry(db):
    mine = repo.create_user(db, 1)
    other = repo.create_user(db, 2)
    wid = repo.add_water(db, mine, "2026-10-30", "10:00", 250, "text")
    assert repo.delete_water(db, other, wid) is False
    assert repo.delete_water(db, mine, wid) is True


@pytest.mark.asyncio
async def test_text_voice_flow_logs_water_without_llm(db, monkeypatch):
    async def no_llm(*a, **k):
        raise AssertionError("вода не должна уходить в LLM")
    monkeypatch.setattr(foodsvc, "parse_text", no_llm)

    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "выпил 2 стакана воды")
    assert repo.day_water_ml(db, uid, _today(db, uid)) == 500
    await send(dp, bot, "/water 300")
    assert repo.day_water_ml(db, uid, _today(db, uid)) == 800
    await send(dp, bot, "/water undo")
    assert repo.day_water_ml(db, uid, _today(db, uid)) == 500
    await send(dp, bot, "/today")                     # не падает, показывает воду


def _today(db, uid):
    from app.timeutils import local_today
    user = db.execute("SELECT tz FROM users WHERE id=?", (uid,)).fetchone()
    return local_today(user["tz"]).isoformat()
