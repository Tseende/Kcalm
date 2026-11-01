"""Open Food Facts: штрихкод, кэш, поиск по бренду и ввод через бота."""
from __future__ import annotations

import os
import tempfile

import httpx
import pytest

from app.adapters import off
from app.db import connect, migrate
from app.services import barcode
from app.storage import repo
from tests.test_routers_smoke import build, onboard, send

NUTELLA = "3017620422003"


def _product(**over):
    base = {"code": NUTELLA, "product_name": "Nutella", "brands": "Nutella, Ferrero",
            "serving_quantity": 15,
            "nutriments": {"energy-kcal_100g": 539, "proteins_100g": 6.3, "fat_100g": 30.9,
                           "carbohydrates_100g": 57.5}}
    base.update(over)
    return base


def _transport(payload: dict, status: int = 200, hits: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if hits is not None:
            hits.append(str(request.url))
        assert "SlimBot" in request.headers["user-agent"]
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def test_barcode_checksum():
    assert off.valid_barcode(NUTELLA)
    assert off.valid_barcode("5449000000996")
    assert not off.valid_barcode("3017620422004")        # неверная контрольная цифра
    assert not off.valid_barcode("12345")
    assert not off.valid_barcode("30176204220AB")


def test_parse_product_requires_complete_sane_data():
    assert off.parse_product(_product())["kcal_100g"] == 539
    assert off.parse_product(_product(nutriments={"energy-kcal_100g": 100})) is None   # нет БЖУ
    bad = _product(nutriments={"energy-kcal_100g": 5000, "proteins_100g": 1,
                               "fat_100g": 1, "carbohydrates_100g": 1})
    assert off.parse_product(bad) is None
    over = _product(nutriments={"energy-kcal_100g": 100, "proteins_100g": 60,
                                "fat_100g": 40, "carbohydrates_100g": 40})
    assert off.parse_product(over) is None
    kj = _product(nutriments={"energy_100g": 2252, "proteins_100g": 6.3, "fat_100g": 30.9,
                              "carbohydrates_100g": 57.5})
    assert round(off.parse_product(kj)["kcal_100g"]) == 538
    assert off.parse_product(_product(product_name="", generic_name="")) is None


@pytest.mark.asyncio
async def test_fetch_barcode_ok_and_failures():
    ok = await off.fetch_barcode(NUTELLA, transport=_transport({"status": 1, "product": _product()}))
    assert ok and ok["name"] == "Nutella" and ok["serving_g"] == 15
    assert await off.fetch_barcode(NUTELLA, transport=_transport({"status": 0})) is None
    assert await off.fetch_barcode(NUTELLA, transport=_transport({}, status=503)) is None
    assert await off.fetch_barcode("123", transport=_transport({})) is None     # даже не ходим в сеть


@pytest.mark.asyncio
async def test_lookup_caches_in_local_db(db):
    hits: list = []
    t = _transport({"status": 1, "product": _product()}, hits=hits)
    ref, serving = await barcode.lookup(db, NUTELLA, transport=t)
    assert ref.source == "off" and ref.kcal_100g == 539 and serving == 15
    again = await barcode.lookup(db, NUTELLA, transport=t)      # теперь из кэша, без сети
    assert again[0].food_id == ref.food_id and len(hits) == 1


@pytest.mark.asyncio
async def test_branded_search_requires_brand_and_name_match():
    products = {"products": [_product(brands="Другой бренд"), _product()]}
    hit = await off.search_branded("Nutella паста", "Ferrero", transport=_transport(products))
    assert hit and "Ferrero" not in hit["name"] and hit["brand"] == "Nutella"
    miss = await off.search_branded("Совсем другое", "Ferrero", transport=_transport(products))
    assert miss is None
    assert await off.search_branded("Nutella", "Ferrero", transport=_transport({}, 503)) is None


def test_message_parsing():
    assert barcode.parse_barcode_message(NUTELLA) == (NUTELLA, None)
    assert barcode.parse_barcode_message(f"штрихкод {NUTELLA} 40 г") == (NUTELLA, 40.0)
    assert barcode.parse_barcode_message("гречка 150") is None
    assert barcode.parse_barcode_message("500") is None
    assert barcode.parse_grams_reply("40") == 40 and barcode.parse_grams_reply("2,5 г") == 2.5
    assert barcode.parse_grams_reply("0") is None and barcode.parse_grams_reply("привет") is None


@pytest.mark.asyncio
async def test_bot_flow_barcode_then_grams(db, monkeypatch):
    async def fake_fetch(code, transport=None):
        return off.parse_product(_product())
    monkeypatch.setattr(off, "fetch_barcode", fake_fetch)
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)

    await send(dp, bot, NUTELLA)                      # спрашивает граммы
    assert repo.latest_pending_input(db, uid, "barcode")
    await send(dp, bot, "40")                         # 40 г → 539*0.4 ≈ 215 ккал
    meal = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchone()
    assert meal and 200 <= meal["kcal_total"] <= 230, meal["kcal_total"]
    assert repo.latest_pending_input(db, uid, "barcode") is None
    # продукт запомнился в личных частых
    assert db.execute("SELECT COUNT(*) c FROM user_foods WHERE user_id=?", (uid,)).fetchone()["c"] == 1


@pytest.mark.asyncio
async def test_bot_flow_bad_checksum_and_unknown(db, monkeypatch):
    async def none_fetch(code, transport=None):
        return None
    monkeypatch.setattr(off, "fetch_barcode", none_fetch)
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    before = len(session.names())
    await send(dp, bot, "3017620422004")              # контрольная цифра не сходится
    await send(dp, bot, NUTELLA)                      # корректный код, но в базе нет
    assert len(session.names()) > before
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 0
