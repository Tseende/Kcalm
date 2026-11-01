"""Приём фото: время приёма пищи, альбомы, подпись после фото, «Другое время», EXIF.

Сети нет: Bot на FakeSession (разные байты для разных file_id), vision и справочник LLM
подменены.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import os
import struct
import tempfile

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Document, PhotoSize, Update, User

from app.db import connect, migrate
from app.domain import mealtime
from app.domain.mealtime import MealTime, parse_mealtime
from app.routers import intake
from app.services import food as foodsvc
from app.storage import repo
from tests.test_routers_smoke import FakeSession, message, onboard, send

pytestmark = pytest.mark.asyncio

_ids = itertools.count(10_000)
NOW = dt.datetime(2026, 5, 20, 14, 30)        # среда, 14:30


# ------------------------------------------------------------------ парсер времени

@pytest.mark.parametrize("text,expected", [
    ("борщ в обед", MealTime("2026-05-20", "13:00", "lunch")),
    ("на завтрак овсянка", MealTime("2026-05-20", "08:00", "breakfast")),
    ("утром выпил кофе", MealTime("2026-05-20", "08:00", "breakfast")),
    ("на перекус яблоко", MealTime("2026-05-20", "14:30", "snack")),     # 16:00 в будущем → сейчас
    ("вчера вечером пицца", MealTime("2026-05-19", "20:00", "dinner")),
    ("вчера на ужин пельмени", MealTime("2026-05-19", "19:00", "dinner")),
    ("позавчера обед в кафе", MealTime("2026-05-18", "13:00", "lunch")),
    ("съел салат в 13:30", MealTime("2026-05-20", "13:30")),
    ("в 9.15 каша", MealTime("2026-05-20", "09:15")),
    ("в 8 утра йогурт", MealTime("2026-05-20", "08:00")),
    ("в 7 вечера ужин", MealTime("2026-05-19", "19:00", "dinner")),     # 19:00 ещё не наступило
    ("в 14 часов суп", MealTime("2026-05-20", "14:00")),
    ("час назад съел бутерброд", MealTime("2026-05-20", "13:30")),
    ("полчаса назад чай с печеньем", MealTime("2026-05-20", "14:00")),
    ("два часа назад", MealTime("2026-05-20", "12:30")),
    ("40 минут назад", MealTime("2026-05-20", "13:50")),
    ("вчера в 22:15 чипсы", MealTime("2026-05-19", "22:15")),
    ("в 23:00 печенье", MealTime("2026-05-19", "23:00")),               # будущее без дня → вчера
])
async def test_parse_mealtime(text, expected):
    assert parse_mealtime(text, NOW) == expected


@pytest.mark.parametrize("text", [
    None, "", "гречка 200 г", "борщ с говядиной", "вчерашний суп", "в 2 раза больше",
    "сегодня", "2 яйца и тост",
])
async def test_parse_mealtime_no_hint(text):
    assert parse_mealtime(text, NOW) is None


async def test_parse_mealtime_midnight_rollover():
    now = dt.datetime(2026, 5, 20, 0, 10)
    assert parse_mealtime("в 23:30 творог", now) == MealTime("2026-05-19", "23:30")
    assert parse_mealtime("час назад", now) == MealTime("2026-05-19", "23:10")


async def test_slot_buttons():
    today = dt.date(2026, 5, 20)
    assert mealtime.slot_time("d", today) == MealTime("2026-05-20", "19:00", "dinner")
    assert mealtime.slot_time("y", today) == MealTime("2026-05-19", "19:00", "dinner")
    assert mealtime.slot_time("zzz", today) is None


def _jpeg_with_exif(stamp: str) -> bytes:
    raw = stamp.encode() + b"\x00"
    # TIFF (little endian): IFD0 с указателем на Exif IFD, в нём DateTimeOriginal
    exif_ifd_off = 8 + 2 + 12 + 4
    value_off = exif_ifd_off + 2 + 12 + 4
    tiff = b"II*\x00" + struct.pack("<I", 8)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif_ifd_off) + b"\0\0\0\0"
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(raw), value_off) + b"\0\0\0\0"
    tiff += raw
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(body) + 2) + body + b"\xff\xda"


async def test_exif_datetime():
    assert mealtime.exif_datetime(_jpeg_with_exif("2026:05:19 18:45:10")) == \
        dt.datetime(2026, 5, 19, 18, 45, 10)
    assert mealtime.exif_datetime(b"\xff\xd8\xff\xe0fake") is None
    assert mealtime.exif_datetime(b"not a jpeg") is None
    assert mealtime.exif_datetime(b"") is None


# ------------------------------------------------------------------ конвейер

class PhotoSession(FakeSession):
    """Разные file_id дают разные байты, чтобы фото не считались дубликатами."""

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        if type(method).__name__ == "GetFile":
            from aiogram.types import File
            return File(file_id=method.file_id, file_unique_id="u",
                        file_path=f"photos/{method.file_id}.jpg").as_(bot)
        return await super().make_request(bot, method, timeout)

    async def stream_content(self, url, *args, **kwargs):  # noqa: ANN002, ANN003
        yield self.payloads.get(str(url).rsplit("/", 1)[-1].split(".")[0], str(url).encode())

    payloads: dict = {}


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def build(db):
    from app.bot import create_dispatcher
    session = PhotoSession()
    session.payloads = {}
    bot = Bot("42:TEST-TOKEN", session=session,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return bot, create_dispatcher(db), session


def _food(name_ru: str, grams: float) -> foodsvc.MealUnderstanding:
    return foodsvc.understanding_from_json({
        "items": [{"name_ru": name_ru, "name_en": name_ru,
                   "amount": {"kind": "grams", "grams": grams,
                              "grams_range": [grams * 0.9, grams * 1.1]},
                   "preparation": "boiled", "confidence": 0.9}],
        "overall_confidence": 0.9,
    })


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(intake, "ALBUM_WAIT_SEC", 0)
    from zoneinfo import ZoneInfo
    # фиксированное «сейчас» (14:30), чтобы «обед 13:00» не зависел от часов прогона
    monkeypatch.setattr(foodsvc, "local_now", lambda tz: NOW.replace(tzinfo=ZoneInfo(tz)))

    async def fake_ref(item, user_id, conn):  # noqa: ANN001
        from app.domain.nutrition import FoodRef
        return FoodRef(name_ru=item.name_ru, name_en=item.name, kcal_100g=100,
                       protein_100g=5, fat_100g=3, carbs_100g=12, source="llm",
                       confidence=0.5)

    monkeypatch.setattr(foodsvc, "_llm_food_ref", fake_ref)


def _photo(bot, file_id: str, caption: str | None = None, group: str | None = None,
           uid: int = 1):
    return message(None, uid, caption=caption, media_group_id=group,
                   photo=[PhotoSize(file_id=file_id, file_unique_id=file_id,
                                    width=3024, height=4032)]).as_(bot)


async def _feed(dp, bot, msg) -> None:
    await dp.feed_update(bot, Update(update_id=next(_ids), message=msg))


async def _drain() -> None:
    """Дождаться сборки альбомов; таймеры окна подписи (90 с) снять."""
    for task in list(intake._BG_TASKS):
        if task.get_coro().__name__ == "_process_photo_after_window":
            task.cancel()
    await asyncio.gather(*list(intake._BG_TASKS), return_exceptions=True)


async def test_photo_recognized_immediately_without_waiting(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)
    calls = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        calls.append(caption)
        return _food("салат", 150)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1"))

    assert calls == [None], "фото без подписи распознаётся сразу"
    meals = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchall()
    assert len(meals) == 1 and meals[0]["source"] == "photo"
    session = repo.fresh_photo_session(db, uid)
    assert session and session["meal_id"] == meals[0]["id"]
    await _drain()


async def test_late_caption_edits_same_meal(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    calls = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        calls.append(caption)
        return _food("борщ", 300) if caption else _food("салат", 150)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1"))
    first = db.execute("SELECT * FROM meals").fetchone()
    assert db.execute("SELECT name_ru FROM meal_items").fetchone()["name_ru"] == "салат"
    edits_before = session.methods.count("EditMessageText")

    await send(dp, bot, "это был борщ на обед")

    meals = db.execute("SELECT * FROM meals WHERE deleted_at IS NULL").fetchall()
    assert len(meals) == 1 and meals[0]["id"] == first["id"], "вторая запись создана"
    names = [r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items")]
    assert names == ["борщ"], names
    assert meals[0]["source"] == "combo" and meals[0]["local_time"] == "13:00"
    assert meals[0]["meal_type"] == "lunch"
    assert calls == [None, "это был борщ на обед"]
    assert session.methods.count("EditMessageText") > edits_before, "карточка не отредактирована"
    assert repo.fresh_photo_session(db, uid) is None
    await _drain()


async def test_question_after_photo_is_not_caption(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        assert caption is None, "вопрос не должен стать подписью"
        return _food("салат", 150)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1"))
    assert intake._looks_like_caption("с сыром") is True
    assert intake._looks_like_caption("сколько мне осталось?") is False
    assert intake._looks_like_caption("выпил 300 мл воды") is False
    await _drain()


async def test_album_becomes_single_meal(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    seen = []

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        import base64
        seen.append((base64.b64decode(b64), caption))
        name = "суп" if b"p1" in base64.b64decode(b64) else "хлеб"
        return _food(name, 200 if name == "суп" else 40)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await asyncio.gather(
        _feed(dp, bot, _photo(bot, "p1", group="g1")),
        _feed(dp, bot, _photo(bot, "p2", caption="обед в столовой", group="g1")),
    )
    await _drain()

    meals = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchall()
    assert len(meals) == 1, "альбом должен стать одним приёмом пищи"
    names = sorted(r["name_ru"] for r in db.execute("SELECT name_ru FROM meal_items"))
    assert names == ["суп", "хлеб"]
    assert len(seen) == 2 and all(c == "обед в столовой" for _, c in seen)
    assert meals[0]["source"] == "combo" and meals[0]["local_time"] == "13:00"
    assert not intake._ALBUMS
    photos = repo.meal_photos(db, meals[0]["id"])
    assert len(photos) == 2 and meals[0]["photo_path"] == photos[0]["path"]
    # одна карточка: один статус и одна правка
    assert session.methods.count("SendMessage") + session.methods.count("SendRichMessage") >= 1


async def test_album_merge_caps_and_confidence():
    a, b = _food("суп", 200), _food("хлеб", 40)
    b.overall_confidence = 0.4
    merged = intake._merge([a, b])
    assert [i.name_ru for i in merged.items] == ["суп", "хлеб"]
    assert merged.overall_confidence == 0.4
    assert [i.name_ru for i in a.items] == ["суп"], "исходное не мутируется"


async def test_caption_time_is_saved_for_photo(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("пицца", 250)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1", caption="вчера вечером"))
    meal = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchone()
    user = repo.get_user_by_tg(db, 1)
    yesterday = (foodsvc.local_now(user["tz"]).date() - dt.timedelta(days=1)).isoformat()
    assert meal["local_date"] == yesterday and meal["local_time"] == "20:00"
    assert meal["meal_type"] == "dinner"
    await _drain()


async def test_other_time_button_moves_meal(db, monkeypatch):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("гречка", 200)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1"))
    meal = db.execute("SELECT * FROM meals").fetchone()

    async def press(data):
        cb = CallbackQuery(id=str(next(_ids)), from_user=User(id=1, is_bot=False, first_name="Т"),
                           chat_instance="x", message=message("карточка").as_(bot),
                           data=data).as_(bot)
        await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))

    kb = intake._meal_kb(meal["id"])
    assert any(b.callback_data == f"meal_time:{meal['id']}"
               for row in kb.inline_keyboard for b in row)
    await press(f"meal_time:{meal['id']}")
    await press(f"meal_at:{meal['id']}:y")
    moved = db.execute("SELECT * FROM meals WHERE id=?", (meal["id"],)).fetchone()
    user = repo.get_user_by_tg(db, 1)
    yesterday = (foodsvc.local_now(user["tz"]).date() - dt.timedelta(days=1)).isoformat()
    assert (moved["local_date"], moved["local_time"], moved["meal_type"]) == \
        (yesterday, "19:00", "dinner")
    assert repo.day_totals(db, uid, yesterday)["kcal"] == moved["kcal_total"] > 0
    await press(f"meal_at:{meal['id']}:b")
    assert db.execute("SELECT meal_type, local_time FROM meals").fetchone()[:] == ("breakfast", "08:00")
    await _drain()


async def test_image_document_uses_fresh_exif(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    user = repo.get_user_by_tg(db, 1)
    shot = dt.datetime.now() - dt.timedelta(days=1)
    session.payloads = {"d1": _jpeg_with_exif(shot.strftime("%Y:%m:%d 18:20:00"))}

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("рис", 180)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    doc = message(None, document=Document(file_id="d1", file_unique_id="d1",
                                          file_name="IMG.jpg", mime_type="image/jpeg")).as_(bot)
    await _feed(dp, bot, doc)
    meal = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchone()
    assert meal is not None, "картинка-файл не обработана"
    assert meal["local_date"] == shot.date().isoformat() and meal["local_time"] == "18:20"
    assert meal["meal_type"] == "dinner"
    await _drain()


async def test_old_exif_is_ignored():
    old = _jpeg_with_exif("2020:01:01 10:00:00")
    assert intake._fresh_exif(old) is None


async def test_food_logged_hook_message_sent(db, monkeypatch):
    from app.services import craving as craving_svc
    bot, dp, session = build(db)
    await onboard(dp, bot)
    got = []

    def note(conn, user, meal_text):  # noqa: ANN001
        got.append(meal_text)
        return "Записал, это хороший выбор."

    monkeypatch.setattr(craving_svc, "note_food_logged", note, raising=False)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("йогурт", 150)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    await _feed(dp, bot, _photo(bot, "p1"))
    assert got and "йогурт" in got[0] and "ккал" in got[0]
    assert "Записал, это хороший выбор." in session.texts
    await _drain()


async def test_typing_indicator_instead_of_status_chain(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)

    async def fake_photo(b64, caption, user_id, conn, **kw):  # noqa: ANN001
        return _food("йогурт", 150)

    monkeypatch.setattr(foodsvc, "parse_photo", fake_photo)
    session.texts.clear()
    session.methods.clear()
    await _feed(dp, bot, _photo(bot, "p1"))
    assert "SendChatAction" in session.methods
    for gone in ("📷 скачиваю…", "📷 распознаю…", "🔢 считаю…"):
        assert gone not in session.texts
    await _drain()
