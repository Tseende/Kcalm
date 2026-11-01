"""Фото на диске + копия в БД (только в облачном режиме).

Render теряет диск при каждом рестарте, а остальной код читает фото по пути из БД. Поэтому
каждый записанный файл дублируется в photo_blobs, а при старте `restore()` возвращает на диск
всё, чего там нет. Локально (`enable` не вызывали) работает как обычная запись файла.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)
_db = None


def enable(db) -> None:
    global _db
    _db = db


def write(path: str | Path, data: bytes) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if _db is not None:
        try:
            with _db:
                _db.execute("INSERT OR REPLACE INTO photo_blobs(path, data) VALUES (?, ?)",
                            (str(p), data))
        except Exception:  # noqa: BLE001 — фото сохранено на диск, сбой копии не должен ронять приём
            log.warning("копия фото в БД не записалась: %s", p, exc_info=True)
    return str(p)


def restore(db) -> int:
    """Вернуть на диск фото из photo_blobs, которых там нет. Возвращает число файлов."""
    n = 0
    for row in db.execute("SELECT path FROM photo_blobs").fetchall():
        p = Path(row[0])
        if p.exists():
            continue
        blob = db.execute("SELECT data FROM photo_blobs WHERE path = ?", (row[0],)).fetchone()
        if blob is None:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bytes(blob[0]))
        n += 1
    return n
