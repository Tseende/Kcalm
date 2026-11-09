"""Диспетчер, который понимает widget_query (aiogram 3.31 этот апдейт не знает).

Update с полем widget_query парсится (extra="allow", поле лежит в model_extra), но штатный
Dispatcher выкидывает его как неизвестный тип. Все пути входа (polling, webhook, feed_raw_update,
feed_webhook_update) заканчиваются в feed_update, поэтому перехват стоит там.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from app.widgets import handler

log = logging.getLogger(__name__)

WIDGET_UPDATE = "widget_query"


def widget_query_of(update: Update):
    extra = update.model_extra or {}
    return extra.get(WIDGET_UPDATE)


class WidgetDispatcher(Dispatcher):
    async def feed_update(self, bot: Bot, update: Update, **kwargs):
        raw = widget_query_of(update)
        if raw is not None:
            await handler.handle_widget_query(bot, raw)
            return None
        return await super().feed_update(bot, update, **kwargs)

    def resolve_used_update_types(self, skip_events: set[str] | None = None) -> list[str]:
        """Типы апдейтов для allowed_updates: обычные плюс widget_query (если виджеты включены)."""
        used = super().resolve_used_update_types(skip_events)
        from app.config import get_config
        if get_config().widgets_enabled and WIDGET_UPDATE not in used:
            used.append(WIDGET_UPDATE)
        return used
