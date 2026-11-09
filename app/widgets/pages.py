"""HTML виджетов с подстановкой ссылки на Mini App и проверкой лимитов Telegram."""
from __future__ import annotations

import zlib
from pathlib import Path

HTML_DIR = Path(__file__).resolve().parent / "html"
APP_PLACEHOLDER = "https://t.me/KcalmBot/app"      # так записана ссылка в day.html
MAX_RAW, MAX_ZIP = 32768, 4096

_cache: dict[tuple[str, str], str] = {}


def app_link(username: str, webapp_url: str) -> str:
    """Mini App по прямой ссылке, если приложение настроено; иначе просто чат с ботом."""
    if not username:
        return ""
    return f"https://t.me/{username}/app" if webapp_url else f"https://t.me/{username}"


def check_limits(html: str) -> None:
    raw = len(html.encode("utf-8"))
    packed = len(zlib.compress(html.encode("utf-8"), 9))
    if not 1 <= raw <= MAX_RAW or packed > MAX_ZIP:
        raise ValueError(f"виджет не проходит лимиты Telegram: {raw} байт, {packed} после zlib")


def load(name: str, link: str = "") -> str:
    """Исходник виджета (day, pause); link заменяет ссылку-плейсхолдер на приложение."""
    key = (name, link)
    if key not in _cache:
        html = (HTML_DIR / f"{name}.html").read_text(encoding="utf-8")
        if link and APP_PLACEHOLDER in html:
            html = html.replace(APP_PLACEHOLDER, link)
        check_limits(html)
        _cache[key] = html
    return _cache[key]
