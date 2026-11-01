"""Локальный просмотр Mini App без Telegram: `python -m app.web.dev`.

Поднимает только веб-сервер (без polling) на 127.0.0.1 против настроенной БД. Запросы
без initData проходят как пользователь WEB_DEV_TG_ID, он должен быть в БД и пройти
онбординг в боте. Без WEB_DEV_TG_ID запуск отказывает.
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from app.config import get_config
from app.db import connect, migrate
from app.web.server import build_app

HOST = "127.0.0.1"


async def main() -> None:
    cfg = get_config()
    if not cfg.web_dev_tg_id:
        raise SystemExit("Задай WEB_DEV_TG_ID в .env (tg id пользователя, уже прошедшего "
                         "онбординг в боте): без него dev-сервер не запускается.")
    logging.basicConfig(level=cfg.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = connect(cfg.db_path)
    migrate(db)
    runner = web.AppRunner(build_app(db, None, cfg), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, HOST, cfg.web_port).start()
    logging.info("dev-сервер: http://%s:%s/app/ (пользователь %s)", HOST, cfg.web_port,
                 cfg.web_dev_tg_id)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
