"""Picooc: подпись, логин, пагинация, маппинг и запись в БД — без сети."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from app.adapters.picooc import (APPVER, PicoocClient, PicoocError, base_params,
                                 map_record, sign)
from app.db import connect, migrate
from app.storage import repo

LOGIN_OK = {
    "code": 0, "msg": "ok",
    "resp": {"user_id": "u-1", "role_id": "r-owner",
             "roles": [{"role_id": "r-owner", "role_name": "owner"},
                       {"role_id": "r-wife", "role_name": "wife"}]},
}


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest().upper()


def router(pages: list[dict], *, login_body: dict | None = None):
    """Транспорт, который отвечает на логин и отдаёт страницы по порядку."""
    state = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/account/login"):
            return httpx.Response(200, json=login_body or LOGIN_OK)
        page = pages[min(state["page"], len(pages) - 1)]
        state["page"] += 1
        return httpx.Response(200, json={"code": 0, "resp": page})

    return httpx.MockTransport(handler)


def page(records: list[dict], *, more: bool = False, last_time: int = 0) -> dict:
    return {"records": records, "continue": more, "lastTime": last_time}


def test_sign_formula_matches_independent_computation():
    device, method, ts = "DEV-1", "bodyIndexList", "1761400000"
    expected = _md5(device + _md5(ts + _md5(method) + _md5(APPVER)))
    assert sign(device, method, ts) == expected
    assert sign(device, method, ts) == sign(device, method, ts)        # детерминизм
    assert sign(device, method, ts + "1") != sign(device, method, ts)  # зависит от ts
    assert base_params(device, method, ts)["push_token"] == "android::DEV-1"


def test_map_record_zero_means_missing():
    rec = {"bodyTime": 1761400000, "weight": 92.4, "body_fat": 24.1,
           "water_race": 55.2, "muscle_race": 41.3, "skeletal_muscle": 0,
           "body_age": 0, "visceral_fat_level": 11, "basic_metabolism": 1840,
           "bmi": 27.9, "bone_mass": 3.4, "mac": "AA:BB", "abnormal_flag": 0}
    row = map_record(rec)
    assert row["weight_kg"] == 92.4 and row["body_fat_pct"] == 24.1
    assert row["body_water_pct"] == 55.2          # water_race → body_water_pct
    assert row["skeletal_muscle_pct"] == 41.3     # muscle_race → доля, не кг
    assert row["muscle_mass_kg"] is None, "0 в skeletal_muscle — это «нет данных»"
    assert row["metabolic_age"] is None, "0 в body_age — это «нет данных»"
    assert row["visceral_fat"] == 11
    assert row["measured_at_utc"].tzinfo is not None
    assert row["source"] == "picooc_api"


def test_map_record_without_body_time_raises():
    with pytest.raises(PicoocError):
        map_record({"weight": 90})


@pytest.mark.asyncio
async def test_login_sends_signed_form_and_parses_roles():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json=LOGIN_OK)

    async with PicoocClient("me@example.com", "secret",
                            transport=httpx.MockTransport(handler),
                            device_id="DEV-1") as client:
        session = await client.login()

    assert seen["path"].endswith("/account/login")
    assert session.user_id == "u-1" and session.role() == "r-owner"
    assert session.role("wife") == "r-wife"

    form = seen["form"]
    assert form["device_id"] == "DEV-1" and form["appver"] == APPVER
    assert form["sign"] == sign("DEV-1", "user_login_new", form["timestamp"])
    req = json.loads(form["reqData"])
    assert req["req"]["email"] == "me@example.com"
    assert req["req"]["password"] == "secret"
    assert req["sign"] == form["sign"]


@pytest.mark.asyncio
async def test_login_error_code_raises_without_leaking_password():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 1001, "msg": "wrong password"})

    async with PicoocClient("me@example.com", "secret",
                            transport=httpx.MockTransport(handler),
                            device_id="DEV") as client:
        with pytest.raises(PicoocError) as err:
            await client.login()
    assert "1001" in str(err.value)
    assert "secret" not in str(err.value)


@pytest.mark.asyncio
async def test_fetch_paginates_skips_deleted_and_uses_last_time():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/account/login"):
            return httpx.Response(200, json=LOGIN_OK)
        params = dict(request.url.params)
        calls.append(params)
        if len(calls) == 1:
            return httpx.Response(200, json={"code": 0, "resp": page([
                {"bodyTime": 1761400000, "weight": 92.4, "abnormal_flag": 0},
                {"bodyTime": 1761300000, "weight": 0, "abnormal_flag": 0},
                {"bodyTime": 1761200000, "weight": 91.0, "is_del": 1},
            ], more=True, last_time=1761200000)})
        return httpx.Response(200, json={"code": 0, "resp": page([
            {"bodyTime": 1751100000, "weight": 93.1, "abnormal_flag": 0},
        ])})

    async with PicoocClient("e", "p", transport=httpx.MockTransport(handler),
                            device_id="DEV") as client:
        rows = await client.fetch_measurements()

    assert [r["weight_kg"] for r in rows] == [92.4, None, 93.1]
    assert len(calls) == 2, "должна быть ровно одна вторая страница"
    assert calls[1]["time"] == "1761200000", "пагинация идёт по lastTime"
    assert calls[0]["method"] == "bodyIndexList"
    assert calls[0]["roleId"] == "r-owner"


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def _record(dt_utc: datetime, weight: float) -> dict:
    return {"bodyTime": int(dt_utc.timestamp()), "weight": weight, "body_fat": 24.1,
            "body_age": 0, "skeletal_muscle": 0, "abnormal_flag": 0}


@pytest.mark.asyncio
async def test_sync_writes_local_date_and_dedups(db):
    from app.services.scale import sync_picooc

    # 22:30 UTC = 01:30 следующего дня в Минске → локальная дата должна сдвинуться
    late = datetime(2026, 10, 20, 22, 30, tzinfo=timezone.utc)
    early = datetime(2026, 10, 18, 6, 0, tzinfo=timezone.utc)
    transport = router([page([_record(late, 92.4), _record(early, 92.9)])])

    uid = repo.create_user(db, 5, tz="Europe/Minsk")
    user = repo.get_user_by_tg(db, 5)

    async with PicoocClient("e", "p", transport=transport, device_id="DEV") as client:
        res = await sync_picooc(db, user, days=30, client=client,
                                now=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc))

    assert res["saved"] == 2 and res["fetched"] == 2
    dates = [r["local_date"] for r in db.execute(
        "SELECT local_date FROM body_measurements WHERE user_id=? ORDER BY local_date", (uid,))]
    assert dates == ["2026-10-18", "2026-10-21"], dates

    row = db.execute("SELECT * FROM body_measurements WHERE local_date='2026-10-21'").fetchone()
    assert row["source"] == "picooc_api" and row["weight_kg"] == 92.4
    assert row["measured_at_utc"] == "2026-10-20 22:30:00"

    # повторный синк не плодит строк
    async with PicoocClient("e", "p", transport=transport, device_id="DEV") as client:
        await sync_picooc(db, user, days=30, client=client,
                          now=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc))
    assert db.execute("SELECT COUNT(*) c FROM body_measurements WHERE user_id=?",
                      (uid,)).fetchone()["c"] == 2


@pytest.mark.asyncio
async def test_sync_respects_window(db):
    from app.services.scale import sync_picooc

    transport = router([page([_record(datetime(2025, 1, 1, 9, tzinfo=timezone.utc), 99.0)])])

    repo.create_user(db, 6, tz="Europe/Minsk")
    user = repo.get_user_by_tg(db, 6)
    async with PicoocClient("e", "p", transport=transport, device_id="DEV") as client:
        res = await sync_picooc(db, user, days=30, client=client,
                                now=datetime(2026, 10, 21, 9, tzinfo=timezone.utc))
    assert res["saved"] == 0 and res["older_than_window"] == 1
