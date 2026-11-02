"""Уточнение записи рассказом о приготовлении: кнопка «Уточнить», текст и голос, правка состава."""
from __future__ import annotations

import json

import pytest
from aiogram.types import CallbackQuery, Update, User, Voice

from app.llm import client as llm
from app.routers import intake
from app.services import food as foodsvc
from app.storage import repo
from tests.test_photo_flow import (_drain, _fast, _feed, _food, _ids, _photo,  # noqa: F401
                                   build, db)
from tests.test_routers_smoke import message, onboard, send

pytestmark = pytest.mark.asyncio


def _items(*pairs: tuple[str, float]) -> foodsvc.MealUnderstanding:
    return foodsvc.understanding_from_json({
        "items": [{"name_ru": n, "name_en": n, "amount": {"kind": "grams", "grams": g},
                   "preparation": "unknown", "confidence": 0.9} for n, g in pairs],
        "overall_confidence": 0.9,
    })


async def _press(dp, bot, data: str) -> None:
    cb = CallbackQuery(id=str(next(_ids)), from_user=User(id=1, is_bot=False, first_name="Т"),
                       chat_instance="x", message=message("карточка").as_(bot),
                       data=data).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))


async def _photo_meal(db, dp, bot, monkeypatch, name="картофель", grams=200):
    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food(name, grams)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1"))
    return db.execute("SELECT * FROM meals").fetchone()


async def test_refine_button_then_text_adds_oil(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    meal = await _photo_meal(db, dp, bot, monkeypatch)
    seen = {}

    async def fake_refine(items, comment, user_id, conn):  # noqa: ANN001
        seen["items"], seen["comment"] = items, comment
        return _items(("картофель", 200), ("масло подсолнечное", 15)), ["добавил масло"]

    monkeypatch.setattr(foodsvc, "refine_meal", fake_refine)
    await _press(dp, bot, f"meal_refine:{meal['id']}")
    assert repo.latest_pending_input(db, uid, "meal_refine")

    await send(dp, bot, "жарила на подсолнечном масле, примерно ложка")

    assert seen["comment"].startswith("жарила") and seen["items"][0]["name_ru"] == "картофель"
    rows = db.execute("SELECT name_ru FROM meal_items WHERE meal_id=?", (meal["id"],)).fetchall()
    assert [r["name_ru"] for r in rows] == ["картофель", "масло подсолнечное"]
    after = repo.get_meal(db, uid, meal["id"])
    assert after["kcal_total"] > meal["kcal_total"]
    assert db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 1, "создана вторая запись"
    assert repo.latest_pending_input(db, uid, "meal_refine") is None
    assert any("Учёл" in t and "масло подсолнечное" in t for t in session.texts), session.texts
    await _drain()


async def test_refine_by_voice_after_button(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    meal = await _photo_meal(db, dp, bot, monkeypatch)

    async def fake_transcribe(ogg, **kw):  # noqa: ANN001
        return "сверху две ложки майонеза"

    async def fake_refine(items, comment, user_id, conn):  # noqa: ANN001
        assert "майонеза" in comment
        return _items(("картофель", 200), ("майонез", 40)), []

    monkeypatch.setattr(llm, "transcribe_ogg", fake_transcribe)
    monkeypatch.setattr(foodsvc, "refine_meal", fake_refine)
    await _press(dp, bot, f"meal_refine:{meal['id']}")
    voice = message(None, 1, voice=Voice(file_id="v1", file_unique_id="v1", duration=3)).as_(bot)
    await _feed(dp, bot, voice)

    names = [r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")]
    assert names == ["картофель", "майонез"]
    assert db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 1
    await _drain()


async def test_voice_right_after_photo_is_caption_not_new_meal(db, monkeypatch):
    bot, dp, _ = build(db)
    await onboard(dp, bot)
    await _photo_meal(db, dp, bot, monkeypatch, name="салат", grams=150)

    async def fake_transcribe(ogg, **kw):  # noqa: ANN001
        return "это греческий салат с оливковым маслом"

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("греческий салат", 200) if caption else _food("салат", 150)

    monkeypatch.setattr(llm, "transcribe_ogg", fake_transcribe)
    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    voice = message(None, 1, voice=Voice(file_id="v2", file_unique_id="v2", duration=3)).as_(bot)
    await _feed(dp, bot, voice)

    assert db.execute("SELECT COUNT(*) FROM meals WHERE deleted_at IS NULL").fetchone()[0] == 1
    assert db.execute("SELECT name_ru FROM meal_items").fetchone()["name_ru"] == "греческий салат"
    await _drain()


async def test_refine_llm_failure_keeps_meal(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    meal = await _photo_meal(db, dp, bot, monkeypatch)

    async def boom(*a, **kw):  # noqa: ANN002, ANN003
        raise llm.LLMError("нет сети")

    monkeypatch.setattr(foodsvc, "refine_meal", boom)
    await _press(dp, bot, f"meal_refine:{meal['id']}")
    await send(dp, bot, "жарила на масле")

    assert repo.get_meal(db, uid, meal["id"])["kcal_total"] == meal["kcal_total"]
    assert any("запись осталась как была" in t for t in session.texts)
    await _drain()


async def test_text_without_pending_refine_is_not_swallowed(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    called = []

    async def fake_refine(*a, **kw):  # noqa: ANN002, ANN003
        called.append(1)
        return _items(("x", 1)), []

    monkeypatch.setattr(foodsvc, "refine_meal", fake_refine)

    async def fake_text(text, user_id, conn):  # noqa: ANN001
        return _items(("яблоко", 150))

    monkeypatch.setattr(foodsvc, "parse_text", fake_text)
    await send(dp, bot, "съела яблоко")
    assert not called
    assert db.execute("SELECT COUNT(*) FROM meals WHERE user_id=?", (uid,)).fetchone()[0] == 1


async def test_refine_meal_keeps_old_grams_and_sends_current_items(db, monkeypatch):
    sent = {}

    async def fake_chat(purpose, messages, **kw):  # noqa: ANN001
        sent["purpose"] = purpose
        sent["user"] = json.loads(messages[1]["content"])
        return {"items": [
            {"name_ru": "Котлета", "name_en": "cutlet"},                    # граммы не вернулись
            {"name_ru": "масло подсолнечное", "name_en": "sunflower oil",
             "amount": {"kind": "grams", "grams": 10}},
        ], "changes": ["добавил масло 10 г"], "overall_confidence": 0.8}, {}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    u, changes = await foodsvc.refine_meal(
        [{"name_ru": "котлета", "name_en": "cutlet", "grams": 120}], "жарила на масле",
        None, db)

    assert sent["purpose"] == "food_refine"
    assert sent["user"]["current_items"] == [
        {"name_ru": "котлета", "name_en": "cutlet", "grams": 120}]
    assert sent["user"]["comment"] == "жарила на масле"
    assert [(i.name_ru, i.explicit_grams) for i in u.items] == \
        [("Котлета", 120.0), ("масло подсолнечное", 10.0)]
    assert changes == ["добавил масло 10 г"]


async def test_cooking_hint_only_for_hidden_fat_dishes():
    assert intake._may_hide_fat(_items(("жареная картошка", 200)))
    assert intake._may_hide_fat(_items(("салат", 150)))
    assert not intake._may_hide_fat(_items(("яблоко", 150), ("творог", 100)))


async def test_diff_text_lists_added_changed_removed():
    from types import SimpleNamespace as NS
    calc = NS(kcal_total=520, items=[NS(name_ru="Картофель", grams=150, kcal=120),
                                     NS(name_ru="масло", grams=15, kcal=135)])
    text = intake._diff_text([{"name_ru": "картофель", "grams": 200},
                              {"name_ru": "лук", "grams": 30}], calc, 400)
    assert "400 → <b>520</b> ккал (+120)" in text
    assert "＋ масло 15 г (135 ккал)" in text
    assert "✎ Картофель: 200 → 150 г" in text
    assert "－ лук" in text


# ------------------------------------------------------------------ чай и кофе в воде

from app.domain.water import hydration_ml  # noqa: E402


@pytest.mark.parametrize("name,grams,expected", [
    ("чай", 250, 250), ("кофе с молоком", 200, 200), ("капучино", 180, 180),
    ("зелёный чай без сахара", 300, 300), ("americano", 150, 150),
    ("сок яблочный", 250, 0), ("какао", 200, 0), ("кола", 330, 0), ("вино", 150, 0),
    ("кофе растворимый порошок", 3, 0), ("чайная ложка сахара", 5, 0),
    ("кофе в зёрнах", 50, 0), ("торт чайная роза", 100, 0),
])
async def test_hydration_ml(name, grams, expected):
    assert hydration_ml(name, "", grams) == expected


async def test_tea_and_coffee_count_as_water_and_follow_meal(db):
    from app.domain.nutrition import calculate_meal
    from app.services.food import _empty_ref
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    u = _items(("кофе с молоком", 250), ("круассан", 60))
    calc = calculate_meal([(i, _empty_ref(i)) for i in u.items])
    meal_id = repo.save_meal(db, uid, "2026-05-20 10:00:00", "2026-05-20", "13:00", "lunch",
                             "text", calc)
    assert repo.day_water_ml(db, uid, "2026-05-20") == 250

    repo.edit_item_grams(db, repo.get_meal_items(db, meal_id)[0]["id"], 300)
    assert repo.day_water_ml(db, uid, "2026-05-20") == 300, "правка граммов не пересчитала воду"

    repo.update_meal_time(db, uid, meal_id, "2026-05-19", "09:00", "breakfast")
    assert repo.day_water_ml(db, uid, "2026-05-20") == 0
    assert repo.day_water_ml(db, uid, "2026-05-19") == 300

    repo.soft_delete_meal(db, uid, meal_id)
    assert repo.day_water_ml(db, uid, "2026-05-19") == 0, "отмена записи не убрала воду"


async def test_option_button_answers_clarification(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        u = _food("суп", 300)
        u.clarification_question = "Какая порция?"
        u.clarification_options = ["Маленькая", "Большая"]
        return u

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    seen = {}

    async def fake_refine(items, comment, user_id, conn):  # noqa: ANN001
        seen["comment"] = comment
        return _items(("суп", 450)), []

    monkeypatch.setattr(foodsvc, "refine_meal", fake_refine)
    await _press(dp, bot, f"meal_ans:{meal['id']}:1")
    assert "Какая порция?" in seen["comment"] and "Большая" in seen["comment"]
    assert db.execute("SELECT grams FROM meal_items WHERE meal_id=?",
                      (meal["id"],)).fetchone()["grams"] == 450
    await _press(dp, bot, f"meal_ans:{meal['id']}:7")          # такого варианта нет
    await _drain()


async def test_fat_quick_answer_and_often_repeat(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    meal = await _photo_meal(db, dp, bot, monkeypatch, name="жареная картошка")
    seen = {}

    async def fake_refine(items, comment, user_id, conn):  # noqa: ANN001
        seen["comment"] = comment
        return _items(("жареная картошка", 200), ("масло подсолнечное", 10)), []

    monkeypatch.setattr(foodsvc, "refine_meal", fake_refine)
    await _press(dp, bot, f"meal_fat:{meal['id']}:0")
    assert "1 столовая ложка" in seen["comment"]
    kcal = repo.get_meal(db, uid, meal["id"])["kcal_total"]

    await send(dp, bot, "/often")
    assert any("частые блюда" in t for t in session.texts), session.texts
    await _press(dp, bot, f"often:{meal['id']}")
    assert db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 2
    assert db.execute("SELECT kcal_total FROM meals ORDER BY id DESC").fetchone()[0] == kcal
    await _drain()
