"""Точка входа: python -m app.bot"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, ErrorEvent
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app import rich
from app.config import get_config
from app.adapters import off
from app.db import connect_from_config, migrate
from app.llm import client as llm_client
from app.middleware import AccessMiddleware, notify, should_notify
from app.routers import (commands, craving, dishes, intake, menu, onboarding, products, reports,
                         support, tracking)
from app.scheduler import build_scheduler
from app.web.server import WEBHOOK_PATH, start_web
from app.widgets import runtime as widgets_runtime
from app.widgets.dispatcher import WidgetDispatcher

log = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="app", description="Приложение: отчёты и настройки"),
    BotCommand(command="talk", description="Поговорить, если тянет переесть"),
    BotCommand(command="often", description="Частые блюда: записать в один клик"),
    BotCommand(command="undo", description="Отменить последнюю запись"),
]

ROUTERS = (onboarding.router, menu.router, reports.router, commands.router, support.router, craving.router, dishes.router, products.router, tracking.router, intake.router)


ERROR_TEXT = "Что-то пошло не так, попробуй ещё раз."


async def on_error(event: ErrorEvent) -> bool:
    """Общий перехват: ошибка в обработчике логируется и не оставляет человека в тишине."""
    exc = event.exception
    log.error("необработанная ошибка в обработчике: %s", type(exc).__name__, exc_info=exc)
    update = event.update
    target = update.message or (update.callback_query.message if update.callback_query else None)
    who = update.message.from_user if update.message else (
        update.callback_query.from_user if update.callback_query else None)
    if target is None or who is None:
        return True
    text = None
    if isinstance(exc, llm_client.LLMLimitError):
        if should_notify("llm_limit", who.id, 60):
            text = llm_client.LIMIT_TEXT
    elif should_notify("error", who.id, 30):
        text = ERROR_TEXT
    if text:
        await notify(target, text)
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:  # noqa: BLE001
            log.debug("cb.answer после ошибки не удался", exc_info=True)
    return True


def create_dispatcher(db) -> Dispatcher:
    """Сборка Dispatcher — единственное место, где регистрируются роутеры.

    Роутеры в aiogram — модульные синглтоны, и повторный include в новый
    Dispatcher падает с «Router is already attached». Поэтому отцепляем их
    перед сборкой: это делает возможным пересборку (тесты, рестарт в процессе).
    """
    for router in ROUTERS:
        router._parent_router = None

    dp = WidgetDispatcher(storage=MemoryStorage())
    dp["db"] = db
    # outer: один раз на апдейт, а не на каждый роутер, куда сообщение «проваливается»
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.include_routers(*ROUTERS)
    dp.errors.register(on_error)
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
    for noisy in ("httpx", "httpcore"):    # на INFO они пишут полные URL (названия продуктов, id Picooc)
        logging.getLogger(noisy).setLevel(logging.WARNING)

    db = connect_from_config(cfg)
    version = migrate(db)
    logging.info("БД готова (%s), schema v%s", "Turso" if cfg.turso_database_url else "локальный SQLite", version)
    # Фото в БД не копируем: база хранит только данные. На временном диске Render файлы живут
    # до рестарта (хватает, чтобы разобрать и показать приём), потом карточка идёт без картинки.

    bot = Bot(cfg.telegram_bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(rich.RichMiddleware())
    dp = create_dispatcher(db)
    widgets_runtime.setup(bot, db, cfg)       # карточка дня и пауза; выключатель WIDGETS_ENABLED

    try:                                   # сбой Telegram при старте не должен ронять бота
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:  # noqa: BLE001
        logging.warning("не удалось обновить список команд", exc_info=True)
    sched = build_scheduler(db, bot)
    sched.start()

    webhook = bool(cfg.webhook_base_url)

    async def configure_webhook() -> None:
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
                return
            except Exception:  # noqa: BLE001
                if attempt == 4:
                    raise
                logging.warning("настройка webhook не удалась, повтор", exc_info=True)
                await asyncio.sleep(3 * (attempt + 1))

    webhook_task: asyncio.Task | None = None
    if webhook:
        # Порт открываем сразу (Render ждёт его при старте), webhook регистрируем фоном с теми же повторами.
        web_runner = await start_web_safely(db, bot, cfg, dp)
        webhook_task = asyncio.create_task(configure_webhook())
    else:
        await configure_webhook()          # polling: webhook снят до начала опроса
        web_runner = await start_web_safely(db, bot, cfg, dp)
    try:
        if webhook:
            if web_runner is None:
                raise SystemExit("webhook включён, но веб-сервер не запустился")
            await webhook_task             # исчерпаны повторы set_webhook: падаем, как раньше, Render перезапустит
            logging.info("режим webhook: %s%s", cfg.webhook_base_url, WEBHOOK_PATH)
            await asyncio.Event().wait()   # апдейты приходят в aiohttp-обработчик
        else:
            await dp.start_polling(bot)
    finally:
        if webhook_task and not webhook_task.done():
            webhook_task.cancel()
        if web_runner:
            await web_runner.cleanup()
        widgets_runtime.teardown()
        sched.shutdown(wait=False)
        db.close()
        await bot.session.close()
        await llm_client.close_client()
        await off.close_client()


if __name__ == "__main__":
    asyncio.run(main())
