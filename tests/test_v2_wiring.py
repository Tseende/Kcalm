"""Подключение Mini App к боту: кнопка «Приложение» в меню, устойчивый старт веб-сервера,
миграция 018 (chat_messages)."""
from __future__ import annotations

import logging
import socket

import pytest

from app import bot as botmod
from app import config as config_mod
from app.config import Config
from app.db import MIGRATIONS_DIR, connect, migrate, user_version
from app.routers import menu


@pytest.fixture()
def restore_config():
    old = config_mod._config
    yield
    config_mod._config = old


def _cfg(**kw) -> Config:
    return Config(_env_file=None, telegram_bot_token="42:T", **kw)


def _buttons(kb):
    return [b for row in kb.inline_keyboard for b in row]


def test_menu_has_no_webapp_button_without_url(restore_config):
    config_mod.set_config(_cfg())
    assert menu.main_kb() is None


def test_menu_has_webapp_button_with_url(restore_config):
    config_mod.set_config(_cfg(webapp_url="https://example.org/app/"))
    web = [b for b in _buttons(menu.main_kb()) if b.web_app is not None]
    assert len(web) == 1
    assert web[0].web_app.url == "https://example.org/app/"
    assert web[0].text == "📱 Открыть приложение"


def test_menu_button_can_open_a_screen(restore_config):
    for base, want in (("https://example.org/app/", "https://example.org/app/?screen=analysis"),
                       ("https://example.org/app/?v=2", "https://example.org/app/?v=2&screen=analysis"),
                       ("https://example.org/app/#x", "https://example.org/app/?screen=analysis#x")):
        config_mod.set_config(_cfg(webapp_url=base))
        btn = _buttons(menu.main_kb("analysis", "🌙 Открыть итог дня"))[0]
        assert btn.web_app.url == want and btn.text == "🌙 Открыть итог дня"


@pytest.mark.asyncio
async def test_start_web_safely_swallows_errors(monkeypatch, caplog):
    async def boom(db, bot, cfg):
        raise RuntimeError("порт занят")

    monkeypatch.setattr(botmod, "start_web", boom)
    with caplog.at_level(logging.ERROR):
        assert await botmod.start_web_safely(None, None, _cfg()) is None
    assert "без него" in caplog.text


@pytest.mark.asyncio
async def test_start_web_safely_port_busy(tmp_path):
    db = connect(tmp_path / "t.db")
    migrate(db)
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    try:
        cfg = _cfg(web_enabled=True, web_host="127.0.0.1", web_port=busy.getsockname()[1],
                   photos_dir=str(tmp_path / "p"))
        assert await botmod.start_web_safely(db, None, cfg) is None
    finally:
        busy.close()


@pytest.mark.asyncio
async def test_start_web_safely_returns_runner(tmp_path):
    db = connect(tmp_path / "t.db")
    migrate(db)
    cfg = _cfg(web_enabled=True, web_host="127.0.0.1", web_port=0,
               photos_dir=str(tmp_path / "p"))
    runner = await botmod.start_web_safely(db, None, cfg)
    try:
        assert runner is not None
    finally:
        await runner.cleanup()


def test_migration_018_chat_messages(tmp_path):
    assert (MIGRATIONS_DIR / "018_chat_messages.sql").is_file()
    db = connect(tmp_path / "t.db")
    assert migrate(db) >= 18
    cols = [r["name"] for r in db.execute("PRAGMA table_info(chat_messages)")]
    assert cols == ["id", "user_id", "role", "text", "meal_id", "created_at"]
    idx = [r["name"] for r in db.execute("PRAGMA index_list(chat_messages)")]
    assert "idx_chat_messages_user" in idx
    assert user_version(db) >= 18


def test_migration_018_over_existing_lazy_table(tmp_path):
    """Прод-БД, где таблицу уже создал старый сервер: миграция не падает, данные целы."""
    db = connect(tmp_path / "t.db")
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        v = int(f.name.split("_", 1)[0])
        if v >= 18:
            break
        db.executescript(f.read_text(encoding="utf-8"))
        db.execute(f"PRAGMA user_version = {v}")
    db.execute("CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
               "role TEXT NOT NULL, text TEXT NOT NULL, meal_id INTEGER, "
               "created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("INSERT INTO chat_messages(user_id, role, text) VALUES (1,'user','привет')")
    db.commit()
    assert migrate(db) >= 18
    assert db.execute("SELECT COUNT(*) c FROM chat_messages").fetchone()["c"] == 1
