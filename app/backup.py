"""Бэкап: БД (VACUUM INTO) + фото, ротация 7 копий БД. Запуск: python -m app.backup

Бэкап не шифруется: он содержит данные о здоровье. Храни папку на диске с шифрованием
(FileVault/BitLocker) и, лучше, на другом носителе (BACKUP_DIR в .env).
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app.config import get_config
from app.db import MIGRATIONS_DIR

log = logging.getLogger(__name__)

ROTATE = 7
REQUIRED_TABLES = ("users", "meals", "meal_items", "body_measurements", "water_log",
                   "craving_events")


def backup_db(db_path: str, backup_dir: str = "./data/backups") -> Path:
    path, _ = _backup_db(Path(db_path), Path(backup_dir))
    return path


def _backup_db(src: Path, out_dir: Path) -> tuple[Path, bool]:
    src = src.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as staging:
        snapshot = Path(staging) / "snapshot.db"
        with closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("VACUUM INTO ?", (str(snapshot),))
        ok = verify_restore(snapshot)
        prefix = "health" if ok else "failed-health"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = out_dir / f"{prefix}_{stamp}_{Path(staging).name}.db"
        snapshot.replace(dest)
    copies = sorted(p for p in out_dir.glob(f"{prefix}_*.db") if p.resolve() != src)
    keep = ROTATE if ok else 1
    for old in copies[:-keep]:
        old.unlink(missing_ok=True)
    return dest, ok


def backup_photos(photos_dir: str, backup_dir: str = "./data/backups") -> int:
    """Скопировать фото еды и посуды, если копия отсутствует или отличается размером.
    Возвращает число скопированных файлов."""
    src = Path(photos_dir)
    if not src.is_dir():
        return 0
    dest = Path(backup_dir) / "photos"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for folder in (src, src / "dishes"):
        for photo in folder.glob("*.jpg"):
            target = dest / photo.relative_to(src)
            size = photo.stat().st_size
            if not size or (target.exists() and target.stat().st_size == size):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as staging:
                staged = Path(staging) / photo.name
                shutil.copy2(photo, staged)
                staged.replace(target)
            copied += 1
    return copied


def latest_schema_version() -> int:
    return max(int(f.name.split("_", 1)[0]) for f in Path(MIGRATIONS_DIR).glob("*.sql"))


def verify_restore(dest: Path) -> bool:
    """Бэкап проверяется как восстановленный: целостность, версия схемы, нужные таблицы."""
    if not dest.is_file():
        return False
    try:
        with closing(sqlite3.connect(dest.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False
            if conn.execute("PRAGMA user_version").fetchone()[0] < latest_schema_version():
                return False
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            return all(t in names for t in REQUIRED_TABLES)
    except sqlite3.DatabaseError:
        return False


def run_backup() -> tuple[Path, bool, int]:
    """Полный цикл: копия БД, проверка, фото. Возвращает (файл, проверка_ок, фото)."""
    cfg = get_config()
    path, ok = _backup_db(Path(cfg.db_path), Path(cfg.backup_dir))
    if not ok:
        log.error("бэкап %s не прошёл проверку восстановления", path)
    photos = backup_photos(cfg.photos_dir, cfg.backup_dir)
    return path, ok, photos


if __name__ == "__main__":
    path, ok, photos = run_backup()
    print(f"backup: {path} restore_check={'ok' if ok else 'FAIL'} photos_copied={photos}")
    sys.exit(0 if ok else 1)
