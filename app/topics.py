"""Две темы личного чата: создание, маршрутизация и контекст обработки.

При недоступном Bot API 10.5 бот продолжает работать в обычном чате.
Редактирование существующих сообщений никогда не меняет их тему.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.chat_context import pending_thread
from app.config import get_config

log = logging.getLogger(__name__)
NAMES = {"diary": "📒 Дневник", "support": "💬 Поговорить"}
INTRO = {
    "diary": "📒 Здесь будут напоминания, карточка дня и красивые отчёты о твоём прогрессе. "
             "Еду и воду записывай в приложении: /app. "
             "Для разговоров о самочувствии и желании переесть открой «💬 Поговорить» или напиши /talk.",
    "support": "💬 Хочется переесть, тревожно или трудно понять, что чувствуешь? "
               "Напиши или запиши голосовое: вместе разберём, что происходит, и найдём посильный шаг. "
               "Без осуждения и запретов. Здесь можно просто поговорить; еду записывай в приложении: /app.",
}
_forced: ContextVar[str | None] = ContextVar("topic_destination", default=None)


@dataclass(frozen=True)
class ChatContext:
    service: "Topics"
    chat_id: int
    thread_id: int | None
    kind: str


current: ContextVar[ChatContext | None] = ContextVar("chat_topic", default=None)
_EXPLICIT_LOG = re.compile(r"^\s*(?:запиши|записать|добавь|добавить|учти|внеси)\b", re.I)


def explicit_log(text: str) -> bool:
    return bool(_EXPLICIT_LOG.match(text))


def log_text(text: str) -> str:
    """Убрать явную просьбу перед разбором воды и еды."""
    return _EXPLICIT_LOG.sub("", text, count=1).lstrip(" :—-,") if current.get() else text


def awaiting_log() -> bool:
    ctx = current.get()
    if ctx is None:
        return False
    from app.storage import repo
    user = repo.get_user_by_tg(ctx.service.db, ctx.chat_id)
    return bool(user and any(repo.latest_pending_input(ctx.service.db, user["id"], kind, 10)
                             for kind in ("barcode", "meal_refine", "product_draft")))


def logging_enabled(msg: Message, text: str | None = None, *, photo: bool = False) -> bool:
    ctx = current.get()
    if ctx and ctx.chat_id == msg.chat.id:
        return False  # в темах чат служит для отчётов и разговора, запись еды — в Mini App
    return get_config().chat_logs_food


def in_support() -> bool:
    ctx = current.get()
    return ctx is not None and ctx.kind == "support"


def destination(kind: str):
    """Назначение отправок в обработчике, включая созданные им фоновые задачи."""
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            token = _forced.set(kind)
            scope = None
            try:
                ctx = current.get()
                if ctx:
                    thread = (await ctx.service.ensure(ctx.service.bot, ctx.chat_id)).get(kind)
                    if thread is not None:
                        scope = pending_thread.set(thread)
                return await fn(*args, **kwargs)
            finally:
                if scope is not None:
                    pending_thread.reset(scope)
                _forced.reset(token)
        return wrapped
    return decorate


class SetMySettings(TelegramMethod[bool]):
    # В установленном aiogram метода 10.5 ещё нет.
    __returning__ = bool
    __api_method__ = "setMySettings"
    has_topics_enabled: bool = True
    allows_users_to_create_topics: bool = False
    has_topic_sidebar: bool = True
    callback_query_mode: str = "default"


class Topics:
    def __init__(self, db):
        self.db = db
        self.bot = None
        self.ready = False
        self._locks = defaultdict(asyncio.Lock)
        self._retry_at: dict[int, float] = {}
        self._redirected: set[tuple[int, int | None]] = set()

    async def setup(self, bot) -> None:
        self.bot = bot
        if not get_config().chat_topics_enabled:
            return
        try:
            await bot(SetMySettings())
        except TelegramAPIError:
            log.warning("Темы недоступны, оставляю обычный чат", exc_info=True)
            return
        self.ready = True

    def stored(self, chat_id: int) -> dict[str, int]:
        return {r["kind"]: r["thread_id"] for r in self.db.execute(
            "SELECT kind, thread_id FROM chat_topics WHERE chat_id=?", (chat_id,))}

    async def ensure(self, bot, chat_id: int) -> dict[str, int]:
        if not self.ready or not get_config().chat_topics_enabled or chat_id <= 0:
            return {}
        from app.storage import repo
        profile = repo.get_user_by_tg(self.db, chat_id)
        if not profile or not profile["onboarded"]:
            return {}
        created = {}
        async with self._locks[chat_id]:
            topics = self.stored(chat_id)
            if time.monotonic() < self._retry_at.get(chat_id, 0):
                return topics
            for kind, name in NAMES.items():
                if kind in topics:
                    row = self.db.execute("SELECT name FROM chat_topics WHERE chat_id=? AND kind=?",
                                          (chat_id, kind)).fetchone()
                    if row["name"] != name:
                        try:
                            await bot.edit_forum_topic(chat_id, topics[kind], name=name)
                        except TelegramAPIError as exc:
                            if "not modified" not in str(exc).lower() and "topic_not_modified" not in str(exc).lower():
                                self._retry_at[chat_id] = time.monotonic() + 60
                                log.warning("Не удалось переименовать тему %s", kind, exc_info=True)
                                continue
                        self.db.execute("UPDATE chat_topics SET name=? WHERE chat_id=? AND kind=?",
                                        (name, chat_id, kind))
                        self.db.commit()
                        created[kind] = topics[kind]
                    continue
                try:
                    topic = await bot.create_forum_topic(chat_id, name)
                except TelegramAPIError:
                    self._retry_at[chat_id] = time.monotonic() + 60
                    log.warning("Не удалось создать тему %s", kind, exc_info=True)
                    break
                # Каждая тема сохраняется сразу: сбой второй не создаст дубликат первой.
                self.db.execute("INSERT INTO chat_topics(chat_id, kind, thread_id, name) VALUES(?,?,?,?)",
                                (chat_id, kind, topic.message_thread_id, name))
                self.db.commit()
                topics[kind] = topic.message_thread_id
                created[kind] = topic.message_thread_id
        # Уже вне блокировки: отправка сама проходит через middleware.
        token = _forced.set(None)
        try:
            for kind, thread in created.items():
                try:
                    await bot.send_message(chat_id, INTRO[kind], message_thread_id=thread,
                                           disable_notification=True)
                except TelegramAPIError:
                    log.info("Не удалось отправить описание темы %s", kind, exc_info=True)
        finally:
            _forced.reset(token)
        return topics

    async def redirect_notice(self, bot, msg: Message, support: int) -> None:
        key = (msg.chat.id, msg.message_thread_id)
        if key in self._redirected:
            return
        try:
            me = await bot.me()
            username = getattr(me, "username", None)
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="💬 Поговорить", url=f"https://t.me/{username}/{support}")]]) if username else None
            await bot.send_message(msg.chat.id, "💬 Продолжим в теме «Поговорить» — мой ответ будет там.",
                                   message_thread_id=msg.message_thread_id, reply_markup=kb,
                                   disable_notification=True)
            self._redirected.add(key)
        except TelegramAPIError:
            log.info("Не удалось показать переход к разговору", exc_info=True)


class TopicMiddleware(BaseMiddleware):
    def __init__(self, service: Topics):
        self.service = service

    async def __call__(self, handler, event, data):
        from app.storage import repo
        msg = event if isinstance(event, Message) else getattr(event, "message", None)
        user = getattr(event, "from_user", None)
        if not isinstance(msg, Message) or msg.chat.type != "private" or not user or user.is_bot:
            return await handler(event, data)
        profile = repo.get_user_by_tg(self.service.db, user.id)
        topics = (await self.service.ensure(data["bot"], msg.chat.id)
                  if profile and profile["onboarded"] else {})
        # Все свободные реплики и голосовые, а также вопросы по отчётам ведут в один разговор.
        conversational = isinstance(event, Message) and (
            bool(msg.text and not msg.text.startswith("/")) or msg.voice is not None or msg.audio is not None)
        ask_report = not isinstance(event, Message) and (getattr(event, "data", "") or "").startswith("rp:ask:")
        target = topics.get("support") if conversational or ask_report else None
        if target is not None:
            if msg.message_thread_id != target:
                await self.service.redirect_notice(data["bot"], msg, target)
            msg = msg.model_copy(update={"message_thread_id": target, "is_topic_message": True}).as_(data["bot"])
            event = msg if isinstance(event, Message) else event.model_copy(update={"message": msg}).as_(data["bot"])
            state = data.get("state")
            if state is not None:
                state = FSMContext(storage=state.storage, key=replace(state.key, thread_id=target))
                data["state"], data["raw_state"] = state, await state.get_state()
        token = scope = None
        if topics:
            kind = next((k for k, v in topics.items() if v == msg.message_thread_id), "diary")
            token = current.set(ChatContext(self.service, msg.chat.id, msg.message_thread_id, kind))
            scope = pending_thread.set(msg.message_thread_id or topics.get("diary", 0))
        try:
            # Служебные сообщения о темах не являются едой или репликой пользователя.
            if msg.forum_topic_created or msg.forum_topic_closed or msg.forum_topic_reopened or msg.forum_topic_edited:
                return
            if (topics and not isinstance(event, Message)
                    and (getattr(event, "data", "") or "").startswith(("meal_", "bc_"))):
                await event.answer("Еду теперь записываем и уточняем в приложении: /app", show_alert=True)
                return
            result = await handler(event, data)
            profile = repo.get_user_by_tg(self.service.db, user.id)
            if not topics and profile and profile["onboarded"]:
                await self.service.ensure(data["bot"], msg.chat.id)
            return result
        finally:
            if scope is not None:
                pending_thread.reset(scope)
            if token is not None:
                current.reset(token)


class TopicRequestMiddleware(BaseRequestMiddleware):
    def __init__(self, service: Topics):
        self.service = service

    async def __call__(self, make_request, bot, method):
        fields = type(method).model_fields
        chat_id = getattr(method, "chat_id", None)
        # Только отправки и typing; edit/delete остаются привязаны к исходному сообщению.
        api = method.__api_method__
        if (not api.startswith(("send", "copy", "forward"))
                or "message_thread_id" not in fields or not isinstance(chat_id, int) or chat_id <= 0
                or not self.service.ready):
            return await make_request(bot, method)
        topics = await self.service.ensure(bot, chat_id)
        ctx = current.get()
        forced = _forced.get()
        original = getattr(method, "message_thread_id", None)
        kind = forced or (ctx.kind if ctx and ctx.chat_id == chat_id else "diary")
        thread = topics.get(kind) if forced or original is None else original
        if thread is None:
            return await make_request(bot, method)
        changes = {"message_thread_id": thread}
        if original != thread and "reply_parameters" in fields:
            changes["reply_parameters"] = None
        routed = method.model_copy(update=changes)
        try:
            return await make_request(bot, routed)
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "topic_closed" in error or "topic closed" in error:
                await bot.reopen_forum_topic(chat_id, thread)
                return await make_request(bot, routed)
            if "message thread not found" not in error and "topic_deleted" not in error:
                raise
            stale_kind = next((k for k, v in topics.items() if v == thread), None)
            if stale_kind is None:
                raise
            self.service.db.execute("DELETE FROM chat_topics WHERE chat_id=? AND kind=? AND thread_id=?",
                                    (chat_id, stale_kind, thread))
            self.service.db.commit()
            self.service._retry_at.pop(chat_id, None)
            replacement = (await self.service.ensure(bot, chat_id)).get(stale_kind)
            if replacement is None:
                raise
            changes["message_thread_id"] = replacement
            if "reply_parameters" in fields:
                changes["reply_parameters"] = None
            return await make_request(bot, method.model_copy(update=changes))
