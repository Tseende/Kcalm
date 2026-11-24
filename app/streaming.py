"""Черновик Telegram во время SSE-генерации; один постоянный ответ после завершения."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import secrets
import time
from contextlib import suppress
from functools import wraps

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Message

from app.config import get_config
from app.llm import client as llm

log = logging.getLogger(__name__)
UPDATE_INTERVAL = 0.7
KEEPALIVE = 4.0
_current = contextvars.ContextVar("telegram_reply_stream", default=None)
finalizing = contextvars.ContextVar("telegram_reply_finalizing", default=False)


class Draft:
    def __init__(self, msg: Message):
        self.msg = msg
        self.draft_id = secrets.randbelow(2**31 - 1) + 1
        self.started = self.finished = self.disabled = False
        self.latest = ""
        self._event = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._last_at = 0.0

    @property
    def active(self):
        return not self.finished and not self.disabled

    async def _send(self, text: str):
        try:
            await self.msg.bot.send_message_draft(
                chat_id=self.msg.chat.id, message_thread_id=self.msg.message_thread_id,
                draft_id=self.draft_id, text=text[:4000], parse_mode=None, can_stop=False)
            self._last_at = time.monotonic()
        except TelegramAPIError:
            self.disabled = True
            log.info("Черновики недоступны, ответ придёт обычным сообщением", exc_info=True)

    async def __call__(self, text: str):
        if not self.active:
            return
        if not self.started:
            self.started = True
            await self._send("")  # нативный Thinking… вместо искусственного текста
            if self.active:
                self._worker = asyncio.create_task(self._run())
        # Показываем законченные фразы: обрывок числа или слова не мелькает в черновике.
        ends = list(re.finditer(r"[.!?…](?:\s|$)", text))
        preview = text[:ends[-1].start() + 1] if ends else ""
        if preview != self.latest:
            self.latest = preview
            self._event.set()

    async def _run(self):
        try:
            while self.active:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._event.wait(), timeout=KEEPALIVE)
                self._event.clear()
                pause = UPDATE_INTERVAL - (time.monotonic() - self._last_at)
                if pause > 0:
                    await asyncio.sleep(pause)
                if self.active:
                    await self._send(self.latest)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — UI не должен прервать разговор
            self.disabled = True
            log.info("Обновление черновика прервано", exc_info=True)

    async def close(self):
        self.finished = True
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker


def response(fn):
    """Контекст только для интерактивных ответов; Mini App и планировщик не затрагиваются."""
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        event = args[0] if args else kwargs.get("msg")
        msg = event if isinstance(event, Message) else getattr(event, "message", None)
        existing = _current.get()
        if (not get_config().chat_streaming or not isinstance(msg, Message)
                or msg.chat.type != "private" or (existing is not None and existing.active)):
            return await fn(*args, **kwargs)
        frame = Draft(msg)
        token, sink = _current.set(frame), llm.reply_sink.set(frame)
        try:
            return await fn(*args, **kwargs)
        finally:
            await frame.close()
            llm.reply_sink.reset(sink)
            _current.reset(token)
    return wrapped


class DraftRequestMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        frame = _current.get()
        if (frame is None or not frame.started or not frame.active
                or not isinstance(method, (SendMessage, EditMessageText))
                or not method.text or method.chat_id != frame.msg.chat.id):
            return await make_request(bot, method)
        await frame.close()
        if isinstance(method, EditMessageText):
            # Редактирование статуса не сохраняет временный draft. Завершаем новым сообщением,
            # как требует Telegram; служебный статус (например, ASR голоса) убираем.
            if method.message_id is not None:
                try:
                    await bot.delete_message(method.chat_id, method.message_id)
                except TelegramAPIError:
                    log.debug("Не удалось убрать статус перед финальным ответом", exc_info=True)
            method = SendMessage(chat_id=method.chat_id, message_thread_id=frame.msg.message_thread_id,
                                 text=method.text, parse_mode=method.parse_mode,
                                 entities=method.entities, reply_markup=method.reply_markup)
        token = finalizing.set(True)
        try:
            return await make_request(bot, method)
        finally:
            finalizing.reset(token)
