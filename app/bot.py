"""Точка входа: python -m app.bot"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app import rich
from app.config import get_config
from app.db import connect_from_config, migrate
from app.middleware import AccessMiddleware
from app.routers import (commands, craving, dishes, intake, menu, onboarding, products, reports,
                         support, tracking)
from app.scheduler import build_scheduler
from app.web.server import WEBHOOK_PATH, start_web

BOT_COMMANDS = [
    BotCommand(command="app", description="Приложение: отчёты и настройки"),
    BotCommand(command="talk", description="Поговорить, если тянет переесть"),
    BotCommand(command="often", description="Частые блюда: записать в один клик"),
    BotCommand(command="undo", description="Отменить последнюю запись"),
]

ROUTERS = (onboarding.router, menu.router, reports.router, commands.router, support.router, craving.router, dishes.router, products.router, tracking.router, intake.router)


def create_dispatcher(db) -> Dispatcher:
    """Сборка Dispatcher — единственное место, где регистрируются роутеры.

    Роутеры в aiogram — модульные синглтоны, и повторный include в новый
    Dispatcher падает с «Router is already attached». Поэтому отцепляем их
    перед сборкой: это делает возможным пересборку (тесты, рестарт в процессе).
    """
    for router in ROUTERS:
        router._parent_router = None

    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    # outer: один раз на апдейт, а не на каждый роутер, куда сообщение «проваливается»
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.include_routers(*ROUTERS)
    return dp


async def start_web_safely(db, bot, cfg, dp=None):
    """Mini App не должна ронять бота: сбой старта веб-сервера только логируется."""
    try:
        return await start_web(db, bot, cfg, dp)
    except Exception:  # noqa: BLE001
        logging.exception("веб-сервер Mini App не запустился, бот работает без него")
        return None


async def main() -> None:
    cfg = get_config()
    cfg.require_runtime_secrets()
    logging.basicConfig(level=cfg.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db = connect_from_config(cfg)
    version = migrate(db)
    logging.info("БД готова (%s), schema v%s", "Turso" if cfg.turso_database_url else "локальный SQLite", version)
    # Фото в БД не копируем: база хранит только данные. На временном диске Render файлы живут
    # до рестарта (хватает, чтобы разобрать и показать приём), потом карточка идёт без картинки.

    bot = Bot(cfg.telegram_bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(rich.RichMiddleware())
    dp = create_dispatcher(db)

    try:                                   # сбой Telegram при старте не должен ронять бота
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:  # noqa: BLE001
        logging.warning("не удалось обновить список команд", exc_info=True)
    sched = build_scheduler(db, bot)
    sched.start()

    webhook = bool(cfg.webhook_base_url)
    # Не отбрасываем присланную еду/скриншоты после краткого простоя процесса.
    for attempt in range(5):
        try:
            if webhook:
                await bot.set_webhook(cfg.webhook_base_url.rstrip("/") + WEBHOOK_PATH,
                                      secret_token=cfg.webhook_secret_value,
                                      allowed_updates=dp.resolve_used_update_types(),
                                      drop_pending_updates=False)
            else:
                await bot.delete_webhook(drop_pending_updates=False)
            break
        except Exception:  # noqa: BLE001
            if attempt == 4:
                raise
            logging.warning("настройка webhook не удалась, повтор", exc_info=True)
            await asyncio.sleep(3 * (attempt + 1))
    web_runner = await start_web_safely(db, bot, cfg, dp)
    try:
        if webhook:
            if web_runner is None:
                raise SystemExit("webhook включён, но веб-сервер не запустился")
            logging.info("режим webhook: %s%s", cfg.webhook_base_url, WEBHOOK_PATH)
            await asyncio.Event().wait()   # апдейты приходят в aiohttp-обработчик
        else:
            await dp.start_polling(bot)
    finally:
        if web_runner:
            await web_runner.cleanup()
        sched.shutdown(wait=False)
        db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
