"""Контекст одного сообщения; фоновые задачи получают собственную копию."""
from contextvars import ContextVar

# None — старый режим без тем; 0 — основной чат при включённых темах.
pending_thread: ContextVar[int | None] = ContextVar("pending_thread", default=None)
