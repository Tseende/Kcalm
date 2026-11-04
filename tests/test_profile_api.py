"""Профиль, ручная норма, напоминания и удаление данных из Mini App."""
from __future__ import annotations

import pytest

from app.storage import repo
from tests.test_web_api import H, _fixed_now, cfg, client, db, make_client, user_id  # noqa: F401  (фикстуры)

pytestmark = pytest.mark.asyncio


async def test_profile_get_shape(client):
    body = await (await client.get("/api/profile", headers=H)).json()
    p = body["profile"]
    assert p["sex"] == "male" and p["height_cm"] == 178.0 and p["goal_weight_kg"] == 80.0
    assert p["activity"] in (1, 2, 3, 4) and p["birth_year"] is None
    assert body["targets"]["kcal"] == 1650


async def test_profile_patch_recomputes_target(client, db, user_id):
    r = await client.patch("/api/profile", json={"birth_year": 1990, "activity": 3, "rate_kg_week": 0.5}, headers=H)
    assert r.status == 200
    body = await r.json()
    assert body["profile"]["birth_year"] == 1990 and body["profile"]["activity"] == 3
    assert body["result"]["target"]["new"] == body["targets"]["kcal"]
    assert body["targets"]["kcal"] != 1650
    assert repo.current_target(db, user_id)["reason"] == "profile"


async def test_profile_patch_validation(client):
    for bad in ({"height_cm": 20}, {"sex": "x"}, {"eating_cutoff": "25:99"}, {"tz": "Mars/Base"}, {"birth_year": 1800}):
        r = await client.patch("/api/profile", json=bad, headers=H)
        assert r.status == 400, bad
    # цель ниже ИМТ 18,5 не ставим
    r = await client.patch("/api/profile", json={"goal_weight_kg": 40}, headers=H)
    assert r.status == 400


async def test_cutoff_moves_kitchen_reminder_only_without_recalc(client, db, user_id):
    before = repo.current_target(db, user_id)["id"]
    r = await client.patch("/api/profile", json={"eating_cutoff": "22:00"}, headers=H)
    assert r.status == 200
    assert repo.current_target(db, user_id)["id"] == before
    assert repo.due_reminders(db, user_id, "kitchen_close")["local_time"] == "21:45"


async def test_manual_target(client, db, user_id):
    r = await client.put("/api/targets", json={"kcal": 2300, "protein": 120, "fat": 60, "carbs": 190}, headers=H)
    assert r.status == 200
    assert (await r.json())["targets"]["kcal"] == 2300
    assert repo.current_target(db, user_id)["reason"] == "manual"
    assert (await client.put("/api/targets", json={"kcal": 500, "protein": 1, "fat": 1, "carbs": 1}, headers=H)).status == 400
    # ниже безопасного минимума (≈1,1 × базовый обмен) калории поднимаются
    low = await (await client.put("/api/targets", json={"kcal": 1000, "protein": 80, "fat": 40, "carbs": 100}, headers=H)).json()
    assert low["result"]["raised"] is True and low["targets"]["kcal"] > 1000


async def test_reminders_list_and_update(client, db, user_id):
    items = (await (await client.get("/api/reminders", headers=H)).json())["reminders"]
    assert {i["kind"] for i in items} == {"morning", "midday", "evening", "weekly", "water", "weigh"}
    r = await client.put("/api/reminders/water", json={"time": "17:15", "enabled": True}, headers=H)
    assert r.status == 200
    water = next(i for i in (await r.json())["reminders"] if i["kind"] == "water")
    assert water["time"] == "17:15" and water["enabled"] is True
    off = await client.put("/api/reminders/water", json={"enabled": False}, headers=H)
    w2 = next(i for i in (await off.json())["reminders"] if i["kind"] == "water")
    assert w2["enabled"] is False and w2["time"] == "17:15"
    assert (await client.put("/api/reminders/nope", json={"enabled": True}, headers=H)).status == 404
    assert (await client.put("/api/reminders/water", json={"time": "99:99"}, headers=H)).status == 400


async def test_delete_account_needs_confirmation(client, db, user_id):
    assert (await client.delete("/api/me", json={}, headers=H)).status == 400
    assert repo.get_user_by_tg(db, 1001) is not None
    r = await client.delete("/api/me", json={"confirm": "удалить"}, headers=H)
    assert r.status == 200
    assert repo.get_user_by_tg(db, 1001) is None
    assert (await client.get("/api/me", headers=H)).status == 409
