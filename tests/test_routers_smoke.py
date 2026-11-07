"""Офлайн-прогон апдейтов через настоящий Dispatcher.

Ловит то, что не видят юнит-тесты домена: поломки проводки aiogram (фильтры,
сигнатуры хендлеров, FSM, callback_data, инъекция db). Сеть не нужна: Bot
работает с FakeSession, LLM подменяется.
"""
from __future__ import annotations

import datetime as dt
import io
import itertools
import sqlite3

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, Chat, File, Message, PhotoSize, Update,
                           User)

from app.db import connect, migrate
from app.middleware import AccessMiddleware
from app.routers import commands, intake, onboarding
from app.services import food as foodsvc
from app.storage import repo

pytestmark = pytest.mark.asyncio

TOKEN = "42:TEST-TOKEN"
_ids = itertools.count(1)


class FakeSession(BaseSession):
    """Отвечает на методы Bot API, ничего не отправляя в сеть."""

    def __init__(self) -> None:
        super().__init__()
        self.methods: list[str] = []
        self.texts: list[str] = []
        self.rich: list[str] = []

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        name = type(method).__name__
        self.methods.append(name)
        if getattr(method, "text", None):
            self.texts.append(method.text)
        rich = getattr(method, "rich_message", None)
        if rich is not None:
            self.texts.append(rich.html or "")
            self.rich.append(rich.html or "")
        if name in {"SendMessage", "EditMessageText", "EditMessageReplyMarkup",
                    "SendDocument", "SendPhoto", "SendChatAction", "SendRichMessage"}:
            return Message(
                message_id=len(self.methods),
                date=dt.datetime.now(tz=dt.timezone.utc),
                chat=Chat(id=1, type="private"),
                text="ok",
            ).as_(bot)
        if name == "GetFile":
            return File(file_id="f", file_unique_id="u", file_path="photos/x.jpg").as_(bot)
        if name == "DownloadFile":
            return io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg")
        return True

    async def stream_content(self, *args, **kwargs):  # noqa: ANN002, ANN003
        yield b""

    async def close(self) -> None:
        return None

    def names(self) -> list[str]:
        return self.methods


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


def build(db: sqlite3.Connection):
    from app.bot import create_dispatcher

    session = FakeSession()
    bot = Bot(TOKEN, session=session,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = create_dispatcher(db)
    return bot, dp, session


def message(text: str | None = None, uid: int = 1, **extra) -> Message:
    return Message(
        message_id=next(_ids),
        date=dt.datetime.now(tz=dt.timezone.utc),
        chat=Chat(id=uid, type="private"),
        from_user=User(id=uid, is_bot=False, first_name="Тест"),
        text=text,
        **extra,
    )


async def send(dp: Dispatcher, bot: Bot, text: str, uid: int = 1) -> None:
    msg = message(text, uid).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), message=msg))


async def onboard(dp: Dispatcher, bot: Bot, uid: int = 1) -> int:
    for answer in ("/start", "м", "35", "178", "92.4", "ок", "ок", "2", "ок", "да"):
        await send(dp, bot, answer, uid)
    user = repo.get_user_by_tg(dp["db"], uid)
    assert user and user["onboarded"] == 1, "онбординг не сохранил профиль"
    return user["id"]


async def test_onboarding_creates_profile_target_and_reminders(db):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)

    user = repo.get_user_by_tg(db, 1)
    assert user["height_cm"] == 178.0 and user["sex"] == "male"
    assert user["goal_weight_kg"] and user["goal_weight_kg"] < 92.4

    target = repo.current_target(db, uid)
    assert target and 1500 <= target["kcal_target"] <= 4000, target["kcal_target"]
    assert target["protein_g"] > 0 and target["carbs_g"] >= 50

    reminders = db.execute("SELECT kind, local_time FROM reminders WHERE user_id=?",
                           (uid,)).fetchall()
    kinds = {r["kind"] for r in reminders}
    assert kinds == {"morning", "midday", "kitchen_close", "evening", "weekly", "water", "weigh"}
    close = [r["local_time"] for r in reminders if r["kind"] == "kitchen_close"][0]
    assert close == "20:45", close

    assert "SendMessage" in session.names()


async def test_commands_answer_on_empty_day(db):
    bot, dp, _ = build(db)
    await onboard(dp, bot)
    for cmd in ("/today", "/week", "/weight", "/sleep", "/target", "/settings",
                "/export", "/sync_scale"):
        await send(dp, bot, cmd)          # ни одна не должна упасть
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0


async def test_text_meal_deterministic_path_and_confirm(db, monkeypatch):
    """Текст → карточка → «Верно»: LLM не вызывается, если продукт есть в базе."""
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)

    async def fake_parse_text(text, user_id, conn):  # noqa: ANN001
        return foodsvc.understanding_from_json({
            "items": [{"name_ru": "гречка", "name_en": "buckwheat",
                       "amount": {"kind": "grams", "grams": 200,
                                  "grams_range": [190, 210]},
                       "preparation": "boiled", "confidence": 0.9}],
            "meal_type_guess": "dinner", "overall_confidence": 0.9,
        })

    async def no_llm(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("LLM-фолбэк не должен вызываться для продукта из базы")

    monkeypatch.setattr(foodsvc, "parse_text", fake_parse_text)
    monkeypatch.setattr(foodsvc, "_llm_food_ref", no_llm)

    await send(dp, bot, "гречка 200 г")

    meal = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchone()
    assert meal is not None, "приём пищи не сохранён"
    assert meal["source"] == "text" and meal["method"] == "db"
    assert 180 <= meal["kcal_total"] <= 260, meal["kcal_total"]
    assert meal["kcal_low"] <= meal["kcal_total"] <= meal["kcal_high"]
    assert meal["local_time"] and meal["local_date"]

    item = db.execute("SELECT * FROM meal_items WHERE meal_id=?", (meal["id"],)).fetchone()
    assert item["snapshot_json"], "нет снимка КБЖУ на момент записи"

    # подтверждение кнопкой не падает и помечает приём
    cb = CallbackQuery(
        id="cb1", from_user=User(id=1, is_bot=False, first_name="Тест"),
        chat_instance="ci", data=f"meal_ok:{meal['id']}",
        message=message("карточка").as_(bot),
    )
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))
    assert db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"] == 1


async def test_undo_removes_day_total(db):
    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)

    from app.domain.nutrition import FoodRef, IngredientEstimate, calculate_meal
    ref = FoodRef(name_ru="гречка", name_en="buckwheat", kcal_100g=110,
                  protein_100g=4.2, fat_100g=1.1, carbs_100g=21.3, source="seed")
    item = IngredientEstimate(name="buckwheat", grams_min=200, grams_max=200)
    calc = calculate_meal([(item, ref)])
    today = dt.date.today().isoformat()
    repo.save_meal(db, uid, "2026-10-28 18:00:00", today, "18:00", "dinner", "text", calc)

    assert repo.day_totals(db, uid, today)["kcal"] > 0
    await send(dp, bot, "/undo")
    assert repo.day_totals(db, uid, today)["kcal"] == 0


async def test_stranger_gets_no_data(db):
    """Whitelist: чужой tg_id получает одно вежливое сообщение с ID и не попадает в базу."""
    from app.config import Config, set_config, get_config
    from app import middleware
    middleware._notified.clear()
    old = get_config()
    set_config(Config(_env_file=None, telegram_bot_token="t", openrouter_api_key="k", allowed_tg_ids="1"))
    try:
        bot, dp, session = build(db)
        await send(dp, bot, "/start", uid=999)
        await send(dp, bot, "/start", uid=999)
        assert len(session.texts) == 1 and "999" in session.texts[0], session.texts
        assert repo.get_user_by_tg(db, 999) is None
    finally:
        set_config(old)


async def test_screenshot_aspect_heuristic():
    """Скриншот телефона vs портретное фото — по пропорции, не по подписи."""
    from app.routers.intake import _looks_like_phone_screenshot

    def ph(w, h):
        return PhotoSize(file_id="f", file_unique_id="u", width=w, height=h)

    assert _looks_like_phone_screenshot(ph(1179, 2556))      # iPhone 15/16
    assert _looks_like_phone_screenshot(ph(1284, 2778))      # iPhone 14 Pro Max
    assert not _looks_like_phone_screenshot(ph(3024, 4032))  # портретное фото 3:4
    assert not _looks_like_phone_screenshot(ph(1080, 1920))  # 9:16 фото на грани
    assert not _looks_like_phone_screenshot(None)


@pytest.mark.asyncio
async def test_tall_screenshot_without_caption_goes_to_screen_parser(db, monkeypatch):
    """Скрин Picooc без подписи идёт в парсер экрана, а не в распознавание еды."""
    from app.services import screens

    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)

    async def fake_parse_screen(image_b64, user_id, conn):  # noqa: ANN001
        return {"screen": "picooc_measure", "date": dt.date.today().isoformat(),
                "metrics": {"weight_kg": {"value": 92.4, "confidence": 0.99},
                            "body_fat_pct": {"value": 24.1, "confidence": 0.98}},
                "missing": ["muscle_mass_kg"], "overall_confidence": 0.96,
                "raw_text": ["92.4 кг", "24.1 %"]}

    async def no_food(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("скриншот весов не должен уходить в распознавание еды")

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse_screen)
    monkeypatch.setattr(foodsvc, "parse_photo", no_food)

    shot = message(None, photo=[PhotoSize(file_id="f1", file_unique_id="u1",
                                          width=1179, height=2556)]).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), message=shot))

    row = db.execute("SELECT * FROM body_measurements WHERE user_id=?", (uid,)).fetchone()
    assert row is not None, "метрики тела не записаны"
    assert row["weight_kg"] == 92.4 and row["source"] == "screenshot"
    assert row["body_fat_pct"] == 24.1
    assert row["muscle_mass_kg"] is None, "нет поля на экране → NULL"
    meals = db.execute("SELECT COUNT(*) c FROM meals WHERE user_id=?", (uid,)).fetchone()["c"]
    assert meals == 0, "скриншот не должен превратиться в приём пищи"


@pytest.mark.asyncio
async def test_wrong_year_asks_confirmation_then_writes_today(db, monkeypatch):
    """Если vision прочитал 2024 вместо 2026 — спрашиваем, а не пишем в прошлое."""
    from app.services import screens

    bot, dp, _ = build(db)
    uid = await onboard(dp, bot)

    async def fake_parse_screen(image_b64, user_id, conn):  # noqa: ANN001
        return {"screen": "picooc_measure", "date": "2024-10-28",
                "metrics": {"weight_kg": {"value": 111.0, "confidence": 0.99}},
                "missing": [], "overall_confidence": 0.95,
                "raw_text": ["111.0кг", "14:28 10/28/2024"]}

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse_screen)

    shot = message(None, photo=[PhotoSize(file_id="f3", file_unique_id="u3",
                                          width=1170, height=5593)]).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), message=shot))

    assert db.execute("SELECT COUNT(*) c FROM body_measurements").fetchone()["c"] == 0, \
        "замер с неверной датой не должен записываться молча"
    pending = db.execute("SELECT * FROM pending_inputs WHERE kind='screenshot_confirm'").fetchone()
    assert pending is not None, "должен быть запрос подтверждения"

    # подтверждаем кнопкой → пишется сегодняшним днём
    cb = CallbackQuery(
        id="cb2", from_user=User(id=1, is_bot=False, first_name="Тест"),
        chat_instance="ci", data=f"screen_yes:{pending['id']}",
        message=message("проверь").as_(bot),
    )
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))

    row = db.execute("SELECT * FROM body_measurements WHERE user_id=?", (uid,)).fetchone()
    assert row is not None and row["weight_kg"] == 111.0
    assert row["local_date"] == dt.date.today().isoformat(), row["local_date"]


@pytest.mark.asyncio
async def test_square_food_photo_not_treated_as_screenshot(db, monkeypatch):
    """Обычное фото 4:3 не должно уходить в парсер скриншотов."""
    from app.routers import intake
    bot, dp, _ = build(db)
    await onboard(dp, bot)

    async def no_screen(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("фото еды не должно уходить в парсер экрана")

    async def no_wait(*args, **kwargs):  # noqa: ANN002, ANN003
        return None                       # не ждём реальные 90 с в тесте

    from app.services import screens
    monkeypatch.setattr(screens, "parse_screenshot", no_screen)
    monkeypatch.setattr(intake, "_process_photo_after_window", no_wait)

    photo = message(None, photo=[PhotoSize(file_id="f2", file_unique_id="u2",
                                           width=3024, height=4032)]).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), message=photo))
    assert db.execute("SELECT COUNT(*) c FROM body_measurements").fetchone()["c"] == 0


async def _press(dp, bot, data: str, uid: int = 1) -> None:
    menu_msg = message("📋 Меню", uid, reply_markup=None).as_(bot)
    cb = CallbackQuery(id=str(next(_ids)), from_user=User(id=uid, is_bot=False, first_name="Тест"),
                       chat_instance="x", message=menu_msg, data=data).as_(bot)
    await dp.feed_update(bot, Update(update_id=next(_ids), callback_query=cb))


async def test_menu_points_to_app_and_old_buttons_still_answer(db):
    from app.routers import menu

    bot, dp, session = build(db)
    await onboard(dp, bot)
    await send(dp, bot, "/menu")
    await send(dp, bot, "/app")
    await send(dp, bot, menu.MENU_BUTTON)                 # старая кнопка под полем ввода
    assert any("риложени" in t for t in session.texts)
    # кнопки старых меню не падают и ведут в приложение
    for data in ("m:today", "m:week", "m:w:300", "m:craving", "m:more"):
        session.texts.clear()
        await _press(dp, bot, data)
        assert any("риложени" in t for t in session.texts), data
    assert db.execute("SELECT COUNT(*) c FROM water_log").fetchone()["c"] == 0   # вода из меню не пишется
