"""Чат с ботом только для разговора (chat_logs_food=false, значение по умолчанию).

Текст и голос ничего не пишут в дневник, а картинка попадает в базу, только если это скриншот сна,
активности или весов. Сети нет: модель и парсер скриншотов подменены.
"""
from __future__ import annotations

import pytest
from aiogram.types import PhotoSize, Voice

from app import config
from app.llm import client as llm
from app.routers import intake
from app.services import food as foodsvc
from app.services import screens
from tests.test_photo_flow import NOW, _drain, _feed, _photo, build  # noqa: F401
from tests.test_photo_flow import db  # noqa: F401  (фикстура)
from tests.test_photo_flow import _fast  # noqa: F401  (autouse: время и справочник)
from tests.test_routers_smoke import message, onboard, send

pytestmark = pytest.mark.asyncio
TODAY = screens.local_date_of("Europe/Minsk")      # как у парсера скриншотов: реальная дата


@pytest.fixture(autouse=True)
def _discussion_mode(monkeypatch):
    """Боевое значение по умолчанию: в conftest для старых тестов включён режим записи."""
    monkeypatch.setattr(config, "_config", config.Config(_env_file=None))
    assert config.get_config().chat_logs_food is False


@pytest.fixture()
def dialog(monkeypatch):
    """Ответы модели-собеседника; сохраняет вопросы, которые до неё дошли."""
    seen: list[str] = []

    async def fake_chat(kind, messages, **kw):  # noqa: ANN001
        seen.append(messages[-1]["content"])
        return {"reply": "Понимаю, звучит как обычный день. Как ты себя чувствуешь?"}, "m"

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    return seen


def _count(db, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("phrase", ["я съел тост", "выпил 2 стакана воды", "спал 7 часов",
                                    "10 тысяч шагов", "гречка 150 г", "штрихкод 4600605123456"])
async def test_text_is_discussion_not_a_record(db, dialog, phrase):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await send(dp, bot, phrase)

    assert len(dialog) == 1 and phrase in dialog[0], "реплика должна дойти до собеседника"
    assert any("Как ты себя чувствуешь" in t for t in session.texts)
    for table in ("meals", "water_log", "sleep_sessions", "activity_daily"):
        assert _count(db, table) == 0, table


async def test_dialog_continues_across_messages(db, dialog):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await send(dp, bot, "переел вечером")
    await send(dp, bot, "я съел ещё тост")
    assert len(dialog) == 2 and _count(db, "meals") == 0


async def test_explicit_commands_still_work_without_llm(db, monkeypatch):
    async def no_llm(*a, **k):
        raise AssertionError("команды словами не должны идти в модель")

    monkeypatch.setattr(llm, "chat_json", no_llm)
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await send(dp, bot, "привет")
    await send(dp, bot, "что ты умеешь")
    assert any("Этот чат для разговора" in t for t in session.texts)
    assert not any("гречка 150" in t for t in session.texts), "справка не учит писать еду в чат"


async def test_voice_is_discussion_not_a_record(db, dialog, monkeypatch):
    async def fake_transcribe(ogg, **kw):  # noqa: ANN001
        return "съел два тоста"

    monkeypatch.setattr(llm, "transcribe_ogg", fake_transcribe)
    bot, dp, session = build(db)
    await onboard(dp, bot)
    voice = message(None, 1, voice=Voice(file_id="v1", file_unique_id="v1", duration=3)).as_(bot)
    await _feed(dp, bot, voice)

    assert len(dialog) == 1 and "тоста" in dialog[0]
    assert _count(db, "meals") == 0


async def test_sleep_screenshot_is_recorded(db, monkeypatch):
    async def fake_parse(image_b64, user_id, conn):  # noqa: ANN001
        return {"screen": "generic_sleep", "date": TODAY, "overall_confidence": 0.95,
                "raw_text": ["7 ч 25 мин"],
                "metrics": {"sleep_duration_min": {"value": 445, "confidence": 0.9}}}

    async def no_food(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("в режиме разговора фото не идёт в распознавание еды")

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse)
    monkeypatch.setattr(foodsvc, "parse_photo", no_food)
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await _feed(dp, bot, _photo(bot, "sleep-1"))            # обычная пропорция, без подписи

    row = db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert (row["duration_min"], row["source"]) == (445, "screenshot")
    assert any("Сон записал" in t for t in session.texts)


async def test_food_photo_is_not_recorded(db, monkeypatch):
    async def not_a_screen(image_b64, user_id, conn):  # noqa: ANN001
        return {"screen": "unknown", "metrics": {}, "overall_confidence": 0.1}

    async def no_food(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("в режиме разговора фото не идёт в распознавание еды")

    monkeypatch.setattr(screens, "parse_screenshot", not_a_screen)
    monkeypatch.setattr(foodsvc, "parse_photo", no_food)
    monkeypatch.setattr(foodsvc, "parse_photos", no_food)
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await _feed(dp, bot, _photo(bot, "plate-1", caption="обед"))

    assert _count(db, "meals") == 0
    assert any(intake.NOT_A_SCREEN in t for t in session.texts)


async def test_album_of_screenshots_records_each(db, monkeypatch):
    shots = iter([("generic_sleep", {"sleep_duration_min": 445}),
                  ("generic_activity", {"steps": 8540})])

    async def fake_parse(image_b64, user_id, conn):  # noqa: ANN001
        screen, metrics = next(shots)
        return {"screen": screen, "date": TODAY, "overall_confidence": 0.95, "raw_text": ["x"],
                "metrics": {k: {"value": v, "confidence": 0.9} for k, v in metrics.items()}}

    monkeypatch.setattr(screens, "parse_screenshot", fake_parse)
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await _feed(dp, bot, _photo(bot, "a1", group="g1"))
    await _feed(dp, bot, _photo(bot, "a2", group="g1"))
    await _drain()

    assert _count(db, "sleep_sessions") == 1 and _count(db, "activity_daily") == 1
    assert _count(db, "meals") == 0
