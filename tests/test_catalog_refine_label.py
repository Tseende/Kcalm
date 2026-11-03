"""Кнопки не улетают вверх, база продуктов шире, запасная оценка без LLM, этикетка по фото."""
from __future__ import annotations

import pytest

from app.domain import matching
from app.domain.generic_foods import generic_ref
from app.llm import client as llm
from app.routers import products as products_router
from app.services import food as foodsvc
from app.storage import repo
from tests.test_photo_flow import _drain, _feed, _photo, build, db  # noqa: F401
from tests.test_refine import _items, _photo_meal, _press
from tests.test_routers_smoke import onboard, send

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ «Уточнить»

async def test_refine_result_lands_at_the_bottom_with_buttons(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    meal = await _photo_meal(db, dp, bot, monkeypatch)

    async def fake_refine(items, comment, user_id, conn):  # noqa: ANN001
        return _items(("картофель", 200), ("масло подсолнечное", 15)), []

    monkeypatch.setattr(foodsvc, "refine_meal", fake_refine)
    await _press(dp, bot, f"meal_refine:{meal['id']}")
    session.methods.clear()
    await send(dp, bot, "жарила на масле, ложка")

    # со старой карточки сняты кнопки, а новая карточка с кнопками — последнее сообщение
    assert "EditMessageReplyMarkup" in session.methods
    assert session.methods[-1] in ("SendMessage", "SendRichMessage")
    assert "EditMessageText" not in session.methods and "EditMessageRichText" not in session.methods
    await _drain()


# ------------------------------------------------------------------ база продуктов

@pytest.mark.parametrize("query, expected", [
    ("сосиски из индейки", "Сосиски из индейки"),
    ("куриные сосиски", "Сосиски куриные"),
    ("молочные сосиски", "Сосиски молочные"),
    ("сардельки", "Сардельки свиные"),
    ("крабовые палочки", "Крабовые палочки"),
])
async def test_common_products_are_in_base(db, query, expected):
    ref = matching.find_food(db, "", query)
    assert ref is not None and ref.name_ru == expected
    assert ref.kcal_100g > 0 and ref.protein_100g > 0


async def test_plural_finds_singular(db):
    ref = matching.find_food(db, "", "сосиски")
    assert ref is not None and ref.name_ru.startswith("Сосиск") and ref.kcal_100g > 200


async def test_seed_is_consistent(db):
    rows = db.execute("SELECT * FROM foods WHERE source='seed' AND external_id LIKE 'seed2:%'").fetchall()
    assert len(rows) >= 100
    for r in rows:
        macro = 4 * r["protein_100g"] + 9 * r["fat_100g"] + 4 * r["carbs_100g"]
        assert abs(r["kcal_100g"] - macro) / max(r["kcal_100g"], macro) <= 0.25, r["name_ru"]


async def test_own_product_beats_seed(db):
    uid = repo.create_user(db, 1)
    repo.create_product(db, uid, name="Сосиски индейка", kcal=150, protein=16, fat=9, carbs=1)
    ref = matching.find_food(db, "", "сосиски из индейки", user_id=uid)
    assert ref.source == "user" and ref.kcal_100g == 150


# ------------------------------------------------------------------ запасная оценка

def test_generic_ref_by_keyword():
    ref = generic_ref("сосиски куриные особые")
    assert ref and ref.kcal_100g > 100 and ref.unresolved is False
    assert generic_ref("неведомая зверюшка") is None


async def test_unknown_product_without_llm_is_not_zero(db, monkeypatch):
    async def boom(*a, **kw):  # noqa: ANN002, ANN003
        raise llm.LLMError("нет сети")

    monkeypatch.setattr(llm, "chat_json", boom)
    u = foodsvc.understanding_from_json({"items": [
        {"name_ru": "колбаска деликатесная", "name_en": "delicacy sausage",
         "amount": {"kind": "grams", "grams": 100}, "confidence": 0.8}], "overall_confidence": 0.8})
    uid = repo.create_user(db, 1)
    calc = await foodsvc.build_calc(u, db, uid)
    assert calc.kcal_total > 100


# ------------------------------------------------------------------ этикетка по фото

async def test_label_photo_without_caption_goes_to_label_reader(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    called = {}

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return foodsvc.understanding_from_json(
            {"items": [], "overall_confidence": 0.9, "photo_kind": "label"})

    async def fake_label(msg, conn, user, image_b64, status):  # noqa: ANN001
        called["ok"] = True

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    monkeypatch.setattr(products_router, "from_label_photo", fake_label)
    await _feed(dp, bot, _photo(bot, "lab1"))
    assert called.get("ok") is True
    assert db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 0
    await _drain()
