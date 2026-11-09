"""Общее состояние виджетов: бот, база, цикл событий, username бота.

setup() вызывается из app.bot.main(). Пока он не вызван (тесты, скрипты) или виджеты выключены,
active() возвращает None, и вся подсистема молчит.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field

from aiogram import Bot

from app.config import Config, get_config

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    bot: Bot
    db: sqlite3.Connection
    cfg: Config
    loop: asyncio.AbstractEventLoop
    username: str = ""
    # (user_id, local_date): сегодня Telegram виджет не принял (старый сервер, ошибка), больше не пробуем
    blocked: set = field(default_factory=set)
    # (user_id, local_date): человек удалил карточку дня, новую в тот же день не навязываем
    deleted: set = field(default_factory=set)


_rt: Runtime | None = None


def setup(bot: Bot, db: sqlite3.Connection, cfg: Config | None = None) -> Runtime | None:
    """Включить виджеты. None, если они выключены флагом."""
    global _rt
    cfg = cfg or get_config()
    if not cfg.widgets_enabled:
        _rt = None
        return None
    _rt = Runtime(bot=bot, db=db, cfg=cfg, loop=asyncio.get_running_loop())
    from app.widgets import day
    day.install()
    return _rt


def teardown() -> None:
    global _rt
    _rt = None
    from app.widgets import day
    day.uninstall()


def active() -> Runtime | None:
    rt = _rt
    if rt is None or not rt.cfg.widgets_enabled:
        return None
    return rt


async def bot_username(rt: Runtime) -> str:
    """Username бота из getMe, один раз за процесс."""
    if not rt.username:
        try:
            rt.username = (await rt.bot.me()).username or ""
        except Exception:  # noqa: BLE001
            log.warning("widgets: getMe не удался, ссылка на приложение будет общей", exc_info=True)
    return rt.username
