"""Виджеты Bot API 10.4: данные карточки, перехват widget_query, запросы, отправка раз в день."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message

from app.config import get_config
from app.domain.nutrition import MealCalculation
from app.services import food as foodsvc
from app.storage import repo
from app.widgets import api, chatdata, day, pages, pause, runtime
from app.widgets.dispatcher import WidgetDispatcher
from tests.test_routers_smoke import TOKEN, FakeSession, build, db, onboard  # noqa: F401

pytestmark = pytest.mark.asyncio


class WSession(FakeSession):
    """FakeSession, который понимает методы виджетов и ведёт журнал вызовов."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.fail_send = False
        self._mid = 1000

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        if isinstance(method, (api.SendWidget, api.EditMessageWidgetData, api.AnswerWidgetQuery)):
            self.calls.append(method)
            self.methods.append(type(method).__name__)
            if isinstance(method, api.SendWidget):
                if self.fail_send:
                    raise TelegramBadRequest(method=method, message="Bad Request: method not found")
                self._mid += 1
                return Message(message_id=self._mid, date=dt.datetime.now(tz=dt.timezone.utc),
                               chat=Chat(id=method.chat_id, type="private"), text="w").as_(bot)
            return True
        if isinstance(method, SendMessage):
            self.calls.append(method)
        return await super().make_request(bot, method, timeout)

    def of(self, cls):
        return [c for c in self.calls if isinstance(c, cls)]


@pytest_asyncio.fixture
async def env(db, monkeypatch):  # noqa: F811
    session = WSession()
    bot = Bot(TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    from app.bot import create_dispatcher
    dp = create_dispatcher(db)
    monkeypatch.setattr(day, "DEBOUNCE_S", 0.01)
    monkeypatch.setattr(pause, "SEND_DELAY_S", 0)
    rt = runtime.setup(bot, db, get_config())
    rt.username = "TestBot"
    uid = await onboard(dp, bot)
    session.calls.clear()
    yield bot, dp, session, uid, rt
    runtime.teardown()


def eat(db, uid, kcal=400, t="09:00", mtype="breakfast", prot=20, fat=10, carbs=50):
    today = foodsvc.local_now(repo.get_user_by_id(db, uid)["tz"]).strftime("%Y-%m-%d")
    calc = MealCalculation(items=[], kcal_total=kcal, kcal_low=kcal, kcal_high=kcal,
                           protein_g=prot, fat_g=fat, carbs_g=carbs, method="db", confidence=0.9)
    return repo.save_meal(db, uid, f"{today} {t}:00", today, t, mtype, "text", calc)


def wq(data, uid=1, msg=1001, qid="q1", chat=None):
    return {"update_id": 5, "widget_query": {
        "id": qid, "from": {"id": uid, "is_bot": False, "first_name": "T"},
        "chat": {"id": chat if chat is not None else uid, "type": "private"},
        "message_id": msg, "chat_instance": "ci", "type": {"type": "data"}, "data": data}}


def answers(session):
    return [c.data for c in session.of(api.AnswerWidgetQuery)]


# ------------------------------------------------------------------ данные карточки

async def test_day_data_fits_and_matches_miniapp(env):
    bot, dp, session, uid, rt = env
    db = rt.db
    eat(db, uid, 420, "08:30", "breakfast", prot=30.55, fat=12.34, carbs=101.66)
    user = repo.get_user_by_id(db, uid)
    today = foodsvc.local_now(user["tz"]).strftime("%Y-%m-%d")
    raw = chatdata.day_data(db, user, today, "21:40")
    data = json.loads(raw)
    from app.web import queries as webq
    from app.web.serializers import targets_json
    totals, tg = webq.day_totals(db, uid, today), targets_json(db, user)
    assert data["u"] == [totals["carbs"], tg["carbs"]] and data["f"] == [totals["fat"], tg["fat"]]
    assert data["p"] == [totals["protein"], tg["protein"]]
    assert data["k"][0] == 420 and data["c"] == 0 and data["lw"] == 0 and data["t"] == "21:40"
    assert data["m"][0][1] == 420 and "Завтрак" in data["m"][0][0]
    assert len(raw.encode()) <= 1024


async def test_day_data_trims_meals_and_budget(env):
    bot, dp, session, uid, rt = env
    for i in range(15):
        eat(rt.db, uid, 100 + i, f"{8 + i // 2:02d}:{(i % 2) * 30:02d}", "snack")
    user = repo.get_user_by_id(rt.db, uid)
    today = foodsvc.local_now(user["tz"]).strftime("%Y-%m-%d")
    raw = chatdata.day_data(rt.db, user, today, "21:40")
    data = json.loads(raw)
    assert len(data["m"]) == chatdata.MAX_MEALS
    assert data["m"][-1][1] == 114                           # остались последние приёмы
    assert len(raw.encode()) <= 1024


async def test_care_mode_hides_kcal_keeps_macros(env):
    bot, dp, session, uid, rt = env
    repo.update_user(rt.db, uid, care_mode=1)
    eat(rt.db, uid, 777, "09:00", prot=25, carbs=40, fat=9)
    user = repo.get_user_by_id(rt.db, uid)
    today = foodsvc.local_now(user["tz"]).strftime("%Y-%m-%d")
    raw = chatdata.day_data(rt.db, user, today, "10:00")
    data = json.loads(raw)
    assert data["c"] == 1 and data["k"] == [0, 0] and "777" not in raw
    assert all(m[1] == 0 for m in data["m"])
    assert data["p"][0] == 25 and data["u"][0] == 40 and data["f"][0] == 9


async def test_lw_only_for_manual_water(env):
    bot, dp, session, uid, rt = env
    user = repo.get_user_by_id(rt.db, uid)
    today = foodsvc.local_now(user["tz"]).strftime("%Y-%m-%d")
    mid = eat(rt.db, uid, 50, "09:00", "snack")
    rt.db.execute("INSERT INTO water_log (user_id, logged_at_utc, local_date, local_time, ml, source, meal_id)"
                  " VALUES (?,?,?,?,?,?,?)", (uid, "2026-01-01 00:00:00", today, "09:00", 200, "text", mid))
    rt.db.commit()
    assert json.loads(chatdata.day_data(rt.db, user, today, "10:00"))["lw"] == 0
    repo.add_water(rt.db, uid, today, "10:00", 250, "command")
    assert json.loads(chatdata.day_data(rt.db, user, today, "10:00"))["lw"] == 1


def test_html_fits_telegram_limits_after_link_substitution():
    for name in ("day", "pause"):
        for link in ("https://t.me/SomeVeryLongBotUsername_bot/app", "https://t.me/KcalmBot", ""):
            html = pages.load(name, link)
            pages.check_limits(html)
    # карточка дня: нажимаются сами элементы (запросы к боту за водой), без <button>; «+» открывает Mini App
    day_html = pages.load("day", pages.app_link("MyBot", "https://x/app/"))
    assert "request(" in day_html and "w:+250" in day_html and "w:undo" in day_html
    assert "<button" not in day_html and "openTelegramLink" in day_html
    assert "https://t.me/MyBot/app" in day_html and pages.APP_PLACEHOLDER not in day_html
    assert pages.app_link("MyBot", "") == "https://t.me/MyBot"


# ---------------------------------------------------------------- перехват апдейта

async def test_allowed_updates_include_widget_query(env):
    bot, dp, *_ = env
    assert isinstance(dp, WidgetDispatcher)
    assert "widget_query" in dp.resolve_used_update_types() and "message" in dp.resolve_used_update_types()


async def test_raw_widget_query_reaches_handler(env):
    bot, dp, session, uid, rt = env
    eat(rt.db, uid)
    await day.flush(uid)
    mid = repo.widget_for_day(rt.db, uid, "day", foodsvc.local_now("Europe/Minsk").strftime("%Y-%m-%d"))["message_id"]
    session.calls.clear()
    await dp.feed_raw_update(bot, wq("w:+250", msg=mid))
    assert answers(session) == ["+250 мл ✓"]
    # webhook-путь тоже доходит
    await dp.feed_webhook_update(bot, wq("w:+500", msg=mid, qid="q2"))
    assert answers(session)[-1] == "+500 мл ✓"


# ------------------------------------------------------------------------ запросы

async def _card(env_):
    bot, dp, session, uid, rt = env_
    eat(rt.db, uid)
    assert await day.flush(uid) == "sent"
    user = repo.get_user_by_id(rt.db, uid)
    row = repo.widget_for_day(rt.db, uid, "day", foodsvc.local_now(user["tz"]).strftime("%Y-%m-%d"))
    session.calls.clear()
    return row


async def test_water_request_writes_edits_and_answers(env):
    bot, dp, session, uid, rt = env
    row = await _card(env)
    await dp.feed_raw_update(bot, wq("w:+250", msg=row["message_id"]))
    assert rt.db.execute("SELECT COUNT(*) c FROM water_log WHERE user_id=?", (uid,)).fetchone()["c"] == 1
    assert answers(session) == ["+250 мл ✓"]
    edits = session.of(api.EditMessageWidgetData)
    assert len(edits) == 1 and json.loads(edits[0].chat_data)["w"][0] == 250
    assert json.loads(edits[0].chat_data)["lw"] == 1
    await day.wait_idle()
    assert len(session.of(api.EditMessageWidgetData)) == 1       # отложенного дубля нет


async def test_undo_keeps_meal_water(env):
    bot, dp, session, uid, rt = env
    row = await _card(env)
    today = row["local_date"]
    rt.db.execute("INSERT INTO water_log (user_id, logged_at_utc, local_date, local_time, ml, source, meal_id)"
                  " VALUES (?,?,?,?,?,?,?)", (uid, "2026-01-01 00:00:00", today, "09:00", 200, "text",
                                              eat(rt.db, uid, 30, "09:10", "snack")))
    rt.db.commit()
    await dp.feed_raw_update(bot, wq("w:undo", msg=row["message_id"]))
    assert answers(session) == ["Нечего отменять"]
    assert repo.day_water_ml(rt.db, uid, today) >= 200
    await dp.feed_raw_update(bot, wq("w:+500", msg=row["message_id"], qid="a"))
    await dp.feed_raw_update(bot, wq("w:undo", msg=row["message_id"], qid="b"))
    assert answers(session)[-1] == "Отменено"
    assert repo.day_water_ml(rt.db, uid, today) >= 200
    assert repo.last_manual_water(rt.db, uid, today) is None


async def test_foreign_and_unknown_requests_rejected(env):
    bot, dp, session, uid, rt = env
    row = await _card(env)
    await dp.feed_raw_update(bot, wq("w:+250", uid=777, msg=row["message_id"], chat=777))
    await dp.feed_raw_update(bot, wq("w:+250", msg=424242, qid="x"))
    await dp.feed_raw_update(bot, wq("w:+9999", msg=row["message_id"], qid="y"))
    await dp.feed_raw_update(bot, wq("cr:ok", msg=row["message_id"], qid="z"))     # код чужого виджета
    assert len(answers(session)) == 4
    assert repo.day_water_ml(rt.db, uid, row["local_date"]) == 0
    assert not session.of(api.EditMessageWidgetData)


async def test_pause_ok_closes_episode(env):
    bot, dp, session, uid, rt = env
    user = repo.get_user_by_id(rt.db, uid)
    event_id = repo.create_craving(rt.db, uid, "2026-11-09", "22:00", "хочу", 0, 0)
    pause.on_episode_opened(user, event_id)
    for _ in range(50):
        await asyncio.sleep(0.01)
        row = repo.widget_for_ref(rt.db, "pause", event_id)
        if row:
            break
    assert row and row["kind"] == "pause"
    session.calls.clear()
    await dp.feed_raw_update(bot, wq("cr:ok", msg=row["message_id"]))
    assert answers(session) == ["Здорово, что дали себе паузу"]
    assert repo.get_craving(rt.db, uid, event_id)["outcome"] == "resisted"
    assert json.loads(session.of(api.EditMessageWidgetData)[0].chat_data)["st"] == "ok"
    session.calls.clear()
    await dp.feed_raw_update(bot, wq("cr:still", msg=row["message_id"], qid="again"))   # уже закрыт
    assert "закончен" in answers(session)[0]


async def test_pause_talk_and_still_send_chat_message(env):
    bot, dp, session, uid, rt = env
    user = repo.get_user_by_id(rt.db, uid)
    event_id = repo.create_craving(rt.db, uid, "2026-11-09", "22:00", None, 0, 0)
    await pause.send(rt, uid, event_id)
    row = repo.widget_for_ref(rt.db, "pause", event_id)
    await pause.send(rt, uid, event_id)                              # второй раз на эпизод не уходит
    assert len(session.of(api.SendWidget)) == 1
    session.calls.clear()
    await dp.feed_raw_update(bot, wq("cr:talk", msg=row["message_id"]))
    assert answers(session) == ["Напишите мне — я здесь"]
    assert session.of(SendMessage)
    session.calls.clear()
    await dp.feed_raw_update(bot, wq("cr:still", msg=row["message_id"], qid="s"))
    assert session.of(SendMessage)                                   # запасной текст без модели
    assert repo.get_craving(rt.db, uid, event_id)["outcome"] == "open"
    assert json.loads(session.of(api.EditMessageWidgetData)[0].chat_data)["st"] == "still"


# ------------------------------------------------------------- отправка и обновление

async def test_first_record_sends_second_only_edits(env):
    bot, dp, session, uid, rt = env
    eat(rt.db, uid, 300)
    await day.wait_idle()
    sends = session.of(api.SendWidget)
    assert len(sends) == 1 and sends[0].allow_queries and sends[0].disable_notification
    assert sends[0].caption is None and sends[0].width is None
    eat(rt.db, uid, 200, "13:00", "lunch")
    await day.wait_idle()
    assert len(session.of(api.SendWidget)) == 1
    edits = session.of(api.EditMessageWidgetData)
    assert edits and json.loads(edits[-1].chat_data)["k"][0] == 500
    today = foodsvc.local_now("Europe/Minsk").strftime("%Y-%m-%d")
    repo.add_water(rt.db, uid, today, "14:00", 250, "text")
    await day.wait_idle()
    assert json.loads(session.of(api.EditMessageWidgetData)[-1].chat_data)["w"][0] == 250


async def test_send_failure_is_not_retried_same_day(env):
    bot, dp, session, uid, rt = env
    session.fail_send = True
    eat(rt.db, uid)
    await day.wait_idle()
    eat(rt.db, uid, 100, "12:00", "lunch")
    await day.wait_idle()
    assert len(session.of(api.SendWidget)) == 1
    assert repo.widget_for_day(rt.db, uid, "day", foodsvc.local_now("Europe/Minsk").strftime("%Y-%m-%d")) is None


async def test_touch_is_noop_without_runtime_or_when_disabled(db, monkeypatch):  # noqa: F811
    day.touch(1)                                           # runtime не поднят: тихо
    bot, dp, session = build(db)
    monkeypatch.setattr(get_config(), "widgets_enabled", False)
    assert runtime.setup(bot, db, get_config()) is None
    day.touch(1)
    assert runtime.active() is None
