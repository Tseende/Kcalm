"""Фаза 2 v2: все фото приёма одним запросом, запасной путь по одному, кнопка
«➕ Фото к приёму», сервисные функции без aiogram."""
from __future__ import annotations

import asyncio
import base64

import pytest

from app.llm import client as llm
from app.routers import intake
from app.services import food as foodsvc
from app.services import meals as meals_svc
from app.storage import repo
from tests.test_photo_flow import _drain, _feed, _food, _photo, build  # noqa: F401
from tests.test_photo_flow import db  # noqa: F401  (фикстура)
from tests.test_photo_flow import _fast  # noqa: F401  (autouse)
from tests.test_routers_smoke import onboard, send
from tests.test_v2_dishes import _press

JSON_TWO = {
    "items": [
        {"name_ru": "суп", "name_en": "soup", "photo_index": 1,
         "amount": {"kind": "grams", "grams": 300}, "preparation": "boiled", "confidence": 0.9},
        {"name_ru": "хлеб", "name_en": "bread", "photo_index": 2,
         "amount": {"kind": "grams", "grams": 40}, "preparation": "baked", "confidence": 0.9},
    ],
    "overall_confidence": 0.85,
}


def _chat(calls, data=JSON_TWO):
    async def fake(purpose, messages, model=None, temperature=0.1, user_id=None, db=None,
                   max_tokens=1500):
        calls.append({"messages": messages, "model": model, "max_tokens": max_tokens})
        return data, "m"
    return fake


def _images(call):
    return [p for p in call["messages"][0]["content"] if p["type"] == "image_url"]


def _texts(call):
    return "\n".join(p.get("text", "") for p in call["messages"][0]["content"]
                     if p["type"] == "text")


# ------------------------------------------------------------------ parse_photos

@pytest.mark.asyncio
async def test_parse_photos_sends_one_request_with_all_images(monkeypatch, db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))
    u = await foodsvc.parse_photos(["QQ==", "Qg==", "Qw=="], "обед", 1, db)
    assert len(calls) == 1, "все фото одним запросом"
    assert len(_images(calls[0])) == 3
    text = _texts(calls[0])
    for needle in ("Фото 1", "Фото 2", "Фото 3", "photo_index", "Одну и ту же еду не считай дважды",
                   "одну и ту же тарелку с разных сторон", "обед"):
        assert needle in text, needle
    assert calls[0]["max_tokens"] >= 2500
    assert [i.name_ru for i in u.items] == ["суп", "хлеб"]


@pytest.mark.asyncio
async def test_parse_photos_caps_at_six_and_attaches_dish_photos_once(monkeypatch, db, tmp_path):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))
    photo = tmp_path / "d.jpg"
    photo.write_bytes(b"empty")
    uid = repo.create_user(db, 1)
    did = repo.create_dish(db, uid, "Белая", "plate", diameter_cm=26, photo_path=str(photo))
    dish = repo.get_dish(db, uid, did)
    await foodsvc.parse_photos(["QQ=="] * 8, None, uid, db, dishes=[dish, dish, None])
    parts = calls[0]["messages"][0]["content"]
    assert len(_images(calls[0])) == 6 + 1, "6 фото приёма и одно фото посуды (одна посуда на двух фото)"
    text = _texts(calls[0])
    assert "Посуда на фото 1: тарелка «Белая», Ø26 см" in text and "Посуда на фото 2" in text
    assert "Посуда на фото 3" not in text
    assert "Фото пустой посуды «Белая»" in text and "не еда" in text
    assert parts[0]["type"] == "text"


@pytest.mark.asyncio
async def test_parse_photos_dish_narrows_band_only_for_dish_photo(monkeypatch, db):
    data = {"items": [
        {"name_ru": "суп", "name_en": "soup", "photo_index": 1, "dish_fill": 0.6,
         "amount": {"kind": "portion", "portion_hint": "medium"}, "confidence": 0.8},
        {"name_ru": "салат", "name_en": "salad", "photo_index": 2, "dish_fill": 0.6,
         "amount": {"kind": "portion", "portion_hint": "medium"}, "confidence": 0.8}],
        "overall_confidence": 0.8}
    monkeypatch.setattr(llm, "chat_json", _chat([], data))
    dish = {"id": 1, "name": "Б", "kind": "plate", "diameter_cm": 26, "photo_path": None}
    u = await foodsvc.parse_photos(["QQ==", "Qg=="], None, 1, db, dishes=[dish, None])
    soup, salad = u.items
    assert (soup.grams_min, soup.grams_max) == pytest.approx((212.5, 287.5))
    assert (salad.grams_min, salad.grams_max) == pytest.approx((187.5, 312.5))


@pytest.mark.asyncio
async def test_parse_photos_escalates_on_low_confidence(monkeypatch, db):
    models: list = []

    async def fake(purpose, messages, model=None, **kw):
        models.append(model)
        return ({**JSON_TWO, "overall_confidence": 0.3 if len(models) == 1 else 0.9}), "m"

    monkeypatch.setattr(llm, "chat_json", fake)
    u = await foodsvc.parse_photos(["QQ==", "Qg=="], None, 1, db)
    assert len(models) == 2 and models[0] != models[1] and u.overall_confidence == 0.9


@pytest.mark.asyncio
async def test_parse_photos_invalid_answer_raises(monkeypatch, db):
    monkeypatch.setattr(llm, "chat_json", _chat([], {"oops": 1}))
    with pytest.raises(llm.LLMError):
        await foodsvc.parse_photos(["QQ==", "Qg=="], None, 1, db)


# ------------------------------------------------------------------ запасной путь

@pytest.mark.asyncio
async def test_parse_images_falls_back_to_per_image_on_failure(monkeypatch, db):
    per_image: list = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        per_image.append(b64)
        return _food("суп" if b64 == "QQ==" else "хлеб", 200)

    async def boom(*a, **k):
        raise llm.LLMError("упало")

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    monkeypatch.setattr(llm, "chat_json", boom)
    u = await meals_svc.parse_images(["QQ==", "Qg=="], "обед", 1, db)
    assert sorted(per_image) == ["QQ==", "Qg=="]
    assert sorted(i.name_ru for i in u.items) == ["суп", "хлеб"]


@pytest.mark.asyncio
async def test_parse_images_falls_back_on_invalid_json_shape(monkeypatch, db):
    per_image: list = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        per_image.append(b64)
        return _food("суп", 200)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    monkeypatch.setattr(llm, "chat_json", _chat([], {"items": "не список"}))
    await meals_svc.parse_images(["QQ==", "Qg=="], None, 1, db)
    assert len(per_image) == 2


@pytest.mark.asyncio
async def test_single_image_uses_parse_photo_and_never_parse_photos(monkeypatch, db):
    calls: list = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        calls.append(kw)
        return _food("суп", 200)

    async def no_multi(*a, **k):
        raise AssertionError("для одного фото parse_photos не нужен")

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    monkeypatch.setattr(foodsvc, "parse_photos", no_multi)
    await meals_svc.parse_images(["QQ=="], None, 1, db)
    dish = {"id": 1, "name": "Б", "kind": "plate", "diameter_cm": 26}
    await meals_svc.parse_images(["QQ=="], None, 1, db, dishes=dish)
    assert calls == [{}, {"dishes": dish}]


@pytest.mark.asyncio
async def test_parse_images_more_than_six_is_chunked(monkeypatch, db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))
    u = await meals_svc.parse_images(["QQ=="] * 8, None, 1, db)
    assert [len(_images(c)) for c in calls] == [6, 2]
    assert len(u.items) == 4


# ------------------------------------------------------------------ чат: альбом одним запросом

@pytest.mark.asyncio
async def test_album_goes_through_single_request(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))

    async def no_single(*a, **k):
        raise AssertionError("альбом должен уйти одним запросом, а не по фото")

    monkeypatch.setattr(foodsvc, "parse_photo", no_single)
    await asyncio.gather(_feed(dp, bot, _photo(bot, "p1", group="g1")),
                         _feed(dp, bot, _photo(bot, "p2", caption="обед", group="g1")))
    await _drain()
    assert len(calls) == 1 and len(_images(calls[0])) == 2 and "обед" in _texts(calls[0])
    meals = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchall()
    assert len(meals) == 1
    assert sorted(r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")) == ["суп", "хлеб"]
    assert len(repo.meal_photos(db, meals[0]["id"])) == 2


@pytest.mark.asyncio
async def test_album_of_same_plate_two_angles_counted_once(db, monkeypatch):
    bot, dp, _ = build(db)
    await onboard(dp, bot)
    once = {"items": [{"name_ru": "паста", "name_en": "pasta", "photo_index": 1,
                       "amount": {"kind": "grams", "grams": 250}, "confidence": 0.9}],
            "overall_confidence": 0.9}
    monkeypatch.setattr(llm, "chat_json", _chat([], once))
    await asyncio.gather(_feed(dp, bot, _photo(bot, "a1", group="g2")),
                         _feed(dp, bot, _photo(bot, "a2", group="g2")))
    await _drain()
    assert [r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")] == ["паста"]


# ------------------------------------------------------------------ чат: «➕ Фото к приёму»

def _per_photo(monkeypatch):
    seen: list = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        seen.append(caption)
        return _food("суп", 300)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    return seen


@pytest.mark.asyncio
async def test_add_photo_button_appends_and_rerecognizes_all(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    _per_photo(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))

    session.texts.clear()
    await _press(dp, bot, f"meal_addphoto:{meal['id']}")
    assert repo.latest_pending_input(db, uid, "meal_add_photo", max_age_min=10)
    assert any("ещё фото этого приёма" in t for t in session.texts)

    await _feed(dp, bot, _photo(bot, "p2"))
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE deleted_at IS NULL").fetchone()["c"] == 1
    photos = repo.meal_photos(db, meal["id"])
    assert len(photos) == 2 and photos[0]["position"] < photos[1]["position"]
    assert len(calls) == 1 and len(_images(calls[0])) == 2, "пересчёт по ВСЕМ фото одним запросом"
    names = sorted(r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items"))
    assert names == ["суп", "хлеб"], "позиции заменены"
    assert db.execute("SELECT kcal_total FROM meals").fetchone()[0] > 0
    assert repo.latest_pending_input(db, uid, "meal_add_photo", max_age_min=10) is None
    assert any("Добавил фото" in t for t in session.texts)

    # следующее фото без кнопки — новый приём, как раньше
    _per_photo(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p3"))
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE deleted_at IS NULL").fetchone()["c"] == 2
    assert len(repo.meal_photos(db, meal["id"])) == 2
    await _drain()


@pytest.mark.asyncio
async def test_add_photo_album_appends_both(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    _per_photo(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))
    await _press(dp, bot, f"meal_addphoto:{meal['id']}")
    await asyncio.gather(_feed(dp, bot, _photo(bot, "p2", group="g9")),
                         _feed(dp, bot, _photo(bot, "p3", group="g9")))
    await _drain()
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE deleted_at IS NULL").fetchone()["c"] == 1
    assert len(repo.meal_photos(db, meal["id"])) == 3
    assert len(_images(calls[0])) == 3
    assert repo.latest_pending_input(db, uid, "meal_add_photo", max_age_min=10) is None
    await _drain()


@pytest.mark.asyncio
async def test_add_photo_pending_expires_after_ten_minutes(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    _per_photo(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    await _press(dp, bot, f"meal_addphoto:{meal['id']}")
    db.execute("UPDATE pending_inputs SET created_at=datetime('now','-11 minutes') "
               "WHERE kind='meal_add_photo'")
    db.commit()
    await _feed(dp, bot, _photo(bot, "p2"))
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE deleted_at IS NULL").fetchone()["c"] == 2
    await _drain()


@pytest.mark.asyncio
async def test_add_photo_failure_leaves_meal_untouched(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    _per_photo(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    await _press(dp, bot, f"meal_addphoto:{meal['id']}")

    async def boom(*a, **k):
        raise llm.LLMError("нет сети")

    monkeypatch.setattr(llm, "chat_json", boom)
    monkeypatch.setattr(foodsvc, "parse_photo", boom)
    await _feed(dp, bot, _photo(bot, "p2"))
    assert len(repo.meal_photos(db, meal["id"])) == 1, "фото не добавляется без пересчёта"
    assert [r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")] == ["суп"]
    assert any("Не смог пересчитать" in t for t in session.texts)
    # повтор того же файла не режется как дубликат
    _per_photo(monkeypatch)
    monkeypatch.setattr(llm, "chat_json", _chat([]))
    await _press(dp, bot, f"meal_addphoto:{meal['id']}")
    await _feed(dp, bot, _photo(bot, "p2"))
    assert len(repo.meal_photos(db, meal["id"])) == 2
    await _drain()


@pytest.mark.asyncio
async def test_add_photo_to_undone_meal_is_rejected(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    _per_photo(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    await _press(dp, bot, f"meal_addphoto:{meal['id']}")
    repo.soft_delete_meal(db, uid, meal["id"])
    await _feed(dp, bot, _photo(bot, "p2"))
    assert any("уже отменена" in t for t in session.texts)
    assert len(repo.meal_photos(db, meal["id"])) == 1
    await _drain()


@pytest.mark.asyncio
async def test_late_caption_uses_all_meal_photos(db, monkeypatch):
    """Подпись после добавленного фото пересчитывает приём по всем его фото."""
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    _per_photo(monkeypatch)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))
    await _press(dp, bot, f"meal_addphoto:{meal['id']}")
    await _feed(dp, bot, _photo(bot, "p2"))
    assert repo.fresh_photo_session(db, uid) is not None
    await send(dp, bot, "это был обед")
    assert len(calls) == 2 and len(_images(calls[1])) == 2 and "это был обед" in _texts(calls[1])
    await _drain()


# ------------------------------------------------------------------ сервис без aiogram

def _user(db):
    uid = repo.create_user(db, 5)
    db.execute("UPDATE users SET tz='Europe/Moscow', onboarded=1 WHERE id=?", (uid,))
    db.commit()
    return repo.get_user_by_tg(db, 5)


def _files(tmp_path, n):
    paths = []
    for i in range(n):
        p = tmp_path / f"s{i}.jpg"
        p.write_bytes(b"jpeg-%d" % i)
        paths.append(str(p))
    return paths


@pytest.mark.asyncio
async def test_recognize_and_save_service(db, monkeypatch, tmp_path):
    user = _user(db)
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls))
    dish = repo.create_dish(db, user["id"], "Белая", "plate", diameter_cm=26)
    paths = _files(tmp_path, 2)
    import datetime as dt
    meal_id = await meals_svc.recognize_and_save(db, user, paths, [dish, None], "обед",
                                                 local_dt=dt.datetime(2026, 5, 19, 18, 45))
    meal = repo.get_meal(db, user["id"], meal_id)
    assert meal["source"] == "combo" and meal["photo_path"] == paths[0]
    assert (meal["local_date"], meal["local_time"]) == ("2026-05-20", "13:00")      # «обед» в подписи главнее
    photos = repo.meal_photos(db, meal_id)
    assert [p["path"] for p in photos] == paths and [p["dish_id"] for p in photos] == [dish, None]
    assert len(calls) == 1 and "Посуда на фото 1" in _texts(calls[0])
    assert len(repo.get_meal_items(db, meal_id)) == 2

    # dish_ids=None → посуда по умолчанию
    repo.update_dish(db, user["id"], dish, is_default=True)
    mid2 = await meals_svc.recognize_and_save(db, user, _files(tmp_path, 1)[:1], None, None,
                                              local_dt=dt.datetime(2026, 5, 19, 18, 45))
    assert [p["dish_id"] for p in repo.meal_photos(db, mid2)] == [dish]
    assert repo.get_meal(db, user["id"], mid2)["local_time"] == "18:45"


@pytest.mark.asyncio
async def test_recognize_and_save_errors(db, monkeypatch, tmp_path):
    user = _user(db)
    with pytest.raises(meals_svc.NoPhotos):
        await meals_svc.recognize_and_save(db, user, [str(tmp_path / "нет.jpg")], [None], None)
    monkeypatch.setattr(llm, "chat_json", _chat([], {"items": [], "overall_confidence": 0.9}))

    async def empty(*a, **k):
        return foodsvc.understanding_from_json({"items": [], "overall_confidence": 0.9})

    monkeypatch.setattr(foodsvc, "parse_photo", empty)
    with pytest.raises(meals_svc.NoFood):
        await meals_svc.recognize_and_save(db, user, _files(tmp_path, 1), [None], None)
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0


@pytest.mark.asyncio
async def test_rerecognize_meal_service(db, monkeypatch, tmp_path):
    user = _user(db)
    paths = _files(tmp_path, 2)
    monkeypatch.setattr(llm, "chat_json", _chat([]))
    meal_id = await meals_svc.recognize_and_save(db, user, paths[:1], [None], "завтрак")
    # новые фото и посуда, комментарий по умолчанию прежний
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _chat(calls, {**JSON_TWO}))
    dish = repo.create_dish(db, user["id"], "Миска", "bowl", diameter_cm=18)
    u = await meals_svc.rerecognize_meal(db, user, meal_id, extra_photos=[paths[1]], dish_id=dish)
    assert len(_images(calls[0])) == 2 and "завтрак" in _texts(calls[0])
    assert [p["dish_id"] for p in repo.meal_photos(db, meal_id)] == [dish, dish]
    assert len(u.items) == 2 and len(repo.get_meal_items(db, meal_id)) == 2
    assert repo.get_meal(db, user["id"], meal_id)["source"] == "combo"     # прежний источник сохранён

    with pytest.raises(LookupError):
        await meals_svc.rerecognize_meal(db, user, 9999)
    repo.soft_delete_meal(db, user["id"], meal_id)
    with pytest.raises(LookupError):
        await meals_svc.rerecognize_meal(db, user, meal_id)


@pytest.mark.asyncio
async def test_rerecognize_failure_changes_nothing(db, monkeypatch, tmp_path):
    user = _user(db)
    paths = _files(tmp_path, 2)
    monkeypatch.setattr(llm, "chat_json", _chat([]))
    meal_id = await meals_svc.recognize_and_save(db, user, paths[:1], [None], None)
    before = [r["name_ru"] for r in repo.get_meal_items(db, meal_id)]

    async def boom(*a, **k):
        raise llm.LLMError("нет сети")

    monkeypatch.setattr(llm, "chat_json", boom)
    monkeypatch.setattr(foodsvc, "parse_photo", boom)
    with pytest.raises(Exception):
        await meals_svc.rerecognize_meal(db, user, meal_id, extra_photos=[paths[1]])
    assert [r["name_ru"] for r in repo.get_meal_items(db, meal_id)] == before
    assert len(repo.meal_photos(db, meal_id)) == 1


def test_merge_alias_kept_for_router():
    assert intake._merge is meals_svc.merge_understandings
