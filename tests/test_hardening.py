"""Усиление перед закрытым тестом: доступ, лимит модели, обработчики ошибок, онбординг,
миграция посуды, удаление данных, ввод API, картинки, вывод и безопасность.

Сети нет: модель отключена в conftest, бот работает на FakeSession."""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app import bot as bot_mod
from app import config as config_mod
from app import middleware, rich, scheduler
from app.config import Config
from app.db import MIGRATIONS_DIR, connect, migrate, _set_version, user_version
from app.domain import craving as cravingdom
from app.llm import client as llm
from app.middleware import AccessMiddleware
from app.routers import onboarding as onb
from app.services import charts
from app.services import food as foodsvc
from app.services import profile as profilesvc
from app.storage import repo
from app.web import server
from app.web.auth import InitDataError, validate_init_data
from tests.test_advice_facts import _save
from tests.test_routers_smoke import build, message, onboard, send  # noqa: F401
from tests.test_routers_smoke import db as smoke_db  # noqa: F401
from tests.test_web_api import (H, JPEG, TOKEN, _fixed_now, add_user, auth, cfg, client,  # noqa: F401
                                db, fake_food, jpeg, make_client, make_config, photo_form,
                                post_meal, user_id)

pytestmark = pytest.mark.asyncio


def use_config(monkeypatch, **kw) -> Config:
    config = Config(_env_file=None, **kw)
    monkeypatch.setattr(config_mod, "_config", config)
    return config


# ------------------------------------------------------------------ 1. доступ

def test_is_allowed_follows_whitelist_and_public_flag():
    closed = Config(_env_file=None, allowed_tg_ids="1,2", allow_public_bot=False)
    assert closed.is_allowed(1) and closed.is_allowed(2) and not closed.is_allowed(3)
    assert not Config(_env_file=None, allowed_tg_ids="", allow_public_bot=False).is_allowed(1)
    assert Config(_env_file=None, allowed_tg_ids="1", allow_public_bot=True).is_allowed(99)


class Ev:
    """Минимальное сообщение: кто написал и что бот ответил."""

    def __init__(self, uid: int) -> None:
        self.from_user = SimpleNamespace(id=uid)
        self.sent: list[str] = []

    async def answer(self, text: str, **kw) -> None:  # noqa: ANN003
        self.sent.append(text)


@pytest.fixture()
def closed_bot(monkeypatch):
    middleware._notified.clear()
    use_config(monkeypatch, allowed_tg_ids="1", allow_public_bot=False)
    yield
    middleware._notified.clear()


async def test_stranger_gets_one_reply_with_id_and_handler_not_called(closed_bot):
    mw, calls = AccessMiddleware(), []

    async def handler(event, data):  # noqa: ANN001
        calls.append(event)

    first, second = Ev(55), Ev(55)
    await mw(handler, first, {})
    await mw(handler, second, {})
    assert calls == []
    assert len(first.sent) == 1 and "55" in first.sent[0] and "закрытом тесте" in first.sent[0]
    assert second.sent == []                                  # повторно не отвечаем

    other = Ev(56)
    await mw(handler, other, {})
    assert len(other.sent) == 1 and "56" in other.sent[0]     # каждому чужому по разу


async def test_allowed_user_passes_and_rate_limit_replies_once(closed_bot):
    mw, calls = AccessMiddleware(max_per_min=2), []

    async def handler(event, data):  # noqa: ANN001
        calls.append(event)

    events = [Ev(1) for _ in range(5)]
    for ev in events:
        await mw(handler, ev, {})
    assert len(calls) == 2
    texts = [t for ev in events for t in ev.sent]
    assert texts == [middleware.RATE_LIMIT_TEXT]              # одно предупреждение, не по каждому


async def test_limit_hook_notifies_once_and_is_reset(closed_bot):
    mw = AccessMiddleware()
    ev = Ev(1)

    async def handler(event, data):  # noqa: ANN001
        hook = llm.limit_hook.get()
        assert hook is not None
        hook()
        hook()                                                # второй вызов в ту же минуту молчит
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    await mw(handler, ev, {})
    assert ev.sent == [llm.LIMIT_TEXT]
    assert llm.limit_hook.get() is None


async def test_web_stranger_gets_not_allowed_code(client):
    resp = await client.get("/api/me", headers=auth(555))
    body = await resp.json()
    assert resp.status == 401 and body["error"] == "not_allowed" and "закрыт" in body["message"]
    assert (await client.get("/api/me", headers=H)).status == 200


async def test_web_public_flag_lets_stranger_in(make_client, db, tmp_path):
    client = await make_client(make_config(tmp_path, allow_public_bot=True))
    resp = await client.get("/api/me", headers=auth(555))
    assert resp.status == 200 and (await resp.json())["onboarded"] is False


# ------------------------------------------------------------------ 2. дневной лимит модели

def _usage(db, uid: int, n: int, model: str = "m") -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.execute("INSERT INTO model_usage_daily (day, user_id, model, requests) VALUES (?,?,?,?)",
               (day, uid, model, n))
    db.commit()


async def test_check_limit_blocks_at_limit_and_counts_all_models(db, monkeypatch):
    uid = add_user(db, 1001)
    use_config(monkeypatch, daily_llm_limit=3)
    llm.check_limit(db, uid, "food_text")                     # 0 из 3
    _usage(db, uid, 2, "a")
    _usage(db, uid, 1, "b")
    assert llm.used_today(db, uid) == 3
    with pytest.raises(llm.LLMLimitError):
        llm.check_limit(db, uid, "food_text")
    assert issubclass(llm.LLMLimitError, llm.LLMError)        # запасные пути ловят как обычный сбой


async def test_check_limit_exemptions_and_off_switch(db, monkeypatch):
    uid = add_user(db, 1001)
    _usage(db, uid, 500)
    use_config(monkeypatch, daily_llm_limit=3)
    llm.check_limit(db, uid, "craving_dialog")                # поддержка в тяге не режется
    llm.check_limit(db, None, "food_text")                    # системный вызов без человека
    llm.check_limit(None, uid, "food_text")
    use_config(monkeypatch, daily_llm_limit=0)
    llm.check_limit(db, uid, "food_text")                     # 0 = лимит выключен


async def test_check_limit_is_per_user_and_notifies_hook(db, monkeypatch):
    a, b = add_user(db, 1001), add_user(db, 1002)
    _usage(db, a, 5)
    use_config(monkeypatch, daily_llm_limit=5)
    llm.check_limit(db, b, "food_text")
    fired = []
    token = llm.limit_hook.set(lambda: fired.append(1))
    try:
        with pytest.raises(llm.LLMLimitError):
            llm.check_limit(db, a, "food_text")
    finally:
        llm.limit_hook.reset(token)
    assert fired == [1]


async def test_real_chat_json_checks_limit_before_network(db, monkeypatch):
    """conftest подменяет chat_json, поэтому берём свежую копию модуля с настоящей функцией."""
    spec = importlib.util.spec_from_file_location("llm_client_copy", llm.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    uid = add_user(db, 1001)
    _usage(db, uid, 9)
    use_config(monkeypatch, daily_llm_limit=9)
    with pytest.raises(fresh.LLMError) as e:
        await fresh.chat_json("food_text", [], user_id=uid, db=db)
    assert type(e.value).__name__ == "LLMLimitError"
    with pytest.raises(fresh.LLMError) as e:
        await fresh.transcribe_ogg(b"x", user_id=uid, db=db)
    assert type(e.value).__name__ == "LLMLimitError"


async def test_web_meal_returns_429_when_limit_reached(make_client, db, tmp_path, monkeypatch,
                                                       fake_food):
    uid = add_user(db, 1001)
    _usage(db, uid, 4)
    client = await make_client(make_config(tmp_path, daily_llm_limit=4))
    resp = await client.post("/api/meals", data=photo_form(jpeg()), headers=H)
    body = await resp.json()
    assert resp.status == 429 and body["error"] == "llm_limit" and "лимит" in body["message"]
    assert db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 0

    ok = await make_client(make_config(tmp_path, daily_llm_limit=0))
    assert (await ok.post("/api/meals", data=photo_form(jpeg()), headers=H)).status == 201


async def test_web_llm_failure_maps_limit_to_429_and_rest_to_502():
    assert server.llm_failure(llm.LLMLimitError("x"), "m").status == 429
    other = server.llm_failure(llm.LLMError("x"), "не вышло")
    assert other.status == 502 and other.code == "llm_unavailable"


async def test_semaphore_is_per_loop_and_bounded():
    sem = llm._get_sem()
    assert sem is llm._get_sem()
    assert llm.MAX_CONCURRENT_REQUESTS >= 1


# ------------------------------------------------------------------ 3. общий обработчик ошибок бота

def _error_event(uid: int, exc: Exception, *, callback: bool = False):
    from aiogram.types import CallbackQuery, Update, User
    from aiogram.types.error_event import ErrorEvent
    msg = message("привет", uid)
    if callback:
        cb = CallbackQuery(id="1", from_user=User(id=uid, is_bot=False, first_name="Т"),
                           chat_instance="c", data="x", message=msg)
        return ErrorEvent(update=Update(update_id=1, callback_query=cb), exception=exc), msg, cb
    return ErrorEvent(update=Update(update_id=1, message=msg), exception=exc), msg, None


async def test_on_error_replies_and_throttles(monkeypatch):
    middleware._notified.clear()
    sent: list[str] = []

    async def fake_notify(target, text):  # noqa: ANN001
        sent.append(text)

    monkeypatch.setattr(bot_mod, "notify", fake_notify)
    ev, _, _ = _error_event(7, RuntimeError("boom"))
    assert await bot_mod.on_error(ev) is True
    assert await bot_mod.on_error(ev) is True                 # вторая ошибка подряд молчит
    assert sent == [bot_mod.ERROR_TEXT]


async def test_on_error_uses_limit_text_for_limit_errors(monkeypatch):
    middleware._notified.clear()
    sent: list[str] = []

    async def fake_notify(target, text):  # noqa: ANN001
        sent.append(text)

    monkeypatch.setattr(bot_mod, "notify", fake_notify)
    ev, _, _ = _error_event(8, llm.LLMLimitError("лимит"))
    await bot_mod.on_error(ev)
    assert sent == [llm.LIMIT_TEXT]


async def test_on_error_closes_callback_spinner(monkeypatch):
    middleware._notified.clear()
    answered: list[tuple] = []

    async def fake_notify(target, text):  # noqa: ANN001
        pass

    async def fake_answer(self, *a, **k):  # noqa: ANN001
        answered.append(a)

    from aiogram.types import CallbackQuery
    monkeypatch.setattr(bot_mod, "notify", fake_notify)
    monkeypatch.setattr(CallbackQuery, "answer", fake_answer)
    ev, _, _ = _error_event(9, ValueError("x"), callback=True)
    assert await bot_mod.on_error(ev) is True
    assert answered == [()]


async def test_handler_crash_inside_dispatcher_does_not_propagate(smoke_db, monkeypatch):
    """Падение хендлера уходит в on_error: человек получает ответ, feed_update не бросает."""
    bot, dp, session = build(smoke_db)
    await onboard(dp, bot)
    middleware._notified.clear()

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("упало")

    monkeypatch.setattr(repo, "day_meals", boom)
    session.texts.clear()
    await send(dp, bot, "/today")                             # не должно бросить
    assert bot_mod.ERROR_TEXT in session.texts


async def test_web_error_middleware_rolls_back_unfinished_writes(client, db, user_id, monkeypatch):
    def broken_day(*a, **k):  # noqa: ANN002, ANN003
        db.execute("INSERT INTO water_log (user_id, local_date, local_time, ml, source) "
                   "VALUES (?,?,?,?,?)", (user_id, "2026-05-20", "10:00", 250, "text"))
        raise RuntimeError("после записи, до commit")

    monkeypatch.setattr(repo, "day_water_ml", broken_day)
    resp = await client.get("/api/day", headers=H)
    assert resp.status == 500
    assert db.execute("SELECT COUNT(*) FROM water_log").fetchone()[0] == 0


# ------------------------------------------------------------------ 4. планировщик

class FakeBot:
    def __init__(self) -> None:
        self.sent: list[int] = []

    async def send_message(self, chat_id, text, **kw):  # noqa: ANN003
        self.sent.append(chat_id)


async def test_one_user_failure_does_not_stop_others_or_followups(db, monkeypatch, caplog):
    users = []
    for tg in (10, 11, 12):
        uid = repo.create_user(db, tg, tz="Europe/Minsk")
        repo.update_user(db, uid, onboarded=1, sex="female", height_cm=165, start_weight_kg=70)
        users.append(uid)
    done, followups = [], []

    async def fake_dispatch(db_, bot, user, now_utc):  # noqa: ANN001
        if user["tg_id"] == 11:
            raise RuntimeError("плохие данные")
        done.append(user["tg_id"])

    async def fake_followups(db_, bot):  # noqa: ANN001
        followups.append(1)

    monkeypatch.setattr(scheduler, "_has_due_work", lambda *a, **k: True)   # облачная предпроверка
    monkeypatch.setattr(scheduler, "_dispatch_user", fake_dispatch)
    monkeypatch.setattr(scheduler, "dispatch_craving_followups", fake_followups)
    with caplog.at_level(logging.ERROR):
        await scheduler.dispatch_due_reminders(db, FakeBot(),
                                               now_utc=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))
    assert sorted(done) == [10, 12] and followups == [1]
    assert any("user=" in r.getMessage() for r in caplog.records)


async def test_followup_failure_is_logged_not_raised(db, monkeypatch):
    async def bad_followups(db_, bot):  # noqa: ANN001
        raise RuntimeError("упало")

    monkeypatch.setattr(scheduler, "dispatch_craving_followups", bad_followups)
    await scheduler.dispatch_due_reminders(db, FakeBot(),
                                           now_utc=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))


# ------------------------------------------------------------------ 5. онбординг

@pytest.mark.parametrize("text,expected", [
    ("м", "м"), ("М", "м"), ("муж", "м"), ("мужской", "м"), ("male", "м"), ("m", "м"),
    ("ж", "ж"), ("жен", "ж"), ("женский", "ж"), ("F", "ж"), ("woman", "ж"),
    ("ок", None), ("да", None), ("", None), ("не знаю", None), ("мм", None)])
def test_parse_sex(text, expected):
    assert onb.parse_sex(text) == expected


def test_parsers_reject_garbage_and_out_of_range():
    assert onb.parse_age("35") == 35 and onb.parse_age("abc") is None and onb.parse_age("500") is None
    assert onb.parse_number("178", *onb.HEIGHT_RANGE) == 178
    assert onb.parse_number("5000", *onb.HEIGHT_RANGE) is None
    assert onb.parse_number("1", *onb.WEIGHT_RANGE) is None
    assert onb.parse_number("nan", *onb.WEIGHT_RANGE) is None
    assert onb.parse_number("inf", *onb.WEIGHT_RANGE) is None
    assert onb.parse_number("92,4", *onb.WEIGHT_RANGE) == 92.4
    assert onb.parse_tz("Europe/Moscow") == "Europe/Moscow" and onb.parse_tz("Нигде/Никак") is None
    assert onb.parse_answer("tz", "ок", "Europe/Minsk") == "Europe/Minsk"
    assert onb.parse_answer("tz", "мусор", "Europe/Minsk") is None


async def _last(session) -> str:
    return session.texts[-1]


async def test_sex_ok_reasks_instead_of_defaulting(smoke_db):
    bot, dp, session = build(smoke_db)
    await send(dp, bot, "/start")
    await send(dp, bot, "ок")
    assert onb.SEX_HINT in await _last(session)
    await send(dp, bot, "ж")
    assert "Возраст" in await _last(session)


async def test_invalid_answers_reask_with_hint_and_do_not_advance(smoke_db):
    bot, dp, session = build(smoke_db)
    await send(dp, bot, "/start")
    for answer in ("м", "35"):
        await send(dp, bot, answer)
    await send(dp, bot, "5000")                              # рост
    assert onb.RETRY_HINTS["height"] in await _last(session) and "Рост" in await _last(session)
    await send(dp, bot, "178")
    await send(dp, bot, "1")                                 # вес
    assert onb.RETRY_HINTS["weight"] in await _last(session)
    await send(dp, bot, "92.4")
    await send(dp, bot, "ок")                                # темп
    await send(dp, bot, "25:99")                             # кухня
    assert onb.RETRY_HINTS["cutoff"] in await _last(session)


async def test_invalid_timezone_reasks_and_ok_takes_default(smoke_db):
    bot, dp, session = build(smoke_db)
    for answer in ("/start", "м", "35", "178", "92.4", "ок", "ок", "2"):
        await send(dp, bot, answer)
    await send(dp, bot, "Нигде/Никак")
    assert onb.TZ_HINT in await _last(session)
    await send(dp, bot, "ок")
    assert "Всё верно?" in await _last(session) and "Europe/Minsk" in await _last(session)
    await send(dp, bot, "да")
    assert repo.get_user_by_tg(smoke_db, 1)["tz"] == "Europe/Minsk"


async def test_non_text_during_onboarding_repeats_question(smoke_db):
    from aiogram.types import Update
    bot, dp, session = build(smoke_db)
    await send(dp, bot, "/start")
    # «не текст»: сообщение без text (контакт)
    from aiogram.types import Contact
    msg = message(None, 1, contact=Contact(phone_number="1", first_name="x")).as_(bot)
    from tests.test_routers_smoke import _ids
    await dp.feed_update(bot, Update(update_id=next(_ids), message=msg))
    assert "ответ текстом" in await _last(session) and "Пол" in await _last(session)
    assert repo.get_user_by_tg(smoke_db, 1) is None


async def test_edit_in_confirmation_validates_range(smoke_db):
    bot, dp, session = build(smoke_db)
    for answer in ("/start", "м", "35", "178", "92.4", "ок", "ок", "2", "ок"):
        await send(dp, bot, answer)
    assert "Всё верно?" in await _last(session)
    await send(dp, bot, "рост 5000")
    assert onb.RETRY_HINTS["height"] in await _last(session)
    await send(dp, bot, "вес 1")
    assert onb.RETRY_HINTS["weight"] in await _last(session)
    await send(dp, bot, "пол ок")
    assert onb.SEX_HINT in await _last(session)
    await send(dp, bot, "рост 180")
    assert "Обновил" in await _last(session)
    await send(dp, bot, "да")
    assert repo.get_user_by_tg(smoke_db, 1)["height_cm"] == 180


# ------------------------------------------------------------------ 6. миграция 026 (посуда)

def _db_at(path, version: int) -> sqlite3.Connection:
    conn = connect(str(path))
    user_version(conn)                                           # в облачном режиме создаёт таблицу версий
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        v = int(f.name.split("_", 1)[0])
        if v <= version:
            with conn:
                conn.executescript(f.read_text(encoding="utf-8"))
                _set_version(conn, v)
    return conn


def test_migration_026_keeps_dishes_and_allows_new_kinds(tmp_path):
    conn = _db_at(tmp_path / "v25.db", 25)
    assert user_version(conn) == 25
    uid = repo.create_user(conn, 1)
    conn.execute("INSERT INTO dishes (id, user_id, name, kind, diameter_cm, volume_ml, is_default) "
                 "VALUES (7, ?, 'Тарелка', 'plate', 24, NULL, 1)", (uid,))
    conn.execute("INSERT INTO dishes (id, user_id, name, kind, volume_ml, deleted_at) "
                 "VALUES (9, ?, 'Старая кружка', 'mug', 300, '2026-01-01')", (uid,))
    conn.execute("INSERT INTO meals (id, user_id, eaten_at_utc, local_date, local_time, meal_type, "
                 "source, kcal_total) VALUES (1, ?, '2026-05-20 10:00:00', '2026-05-20', '13:00', "
                 "'lunch', 'text', 100)", (uid,))
    conn.execute("INSERT INTO meal_photos (meal_id, path, dish_id) VALUES (1, 'a.jpg', 7)")
    conn.commit()
    before = [tuple(r) for r in conn.execute("SELECT * FROM dishes ORDER BY id")]
    with pytest.raises((sqlite3.IntegrityError, ValueError)):      # libsql-обёртка бросает ValueError
        conn.execute("INSERT INTO dishes (user_id, name, kind) VALUES (?, 'Х', 'salad_bowl')", (uid,))
    conn.rollback()

    assert migrate(conn) == 26
    assert [tuple(r) for r in conn.execute("SELECT * FROM dishes ORDER BY id")] == before
    assert conn.execute("SELECT dish_id FROM meal_photos").fetchone()[0] == 7
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert "idx_dishes_user" in names and "dishes_new" not in names
    for kind in ("salad_bowl", "pot", "jar", "thermos", "plate", "other"):
        repo.create_dish(conn, uid, f"посуда {kind}", kind)
    assert repo.get_dish(conn, uid, 7)["name"] == "Тарелка"
    assert migrate(conn) == 26                                 # повтор безвреден
    conn.close()


def test_migration_026_survives_leftover_dishes_new(tmp_path):
    conn = _db_at(tmp_path / "v25b.db", 25)
    conn.execute("CREATE TABLE dishes_new (junk INTEGER)")     # хвост от оборванной попытки
    conn.commit()
    assert migrate(conn) == 26
    conn.close()


async def test_dish_with_new_kind_is_created_via_api(client):
    resp = await client.post("/api/dishes", json={"name": "Салатник", "kind": "salad_bowl"}, headers=H)
    assert resp.status == 201 and (await resp.json())["kind"] == "salad_bowl"


async def test_dish_integrity_error_becomes_400(client, monkeypatch):
    def refuse(*a, **k):  # noqa: ANN002, ANN003
        raise sqlite3.IntegrityError("CHECK constraint failed")

    monkeypatch.setattr(repo, "create_dish", refuse)
    resp = await client.post("/api/dishes", json={"name": "Х", "kind": "plate"}, headers=H)
    assert resp.status == 400 and (await resp.json())["error"] == "bad_field"


# ------------------------------------------------------------------ 7. удаление всех данных

def _file(root: Path, name: str, data: bytes = b"x") -> str:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_bytes(data)
    return str(p)


async def test_delete_all_data_removes_everything_and_keeps_shared_files(db, tmp_path, monkeypatch):
    photos = tmp_path / "photos"
    use_config(monkeypatch, photos_dir=str(photos), allow_public_bot=True)
    a, b = add_user(db, 1001), add_user(db, 1002)
    own, shared, dish_photo = (_file(photos, "own.jpg"), _file(photos, "shared.jpg"),
                               _file(photos / "dishes", "d.jpg"))
    outside = _file(tmp_path / "elsewhere", "keep.jpg")
    for p in (own, shared, dish_photo, outside):
        db.execute("INSERT INTO photo_blobs (path, data) VALUES (?, ?)", (p, b"x"))
    db.commit()

    meal_a = _save(db, a, "10:00", "гречка", 200)
    meal_b = _save(db, b, "11:00", "гречка", 200)
    repo.add_meal_photos(db, meal_a, [own, shared, outside])
    repo.add_meal_photos(db, meal_b, [shared])
    repo.create_dish(db, a, "Тарелка", "plate", photo_path=dish_photo)
    repo.add_favorite(db, a, meal_a)
    db.execute("INSERT INTO chat_messages (user_id, role, text) VALUES (?, 'user', 'привет')", (a,))
    _usage(db, a, 3)
    db.commit()

    profilesvc.delete_all_data(db, repo.get_user_by_tg(db, 1001))

    assert repo.get_user_by_tg(db, 1001) is None
    for table in ("dishes", "favorite_meals", "chat_messages", "meals", "model_usage_daily"):
        assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (a,)).fetchone()[0] == 0
    assert not Path(own).exists() and not Path(dish_photo).exists()
    assert Path(shared).exists()                                 # чужой снимок с тем же именем остаётся
    assert Path(outside).exists()                                # вне папки фото файлы не трогаем
    blobs = {r[0] for r in db.execute("SELECT path FROM photo_blobs")}
    assert own not in blobs and dish_photo not in blobs and shared in blobs
    assert repo.get_user_by_tg(db, 1002) is not None
    assert db.execute("SELECT COUNT(*) FROM meals WHERE user_id=?", (b,)).fetchone()[0] == 1


async def test_delete_all_data_rolls_back_on_failure(db, tmp_path, monkeypatch):
    photos = tmp_path / "photos"
    use_config(monkeypatch, photos_dir=str(photos), allow_public_bot=True)
    uid = add_user(db, 1001)
    own = _file(photos, "own.jpg")
    meal = _save(db, uid, "10:00", "гречка", 200)
    repo.add_meal_photos(db, meal, [own])
    repo.create_dish(db, uid, "Миска", "bowl")
    db.execute("INSERT INTO photo_blobs (path, data) VALUES (?, ?)", (own, b"x"))
    db.commit()

    real_execute = db.execute

    class Flaky:
        """Обёртка: на удалении пользователя соединение «падает»."""

        def __getattr__(self, name):
            return getattr(db, name)

        def __enter__(self):
            return db.__enter__()

        def __exit__(self, *exc):
            return db.__exit__(*exc)

        def execute(self, sql, *a):
            if sql.startswith("DELETE FROM users"):
                raise sqlite3.OperationalError("database is locked")
            return real_execute(sql, *a)

    with pytest.raises(sqlite3.OperationalError):
        profilesvc.delete_all_data(Flaky(), repo.get_user_by_tg(db, 1001))
    assert repo.get_user_by_tg(db, 1001) is not None              # ничего не потеряно
    assert db.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 1
    assert Path(own).exists()
    assert db.execute("SELECT COUNT(*) FROM photo_blobs").fetchone()[0] == 1


async def test_delete_account_endpoint_with_dishes_and_favorites(client, db, user_id):
    await client.post("/api/dishes", json={"name": "Миска", "kind": "bowl"}, headers=H)
    meal = _save(db, user_id, "10:00", "гречка", 200)
    repo.add_favorite(db, user_id, meal)
    resp = await client.delete("/api/me", json={"confirm": "УДАЛИТЬ"}, headers=H)
    assert resp.status == 200 and (await resp.json()) == {"deleted": True}
    assert db.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 0


# ------------------------------------------------------------------ 8. надёжность API

async def test_patch_product_with_null_macros_is_400(client):
    created = await client.post("/api/products", headers=H, json={
        "name": "Творог", "kcal": 121, "protein": 17, "fat": 5, "carbs": 1.8})
    pid = (await created.json())["id"]
    for field in ("kcal", "protein", "fat", "carbs"):
        resp = await client.patch(f"/api/products/{pid}", json={field: None}, headers=H)
        assert resp.status == 400 and (await resp.json())["error"] == "bad_field", field
    ok = await client.patch(f"/api/products/{pid}", json={"brand": None, "kcal": 100}, headers=H)
    assert ok.status == 200


async def test_meal_comment_too_long_is_400(client, fake_food):
    resp = await client.post("/api/meals", data=photo_form(jpeg(), comment="я" * 2001), headers=H)
    assert resp.status == 400 and (await resp.json())["error"] == "text_too_long"


async def test_json_body_over_64kb_is_413(client):
    big = json.dumps({"ml": 250, "pad": "x" * (server.MAX_FIELD_BYTES + 10)})
    resp = await client.post("/api/water", data=big, headers={**H, "Content-Type": "application/json"})
    assert resp.status == 413 and (await resp.json())["error"] == "too_large"

    async def chunks():
        yield big.encode()

    streamed = await client.post("/api/water", data=chunks(),
                                 headers={**H, "Content-Type": "application/json"})
    assert streamed.status == 413


@pytest.mark.parametrize("value", ["abc", "1.5", "-3", "99999999999999999999", True, [1], None, "0"])
def test_bounded_int_rejects_garbage(value):
    with pytest.raises(server.ApiError) as e:
        server.bounded_int(value, "x")
    assert e.value.status == 400 and e.value.code == "bad_field"


def test_bounded_int_accepts_numbers_and_numeric_strings():
    assert server.bounded_int(5, "x") == 5 and server.bounded_int(" 42 ", "x") == 42
    assert server.bounded_int("0", "x", low=0) == 0


async def test_favorite_with_garbage_meal_id_is_400(client):
    for value in ("abc", 10 ** 30, True, 1.5):
        resp = await client.post("/api/favorites", json={"meal_id": value}, headers=H)
        assert resp.status == 400, value


async def test_non_ascii_hash_is_bad_hash_not_crash():
    from urllib.parse import urlencode
    init = urlencode({"auth_date": "9999999999", "user": json.dumps({"id": 1}), "hash": "хеш-кириллица"})
    with pytest.raises(InitDataError) as e:
        validate_init_data(init, TOKEN)
    assert e.value.code == "bad_hash"


# ------------------------------------------------------------------ 9. разбор ответа модели

@pytest.mark.parametrize("data", [
    {"items": "не список"},
    {"items": [None]},
    {"items": [{"name_ru": "x", "amount": "много"}]},
    {"items": [{"name_ru": "x", "amount": {"kind": "grams", "grams": "abc"}}]},
    {"items": [{"name_ru": "x", "amount": {"kind": "grams", "grams": 100, "grams_range": 5}}]},
    {"items": [1, 2, 3]},
])
def test_malformed_llm_json_becomes_llm_error(data):
    try:
        foodsvc.understanding_from_json(data)
    except llm.LLMError:
        pass                                                    # ожидаемый путь
    # без исключения тоже допустимо: мусор отфильтрован, главное, что нет TypeError/AttributeError


async def test_refine_meal_with_malformed_answer_raises_llm_error(db, monkeypatch):
    async def odd(*a, **k):  # noqa: ANN002, ANN003
        return {"items": [5, "x"], "changes": 7}, "m"

    monkeypatch.setattr(llm, "chat_json", odd)
    with pytest.raises(llm.LLMError):
        await foodsvc.refine_meal([{"name_ru": "гречка", "grams": 100}], "с маслом", None, db)


# ------------------------------------------------------------------ 10. картинки

def _image(size: tuple[int, int], fmt: str = "JPEG", exif_orientation: int | None = None) -> bytes:
    from PIL import Image
    im = Image.new("RGB", size, (200, 30, 30))
    out = io.BytesIO()
    kwargs = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    im.save(out, format=fmt, **kwargs)
    return out.getvalue()


def test_image_too_large_checks_only_header(monkeypatch):
    monkeypatch.setattr(foodsvc, "MAX_IMAGE_PIXELS", 10_000)
    assert foodsvc.image_too_large(_image((200, 200)))          # 40 000 пикселей
    assert not foodsvc.image_too_large(_image((50, 50)))
    assert not foodsvc.image_too_large(b"not an image")


def test_shrink_b64_keeps_exif_orientation_and_shrinks():
    from PIL import Image
    raw = _image((3000, 1000), exif_orientation=6)              # хранится лёжа, показывается стоя
    out = base64.b64decode(foodsvc.shrink_b64(base64.b64encode(raw).decode(), max_side=500))
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) <= 500
        assert im.size[1] > im.size[0]                          # повёрнуто: высота больше ширины


def test_shrink_b64_small_image_is_returned_as_is_and_garbage_survives():
    small = base64.b64encode(_image((100, 100))).decode()
    assert foodsvc.shrink_b64(small, max_side=500) == small
    junk = base64.b64encode(b"junk").decode()
    assert foodsvc.shrink_b64(junk) == junk


async def test_web_rejects_oversized_image_with_400(client, monkeypatch, fake_food):
    monkeypatch.setattr(foodsvc, "MAX_IMAGE_PIXELS", 10_000)
    big = _image((200, 200))
    form = aiohttp.FormData(default_to_multipart=True)
    form.add_field("photo", big, filename="p.jpg", content_type="image/jpeg")
    resp = await client.post("/api/meals", data=form, headers=H)
    assert resp.status == 400 and (await resp.json())["error"] == "image_too_large"


# ------------------------------------------------------------------ 11. графики и rich

async def test_chart_functions_share_one_lock():
    for fn in (charts.day_chart, charts.week_chart, charts.weight_chart, charts.body_chart):
        assert hasattr(fn, "__wrapped__"), fn.__name__          # обёрнуты functools.wraps
    assert charts._PLOT_LOCK.acquire(blocking=False)             # RLock свободен
    charts._PLOT_LOCK.release()


async def test_chart_lock_serialises_threads(monkeypatch):
    active, peak = [0], [0]

    @charts._plot_locked
    def work():
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        import time
        time.sleep(0.02)
        active[0] -= 1

    await asyncio.gather(*(asyncio.to_thread(work) for _ in range(6)))
    assert peak[0] == 1


def test_strip_tags():
    assert rich.strip_tags("<b>Привет</b> &amp; <i>мир</i><br>ок") == "Привет & мирок"
    assert rich.strip_tags("") == ""


async def test_rich_third_attempt_sends_without_markup():
    calls: list = []

    async def make_request(bot, method):  # noqa: ANN001
        calls.append(method)
        if len(calls) < 3:
            raise TelegramBadRequest(method=method,
                                     message="Bad Request: can't parse entities" if len(calls) == 2
                                     else "Bad Request: rich not supported")
        return "ok"

    mw = rich.RichMiddleware()
    method = SendMessage(chat_id=1, text="<b>Привет</b> <неверный тег")
    assert await mw(make_request, SimpleNamespace(), method) == "ok"
    assert len(calls) == 3
    last = calls[-1]
    assert last.parse_mode is None and "<" not in last.text.replace("<неверный тег", "")
    assert "Привет" in last.text


async def test_rich_other_bad_request_is_not_swallowed():
    async def make_request(bot, method):  # noqa: ANN001
        raise TelegramBadRequest(method=method, message="Bad Request: chat not found")

    with pytest.raises(TelegramBadRequest):
        await rich.RichMiddleware()(make_request, SimpleNamespace(), SendMessage(chat_id=1, text="x"))


# ------------------------------------------------------------------ 12. детектор риска

@pytest.mark.parametrize("text", [
    "вызываю рвоту после еды", "засовываю пальцы в горло", "буду морить себя голодом",
    "не ем уже три дня", "не ела пять дней", "у меня булимия", "похоже на анорексию"])
def test_purging_and_restriction_phrases_are_flagged(text):
    assert cravingdom.risk_level(text) == "purging", text


@pytest.mark.parametrize("text", [
    "хочу покончить с собой", "думаю про суицид", "мне жить не хочется", "не хочется жить",
    "незачем жить", "лучше бы я умерла", "лучше бы мне умереть"])
def test_self_harm_phrases_are_flagged(text):
    assert cravingdom.risk_level(text) == "self_harm", text


@pytest.mark.parametrize("text", [
    "не ем после шести", "не ем сладкое", "съела пиццу и теперь жалею", "хочу есть",
    "не ем мясо уже месяц", "умираю с голоду", "готовлю на неделю", "хочется жить здоровее"])
def test_everyday_phrases_are_not_flagged(text):
    assert cravingdom.risk_level(text) == "none", text


# ------------------------------------------------------------------ 13. журнал

async def test_off_search_warning_does_not_leak_product_name():
    import inspect

    from app.adapters import off
    src = inspect.getsource(off.search_branded)
    assert "type(e).__name__" in src
