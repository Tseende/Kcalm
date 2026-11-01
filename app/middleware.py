"""Middleware: whitelist + простой rate-limit. Дедуп по file_hash — в intake."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from aiogram import BaseMiddleware
from aiogram.types import Message

from app.config import get_config

log = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    def __init__(self, max_per_min: int = 20) -> None:
        self.max_per_min = max_per_min
        self._hits: dict[int, deque] = defaultdict(deque)

    async def __call__(self, handler, event: Message, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        allowed = get_config().allowed_ids
        if allowed and user.id not in allowed:
            log.info("whitelist: %s отклонён", user.id)
            return
        now = time.monotonic()
        dq = self._hits[user.id]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.max_per_min:
            return
        dq.append(now)
        return await handler(event, data)
