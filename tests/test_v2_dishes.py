"""Фаза 1 v2: посуда. Миграция 016, CRUD, разбор фразы, строка посуды в запросе к модели,
сужение диапазона до ±15 %, кнопка «🍽 Посуда», посуда по умолчанию."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile

import pytest
from aiogram.types import CallbackQuery, User

from app.db import MIGRATIONS_DIR, connect, migrate, user_version
from app.domain import dishes as dishdom
from app.domain import intents
from app.llm import client as llm
from app.routers import intake
from app.services import dishes as dishsvc
from app.services import food as foodsvc
from app.storage import repo
from tests.test_photo_flow import _drain, _feed, _food, _ids, _photo, build  # noqa: F401
from tests.test_photo_flow import db  # noqa: F401  (фикстура)
from tests.test_photo_flow import _fast  # noqa: F401  (autouse: время и справочник)
from tests.test_routers_smoke import message, onboard, send


@pytest.fixture()
def plain_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ------------------------------------------------------------------ миграция

def test_migration_016_creates_dishes(plain_db):
    assert user_version(plain_db) >= 16
    cols = {r["name"] for r in plain_db.execute("PRAGMA table_info(dishes)")}
    assert {"id", "user_id", "name", "kind", "diameter_cm", "depth_cm", "volume_ml",
            "photo_path", "is_default", "deleted_at", "created_at"} <= cols
    idx = {r["name"] for r in plain_db.execute("PRAGMA index_list(dishes)")}
    assert "idx_dishes_user" in idx
    uid = repo.create_user(plain_db, 1)
    with pytest.raises(sqlite3.IntegrityError):
        plain_db.execute("INSERT INTO dishes (user_id, name, kind) VALUES (?,?,?)",
                         (uid, "x", "spaceship"))


def test_migration_016_on_v15_with_data():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(f.name.split("_", 1)[0])
        if version > 15:
            break
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {version}")
    uid = repo.create_user(conn, 7)
    assert user_version(conn) == 15
    migrate(conn)
    assert user_version(conn) >= 16
    assert repo.get_user_by_tg(conn, 7)["id"] == uid
    assert repo.list_dishes(conn, uid) == []
    conn.close()
    os.unlink(path)


# ------------------------------------------------------------------ CRUD

def test_dish_crud_and_single_default(plain_db):
    uid = repo.create_user(plain_db, 1)
    other = repo.create_user(plain_db, 2)
    a = repo.create_dish(plain_db, uid, "Белая", "plate", diameter_cm=26)
    b = repo.create_dish(plain_db, uid, "Кружка", "mug", volume_ml=350, is_default=True)
    assert repo.default_dish(plain_db, uid)["id"] == b
    repo.update_dish(plain_db, uid, a, is_default=True)
    assert repo.default_dish(plain_db, uid)["id"] == a
    defaults = plain_db.execute("SELECT COUNT(*) c FROM dishes WHERE user_id=? AND is_default=1",
                                (uid,)).fetchone()["c"]
    assert defaults == 1
    c = repo.create_dish(plain_db, uid, "Миска", "bowl", is_default=True)
    assert [r["id"] for r in repo.list_dishes(plain_db, uid)][0] == c      # умолчание первым
    assert repo.default_dish(plain_db, uid)["id"] == c
    assert repo.get_dish(plain_db, uid, a)["is_default"] == 0

    assert repo.update_dish(plain_db, uid, a, name="Синяя", depth_cm=3.5)
    got = repo.get_dish(plain_db, uid, a)
    assert (got["name"], got["depth_cm"], got["diameter_cm"]) == ("Синяя", 3.5, 26)
    assert repo.update_dish(plain_db, other, a, name="Чужая") is False       # чужую не правим
    assert repo.get_dish(plain_db, other, a) is None
    with pytest.raises(ValueError):
        repo.update_dish(plain_db, uid, a, kind="spaceship")
    with pytest.raises(ValueError):
        repo.create_dish(plain_db, uid, " ", "plate")

    assert repo.delete_dish(plain_db, uid, c) is True
    assert repo.delete_dish(plain_db, uid, c) is False
    assert repo.default_dish(plain_db, uid) is None                          # удалённая не умолчание
    assert repo.get_dish(plain_db, uid, c) is None
    assert repo.get_dish(plain_db, uid, c, include_deleted=True)["deleted_at"]
    assert {r["id"] for r in repo.list_dishes(plain_db, uid)} == {a, b}


# ------------------------------------------------------------------ разбор фразы

@pytest.mark.parametrize("text,expected", [
    ("добавь посуду: глубокая тарелка 20 см 500 мл",
     dict(kind="deep_plate", diameter_cm=20.0, volume_ml=500.0, depth_cm=None)),
    ("посуда кружка 350 мл", dict(kind="mug", volume_ml=350.0, diameter_cm=None)),
    ("Добавь посуду «Белая» тарелка Ø26 см по умолчанию",
     dict(kind="plate", name="Белая", diameter_cm=26.0, is_default=True)),
    ("добавь посуду миска 180 мм глубина 6 см 0,8 л",
     dict(kind="bowl", diameter_cm=18.0, depth_cm=6.0, volume_ml=800.0)),
    ("посуда стакан 250 мл", dict(kind="glass", volume_ml=250.0)),
    ("добавь посуду чашка 200 мл", dict(kind="cup", volume_ml=200.0)),
    ("посуда пиала 12 см", dict(kind="bowl", diameter_cm=12.0)),
    ("добавь посуду сковорода 28 см", dict(kind="pan", diameter_cm=28.0)),
    ("посуда ланчбокс 1 л", dict(kind="container", volume_ml=1000.0)),
    ("добавь посуду контейнер 750 мл", dict(kind="container", volume_ml=750.0)),
    ("посуда 20 см", dict(kind="other", diameter_cm=20.0)),
])
def test_parse_dish_text(text, expected):
    got = dishdom.parse_dish_text(text)
    assert got is not None, text
    for key, value in expected.items():
        assert got[key] == value, (text, key, got)


@pytest.mark.parametrize("text", ["", "добавь посуду", "посуда:", "привет", "кружка 50 л"])
def test_parse_dish_text_rejects(text):
    assert dishdom.parse_dish_text(text) is None


def test_describe_dish():
    dish = {"name": "Белая", "kind": "deep_plate", "diameter_cm": 20.0, "depth_cm": 5.0,
            "volume_ml": 500.0}
    assert dishdom.describe_dish(dish) == \
        "глубокая тарелка «Белая», Ø20 см, глубина 5 см, объём 500 мл"
    assert dishdom.describe_dish({"name": "Кружка", "kind": "mug", "volume_ml": 350,
                                  "diameter_cm": None, "depth_cm": None}) == "кружка, объём 350 мл"


def test_dish_intents():
    assert intents.detect("добавь посуду: глубокая тарелка 20 см 500 мл") == \
        intents.Intent("dish_add", "глубокая тарелка 20 см 500 мл")
    assert intents.detect("Посуда кружка 350 мл").name == "dish_add"
    assert intents.detect("моя посуда").name == "dishes"
    assert intents.detect("посуда").name == "dishes"
    assert intents.detect("съел тарелку супа") is None


# ------------------------------------------------------------------ запрос к модели

def _fake_chat(calls, data=None):
    async def fake(purpose, messages, model=None, temperature=0.1, user_id=None, db=None,
                   max_tokens=1500):
        calls.append({"purpose": purpose, "messages": messages, "model": model,
                      "max_tokens": max_tokens})
        return (data or {
            "items": [{"name_ru": "суп", "name_en": "soup", "dish_fill": 0.66, "volume_ml": 330,
                       "amount": {"kind": "portion", "grams_range": [250, 400]},
                       "confidence": 0.8}],
            "overall_confidence": 0.8}), "m"
    return fake


def _parts(call):
    return call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_dish_line_and_photo_in_prompt(monkeypatch, plain_db, tmp_path):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _fake_chat(calls))
    photo = tmp_path / "empty.jpg"
    photo.write_bytes(b"empty-dish")
    uid = repo.create_user(plain_db, 1)
    did = repo.create_dish(plain_db, uid, "Белая", "deep_plate", diameter_cm=20, depth_cm=5,
                           volume_ml=500, photo_path=str(photo))
    dish = repo.get_dish(plain_db, uid, did)
    u = await foodsvc.parse_photo("AAAA", "обед", uid, plain_db, dishes=dish)
    parts = _parts(calls[0])
    text = parts[0]["text"]
    assert ("Посуда на фото: глубокая тарелка «Белая», Ø20 см, глубина 5 см, объём 500 мл. "
            "Используй её размер как масштаб") in text
    assert "плотность продукта" in text and "обед" in text
    images = [p for p in parts if p["type"] == "image_url"]
    assert len(images) == 2, "фото пустой посуды должно уйти вторым изображением"
    assert any("Фото пустой посуды (для масштаба, не еда)" in p.get("text", "") for p in parts)
    import base64
    assert images[1]["image_url"]["url"].endswith(base64.b64encode(b"empty-dish").decode())
    it = u.items[0]                                   # диапазон модели 250–400 сужен до ±15 % от 325
    assert (it.grams_min, it.grams_max) == pytest.approx((276.25, 373.75))


@pytest.mark.asyncio
async def test_no_dish_means_plain_prompt(monkeypatch, plain_db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _fake_chat(calls))
    await foodsvc.parse_photo("AAAA", None, 1, plain_db)
    parts = _parts(calls[0])
    assert "Используй её размер как масштаб" not in parts[0]["text"]
    assert len(parts) == 2


@pytest.mark.asyncio
async def test_dish_without_photo_file_still_works(monkeypatch, plain_db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _fake_chat(calls))
    dish = {"id": 1, "name": "Кружка", "kind": "mug", "volume_ml": 350, "diameter_cm": None,
            "depth_cm": None, "photo_path": "/нет/такого/файла.jpg"}
    await foodsvc.parse_photo("AAAA", None, 1, plain_db, dishes=[dish])
    assert len(_parts(calls[0])) == 2 and "кружка, объём 350 мл" in _parts(calls[0])[0]["text"]


def test_vision_prompt_describes_dishes_and_photos():
    text = foodsvc.load_prompt("vision")
    for needle in ("Известная посуда", "dish_fill", "volume_ml", "photo_index", "Несколько фото"):
        assert needle in text


# ------------------------------------------------------------------ диапазон ±15 %

def _item(**extra):
    base = {"name_ru": "суп", "name_en": "soup", "confidence": 0.8,
            "amount": {"kind": "portion", "portion_hint": "medium"}}
    base.update(extra)
    return {"items": [base], "overall_confidence": 0.8}


def test_band_15_percent_with_dish():
    u = foodsvc.understanding_from_json(_item(dish_fill=0.7), {1})
    it = u.items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((212.5, 287.5))
    u = foodsvc.understanding_from_json(_item(volume_ml=300), {1})
    assert u.items[0].grams_min == pytest.approx(212.5)


def test_band_stays_25_without_dish_or_without_reference():
    plain = foodsvc.understanding_from_json(_item(dish_fill=0.7), None).items[0]
    assert (plain.grams_min, plain.grams_max) == pytest.approx((187.5, 312.5))
    no_ref = foodsvc.understanding_from_json(_item(), {1}).items[0]     # модель посуду не использовала
    assert (no_ref.grams_min, no_ref.grams_max) == pytest.approx((187.5, 312.5))
    other_photo = foodsvc.understanding_from_json(_item(dish_fill=0.5, photo_index=2), {1}).items[0]
    assert other_photo.grams_min == pytest.approx(187.5)


def test_explicit_grams_keep_10_percent_with_dish():
    raw = _item(dish_fill=0.5)
    raw["items"][0]["amount"] = {"kind": "grams", "grams": 300}
    it = foodsvc.understanding_from_json(raw, {1}).items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((270, 330))
    assert it.explicit_grams == 300


def test_wide_model_range_is_narrowed_and_garbage_fields_tolerated():
    raw = _item(dish_fill="много", volume_ml=400)
    raw["items"][0]["amount"] = {"kind": "portion", "grams_range": [200, 400]}
    it = foodsvc.understanding_from_json(raw, {1}).items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((255, 345))
    junk = foodsvc.understanding_from_json(_item(dish_fill="?", volume_ml=None), {1}).items[0]
    assert junk.grams_min == pytest.approx(187.5)


# ------------------------------------------------------------------ чат: кнопка «🍽 Посуда»

def _recorder(monkeypatch, names=("салат", "борщ")):
    """parse_photo пишет, с какой посудой его вызвали; ответ зависит от наличия посуды."""
    seen: list = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        dish = kw.get("dishes")
        seen.append(dish["name"] if dish is not None else None)
        return _food(names[1] if dish is not None else names[0], 250 if dish is not None else 150)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    return seen


async def _press(dp, bot, data):
    from aiogram.types import Update
    cb = CallbackQuery(id=str(next(_ids)), from_user=User(id=1, is_bot=False, first_name="Т"),
                       chat_instance="x", message=message("карточка").as_(bot), data=data).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))


def test_meal_keyboard_has_dish_and_add_photo_buttons():
    kb = intake._meal_kb(5)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "meal_dishes:5" in data and "meal_addphoto:5" in data
    assert len(kb.inline_keyboard) <= 3 and all(len(r) <= 3 for r in kb.inline_keyboard)


@pytest.mark.asyncio
async def test_meal_dish_callback_reparses_and_stores_dish(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    seen = _recorder(monkeypatch)
    did = repo.create_dish(db, uid, "Белая", "deep_plate", diameter_cm=20, volume_ml=500)
    await _feed(dp, bot, _photo(bot, "p1"))
    assert seen == [None] and [r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")] == ["салат"]
    meal = db.execute("SELECT * FROM meals").fetchone()

    await _press(dp, bot, f"meal_dishes:{meal['id']}")             # открыть список
    await _press(dp, bot, f"meal_dish:{meal['id']}:{did}")          # выбрать
    assert seen == [None, "Белая"]
    names = [r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")]
    assert names == ["борщ"], "позиции должны замениться"
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 1
    photos = repo.meal_photos(db, meal["id"])
    assert [p["dish_id"] for p in photos] == [did]
    assert any("Белая" in t for t in session.texts), "карточка должна показать посуду"
    assert db.execute("SELECT kcal_total FROM meals").fetchone()[0] > 0

    await _press(dp, bot, f"meal_dish:{meal['id']}:0")              # «без посуды»
    assert seen[-1] is None and repo.meal_photos(db, meal["id"])[0]["dish_id"] is None
    await _drain()


@pytest.mark.asyncio
async def test_meal_dish_foreign_or_missing_dish_rejected(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    other = repo.create_user(db, 99)
    foreign = repo.create_dish(db, other, "Чужая", "plate", diameter_cm=24)
    seen = _recorder(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    await _press(dp, bot, f"meal_dish:{meal['id']}:{foreign}")
    await _press(dp, bot, f"meal_dish:{meal['id']}:9999")
    assert seen == [None], "чужая и несуществующая посуда не должны запускать пересчёт"
    await _drain()


@pytest.mark.asyncio
async def test_meal_dish_failure_keeps_meal(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    seen = _recorder(monkeypatch)
    did = repo.create_dish(db, uid, "Белая", "plate", diameter_cm=26)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()

    async def boom(*a, **k):
        raise llm.LLMError("нет сети")
    monkeypatch.setattr(foodsvc, "parse_photo", boom)
    await _press(dp, bot, f"meal_dish:{meal['id']}:{did}")
    assert [r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")] == ["салат"]
    assert repo.meal_photos(db, meal["id"])[0]["dish_id"] is None
    assert any("Не смог пересчитать" in t for t in session.texts)
    await _drain()


@pytest.mark.asyncio
async def test_dish_picker_without_dishes_shows_hint(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    _recorder(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    session.texts.clear()
    await _press(dp, bot, f"meal_dishes:{meal['id']}")
    assert any("Посуды пока нет" in t and "добавь посуду" in t for t in session.texts)
    await _drain()


@pytest.mark.asyncio
async def test_default_dish_applied_automatically(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    seen = _recorder(monkeypatch)
    did = repo.create_dish(db, uid, "Белая", "plate", diameter_cm=26, is_default=True)
    await _feed(dp, bot, _photo(bot, "p1"))
    assert seen == ["Белая"], "посуда по умолчанию должна уйти в распознавание"
    meal = db.execute("SELECT * FROM meals").fetchone()
    assert [p["dish_id"] for p in repo.meal_photos(db, meal["id"])] == [did]
    assert any("🍽 Белая" in t for t in session.texts)
    await _drain()


@pytest.mark.asyncio
async def test_no_default_dish_no_dish(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    seen = _recorder(monkeypatch)
    repo.create_dish(db, uid, "Белая", "plate", diameter_cm=26)       # есть, но не умолчание
    await _feed(dp, bot, _photo(bot, "p1"))
    assert seen == [None]
    assert not any("🍽 Белая" in t for t in session.texts)
    await _drain()


# ------------------------------------------------------------------ чат: /dishes и фразы

@pytest.mark.asyncio
async def test_add_dish_by_phrase_and_dishes_command(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "добавь посуду: глубокая тарелка 20 см 500 мл")
    rows = repo.list_dishes(db, uid)
    assert len(rows) == 1 and rows[0]["kind"] == "deep_plate" and rows[0]["volume_ml"] == 500
    assert any("Добавил" in t and "глубокая тарелка" in t for t in session.texts)
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0, "это не еда"
    await send(dp, bot, "посуда кружка 350 мл")
    assert len(repo.list_dishes(db, uid)) == 2
    session.texts.clear()
    await send(dp, bot, "/dishes")
    assert any("Твоя посуда" in t and "кружка" in t for t in session.texts)
    await send(dp, bot, "добавь посуду: что-то непонятное")
    assert len(repo.list_dishes(db, uid)) == 2
    assert any("Не понял, что за посуда" in t for t in session.texts)


@pytest.mark.asyncio
async def test_dishes_buttons_default_and_delete(db):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    a = repo.create_dish(db, uid, "Белая", "plate", diameter_cm=26)
    b = repo.create_dish(db, uid, "Кружка", "mug", volume_ml=350)
    await _press(dp, bot, f"dish_def:{b}")
    assert repo.default_dish(db, uid)["id"] == b
    await _press(dp, bot, f"dish_def:{a}")
    assert repo.default_dish(db, uid)["id"] == a and repo.get_dish(db, uid, b)["is_default"] == 0
    await _press(dp, bot, f"dish_def:{a}")                           # повтор снимает
    assert repo.default_dish(db, uid) is None
    await _press(dp, bot, f"dish_del:{b}")
    assert repo.get_dish(db, uid, b) is None and repo.get_dish(db, uid, a)


@pytest.mark.asyncio
async def test_add_dish_phrase_right_after_photo_is_not_a_caption(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    seen = _recorder(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    await send(dp, bot, "добавь посуду: тарелка 26 см")
    assert seen == [None], "фраза про посуду не должна пересчитывать фото как подпись"
    assert len(repo.list_dishes(db, uid)) == 1
    await _drain()


@pytest.mark.asyncio
async def test_dishes_in_menu_and_commands(db):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await send(dp, bot, "/dishes")
    assert any("Посуды пока нет" in t for t in session.texts)


def test_save_dish_photo_under_photos_dishes(tmp_path):
    path = dishsvc.save_dish_photo(b"jpeg-bytes")
    assert "/photos/dishes/" in path.replace("\\", "/") and path.endswith(".jpg")
    assert dishsvc.photo_b64({"photo_path": path})
    assert dishsvc.photo_b64({"photo_path": None}) is None
    assert dishsvc.photo_b64({"photo_path": "/нет/файла"}) is None
