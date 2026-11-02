"""Фаза 3 v2: свои продукты. Миграция 017, матчинг (свои важнее, чужие не видны), CRUD и мягкое
удаление, проверка КБЖУ, разбор фразы и этикетки, карточка черновика (сохранить / исправить /
отмена), «💾 В мои продукты», сохранение по штрихкоду, /products."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest
from aiogram.types import CallbackQuery, Update, User

from app.db import MIGRATIONS_DIR, connect, migrate, user_version
from app.domain import intents
from app.domain import products as dom
from app.domain.matching import find_food
from app.llm import client as llm
from app.services import food as foodsvc
from app.services import products as prodsvc
from app.storage import repo
from tests.test_photo_flow import _feed, _food, _ids, _photo, build  # noqa: F401
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


TVOROG = dict(name="Творог 5%", kcal=121, protein=17, fat=5, carbs=1.8, brand="Савушкин")


def _mk(conn, uid, **over):
    spec = {**TVOROG, **over}
    return repo.create_product(conn, uid, spec.pop("name"), spec.pop("kcal"), spec.pop("protein"),
                               spec.pop("fat"), spec.pop("carbs"), **spec)


async def _press(dp, bot, data, uid=1):
    cb = CallbackQuery(id=str(next(_ids)), from_user=User(id=uid, is_bot=False, first_name="Т"),
                       chat_instance="x", message=message("карточка", uid).as_(bot),
                       data=data).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))


def _label_llm(monkeypatch, data):
    calls = []

    async def fake(purpose, messages, **kw):
        calls.append(purpose)
        return data, "fake-model"

    monkeypatch.setattr(llm, "chat_json", fake)
    return calls


LABEL = {"name": "Творог 5%", "brand": "Савушкин",
         "per_100g": {"kcal": 121, "protein": 17, "fat": 5, "carbs": 1.8},
         "per_serving": {}, "serving_g": None, "package_g": 180, "kj_100g": None,
         "confidence": 0.93, "notes": ""}


# ------------------------------------------------------------------ миграция

def test_migration_017_columns_and_index(plain_db):
    assert user_version(plain_db) >= 17
    cols = {r["name"] for r in plain_db.execute("PRAGMA table_info(foods)")}
    assert {"owner_user_id", "default_grams", "unit_name", "unit_grams", "brand",
            "deleted_at", "created_at"} <= cols
    assert "idx_foods_owner" in {r["name"] for r in plain_db.execute("PRAGMA index_list(foods)")}


def test_migration_017_applies_on_top_of_v16():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(f.name.split("_")[0])
        if version > 16:
            break
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {version}")
    seeded = conn.execute("SELECT COUNT(*) c FROM foods").fetchone()["c"]
    assert user_version(conn) == 16
    migrate(conn)
    assert user_version(conn) >= 17
    assert conn.execute("SELECT COUNT(*) c FROM foods WHERE owner_user_id IS NULL").fetchone()["c"] \
        == seeded                                     # общий справочник не тронут
    conn.close()
    os.unlink(path)


# ------------------------------------------------------------------ матчинг

def test_own_product_beats_seed_for_same_query(plain_db):
    uid = repo.create_user(plain_db, 1)
    assert find_food(plain_db, "", "творог", user_id=uid).source != "user"
    pid = _mk(plain_db, uid)
    for query in ("творог", "творог 5%", "творог савушкин", "Творог Савушкин"):
        ref = find_food(plain_db, "", query, user_id=uid)
        assert ref and ref.food_id == pid and ref.kcal_100g == 121, query


def test_own_product_never_visible_to_other_users(plain_db):
    uid = repo.create_user(plain_db, 1)
    other = repo.create_user(plain_db, 2)
    pid = _mk(plain_db, uid, name="Суперкаша Марсианская", brand=None, kcal=350, protein=10,
              fat=5, carbs=60)
    assert find_food(plain_db, "", "суперкаша марсианская", user_id=uid).food_id == pid
    assert find_food(plain_db, "", "суперкаша марсианская", user_id=other) is None
    assert find_food(plain_db, "", "суперкаша марсианская", user_id=None) is None
    assert repo.get_product(plain_db, other, pid) is None
    assert repo.list_products(plain_db, other) == []


def test_own_product_not_matched_by_unrelated_words(plain_db):
    uid = repo.create_user(plain_db, 1)
    pid = _mk(plain_db, uid)
    for query in ("гречка", "борщ", "яблоко"):
        ref = find_food(plain_db, "", query, user_id=uid)
        assert ref is None or ref.food_id != pid, query


def test_deleted_product_is_not_matched_but_history_keeps_working(plain_db):
    uid = repo.create_user(plain_db, 1)
    pid = _mk(plain_db, uid, name="Суперкаша Марсианская", brand=None)
    assert repo.delete_product(plain_db, uid, pid) is True
    assert find_food(plain_db, "", "суперкаша марсианская", user_id=uid) is None
    row = plain_db.execute("SELECT * FROM foods WHERE id=?", (pid,)).fetchone()
    assert row["deleted_at"] and row["name_ru"] == "Суперкаша Марсианская"   # строка на месте
    assert plain_db.execute("SELECT COUNT(*) c FROM food_aliases WHERE food_id=?",
                            (pid,)).fetchone()["c"] == 0


def test_user_default_grams_falls_back_to_product_portion(plain_db):
    uid = repo.create_user(plain_db, 1)
    pid = _mk(plain_db, uid, default_grams=180, unit_name="пачка", unit_grams=180)
    assert repo.user_default_grams(plain_db, uid, pid) == 180
    repo.touch_user_food(plain_db, uid, pid, default_grams=150)     # обученная порция важнее
    assert repo.user_default_grams(plain_db, uid, pid) == 150


# ------------------------------------------------------------------ CRUD

def test_product_crud(plain_db):
    uid = repo.create_user(plain_db, 1)
    other = repo.create_user(plain_db, 2)
    a = _mk(plain_db, uid)
    b = _mk(plain_db, uid, name="Йогурт греческий", brand=None, kcal=66, protein=9, fat=2,
            carbs=3.5)
    got = repo.get_product(plain_db, uid, a)
    assert (got["name_ru"], got["brand"], got["kcal_100g"], got["source"]) == \
        ("Творог 5%", "Савушкин", 121, "user")
    assert got["owner_user_id"] == uid and got["external_id"].startswith(f"u{uid}-")

    upd = repo.update_product(plain_db, uid, a, kcal=125, default_grams=200, unit_name="пачка")
    assert (upd["kcal_100g"], upd["default_grams"], upd["unit_name"]) == (125, 200, "пачка")
    assert upd["protein_100g"] == 17                              # остальное не тронуто
    assert repo.update_product(plain_db, other, a, kcal=1) is None   # чужой не правим
    assert repo.get_product(plain_db, uid, a)["kcal_100g"] == 125

    assert [r["name_ru"] for r in repo.list_products(plain_db, uid)] == \
        ["Йогурт греческий", "Творог 5%"]
    assert [r["name_ru"] for r in repo.list_products(plain_db, uid, q="ТВОРОГ")] == ["Творог 5%"]
    assert [r["name_ru"] for r in repo.list_products(plain_db, uid, q="савуш")] == ["Творог 5%"]
    repo.touch_user_food(plain_db, uid, a)
    assert repo.list_products(plain_db, uid)[0]["id"] == a        # чаще используемый выше

    assert repo.delete_product(plain_db, other, a) is False
    assert repo.delete_product(plain_db, uid, a) is True
    assert repo.delete_product(plain_db, uid, a) is False
    assert repo.get_product(plain_db, uid, a) is None
    assert [r["id"] for r in repo.list_products(plain_db, uid)] == [b]


def test_product_values_are_validated(plain_db):
    uid = repo.create_user(plain_db, 1)
    for bad in (dict(kcal=901), dict(kcal=-1), dict(protein=101), dict(fat=-0.5),
                dict(carbs=150), dict(name="  ")):
        with pytest.raises(ValueError):
            _mk(plain_db, uid, **bad)
    pid = _mk(plain_db, uid)
    with pytest.raises(ValueError):
        repo.update_product(plain_db, uid, pid, kcal=1000)
    with pytest.raises(ValueError):
        repo.update_product(plain_db, uid, pid, colour="red")
    assert repo.get_product(plain_db, uid, pid)["kcal_100g"] == 121
    assert len(repo.list_products(plain_db, uid)) == 1


@pytest.mark.parametrize("kcal,p,f,c,expect_warn", [
    (121, 17, 5, 1.8, False),            # 17*4+5*9+1.8*4 = 120
    (900, 0, 100, 0, False),             # чистый жир
    (0, 0, 0, 0, False),                 # вода
    (300, 17, 5, 1.8, True),             # ккал не сходятся с БЖУ
    (100, 60, 30, 30, True),             # Б+Ж+У > 100 г
])
def test_check_values_warns_but_does_not_block(kcal, p, f, c, expect_warn):
    assert bool(dom.check_values(kcal, p, f, c)) is expect_warn


def test_save_product_with_warning_still_saves(plain_db):
    uid = repo.create_user(plain_db, 1)
    user = repo.get_user_by_tg(plain_db, 1)
    pid, warnings = prodsvc.save_product(plain_db, user, {"name": "Странный", "kcal": 300,
                                                          "protein": 17, "fat": 5, "carbs": 1.8})
    assert warnings and repo.get_product(plain_db, uid, pid)["kcal_100g"] == 300


def test_save_product_dedupes_by_name_and_brand_and_derives_kcal(plain_db):
    uid = repo.create_user(plain_db, 1)
    user = repo.get_user_by_tg(plain_db, 1)
    first, _ = prodsvc.save_product(plain_db, user, dict(TVOROG))
    again, _ = prodsvc.save_product(plain_db, user, {**TVOROG, "name": "творог 5%", "kcal": 125})
    assert again == first and len(repo.list_products(plain_db, uid)) == 1
    assert repo.get_product(plain_db, uid, first)["kcal_100g"] == 125
    pid, _ = prodsvc.save_product(plain_db, user, {"name": "Сырок", "protein": 10, "fat": 20,
                                                   "carbs": 5})
    assert repo.get_product(plain_db, uid, pid)["kcal_100g"] == 240        # 40+180+20
    with pytest.raises(ValueError, match="ккал|Ж|У|Б"):
        prodsvc.save_product(plain_db, user, {"name": "Пустой", "protein": 1})


# ------------------------------------------------------------------ разбор фразы

@pytest.mark.parametrize("text,expected", [
    ("добавь продукт: творог 5% Савушкин, 121 ккал, Б 17, Ж 5, У 1.8",
     dict(name="творог 5% Савушкин", kcal=121, protein=17, fat=5, carbs=1.8)),
    ("добавь продукт творог, 121 ккал, белки 17 жиры 5 углеводы 1,8",
     dict(name="творог", kcal=121, protein=17, fat=5, carbs=1.8)),
    ("добавь продукт: сырок Б.Ю. Александров, бжу 8/20/22, 292 ккал на 100 г",
     dict(kcal=292, protein=8, fat=20, carbs=22)),
    ("добавь продукт: печенье 30 г: 140 ккал, 2 г белка, 6 г жиров, 19 г углеводов на 30 г",
     dict(kcal=467, protein=6.7, fat=20, carbs=63.3)),          # пересчёт на 100 г
    ("добавь продукт: сок, 1000 кДж, Б 0, Ж 0, У 10",
     dict(kcal=239, carbs=10)),
    ("добавь продукт: молоко жирное 3,2%, 60 ккал, б 3 ж 3.2 у 4.7",
     dict(name="молоко жирное 3,2%", kcal=60, protein=3, fat=3.2, carbs=4.7)),
])
def test_parse_product_text(text, expected):
    got = dom.parse_product_text(text)
    assert got is not None, text
    for key, value in expected.items():
        assert got[key] == pytest.approx(value, abs=0.15), (text, key, got)


@pytest.mark.parametrize("text", ["", "добавь продукт", "добавь продукт: творог", "привет"])
def test_parse_product_text_rejects(text):
    assert dom.parse_product_text(text) is None


def test_product_intents():
    got = intents.detect("добавь продукт: творог 5%, 121 ккал, Б 17, Ж 5, У 1.8")
    assert got and got.name == "product_add"
    assert intents.detect("мои продукты") == intents.Intent("products")
    assert intents.detect("покажи мои продукты") == intents.Intent("products")
    assert intents.detect("мои частые продукты").name == "foods"      # старая команда жива
    assert intents.detect("добавь продукт") is None
    assert intents.detect("съел творог 150 г") is None


def test_caption_says_label():
    for caption in ("этикетка", "Состав и КБЖУ", "продукт", "пищевая ценность", "nutrition facts"):
        assert dom.caption_says_label(caption), caption
    for caption in (None, "", "обед", "состав тела", "весы", "сон 7 часов"):
        assert not dom.caption_says_label(caption), caption


# ------------------------------------------------------------------ разбор этикетки

def test_label_draft_per_100g():
    draft, conf, notes = prodsvc.label_draft(LABEL)
    assert (draft["name"], draft["brand"], draft["kcal"]) == ("Творог 5%", "Савушкин", 121)
    assert (draft["unit_name"], draft["unit_grams"]) == ("упаковка", 180)
    assert conf == pytest.approx(0.93) and notes == []


def test_label_draft_converts_serving_and_kj():
    draft, _, notes = prodsvc.label_draft({
        "name": "Печенье", "per_100g": {}, "serving_g": 30,
        "per_serving": {"kcal": 140, "protein": 2, "fat": 6, "carbs": 19}, "confidence": 0.9})
    assert (draft["kcal"], draft["protein"], draft["fat"], draft["carbs"]) == (467, 6.7, 20, 63.3)
    assert (draft["unit_name"], draft["unit_grams"], draft["default_grams"]) == ("порция", 30, 30)
    assert any("порци" in n for n in notes)
    draft, _, notes = prodsvc.label_draft({
        "name": "Сок", "per_100g": {"protein": 0, "fat": 0, "carbs": 10}, "kj_100g": 167,
        "confidence": 0.8})
    assert draft["kcal"] == 40 and any("кДж" in n for n in notes)


def test_label_draft_drops_impossible_values_and_derives_kcal():
    draft, _, notes = prodsvc.label_draft({
        "name": "X", "per_100g": {"kcal": 5000, "protein": 10, "fat": 20, "carbs": 5},
        "confidence": 0.5})
    assert draft["kcal"] == 240 and any("вне допустимых" in n for n in notes)   # из БЖУ
    draft, conf, _ = prodsvc.label_draft("мусор")
    assert conf == 0 and draft["kcal"] is None


@pytest.mark.asyncio
async def test_parse_label_calls_vision_model(db, monkeypatch):
    calls = _label_llm(monkeypatch, LABEL)
    repo.create_user(db, 1)
    draft, conf, _ = await prodsvc.parse_label(db, repo.get_user_by_tg(db, 1), "Zm9v")
    assert calls == ["label_parse"] and draft["protein"] == 17 and conf > 0.9


# ------------------------------------------------------------------ чат: этикетка

def _cards(session):
    return [t for t in session.texts if t.startswith("🥫")]


@pytest.mark.asyncio
async def test_label_photo_draft_then_save(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    _label_llm(monkeypatch, LABEL)
    await _feed(dp, bot, _photo(bot, "lab1", caption="этикетка"))
    cards = _cards(session)
    assert cards and "Творог 5%" in cards[-1] and "121 ккал" in cards[-1]
    assert repo.list_products(db, uid) == []                       # само не сохраняется
    pending = repo.latest_pending_input(db, uid, "product_draft")
    assert pending and json.loads(pending["payload_json"])["draft"]["kcal"] == 121
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0   # это не еда

    await _press(dp, bot, f"prod_save:{pending['id']}")
    rows = repo.list_products(db, uid)
    assert [(r["name_ru"], r["brand"], r["kcal_100g"], r["unit_name"]) for r in rows] == \
        [("Творог 5%", "Савушкин", 121, "упаковка")]
    assert repo.latest_pending_input(db, uid, "product_draft") is None
    assert any("Сохранил" in t for t in session.texts)


@pytest.mark.asyncio
async def test_label_photo_fix_by_text_then_save(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    data = {**LABEL, "per_100g": {"kcal": 121, "protein": 17, "fat": None, "carbs": 1.8}}
    _label_llm(monkeypatch, data)
    await _feed(dp, bot, _photo(bot, "lab2", caption="Состав"))
    pending = repo.latest_pending_input(db, uid, "product_draft")
    assert "Не хватает: Ж" in _cards(session)[-1]
    kb_cards = [m for m in session.methods if m == "EditMessageText"]
    assert kb_cards                                                 # карточка показана
    assert json.loads(pending["payload_json"])["draft"]["fat"] is None

    await _press(dp, bot, f"prod_save:{pending['id']}")             # без Ж сохранить нельзя
    assert repo.list_products(db, uid) == []

    await _press(dp, bot, f"prod_edit:{pending['id']}")
    await send(dp, bot, "жиры 5")                                    # правка, а не еда
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0
    payload = json.loads(repo.pending_input(db, pending["id"], uid)["payload_json"])
    assert payload["draft"]["fat"] == 5 and payload["draft"]["protein"] == 17
    await send(dp, bot, "название: Творог Савушкин")
    await _press(dp, bot, f"prod_save:{pending['id']}")
    rows = repo.list_products(db, uid)
    assert [(r["name_ru"], r["fat_100g"]) for r in rows] == [("Творог Савушкин", 5)]


@pytest.mark.asyncio
async def test_label_photo_cancel(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    _label_llm(monkeypatch, LABEL)
    await _feed(dp, bot, _photo(bot, "lab3", caption="этикетка"))
    pending = repo.latest_pending_input(db, uid, "product_draft")
    await _press(dp, bot, f"prod_cancel:{pending['id']}")
    assert repo.list_products(db, uid) == []
    assert repo.latest_pending_input(db, uid, "product_draft") is None
    await _press(dp, bot, f"prod_save:{pending['id']}")             # старая кнопка — «неактуально»
    assert repo.list_products(db, uid) == []


@pytest.mark.asyncio
async def test_label_without_table_says_so(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    _label_llm(monkeypatch, {"name": None, "per_100g": {}, "confidence": 0})
    await _feed(dp, bot, _photo(bot, "lab4", caption="этикетка"))
    assert any("Не вижу таблицу" in t for t in session.texts)
    assert repo.latest_pending_input(db, uid, "product_draft") is None


@pytest.mark.asyncio
async def test_label_llm_failure_is_graceful(db):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await _feed(dp, bot, _photo(bot, "lab5", caption="этикетка"))   # chat_json «офлайн»
    assert any("Не смог прочитать этикетку" in t for t in session.texts)


# ------------------------------------------------------------------ чат: текст

@pytest.mark.asyncio
async def test_add_product_by_text_saves_and_is_used_in_meals(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "добавь продукт: творог 5% Савушкин, 121 ккал, Б 17, Ж 5, У 1.8")
    rows = repo.list_products(db, uid)
    assert [(r["name_ru"], r["kcal_100g"]) for r in rows] == [("творог 5% Савушкин", 121)]
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0
    assert any("Сохранил" in t for t in session.texts)

    async def fake_parse(text, user_id, conn):  # noqa: ANN001
        return _food("творог", 150)

    async def no_llm(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("свой продукт должен считаться без LLM")

    monkeypatch.setattr(foodsvc, "parse_text", fake_parse)
    monkeypatch.setattr(foodsvc, "_llm_food_ref", no_llm)
    await send(dp, bot, "творог 150 г")
    meal = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchone()
    assert meal["kcal_total"] == pytest.approx(121 * 1.5, abs=3)    # 181, а не справочные 145


@pytest.mark.asyncio
async def test_add_product_by_text_incomplete_opens_card(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "добавь продукт: творог, Б 17, Ж 5")        # нет У и ккал
    assert repo.list_products(db, uid) == []
    pending = repo.latest_pending_input(db, uid, "product_draft")
    assert pending and json.loads(pending["payload_json"])["editing"] is True
    assert "Не хватает" in _cards(session)[-1]
    await send(dp, bot, "углеводы 3")
    await _press(dp, bot, f"prod_save:{pending['id']}")
    row = repo.list_products(db, uid)[0]
    assert (row["name_ru"], row["carbs_100g"], row["kcal_100g"]) == ("творог", 3, 125)   # 17*4+5*9+3*4


@pytest.mark.asyncio
async def test_add_product_text_without_numbers_shows_help(db):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await send(dp, bot, "добавь продукт: просто красивая штука")
    assert any("Не нашёл чисел" in t for t in session.texts)


# ------------------------------------------------------------------ из записи и по штрихкоду

@pytest.mark.asyncio
async def test_item_save_button_creates_product(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)

    async def fake_parse(text, user_id, conn):  # noqa: ANN001
        return _food("салат", 200)

    monkeypatch.setattr(foodsvc, "parse_text", fake_parse)
    await send(dp, bot, "салат 200 г")
    item = db.execute("SELECT * FROM meal_items").fetchone()
    from app.routers import intake
    await _press(dp, bot, f"item_edit:{item['id']}")
    await _press(dp, bot, f"item_save:{item['id']}")
    row = repo.list_products(db, uid)[0]
    assert row["default_grams"] == 200 and row["name_ru"] == "салат"
    assert row["kcal_100g"] == pytest.approx(item["kcal_100g"], abs=1)
    assert any("Сохранил" in t for t in session.texts)
    kb = intake._meal_kb(1)                                            # карточка не сломана
    assert kb.inline_keyboard


@pytest.mark.asyncio
async def test_item_save_rejects_foreign_item(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot, uid=1)
    other_uid = await onboard(dp, bot, uid=2)

    async def fake_parse(text, user_id, conn):  # noqa: ANN001
        return _food("салат", 200)

    monkeypatch.setattr(foodsvc, "parse_text", fake_parse)
    await send(dp, bot, "салат 200 г", uid=2)
    item = db.execute("SELECT * FROM meal_items").fetchone()
    await _press(dp, bot, f"item_save:{item['id']}", uid=1)            # чужая позиция
    assert repo.list_products(db, 1) == [] and repo.list_products(db, other_uid) == []


@pytest.mark.asyncio
async def test_barcode_save_button_copies_off_product(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    fid = repo.save_off_food(db, "4600605123453", "Кефир (Простоквашино)", "Kefir",
                             52, 3.0, 2.5, 4.0)
    await _press(dp, bot, f"bc_save:{fid}")
    row = repo.list_products(db, uid)[0]
    assert (row["name_ru"], row["brand"], row["kcal_100g"], row["barcode"]) == \
        ("Кефир", "Простоквашино", 52, "4600605123453")
    assert row["id"] != fid and row["owner_user_id"] == uid
    await _press(dp, bot, f"bc_save:{fid}")                            # повтор не плодит копии
    assert len(repo.list_products(db, uid)) == 1
    # свой продукт по штрихкоду важнее общего
    assert repo.food_by_barcode(db, "4600605123453", uid)["id"] == row["id"]
    assert repo.food_by_barcode(db, "4600605123453")["id"] == fid


# ------------------------------------------------------------------ /products

@pytest.mark.asyncio
async def test_products_command_lists_and_deletes(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    await send(dp, bot, "/products")
    assert any("Своих продуктов пока нет" in t for t in session.texts)
    a = _mk(db, uid)
    b = _mk(db, uid, name="Йогурт", brand=None, kcal=66, protein=9, fat=2, carbs=3.5)
    session.texts.clear()
    await send(dp, bot, "/products")
    text = session.texts[-1]
    assert "Творог 5%" in text and "Йогурт" in text and "121 ккал" in text
    await send(dp, bot, "мои продукты")                                 # словами: список в приложении
    assert "риложени" in session.texts[-1]
    await _press(dp, bot, f"prod_del:{a}")
    assert [r["id"] for r in repo.list_products(db, uid)] == [b]
    await _press(dp, bot, f"prod_del:{a}")                              # повтор безопасен
    assert [r["id"] for r in repo.list_products(db, uid)] == [b]


@pytest.mark.asyncio
async def test_products_in_menu_commands_and_help(db):
    from app.services import assistant
    assert "/app" in assistant.HELP_TEXT


@pytest.mark.asyncio
async def test_text_intent_beats_late_caption_and_refine(db, monkeypatch):
    """«добавь продукт ...» сразу после фото еды не должна стать подписью к ней."""
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("салат", 200)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "food1"))
    meals_before = db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"]
    await send(dp, bot, "добавь продукт: творог, 121 ккал, Б 17, Ж 5, У 1.8")
    assert len(repo.list_products(db, uid)) == 1
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == meals_before
