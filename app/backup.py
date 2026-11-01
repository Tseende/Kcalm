"""Бэкап: БД (VACUUM INTO) + фото, ротация 7 копий БД. Запуск: python -m app.backup

Бэкап не шифруется: он содержит данные о здоровье. Храни папку на диске с шифрованием
(FileVault/BitLocker) и, лучше, на другом носителе (BACKUP_DIR в .env).
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.config import get_config
from app.db import MIGRATIONS_DIR

log = logging.getLogger(__name__)

ROTATE = 7
REQUIRED_TABLES = ("users", "meals", "meal_items", "body_measurements", "water_log",
                   "craving_events")


def backup_db(db_path: str, backup_dir: str = "./data/backups") -> Path:
    src = Path(db_path)
    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"health_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    conn = sqlite3.connect(src)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    copies = sorted(out_dir.glob("health_*.db"))
    for old in copies[:-ROTATE]:
        old.unlink()
    return dest


def backup_photos(photos_dir: str, backup_dir: str = "./data/backups") -> int:
    """Скопировать новые фото (имя файла = хэш содержимого, поэтому достаточно «нет такого»).
    Возвращает число скопированных файлов."""
    src = Path(photos_dir)
    if not src.is_dir():
        return 0
    dest = Path(backup_dir) / "photos"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for photo in src.glob("*.jpg"):
        target = dest / photo.name
        if photo.stat().st_size and (not target.exists() or target.stat().st_size == 0):
            shutil.copy2(photo, target)
            copied += 1
    return copied


def latest_schema_version() -> int:
    return max(int(f.name.split("_", 1)[0]) for f in Path(MIGRATIONS_DIR).glob("*.sql"))


def verify_restore(dest: Path) -> bool:
    """Бэкап проверяется как восстановленный: целостность, версия схемы, нужные таблицы."""
    conn = sqlite3.connect(dest)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return False
        if conn.execute("PRAGMA user_version").fetchone()[0] < latest_schema_version():
            return False
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return all(t in names for t in REQUIRED_TABLES)
    finally:
        conn.close()


def run_backup() -> tuple[Path, bool, int]:
    """Полный цикл: копия БД, проверка, фото. Возвращает (файл, проверка_ок, фото)."""
    cfg = get_config()
    path = backup_db(cfg.db_path, cfg.backup_dir)
    ok = verify_restore(path)
    if not ok:
        log.error("бэкап %s не прошёл проверку восстановления", path)
    photos = backup_photos(cfg.photos_dir, cfg.backup_dir)
    return path, ok, photos


if __name__ == "__main__":
    path, ok, photos = run_backup()
    print(f"backup: {path} restore_check={'ok' if ok else 'FAIL'} photos_copied={photos}")
    sys.exit(0 if ok else 1)
