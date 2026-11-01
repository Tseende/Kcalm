"""Мелкие текстовые хелперы. Бот работает в ParseMode.HTML, поэтому любой текст,
пришедший от LLM или из OCR, перед вставкой в сообщение нужно экранировать."""
from __future__ import annotations

import html


def esc(value: object) -> str:
    """Экранировать <, > и & в строке, которую увидит Telegram как HTML."""
    return html.escape(str(value), quote=False)
