"""Проверка Telegram WebApp initData (HMAC-SHA256). Чистые функции: без сети и БД.

Алгоритм из документации Telegram: secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token),
data_check_string = отсортированные строки "k=v" без hash через \\n, подпись — hex HMAC.
Сами initData и токены в сообщения исключений и в логи не попадают.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_S = 24 * 3600
FUTURE_SKEW_S = 300           # часы клиента и сервера могут слегка расходиться


class InitDataError(ValueError):
    """initData не прошла проверку (причина в .code, без самих данных)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def compute_hash(fields: dict[str, str], bot_token: str) -> str:
    """Hex-подпись для набора полей (без hash). Нужна и тестам, чтобы собрать валидную строку."""
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields) if k != "hash")
    return hmac.new(_secret_key(bot_token), check.encode(), hashlib.sha256).hexdigest()


def validate_init_data(init_data: str, bot_token: str, max_age_s: int = MAX_AGE_S,
                       now: float | None = None) -> dict:
    """Проверить initData и вернуть разобранные поля: user — уже dict, auth_date — int.

    Исключение InitDataError: empty, no_token, malformed, no_hash, bad_hash, no_auth_date,
    expired, from_future, no_user, bad_user."""
    if not init_data or not isinstance(init_data, str):
        raise InitDataError("empty")
    if not bot_token:
        raise InitDataError("no_token")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise InitDataError("malformed") from None
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise InitDataError("malformed")             # повторяющийся ключ
        fields[key] = value
    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("no_hash")
    if not hmac.compare_digest(compute_hash(fields, bot_token), received.lower()):
        raise InitDataError("bad_hash")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError):
        raise InitDataError("no_auth_date") from None
    now = time.time() if now is None else now
    if now - auth_date > max_age_s:
        raise InitDataError("expired")
    if auth_date - now > FUTURE_SKEW_S:
        raise InitDataError("from_future")

    raw_user = fields.get("user")
    if not raw_user:
        raise InitDataError("no_user")
    try:
        user = json.loads(raw_user)
        tg_id = int(user["id"])
    except (ValueError, KeyError, TypeError):
        raise InitDataError("bad_user") from None
    if not isinstance(user, dict) or tg_id <= 0:
        raise InitDataError("bad_user")
    user["id"] = tg_id
    return {**fields, "auth_date": auth_date, "user": user}
