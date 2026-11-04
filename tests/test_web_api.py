"""Фаза 5: веб-сервер Mini App. Проверка initData, права доступа, формы ответов контракта
docs/V2_API.md. Сети нет: распознавание и ассистент подменены, сервер поднят на
aiohttp.test_utils.TestServer."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from app import config as config_mod
from app.config import Config
from app.db import connect, migrate
from app.services import assistant
from app.services import food as foodsvc
from app.storage import repo
from app.web import server
from app.web.auth import InitDataError, compute_hash, validate_init_data
from tests.test_photo_flow import _food

pytestmark = pytest.mark.asyncio

TOKEN = "42:TEST-TOKEN"
NOW = dt.datetime(2026, 5, 20, 14, 30)
TODAY = "2026-05-20"
JPEG = b"\xff\xd8\xff\xe0jpeg-bytes-"
PNG = b"\x89PNG\r\n\x1a\nxx"
WEBP = b"RIFF\x00\x00\x00\x00WEBPxx"


# ------------------------------------------------------------------ вспомогательное

def make_init_data(user_id: int = 1001, first_name: str = "Тест", *, token: str = TOKEN,
                   age: int = 0, tamper: bool = False) -> str:
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "AAH",
              "user": json.dumps({"id": user_id, "first_name": first_name}, ensure_ascii=False)}
    fields["hash"] = compute_hash(fields, token)
    if tamper:
        fields["user"] = fields["user"].replace(str(user_id), str(user_id + 1))
    return urlencode(fields)


def auth(user_id: int = 1001, **kw) -> dict:
    return {"Authorization": "tma " + make_init_data(user_id, **kw)}


H = auth()


def jpeg(tag: str = "a") -> bytes:
    return JPEG + tag.encode()


def photo_form(*datas: bytes, **fields) -> aiohttp.FormData:
    form = aiohttp.FormData(default_to_multipart=True)
    for i, data in enumerate(datas):
        form.add_field("photo", data, filename=f"p{i}.jpg", content_type="image/jpeg")
    for key, value in fields.items():
        for v in value if isinstance(value, list) else [value]:
            form.add_field(key, str(v))
    return form


@pytest.fixture()
def db():
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def make_config(tmp_path, **kw) -> Config:
    base = dict(telegram_bot_token=TOKEN, allowed_tg_ids="1001,1002",
                photos_dir=str(tmp_path / "photos"))
    return Config(_env_file=None, **{**base, **kw})


def add_user(db, tg_id: int, onboarded: int = 1) -> int:
    uid = repo.create_user(db, tg_id, onboarded=onboarded, sex="male", height_cm=178,
                           start_weight_kg=92.4, goal_weight_kg=80, tz="Europe/Minsk")
    repo.insert_target(db, uid, "2026-01-01", 1650, 110, 55, 170, 2100, "test")
    return uid


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(foodsvc, "local_now", lambda tz: NOW.replace(tzinfo=ZoneInfo(tz)))

    async def fake_ref(item, user_id, conn):  # noqa: ANN001
        from app.domain.nutrition import FoodRef
        return FoodRef(name_ru=item.name_ru, name_en=item.name, kcal_100g=100, protein_100g=5,
                       fat_100g=3, carbs_100g=12, source="llm", confidence=0.5)

    monkeypatch.setattr(foodsvc, "_llm_food_ref", fake_ref)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(config_mod, "_config", config)
    return config


@pytest_asyncio.fixture()
async def make_client(db, cfg, monkeypatch):
    clients: list[TestClient] = []

    async def factory(config: Config | None = None) -> TestClient:
        if config is not None:
            monkeypatch.setattr(config_mod, "_config", config)
        client = TestClient(TestServer(server.build_app(db, None, config or cfg)))
        await client.start_server()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.close()


@pytest_asyncio.fixture()
async def client(make_client, db):
    add_user(db, 1001)
    return await make_client()


@pytest.fixture()
def user_id(db):
    return repo.get_user_by_tg(db, 1001)["id"]


@pytest.fixture()
def fake_food(monkeypatch):
    async def parse_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("гречка", 300)

    async def parse_photos(imgs, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("гречка", 300)

    async def parse_text(text, user_id, conn):  # noqa: ANN001
        return _food("гречка", 150)

    monkeypatch.setattr(foodsvc, "parse_photo", parse_photo)
    monkeypatch.setattr(foodsvc, "parse_photos", parse_photos)
    monkeypatch.setattr(foodsvc, "parse_text", parse_text)


async def post_meal(client, *photos: bytes, headers=None, **fields):
    resp = await client.post("/api/meals", data=photo_form(*photos, **fields),
                             headers=headers or H)
    return resp


# ------------------------------------------------------------------ initData

def test_validate_init_data_valid():
    data = validate_init_data(make_init_data(7, "Аня"), TOKEN)
    assert data["user"] == {"id": 7, "first_name": "Аня"}
    assert isinstance(data["auth_date"], int)


@pytest.mark.parametrize("kwargs,code", [
    ({"token": "other:TOKEN"}, "bad_hash"),
    ({"tamper": True}, "bad_hash"),
    ({"age": 86401}, "expired"),
    ({"age": -3600}, "from_future"),
])
def test_validate_init_data_rejects(kwargs, code):
    with pytest.raises(InitDataError) as e:
        validate_init_data(make_init_data(**kwargs), TOKEN)
    assert e.value.code == code


def test_validate_init_data_age_window():
    validate_init_data(make_init_data(age=86000), TOKEN)
    now = time.time()
    with pytest.raises(InitDataError):
        validate_init_data(make_init_data(age=100), TOKEN, max_age_s=50, now=now)


@pytest.mark.parametrize("raw", ["", "garbage", "a=1&a=2&hash=00", "auth_date=1&user=%7B%7D"])
def test_validate_init_data_malformed(raw):
    with pytest.raises(InitDataError):
        validate_init_data(raw, TOKEN)


def test_validate_init_data_missing_parts():
    fields = {"auth_date": str(int(time.time()))}
    fields["hash"] = compute_hash(fields, TOKEN)
    with pytest.raises(InitDataError) as e:
        validate_init_data(urlencode(fields), TOKEN)
    assert e.value.code == "no_user"
    with pytest.raises(InitDataError) as e:
        validate_init_data(make_init_data(), "")
    assert e.value.code == "no_token"


def test_validate_init_data_exception_has_no_payload():
    init = make_init_data(tamper=True)
    with pytest.raises(InitDataError) as e:
        validate_init_data(init, TOKEN)
    assert init not in str(e.value) and TOKEN not in str(e.value)


# ------------------------------------------------------------------ авторизация сервера

async def test_no_header_is_401(client):
    resp = await client.get("/api/me")
    assert resp.status == 401
    body = await resp.json()
    assert body["error"] == "unauthorized" and body["message"]


@pytest.mark.parametrize("header", ["Bearer abc", "tma", "tma ", "tma garbage"])
async def test_bad_header_is_401(client, header):
    resp = await client.get("/api/me", headers={"Authorization": header})
    assert resp.status == 401


async def test_bad_hash_and_expired_are_401(client):
    assert (await client.get("/api/me", headers=auth(tamper=True))).status == 401
    assert (await client.get("/api/me", headers=auth(age=90000))).status == 401


async def test_not_whitelisted_is_401(client, db):
    add_user(db, 555)
    assert (await client.get("/api/me", headers=auth(555))).status == 401


async def test_public_bot_skips_whitelist(make_client, db, tmp_path):
    add_user(db, 555)
    client = await make_client(make_config(tmp_path, allow_public_bot=True, allowed_tg_ids=""))
    assert (await client.get("/api/me", headers=auth(555))).status == 200


async def test_empty_whitelist_denies_by_default(make_client, db, tmp_path):
    add_user(db, 555)
    client = await make_client(make_config(tmp_path, allowed_tg_ids=""))
    assert (await client.get("/api/me", headers=auth(555))).status == 401


async def test_not_onboarded_is_409(client, db):
    add_user(db, 1002, onboarded=0)
    resp = await client.get("/api/me", headers=auth(1002))
    assert resp.status == 409 and (await resp.json())["error"] == "not_onboarded"
    assert (await client.get("/api/me", headers=auth(1002 + 1))).status == 401   # не в whitelist


async def test_dev_mode_from_localhost(make_client, db, tmp_path):
    add_user(db, 1001)
    client = await make_client(make_config(tmp_path, web_dev_tg_id=1001))
    resp = await client.get("/api/me")
    assert resp.status == 200
    assert (await resp.json())["user"]["tg_id"] == 1001
    # заголовок через прокси: dev-режим закрыт
    assert (await client.get("/api/me", headers={"X-Forwarded-For": "1.2.3.4"})).status == 401
    # неверный заголовок в dev-режиме не превращается в dev-пользователя
    assert (await client.get("/api/me", headers=auth(tamper=True))).status == 401


async def test_dev_mode_off_without_setting(client):
    assert (await client.get("/api/me")).status == 401


def test_dev_allowed_only_local():
    cfg = SimpleNamespace(web_dev_tg_id=5)
    assert server.dev_allowed("127.0.0.1", {}, cfg)
    assert server.dev_allowed("::1", {}, cfg)
    assert not server.dev_allowed("10.0.0.5", {}, cfg)
    assert not server.dev_allowed("192.168.1.2", {}, cfg)
    assert not server.dev_allowed(None, {}, cfg)
    assert not server.dev_allowed("127.0.0.1", {"X-Real-IP": "9.9.9.9"}, cfg)
    assert not server.dev_allowed("127.0.0.1", {}, SimpleNamespace(web_dev_tg_id=0))


# ------------------------------------------------------------------ /api/me и /api/day

async def test_me_shape(client, db, user_id):
    repo.save_body_measurement(db, user_id, "2026-05-10", {"weight_kg": 90.0}, "manual", 1.0)
    resp = await client.get("/api/me", headers=auth(first_name="Жан"))
    assert resp.status == 200
    body = await resp.json()
    assert body["user"] == {"tg_id": 1001, "first_name": "Жан", "sex": "male", "height_cm": 178.0,
                            "goal_weight_kg": 80.0, "tz": "Europe/Minsk", "eating_cutoff": "21:00"}
    assert body["targets"]["kcal"] == 1650 and body["targets"]["protein"] == 110
    assert body["targets"]["water_ml"] >= 1500
    assert body["weight"] == {"latest": 90.0, "trend": 90.0, "start": 92.4}


async def test_me_weight_without_measurements(client):
    body = await (await client.get("/api/me", headers=H)).json()
    assert body["weight"] == {"latest": 92.4, "trend": None, "start": 92.4}


async def test_day_shape_and_content(client, db, user_id, fake_food):
    meal = await (await post_meal(client, jpeg("1"), comment="обед")).json()
    await client.post("/api/water", json={"ml": 250}, headers=H)
    repo.save_sleep(db, user_id, TODAY, {"duration_min": 420, "score": 80}, "screenshot", 0.9)
    repo.save_activity(db, user_id, TODAY, {"steps": 9800, "active_kcal": 300}, "screenshot", 0.9)
    repo.save_body_measurement(db, user_id, TODAY, {"weight_kg": 82.4}, "manual", 1.0)

    resp = await client.get("/api/day", headers=H)
    assert resp.status == 200
    day = await resp.json()
    assert set(day) == {"date", "targets", "totals", "remaining_kcal", "water_ml", "meals",
                        "sleep", "activity", "weight", "weight_last"}
    assert day["weight_last"] == {"kg": 82.4, "date": TODAY}
    assert day["date"] == TODAY and day["water_ml"] == 250
    assert day["totals"]["kcal"] == meal["kcal"] > 0
    assert day["remaining_kcal"] == 1650 - meal["kcal"]
    assert [m["id"] for m in day["meals"]] == [meal["id"]]
    assert day["sleep"] == {"duration_min": 420, "score": 80}
    assert day["activity"] == {"steps": 9800, "active_kcal": 300}
    assert day["weight"] == 82.4


async def test_day_empty_and_params(client):
    day = await (await client.get("/api/day?date=2026-05-01", headers=H)).json()
    assert day["weight_last"] is None or day["weight_last"]["date"] <= "2026-05-01"
    assert day["meals"] == [] and day["sleep"] is None and day["activity"] is None
    assert day["weight"] is None and day["totals"]["kcal"] == 0
    assert (await client.get("/api/day?date=вчера", headers=H)).status == 400


# ------------------------------------------------------------------ приёмы пищи

async def test_meal_photo_groups_reach_the_model(client, monkeypatch, fake_food):
    seen = {}

    async def parse_photos(imgs, caption, user_id, conn, **kw):  # noqa: ANN001
        seen.update(kw)
        return _food("гречка", 300)

    monkeypatch.setattr(foodsvc, "parse_photos", parse_photos)
    resp = await post_meal(client, jpeg("1"), jpeg("2"), jpeg("3"), groups="0,0,1")
    assert resp.status == 201
    assert seen["groups"] == [0, 0, 1]


async def test_meal_photo_groups_must_match_photos(client, fake_food):
    assert (await post_meal(client, jpeg("1"), jpeg("2"), groups="0")).status == 400
    assert (await post_meal(client, jpeg("1"), groups="a")).status == 400


def test_groups_note_describes_dishes():
    assert foodsvc._groups_note(None, 2) == []
    assert foodsvc._groups_note([0, 1], 2) == []
    note = foodsvc._groups_note([0, 0, 1], 3)
    assert "Блюдо 1: фото 1, 2" in note and "Блюдо 2: фото 3" in note


async def test_meal_upload_photo(client, db, user_id, fake_food):
    dish_id = repo.create_dish(db, user_id, "Тарелка", "plate", diameter_cm=26)
    resp = await post_meal(client, jpeg("1"), jpeg("2"), comment="гречка на обед",
                           dish_id=[str(dish_id), ""], time="13:10")
    assert resp.status == 201
    meal = await resp.json()
    assert set(meal) == {"id", "date", "time", "type", "source", "kcal", "kcal_low", "kcal_high",
                         "protein", "fat", "carbs", "confidence", "photos", "items", "comment", "favorite_id"}
    assert meal["source"] == "combo" and meal["comment"] == "гречка на обед"
    assert meal["date"] == TODAY and meal["time"] == "13:00"          # «в обед» из подписи главнее
    assert meal["type"] == "lunch"
    assert [p["dish_id"] for p in meal["photos"]] == [dish_id, None]
    assert all(p["url"] == f"/api/photos/{p['id']}" for p in meal["photos"])
    item = meal["items"][0]
    assert set(item) == {"id", "name", "grams", "kcal", "protein", "fat", "carbs", "per100",
                         "confidence", "edited"}
    assert item["name"] == "гречка" and item["grams"] == 300.0 and item["edited"] is False
    assert set(item["per100"]) == {"kcal", "protein", "fat", "carbs"} and item["per100"]["kcal"] > 0

    got = await client.get(f"/api/meals/{meal['id']}", headers=H)
    assert (await got.json()) == meal
    photo = await client.get(meal["photos"][0]["url"], headers=H)
    assert photo.status == 200 and (await photo.read()) == jpeg("1")
    assert photo.headers["Content-Type"] == "image/jpeg"


async def test_meal_form_time_used_without_comment_time(client, fake_food):
    meal = await (await post_meal(client, jpeg(), time="09:15", date="2026-05-19")).json()
    assert (meal["date"], meal["time"], meal["source"]) == ("2026-05-19", "09:15", "photo")


async def test_meal_upload_text_only(client, fake_food):
    form = aiohttp.FormData(default_to_multipart=True)
    form.add_field("text", "гречка 150 г")
    resp = await client.post("/api/meals", data=form, headers=H)
    assert resp.status == 201
    meal = await resp.json()
    assert meal["source"] == "text" and meal["photos"] == [] and meal["kcal"] > 0


async def test_meal_upload_validation(client, fake_food):
    assert (await post_meal(client)).status == 400                           # ни фото, ни текста
    assert (await post_meal(client, *[jpeg(str(i)) for i in range(7)])).status == 400
    bad = await post_meal(client, b"GIF89a-not-allowed")
    assert bad.status == 415 and (await bad.json())["error"] == "unsupported_media"
    assert (await post_meal(client, jpeg(), dish_id="abc")).status == 400
    assert (await post_meal(client, jpeg(), time="25:99")).status == 400
    assert (await post_meal(client, jpeg(), date="2030-01-01")).status == 400
    plain = await client.post("/api/meals", json={"text": "x"}, headers=H)
    assert plain.status == 400
    assert (await post_meal(client, PNG)).status == 201
    assert (await post_meal(client, WEBP)).status == 201


async def test_meal_upload_too_large(make_client, db, tmp_path, fake_food):
    add_user(db, 1001)
    client = await make_client(make_config(tmp_path, max_media_bytes=2000))
    resp = await post_meal(client, JPEG + b"x" * 3000)
    assert resp.status == 413 and (await resp.json())["error"] == "too_large"


async def test_meal_no_food_and_llm_errors(client, monkeypatch):
    from app.llm import client as llm

    async def empty(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return foodsvc.understanding_from_json({"items": [], "overall_confidence": 0.9})

    monkeypatch.setattr(foodsvc, "parse_photo", empty)
    resp = await post_meal(client, jpeg("n"))
    assert resp.status == 422 and (await resp.json())["error"] == "no_food"

    async def broken(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        raise llm.LLMError("нет сети")

    monkeypatch.setattr(foodsvc, "parse_photo", broken)
    resp = await post_meal(client, jpeg("m"))
    assert resp.status == 502 and (await resp.json())["error"] == "llm_unavailable"


async def test_meal_patch_delete(client, fake_food):
    meal = await (await post_meal(client, jpeg())).json()
    url = f"/api/meals/{meal['id']}"
    patched = await client.patch(url, json={"time": "08:05", "type": "breakfast"}, headers=H)
    assert patched.status == 200
    body = await patched.json()
    assert (body["time"], body["type"], body["date"]) == ("08:05", "breakfast", TODAY)
    assert (await client.patch(url, json={"type": "brunch"}, headers=H)).status == 400
    assert (await client.patch(url, json={"time": "8"}, headers=H)).status == 400

    assert (await client.delete(url, headers=H)).status == 204
    assert (await client.get(url, headers=H)).status == 404
    assert (await client.delete(url, headers=H)).status == 404
    assert (await client.patch(url, json={"time": "09:00"}, headers=H)).status == 404
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["meals"] == []


async def test_meal_refine(client, monkeypatch, fake_food):
    meal = await (await post_meal(client, jpeg())).json()

    async def fake_refine(items, comment, user_id, conn):  # noqa: ANN001
        assert items[0]["name_ru"] == "гречка" and comment == "с маслом"
        return _food("гречка с маслом", 200), ["масло"]

    monkeypatch.setattr(foodsvc, "refine_meal", fake_refine)
    resp = await client.post(f"/api/meals/{meal['id']}/refine", json={"comment": "с маслом"},
                             headers=H)
    assert resp.status == 200
    refined = await resp.json()
    assert refined["id"] == meal["id"] and refined["items"][0]["name"] == "гречка с маслом"
    assert refined["items"][0]["grams"] == 200.0 and len(refined["photos"]) == 1
    assert (await client.post(f"/api/meals/{meal['id']}/refine", json={"comment": " "},
                              headers=H)).status == 400


async def test_item_patch_recalculates_and_delete(client, fake_food):
    meal = await (await post_meal(client, jpeg())).json()
    item_id = meal["items"][0]["id"]
    full = meal["kcal"]
    resp = await client.patch(f"/api/items/{item_id}", json={"grams": 150}, headers=H)
    assert resp.status == 200
    after = await resp.json()
    assert after["id"] == meal["id"]
    assert after["items"][0]["grams"] == 150.0
    assert after["items"][0]["kcal"] == pytest.approx(full / 2, abs=1)
    assert after["items"][0]["edited"] is True and after["kcal"] == pytest.approx(full / 2, abs=1)
    assert after["protein"] == pytest.approx(meal["protein"] / 2, abs=0.2)
    assert after["kcal_low"] <= after["kcal"] <= after["kcal_high"]
    grown = await (await client.patch(f"/api/items/{item_id}", json={"grams": 900},
                                      headers=H)).json()
    assert grown["kcal_low"] <= grown["kcal"] <= grown["kcal_high"]    # диапазон не отстаёт от итога
    for bad_grams in (0, -5, "много", 99999, None):
        assert (await client.patch(f"/api/items/{item_id}", json={"grams": bad_grams},
                                   headers=H)).status == 400
    gone = await client.delete(f"/api/items/{item_id}", headers=H)
    assert gone.status == 200 and (await gone.json())["items"] == []
    assert (await client.delete(f"/api/items/{item_id}", headers=H)).status == 404


async def test_item_barcode_replaces_per100_keeps_grams(client, db, fake_food, monkeypatch):
    from app.adapters import off
    meal = await (await post_meal(client, jpeg())).json()
    item = meal["items"][0]

    async def fake_fetch(code, transport=None):
        return {"name": "Сосиски куриные", "brand": "Папа может", "kcal_100g": 200,
                "protein_100g": 10.0, "fat_100g": 15.0, "carbs_100g": 2.0, "serving_g": None}
    monkeypatch.setattr(off, "fetch_barcode", fake_fetch)
    code = ean13("460700489001")
    assert off.valid_barcode(code)
    for bad_code in ("123", "abc", "", "4607004890009"):
        assert (await client.post(f"/api/items/{item['id']}/barcode", json={"code": bad_code},
                                  headers=H)).status == 400
    resp = await client.post(f"/api/items/{item['id']}/barcode", json={"code": code}, headers=H)
    assert resp.status == 200
    after = (await resp.json())["items"][0]
    assert after["name"] == "Сосиски куриные Папа может" and after["grams"] == item["grams"]
    assert after["per100"] == {"kcal": 200, "protein": 10.0, "fat": 15.0, "carbs": 2.0}
    assert after["kcal"] == pytest.approx(200 * item["grams"] / 100, abs=3) and after["edited"] is True
    # граммы после уточнения считаются уже от продукта с упаковки
    again = (await (await client.patch(f"/api/items/{item['id']}", json={"grams": 50},
                                       headers=H)).json())["items"][0]
    assert again["kcal"] == pytest.approx(100, abs=3)

    async def nothing(code, transport=None):
        return None
    monkeypatch.setattr(off, "fetch_barcode", nothing)
    missing = await client.post(f"/api/items/{item['id']}/barcode", json={"code": code}, headers=H)
    assert missing.status == 404


async def test_ownership_returns_404(client, db, user_id, fake_food):
    add_user(db, 1002)
    meal = await (await post_meal(client, jpeg("own"))).json()
    dish = await (await client.post("/api/dishes", json={"name": "Моя", "kind": "bowl"},
                                    headers=H)).json()
    other = auth(1002)
    item_id, photo_url = meal["items"][0]["id"], meal["photos"][0]["url"]
    checks = [
        client.get(f"/api/meals/{meal['id']}", headers=other),
        client.patch(f"/api/meals/{meal['id']}", json={"time": "10:00"}, headers=other),
        client.delete(f"/api/meals/{meal['id']}", headers=other),
        client.post(f"/api/meals/{meal['id']}/refine", json={"comment": "x"}, headers=other),
        client.patch(f"/api/items/{item_id}", json={"grams": 10}, headers=other),
        client.delete(f"/api/items/{item_id}", headers=other),
        client.post(f"/api/items/{item_id}/barcode", json={"code": ean13("460700489001")}, headers=other),
        client.get(photo_url, headers=other),
        client.patch(f"/api/dishes/{dish['id']}", json={"name": "чужая"}, headers=other),
        client.delete(f"/api/dishes/{dish['id']}", headers=other),
        client.get(f"/api/dishes/{dish['id']}/photo", headers=other),
        client.delete("/api/water/999", headers=other),
    ]
    for pending in checks:
        resp = await pending
        assert resp.status == 404, await resp.text()
    # у владельца всё на месте
    assert (await client.get(f"/api/meals/{meal['id']}", headers=H)).status == 200
    assert (await (await client.get("/api/day", headers=auth(1002))).json())["meals"] == []
    # чужая посуда не подставляется в распознавание
    resp = await post_meal(client, jpeg("x"), headers=other, dish_id=str(dish["id"]))
    assert (await resp.json())["photos"][0]["dish_id"] is None


async def test_photo_serving_is_limited_to_photos_dir(client, db, user_id, tmp_path, fake_food):
    meal = await (await post_meal(client, jpeg())).json()
    secret = tmp_path / "secret.txt"
    secret.write_text("секрет")
    db.execute("UPDATE meal_photos SET path=? WHERE id=?", (str(secret), meal["photos"][0]["id"]))
    db.commit()
    assert (await client.get(meal["photos"][0]["url"], headers=H)).status == 404
    db.execute("UPDATE meal_photos SET path=? WHERE id=?",
               (str(tmp_path / "photos" / ".." / "secret.txt"), meal["photos"][0]["id"]))
    db.commit()
    assert (await client.get(meal["photos"][0]["url"], headers=H)).status == 404
    db.execute("UPDATE meal_photos SET path=? WHERE id=?",
               (str(tmp_path / "photos" / "missing.jpg"), meal["photos"][0]["id"]))
    db.commit()
    assert (await client.get(meal["photos"][0]["url"], headers=H)).status == 404
    assert (await client.get("/api/photos/abc", headers=H)).status == 404


# ------------------------------------------------------------------ посуда

async def test_dishes_crud_with_photo(client):
    assert (await (await client.get("/api/dishes", headers=H)).json()) == {"dishes": []}
    form = aiohttp.FormData()
    for key, value in {"name": "Глубокая тарелка", "kind": "deep_plate", "diameter_cm": "20",
                       "depth_cm": "5", "volume_ml": "500", "is_default": "true"}.items():
        form.add_field(key, value)
    form.add_field("photo", jpeg("dish"), filename="d.jpg", content_type="image/jpeg")
    resp = await client.post("/api/dishes", data=form, headers=H)
    assert resp.status == 201
    dish = await resp.json()
    assert dish == {"id": dish["id"], "name": "Глубокая тарелка", "kind": "deep_plate",
                    "diameter_cm": 20.0, "depth_cm": 5.0, "volume_ml": 500,
                    "photo_url": f"/api/dishes/{dish['id']}/photo", "is_default": True}
    photo = await client.get(dish["photo_url"], headers=H)
    assert photo.status == 200 and (await photo.read()) == jpeg("dish")

    patched = await client.patch(f"/api/dishes/{dish['id']}",
                                 json={"name": "Миска", "kind": "bowl", "volume_ml": None,
                                       "is_default": False}, headers=H)
    assert patched.status == 200
    body = await patched.json()
    assert (body["name"], body["kind"], body["volume_ml"], body["is_default"]) == \
        ("Миска", "bowl", None, False)
    assert body["diameter_cm"] == 20.0 and body["photo_url"]

    listed = await (await client.get("/api/dishes", headers=H)).json()
    assert [d["id"] for d in listed["dishes"]] == [dish["id"]]
    assert (await client.delete(f"/api/dishes/{dish['id']}", headers=H)).status == 204
    assert (await client.delete(f"/api/dishes/{dish['id']}", headers=H)).status == 404
    assert (await client.get("/api/dishes", headers=H)).status == 200
    assert (await client.get(f"/api/dishes/{dish['id']}/photo", headers=H)).status == 404


async def test_dish_without_photo_and_validation(client):
    dish = await (await client.post("/api/dishes", json={"name": "Кружка", "kind": "mug"},
                                    headers=H)).json()
    assert dish["photo_url"] is None
    assert (await client.get(f"/api/dishes/{dish['id']}/photo", headers=H)).status == 404
    for payload in ({"kind": "plate"}, {"name": "  ", "kind": "plate"},
                    {"name": "Х", "kind": "rocket"}, {"name": "Х", "diameter_cm": 500},
                    {"name": "Х", "volume_ml": "много"}):
        assert (await client.post("/api/dishes", json=payload, headers=H)).status == 400, payload
    bad = aiohttp.FormData()
    bad.add_field("name", "Х")
    bad.add_field("photo", b"not an image", filename="x.jpg", content_type="image/jpeg")
    assert (await client.post("/api/dishes", data=bad, headers=H)).status == 415
    assert (await client.patch("/api/dishes/abc", json={"name": "Ы"}, headers=H)).status == 404


# ------------------------------------------------------------------ вода и вес

async def test_water_add_and_undo(client):
    first = await client.post("/api/water", json={"ml": 250}, headers=H)
    assert first.status == 201
    a = await first.json()
    assert a["water_ml"] == 250 and a["goal_ml"] >= 1500 and isinstance(a["id"], int)
    b = await (await client.post("/api/water", json={"ml": 500}, headers=H)).json()
    assert b["water_ml"] == 750
    undone = await client.delete(f"/api/water/{b['id']}", headers=H)
    assert undone.status == 200
    assert await undone.json() == {"water_ml": 250, "goal_ml": a["goal_ml"]}
    assert (await client.delete(f"/api/water/{b['id']}", headers=H)).status == 404
    for payload in ({"ml": 0}, {"ml": 5}, {"ml": 99999}, {"ml": "x"}, {}):
        assert (await client.post("/api/water", json=payload, headers=H)).status == 400, payload


async def test_weight_post(client):
    resp = await client.post("/api/weight", json={"weight_kg": 82.1}, headers=H)
    assert resp.status == 200
    assert await resp.json() == {"weight": 82.1, "trend": 82.1}
    again = await (await client.post("/api/weight", json={"weight_kg": 81.9}, headers=H)).json()
    assert again["weight"] == 81.9
    me = await (await client.get("/api/me", headers=H)).json()
    assert me["weight"]["latest"] == 81.9
    for payload in ({"weight_kg": 5}, {"weight_kg": 900}, {"weight_kg": "x"}, {}):
        assert (await client.post("/api/weight", json=payload, headers=H)).status == 400


# ------------------------------------------------------------------ статистика

async def test_stats_shape_and_values(client, db, user_id, fake_food):
    meal = await (await post_meal(client, jpeg(), time="08:00")).json()
    await client.post("/api/water", json={"ml": 300}, headers=H)
    await client.post("/api/weight", json={"weight_kg": 82.0}, headers=H)
    repo.save_body_measurement(db, user_id, "2026-05-15", {"weight_kg": 83.0, "fasting": 1},
                               "manual", 1.0)
    repo.save_sleep(db, user_id, "2026-05-19", {"duration_min": 480}, "screenshot", 0.9)
    repo.save_activity(db, user_id, "2026-05-18", {"steps": 8000}, "screenshot", 0.9)

    stats = await (await client.get("/api/stats?range=week", headers=H)).json()
    assert stats["range"] == "week" and len(stats["days"]) == 7
    assert [d["date"] for d in stats["days"]][0] == "2026-05-14"
    row_keys = {"date", "kcal", "protein", "fat", "carbs", "late_kcal", "target_kcal", "water_ml", "weight",
                "weight_trend", "sleep_min", "steps"}
    assert all(set(d) == row_keys for d in stats["days"])
    by_date = {d["date"]: d for d in stats["days"]}
    today = by_date[TODAY]
    assert today["kcal"] == meal["kcal"] and today["water_ml"] == 300 and today["weight"] == 82.0
    assert today["target_kcal"] == 1650 and today["sleep_min"] is None
    assert by_date["2026-05-14"]["kcal"] is None and by_date["2026-05-14"]["weight_trend"] is None
    assert by_date["2026-05-15"]["weight"] == 83.0 and by_date["2026-05-16"]["weight"] is None
    assert by_date["2026-05-16"]["weight_trend"] == 83.0           # тренд тянется между замерами
    assert by_date["2026-05-19"]["sleep_min"] == 480 and by_date["2026-05-18"]["steps"] == 8000
    summary = stats["summary"]
    assert summary == {"avg_kcal": meal["kcal"], "avg_protein": meal["protein"],
                       "days_logged": 1, "days_on_target": 0, "weight_change": -1.0,
                       "avg_sleep_min": 480, "avg_steps": 8000, "avg_water": 300}


async def test_stats_empty_has_nulls_and_ranges(client):
    for name, n in (("week", 7), ("month", 30), ("90d", 90)):
        body = await (await client.get(f"/api/stats?range={name}", headers=H)).json()
        assert len(body["days"]) == n
    summary = body["summary"]
    assert summary["avg_kcal"] is None and summary["weight_change"] is None
    assert summary["days_logged"] == 0 and summary["avg_steps"] is None
    assert (await client.get("/api/stats?range=year", headers=H)).status == 400
    assert (await (await client.get("/api/stats", headers=H)).json())["range"] == "week"


async def test_stats_days_on_target(client, db, user_id):
    for i, kcal in enumerate((1600, 1700, 900)):
        day = f"2026-05-{18 + i}"
        db.execute("""INSERT INTO meals (user_id, eaten_at_utc, local_date, local_time, meal_type,
                      source, kcal_total) VALUES (?, ?, ?, '12:00', 'lunch', 'manual', ?)""",
                   (user_id, day + " 09:00:00", day, kcal))
    db.commit()
    summary = (await (await client.get("/api/stats?range=week", headers=H)).json())["summary"]
    assert summary["days_logged"] == 3 and summary["days_on_target"] == 2
    assert summary["avg_kcal"] == 1400


# ------------------------------------------------------------------ анализ дня

async def test_analysis_empty_day_asks_to_log(client):
    data = await (await client.get(f"/api/analysis?date={TODAY}", headers=H)).json()
    assert data["date"] == TODAY and data["headline"]
    assert {r["label"] for r in data["rows"]} >= {"Белок", "Вода", "Шаги", "Сон"}
    assert all(r["status"] in ("good", "ok", "bad", "none") for r in data["rows"])


async def test_analysis_reflects_logged_day(client, db, user_id, fake_food):
    await post_meal(client, jpeg())
    repo.save_activity(db, user_id, TODAY, {"steps": 12000}, "screenshot", 0.9)
    data = await (await client.get(f"/api/analysis?date={TODAY}", headers=H)).json()
    assert any("12" in g and "шаг" in g.lower() for g in data["good"]), data
    assert data["tips"] == [] or all({"icon", "title", "text"} <= set(t) for t in data["tips"])


async def test_analysis_bad_date_and_auth(client):
    assert (await client.get("/api/analysis?date=вчера", headers=H)).status == 400
    assert (await client.get("/api/analysis")).status == 401


# ------------------------------------------------------------------ выгрузка CSV

async def test_export_csv(client, db, user_id, fake_food):
    await post_meal(client, jpeg())
    await client.post("/api/water", json={"ml": 250}, headers=H)
    repo.save_body_measurement(db, user_id, TODAY, {"weight_kg": 82.4}, "manual", 1.0)
    repo.save_sleep(db, user_id, TODAY, {"duration_min": 420}, "screenshot", 0.9)
    repo.save_activity(db, user_id, TODAY, {"steps": 5000}, "screenshot", 0.9)
    expected = {"meals": ("id,date,time,type,kcal", "гречка 300 г"),
                "body": ("date,weight_kg", "82.4"), "water": ("date,time,ml,source", "250"),
                "sleep": ("wake_date,duration_min", "420"),
                "activity": ("date,steps", "5000")}
    for kind, (head, needle) in expected.items():
        resp = await client.get(f"/api/export.csv?kind={kind}", headers=H)
        assert resp.status == 200, kind
        assert resp.headers["Content-Type"].startswith("text/csv")
        assert kind in resp.headers["Content-Disposition"]
        text = (await resp.read()).decode("utf-8-sig")
        assert text.startswith(head) and needle in text, (kind, text)
    assert (await client.get("/api/export.csv?kind=secrets", headers=H)).status == 400
    assert (await client.get("/api/export.csv?kind=meals")).status == 401


# ------------------------------------------------------------------ статика и служебное

async def test_static_index_and_redirect(client):
    for path in ("/app/", "/app"):
        resp = await client.get(path)               # статика без авторизации
        assert resp.status == 200, path
        assert resp.headers["Cache-Control"] == "no-cache"
        assert "text/html" in resp.headers["Content-Type"]
        assert (await resp.read()) == (server.STATIC_DIR / "index.html").read_bytes()
    root = await client.get("/", allow_redirects=False)
    assert root.status == 302 and root.headers["Location"] == "/app/"
    health = await client.get("/healthz")
    assert health.status == 200 and (await health.json()) == {"ok": True}


async def test_static_files_and_traversal(client):
    css = await client.get("/app/css/app.css")
    assert css.status == 200 and "text/css" in css.headers["Content-Type"]
    for path in ("/app/js", "/app/nope.js", "/app/%2e%2e/server.py", "/app/..%2fserver.py",
                 "/app/css/../../server.py", "/app/%2e%2e%2f%2e%2e%2fconfig.py"):
        resp = await client.get(path)
        assert resp.status == 404, path


async def test_api_errors_are_json(client):
    resp = await client.get("/api/unknown", headers=H)
    assert resp.status == 404 and (await resp.json())["error"] == "not_found"
    resp = await client.put("/api/me", headers=H)
    assert resp.status == 405 and (await resp.json())["error"] == "method_not_allowed"
    resp = await client.post("/api/water", data="не json", headers=H)
    assert resp.status == 400 and (await resp.json())["error"] == "bad_json"


async def test_build_app_chat_table_from_migration(db, cfg):
    server.build_app(db, None, cfg)
    cols = [r["name"] for r in db.execute("PRAGMA table_info(chat_messages)")]
    assert cols == ["id", "user_id", "role", "text", "meal_id", "created_at"]
    server.build_app(db, None, cfg)                       # повторно: без ошибок


async def test_start_web_respects_flag(db, tmp_path):
    assert await server.start_web(db, None, make_config(tmp_path)) is None
    config = make_config(tmp_path, web_enabled=True, web_host="127.0.0.1", web_port=0)
    runner = await server.start_web(db, None, config)
    try:
        assert runner is not None
    finally:
        await runner.cleanup()


def test_config_web_defaults(tmp_path):
    c = Config(_env_file=None)
    assert (c.web_enabled, c.web_host, c.web_port, c.webapp_url, c.web_dev_tg_id) == \
        (False, "0.0.0.0", 8080, "", 0)


# ------------------------------------------------------------------ фазы 3–4 (сервисов может не быть)

def _absent(name: str) -> bool:
    return importlib.util.find_spec(f"app.services.{name}") is None


@pytest.mark.skipif(not _absent("products") or not _absent("tracking"),
                    reason="сервисы фаз 3–4 уже есть")
async def test_phase34_endpoints_not_ready(client):
    calls = [
        client.get("/api/products", headers=H),
        client.post("/api/products", json={"name": "Х", "kcal": 1, "protein": 1, "fat": 1,
                                           "carbs": 1}, headers=H),
        client.patch("/api/products/1", json={"name": "Х"}, headers=H),
        client.delete("/api/products/1", headers=H),
        client.post("/api/products/label", data=photo_form(jpeg()), headers=H),
        client.post("/api/screens", data=photo_form(jpeg()), headers=H),
        client.post("/api/screens/1/confirm", json={"apply": True}, headers=H),
        client.post("/api/sleep", json={"duration_min": 400}, headers=H),
        client.post("/api/activity", json={"steps": 100}, headers=H),
    ]
    for pending in calls:
        resp = await pending
        assert resp.status == 501
        assert (await resp.json())["error"] == "not_ready"


async def test_products_endpoints(client, monkeypatch, fake_food):
    from app.services import products as productsvc

    created = await client.post("/api/products", headers=H, json={
        "name": "Творог 5%", "brand": "Савушкин", "kcal": 121, "protein": 17, "fat": 5,
        "carbs": 1.8, "default_grams": 200, "unit_name": "пачка", "unit_grams": 180})
    assert created.status == 201
    product = await created.json()
    assert set(product) == {"id", "name", "brand", "kcal", "protein", "fat", "carbs",
                            "default_grams", "unit_name", "unit_grams", "barcode", "icon", "times_used"}
    assert (product["name"], product["brand"], product["kcal"], product["protein"],
            product["carbs"], product["unit_grams"]) == \
        ("Творог 5%", "Савушкин", 121, 17.0, 1.8, 180)
    pid = product["id"]
    listed = (await (await client.get("/api/products?q=Тво", headers=H)).json())["products"]
    assert [p["id"] for p in listed] == [pid]
    assert (await (await client.get("/api/products?q=zzz", headers=H)).json()) == {"products": []}
    patched = await client.patch(f"/api/products/{pid}", json={"name": "Творог 9%"}, headers=H)
    assert patched.status == 200 and (await patched.json())["name"] == "Творог 9%"
    with_icon = await client.patch(f"/api/products/{pid}", json={"icon": "yogurt"}, headers=H)
    assert with_icon.status == 200 and (await with_icon.json())["icon"] == "yogurt"
    assert (await client.patch(f"/api/products/{pid}", json={"icon": "../x"}, headers=H)).status == 400
    assert (await client.patch("/api/products/99999", json={"name": "x"}, headers=H)).status == 404
    assert (await client.post("/api/products", json={"name": "Х"}, headers=H)).status == 400
    assert (await client.post("/api/products", headers=H, json={
        "name": "Х", "kcal": 5000, "protein": 1, "fat": 1, "carbs": 1})).status == 400

    async def label(db, user, image_b64):  # noqa: ANN001
        import base64
        assert [base64.b64decode(b) for b in image_b64] == [jpeg("lab"), jpeg("lab2")]
        return ({"name": "Творог", "brand": None, "kcal": 121, "protein": 17, "fat": 5,
                 "carbs": 1.8, "default_grams": None, "unit_name": None, "unit_grams": None},
                0.9, ["вес пачки не виден"])

    monkeypatch.setattr(productsvc, "parse_label", label)
    lab = await client.post("/api/products/label", data=photo_form(jpeg("lab"), jpeg("lab2")), headers=H)
    assert lab.status == 200
    body = await lab.json()
    assert body["draft"]["name"] == "Творог" and body["draft"]["id"] is None
    assert body["confidence"] == 0.9 and body["notes"] == ["вес пачки не виден"]
    assert (await client.post("/api/products/label", data=photo_form(), headers=H)).status == 400
    five = photo_form(*[jpeg(str(i)) for i in range(5)])
    assert (await client.post("/api/products/label", data=five, headers=H)).status == 400

    meal = await (await post_meal(client, jpeg())).json()
    saved = await client.post(f"/api/items/{meal['items'][0]['id']}/save_product", headers=H,
                              json={"name": "Моя гречка"})
    assert saved.status == 201 and (await saved.json())["name"] == "Моя гречка"
    assert (await client.post("/api/items/99999/save_product", headers=H)).status == 404

    assert (await client.delete(f"/api/products/{pid}", headers=H)).status == 204
    assert (await client.delete(f"/api/products/{pid}", headers=H)).status == 404


async def test_products_are_private(client, db):
    add_user(db, 1002)
    mine = await (await client.post("/api/products", headers=H, json={
        "name": "Моё", "kcal": 100, "protein": 1, "fat": 1, "carbs": 1})).json()
    other = auth(1002)
    assert (await (await client.get("/api/products", headers=other)).json()) == {"products": []}
    assert (await client.patch(f"/api/products/{mine['id']}", json={"name": "чужое"},
                               headers=other)).status == 404
    assert (await client.delete(f"/api/products/{mine['id']}", headers=other)).status == 404


async def test_sleep_and_activity(client, db, user_id):
    assert (await client.post("/api/sleep", headers=H, json={
        "date": TODAY, "start": "23:30", "end": "07:10", "score": 80})).status == 200
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["sleep"] == {"duration_min": 460, "score": 80}
    assert (await client.post("/api/sleep", json={"date": TODAY, "duration_min": "420"},
                              headers=H)).status == 200
    assert (await client.post("/api/sleep", json={"start": "25:00"}, headers=H)).status == 400
    assert (await client.post("/api/sleep", json={"score": 500}, headers=H)).status == 400
    assert (await client.post("/api/sleep", json={}, headers=H)).status == 400
    assert (await client.post("/api/sleep", json={"start": "23:00"}, headers=H)).status == 400

    resp = await client.post("/api/activity", headers=H, json={
        "date": TODAY, "steps": 10000, "active_kcal": 350, "workout": "бег 30 мин"})
    assert resp.status == 200 and (await resp.json()) == {"ok": True}
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["activity"] == {"steps": 10000, "active_kcal": 350}
    assert (await client.post("/api/activity", json={"steps": -1}, headers=H)).status == 400
    assert (await client.post("/api/activity", json={}, headers=H)).status == 400
    past = await client.post("/api/sleep", json={"date": "2026-05-01", "duration_min": 400},
                             headers=H)
    assert past.status == 200
    assert (await (await client.get("/api/day?date=2026-05-01", headers=H)).json())["sleep"][
        "duration_min"] == 400


async def test_screens_upload_and_confirm(client, db, user_id, monkeypatch):
    from app.services import screens as screens_svc

    async def parse(b64, uid, conn):  # noqa: ANN001
        return {"screen": "mifitness_activity", "date": TODAY, "overall_confidence": 0.95,
                "metrics": {"steps": {"value": 9800, "confidence": 0.95}}}

    monkeypatch.setattr(screens_svc, "parse_screenshot", parse)
    shot = await client.post("/api/screens", data=photo_form(jpeg("scr")), headers=H)
    assert shot.status == 201
    body = await shot.json()
    assert body["screen"] == "mifitness_activity" and body["metrics"] == {"steps": 9800}
    assert isinstance(body["applied"], bool) and isinstance(body["needs_confirm"], bool)
    assert body["needs_confirm"] is True and body["applied"] is False   # дата не распознана
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["activity"] is None
    confirm = await client.post(f"/api/screens/{body['id']}/confirm", json={"apply": True},
                                headers=H)
    assert confirm.status == 200 and (await confirm.json()) == {"applied": True}
    today = dt.datetime.now(ZoneInfo("Europe/Minsk")).strftime("%Y-%m-%d")
    day = await (await client.get(f"/api/day?date={today}", headers=H)).json()
    assert day["activity"]["steps"] == 9800
    assert (await client.post("/api/screens", data=photo_form(b"nope"), headers=H)).status == 415
    assert (await client.post("/api/screens", data=photo_form(), headers=H)).status == 400
    miss = await client.post("/api/screens/9999/confirm", json={"apply": True}, headers=H)
    assert miss.status == 404
    declined = await client.post("/api/screens/9999/confirm", json={"apply": False}, headers=H)
    assert declined.status == 200 and (await declined.json()) == {"applied": False}


# ------------------------------------------------------------------ избранное

async def _two_colas(client):
    for _ in range(2):
        await client.post("/api/drinks", json={"drink": "cola", "ml": 330}, headers=H)
    items = (await (await client.get("/api/frequent", headers=H)).json())["items"]
    return items[0]["meal_id"]


async def test_favorites_flow(client):
    meal_id = await _two_colas(client)
    added = await client.post("/api/favorites", json={"meal_id": meal_id}, headers=H)
    assert added.status == 201
    fav_id = (await added.json())["id"]
    again = await client.post("/api/favorites", json={"meal_id": meal_id}, headers=H)
    assert (await again.json())["id"] == fav_id                       # тот же состав не двоится

    items = (await (await client.get("/api/frequent", headers=H)).json())["items"]
    assert [(i["favorite_id"], i["title"]) for i in items] == [(fav_id, "Кола")]   # частое не дублирует избранное

    meal = await (await client.get(f"/api/meals/{meal_id}", headers=H)).json()
    assert meal["favorite_id"] == fav_id

    rep = await client.post(f"/api/favorites/{fav_id}/repeat", headers=H)
    assert rep.status == 201 and (await rep.json())["kcal"] == 140

    # исходную запись убрали из дневника: избранное всё равно записывается
    assert (await client.delete(f"/api/meals/{meal_id}", headers=H)).status == 204
    assert (await client.post(f"/api/favorites/{fav_id}/repeat", headers=H)).status == 201

    assert (await client.delete(f"/api/favorites/{fav_id}", headers=H)).status == 204
    assert (await client.delete(f"/api/favorites/{fav_id}", headers=H)).status == 404
    assert (await client.post(f"/api/favorites/{fav_id}/repeat", headers=H)).status == 404


async def test_favorites_validation_and_isolation(client, db):
    assert (await client.post("/api/favorites", json={"meal_id": 424242}, headers=H)).status == 404
    assert (await client.post("/api/favorites", json={}, headers=H)).status == 400
    meal_id = await _two_colas(client)
    other = add_user(db, 1002)
    assert repo.add_favorite(db, other, meal_id) is None              # чужой приём в избранное не взять
    fav_id = (await (await client.post("/api/favorites", json={"meal_id": meal_id}, headers=H)).json())["id"]
    assert repo.list_favorites(db, other) == []
    assert repo.remove_favorite(db, other, fav_id) is False
    assert repo.favorite_meal_id(db, other, fav_id) is None


# ------------------------------------------------------------------ штрихкод и Open Food Facts

def ean13(body12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


OFF_FOUND = {"code": "x", "name": "Кефир 2,5%", "brand": "Простоквашино", "kcal_100g": 53.0,
             "protein_100g": 2.9, "fat_100g": 2.5, "carbs_100g": 4.0, "serving_g": 250.0}


async def test_barcode_endpoint(client, db, user_id, monkeypatch):
    from app.adapters import off
    code = ean13("460700489000")

    async def fake_fetch(c, transport=None):  # noqa: ANN001
        return dict(OFF_FOUND, code=c) if c == code else None

    monkeypatch.setattr(off, "fetch_barcode", fake_fetch)
    assert (await client.get("/api/products/barcode/123", headers=H)).status == 400
    assert (await client.get("/api/products/barcode/4607004890009", headers=H)).status == 400   # контрольная цифра
    assert (await client.get(f"/api/products/barcode/{ean13('460700489001')}", headers=H)).status == 404

    resp = await client.get(f"/api/products/barcode/{code}", headers=H)
    assert resp.status == 200
    body = await resp.json()
    assert body["source"] == "off" and body["existing_id"] is None
    d = body["draft"]
    assert (d["name"], d["brand"], d["kcal"], d["barcode"], d["unit_grams"]) == \
        ("Кефир 2,5%", "Простоквашино", 53, code, 250)

    pid = repo.create_product(db, user_id, "Мой кефир", 50, 3, 2.5, 4, barcode=code)
    again = await (await client.get(f"/api/products/barcode/{code}", headers=H)).json()
    assert again["existing_id"] == pid                                  # такой продукт у вас уже есть


async def test_barcode_falls_back_to_local_cache_when_off_down(client, db, user_id, monkeypatch):
    from app.adapters import off
    code = ean13("460700489002")
    repo.create_product(db, user_id, "Сырок", 330, 8, 24, 20, barcode=code)

    async def down(c, transport=None):  # noqa: ANN001
        return None

    monkeypatch.setattr(off, "fetch_barcode", down)
    resp = await client.get(f"/api/products/barcode/{code}", headers=H)
    assert resp.status == 200 and (await resp.json())["draft"]["name"] == "Сырок"


def test_label_draft_reads_barcode_and_rejects_bad_checksum():
    from app.services import products as productsvc
    code = ean13("460700489000")
    draft, _, notes = productsvc.label_draft({"name": "X", "per_100g": {"kcal": 50, "protein": 1, "fat": 1, "carbs": 9},
                                              "barcode": f"{code[:1]} {code[1:]}", "confidence": 0.9})
    assert draft["barcode"] == code and notes == []
    draft, _, notes = productsvc.label_draft({"name": "X", "per_100g": {"kcal": 50, "protein": 1, "fat": 1, "carbs": 9},
                                              "barcode": "4607004890009", "confidence": 0.9})
    assert draft["barcode"] is None and any("штрихкод" in n for n in notes)


async def test_enrich_with_off(monkeypatch):
    from app.adapters import off
    from app.services import products as productsvc
    code = ean13("460700489000")

    async def fake_fetch(c, transport=None):  # noqa: ANN001
        return dict(OFF_FOUND, code=c)

    monkeypatch.setattr(off, "fetch_barcode", fake_fetch)
    # на фото только штрихкод: всё берём из OFF
    empty = {"name": None, "brand": None, "kcal": None, "protein": None, "fat": None, "carbs": None,
             "default_grams": None, "unit_name": None, "unit_grams": None, "barcode": code}
    out, notes = await productsvc.enrich_with_off(empty, [])
    assert (out["name"], out["kcal"], out["fat"]) == ("Кефир 2,5%", 53, 2.5) and any("Open Food Facts" in n for n in notes)
    # числа с упаковки главнее; сильное расхождение — только заметка
    label = dict(empty, name="Кефир", kcal=80, protein=3, fat=3, carbs=5)
    out, notes = await productsvc.enrich_with_off(label, [])
    assert out["kcal"] == 80 and out["name"] == "Кефир" and out["brand"] == "Простоквашино"
    assert any("80" in n and "53" in n for n in notes)
    # нет штрихкода: ничего не меняется и в сеть не ходим
    same, same_notes = await productsvc.enrich_with_off(dict(label, barcode=None), ["a"])
    assert same["kcal"] == 80 and same_notes == ["a"]


# ------------------------------------------------------------------ анализ за неделю и месяц

def _seed_period(db, user_id, days=14):
    today = dt.date.fromisoformat(TODAY)
    for i in range(days):
        d = (today - dt.timedelta(days=i)).isoformat()
        kcal = 2600 if i % 2 else 1700          # после коротких ночей ест больше
        db.execute(
            """INSERT INTO meals (user_id, eaten_at_utc, local_date, local_time, meal_type, source,
               kcal_total, protein_g, fat_g, carbs_g, kcal_low, kcal_high, confidence, method)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, d + "T10:00:00+00:00", d, "13:00", "lunch", "text", kcal, 60, 50, 200, kcal, kcal, 0.8, "mixed"))
        repo.save_sleep(db, user_id, d, {"duration_min": 330 if i % 2 else 480}, "screenshot", 0.9)
        repo.save_activity(db, user_id, d, {"steps": 4000 + 100 * i}, "screenshot", 0.9)
        repo.save_body_measurement(db, user_id, d, {"weight_kg": 90 + 0.1 * i}, "manual", 1.0)
    db.commit()


async def test_period_analysis_week(client, db, user_id):
    _seed_period(db, user_id)
    resp = await client.get("/api/analysis?period=week", headers=H)
    assert resp.status == 200
    a = await resp.json()
    assert a["period"] == "week" and a["days"] == 7 and a["logged_days"] == 7
    keys = {m["key"] for m in a["metrics"]}
    assert {"kcal", "protein", "steps", "sleep", "logged"} <= keys
    assert a["weight"]["change"] < 0 and a["weight"]["weighins"] == 7
    assert any(p["title"] == "Сон и аппетит" for p in a["patterns"]) or a["tips"]
    assert a["focus"] and a["focus"]["text"]
    assert all("prio" not in t for t in a["tips"])


async def test_period_analysis_month_empty_and_bad_period(client):
    a = await (await client.get("/api/analysis?period=month", headers=H)).json()
    assert a["days"] == 30 and a["logged_days"] == 0 and a["focus"] is None
    bad = await client.get("/api/analysis?period=year", headers=H)
    assert bad.status == 400


async def test_stats_has_previous_period_and_goals(client, db, user_id):
    _seed_period(db, user_id)
    s = await (await client.get("/api/stats?range=week", headers=H)).json()
    assert s["prev_summary"]["days_logged"] == 7
    assert s["goals"]["steps"] == 8000 and s["goals"]["water_ml"] >= 1500
