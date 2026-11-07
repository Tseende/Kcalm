"""Middleware: whitelist + простой rate-limit. Дедуп по file_hash — в intake."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.config import get_config
from app.llm import client as llm_client

log = logging.getLogger(__name__)

CLOSED_TEST_TEXT = ("Бот сейчас в закрытом тесте. Твой Telegram ID: {id}. "
                    "Передай его владельцу, чтобы получить доступ.")
RATE_LIMIT_TEXT = "Слишком много сообщений подряд, подожди минуту."

_notified: dict[tuple[str, int], float] = {}     # (вид, user_id) -> когда последний раз писали


def should_notify(kind: str, user_id: int, every_s: float | None = None) -> bool:
    """Одно вежливое сообщение на человека за процесс (every_s=None) или не чаще раза в every_s секунд."""
    now = time.monotonic()
    last = _notified.get((kind, user_id))
    if last is not None and (every_s is None or now - last < every_s):
        return False
    _notified[(kind, user_id)] = now
    return True


async def notify(event, text: str) -> None:
    """Ответить на сообщение или всплывающей подсказкой на кнопку; сбой отправки не мешает."""
    try:
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
    except Exception:  # noqa: BLE001
        log.debug("middleware: не удалось ответить", exc_info=True)


class AccessMiddleware(BaseMiddleware):
    def __init__(self, max_per_min: int = 20) -> None:
        self.max_per_min = max_per_min
        self._hits: dict[int, deque] = defaultdict(deque)

    async def __call__(self, handler, event: Message | CallbackQuery, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        if not get_config().is_allowed(user.id):
            log.info("whitelist: %s отклонён", user.id)
            if should_notify("closed", user.id):
                await notify(event, CLOSED_TEST_TEXT.format(id=user.id))
            elif isinstance(event, CallbackQuery):
                await _quiet_answer(event)
            return
        now = time.monotonic()
        dq = self._hits[user.id]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.max_per_min:
            if should_notify("rate", user.id, 60):
                await notify(event, RATE_LIMIT_TEXT)
            elif isinstance(event, CallbackQuery):
                await _quiet_answer(event)
            return
        dq.append(now)
        token = llm_client.limit_hook.set(_limit_notifier(event, user.id))
        try:
            return await handler(event, data)
        finally:
            llm_client.limit_hook.reset(token)


def _limit_notifier(event, user_id: int):
    """Функция для llm-слоя: сообщить про дневной лимит, не чаще раза в минуту. Копия контекста уходит и в
    фоновые задачи обработчика (альбомы), так что уведомление доходит и оттуда."""
    def hook() -> None:
        if not should_notify("llm_limit", user_id, 60):
            return
        target = getattr(event, "message", None) or event      # у кнопки пишем в чат, а не всплывашкой
        asyncio.get_running_loop().create_task(_send(target, llm_client.LIMIT_TEXT))
    return hook


async def _send(target, text: str) -> None:
    try:
        await target.answer(text)
    except Exception:  # noqa: BLE001
        log.debug("middleware: не удалось сообщить про лимит", exc_info=True)


async def _quiet_answer(cb: CallbackQuery) -> None:
    """Закрыть «часики» на кнопке без текста."""
    try:
        await cb.answer()
    except Exception:  # noqa: BLE001
        log.debug("middleware: cb.answer не удался", exc_info=True)
