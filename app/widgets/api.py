"""Методы Bot API 10.4 для виджетов, которых нет в aiogram 3.31."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram.client.default import Default
from aiogram.methods import TelegramMethod
from aiogram.types import Message, ReplyParameters, ReplyMarkupUnion


class SendWidget(TelegramMethod[Message]):
    """sendWidget: сообщение с HTML-виджетом (html ≤ 32768 байт и ≤ 4096 после zlib level 9)."""

    __returning__ = Message
    __api_method__ = "sendWidget"

    chat_id: int | str
    html: str
    width: int | None = None
    height: int | None = None
    public_data: str | None = None
    chat_data: str | None = None            # 0–1024 байт, свои данные у каждого сообщения
    caption: str | None = None
    parse_mode: str | Default | None = Default("parse_mode")
    allow_queries: bool | None = None
    disable_notification: bool | None = None
    protect_content: bool | Default | None = Default("protect_content")
    reply_parameters: ReplyParameters | None = None
    reply_markup: ReplyMarkupUnion | None = None


class EditMessageWidgetData(TelegramMethod[bool]):
    """editMessageWidgetData: меняет данные виджета в уже отправленном сообщении (подпись не трогает)."""

    __returning__ = bool
    __api_method__ = "editMessageWidgetData"

    chat_id: int | str | None = None
    message_id: int | None = None
    chat_data: str | None = None
    public_data: str | None = None


class AnswerWidgetQuery(TelegramMethod[bool]):
    """answerWidgetQuery: ответ на запрос виджета за 15 секунд; data (до 1024 байт) видит только он."""

    __returning__ = bool
    __api_method__ = "answerWidgetQuery"

    widget_query_id: str
    data: str | None = None


@dataclass(frozen=True)
class WidgetQuery:
    """Разобранный widget_query (в aiogram 3.31 лежит в update.model_extra сырым словарём)."""

    id: str
    user_id: int
    chat_id: int | None
    message_id: int | None
    data: str

    @classmethod
    def parse(cls, raw: Any) -> "WidgetQuery | None":
        """None, если структура не похожа на запрос из чата (инлайн, без данных, битая)."""
        if not isinstance(raw, dict):
            return None
        sender = raw.get("from")
        chat = raw.get("chat")
        qid, data = raw.get("id"), raw.get("data")
        if not (isinstance(sender, dict) and isinstance(sender.get("id"), int)
                and isinstance(qid, str) and qid and isinstance(data, str)):
            return None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        message_id = raw.get("message_id")
        return cls(id=qid, user_id=sender["id"],
                   chat_id=chat_id if isinstance(chat_id, int) else None,
                   message_id=message_id if isinstance(message_id, int) else None,
                   data=data)
