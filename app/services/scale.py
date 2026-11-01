"""Синхронизация весов Picooc → body_measurements.

API неофициальный, поэтому сервис ведёт себя как «ускоритель»:
любая ошибка пробрасывается наверх, а скриншоты остаются рабочим путём.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.adapters.picooc import PicoocClient, PicoocError
from app.config import get_config
from app.domain import weighin
from app.storage import repo

log = logging.getLogger(__name__)

EXCLUDED_KEYS = {"measured_at_utc", "source", "device_id"}


def is_picooc_owner(user) -> bool:
    """Данные облака Picooc из .env можно писать только в профиль владельца."""
    owner = get_config().picooc_owner_id
    return owner is not None and user["tg_id"] == owner


async def sync_picooc(conn: sqlite3.Connection, user, days: int = 30, *,
                      client: PicoocClient | None = None,
                      now: datetime | None = None) -> dict:
    """Забрать замеры за последние `days` дней и записать в БД.

    Возвращает счётчики для человекочитаемого ответа. Дубли (тот же день и вес)
    обновляются, а не плодятся: дедуп на стороне repo.
    """
    cfg = get_config()
    if client is None and not (cfg.picooc_user and cfg.picooc_password):
        raise PicoocError("не заданы PICOOC_USER и PICOOC_PASSWORD в .env")
    if client is None and not is_picooc_owner(user):
        raise PicoocError("Picooc подключён к аккаунту владельца бота, "
                          "для тебя доступны только скриншоты весов")

    tz = ZoneInfo(user["tz"] or cfg.tz)
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    since = today - timedelta(days=days)

    own_client = client is None
    if client is None:
        client = PicoocClient(cfg.picooc_user, cfg.picooc_password)
    try:
        rows = await client.fetch_measurements()
    finally:
        if own_client:
            await client.close()

    saved = old = empty = 0
    newest: tuple[str, float | None] | None = None
    for row in rows:
        measured = row["measured_at_utc"]
        local_date = measured.astimezone(tz).date()
        if local_date < since:
            old += 1
            continue
        metrics = {k: v for k, v in row.items() if k not in EXCLUDED_KEYS}
        if metrics.get("weight_kg") is None:
            empty += 1                       # без веса замер бесполезен
            continue
        local_time = measured.astimezone(tz).strftime("%H:%M")
        metrics["fasting"] = weighin.classify_fasting(
            local_time, repo.first_meal_time(conn, user["id"], local_date.isoformat()))
        rid = repo.save_body_measurement(
            conn, user["id"], local_date.isoformat(), metrics,
            source="picooc_api", confidence=0.95,
            measured_utc=measured.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if rid is None:
            empty += 1
            continue
        saved += 1
        key = (local_date.isoformat(), metrics.get("weight_kg"))
        if newest is None or key[0] >= newest[0]:
            newest = key

    result = {
        "fetched": len(rows), "saved": saved, "older_than_window": old,
        "without_weight": empty, "window_days": days,
        "newest_date": newest[0] if newest else None,
        "newest_weight": newest[1] if newest else None,
    }
    log.info("picooc sync user=%s: %s", user["id"], result)
    return result
