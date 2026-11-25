"""Фото на диске с облачной копией и восстановлением по запросу."""
from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)
_db = None
_root: Path | None = None
_TEMP_RETENTION_SECONDS = 30 * 60


def enable(db, root: str | Path | None = None, *, temporary: bool = False) -> None:
    global _db, _root
    _db = db
    _root = Path(root).resolve() if root is not None else None
    if temporary:
        prune_temporary()


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


def ensure(path: str | Path) -> Path:
    """Восстановить один файл из БД, если локальная копия потеряна."""
    p = Path(path)
    if p.is_file() or _db is None:
        return p
    if _root is not None and _root not in p.resolve().parents:
        return p
    try:
        _restore_file(_db, p)
    except Exception:  # noqa: BLE001 — вызывающий код сохранит обычную обработку отсутствующего файла
        log.warning("не удалось восстановить фото: %s", p, exc_info=True)
    return p


def read(path: str | Path) -> bytes:
    return ensure(path).read_bytes()


def _restore_file(db, path: Path) -> bool:
    blob = db.execute("SELECT data FROM photo_blobs WHERE path = ?", (str(path),)).fetchone()
    if blob is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(blob[0]))
    return True


def restore(db) -> int:
    """Вернуть на диск фото из photo_blobs, которых там нет. Возвращает число файлов."""
    n = 0
    for row in db.execute("SELECT path FROM photo_blobs").fetchall():
        p = Path(row[0])
        if p.exists():
            continue
        n += _restore_file(db, p)
    return n


def prune_temporary() -> None:
    """Keep meal uploads only for the short window for album and caption edits."""
    if _root is None:
        return
    cutoff = time.time() - _TEMP_RETENTION_SECONDS
    for path in _root.glob("*.jpg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            log.warning("Could not remove an expired upload", exc_info=True)
