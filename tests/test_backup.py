"""Бэкап: копия БД проходит проверку восстановления, фото копируются инкрементально."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import backup
from app.db import connect, migrate
from app.storage import repo


def _make_db(path: Path):
    conn = connect(str(path))
    migrate(conn)
    uid = repo.create_user(conn, 1)
    repo.add_water(conn, uid, "2026-10-30", "10:00", 250, "text")
    conn.close()


def test_backup_roundtrip_and_verification(tmp_path):
    db = tmp_path / "health.db"
    _make_db(db)
    dest = backup.backup_db(str(db), str(tmp_path / "bk"))
    assert backup.verify_restore(dest)
    restored = sqlite3.connect(dest)
    assert restored.execute("SELECT SUM(ml) FROM water_log").fetchone()[0] == 250
    restored.close()


def test_verify_rejects_incomplete_or_corrupt_backup(tmp_path):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE users (id INTEGER)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    assert not backup.verify_restore(old)
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database" * 100)
    assert not backup.verify_restore(garbage)


def test_rotation_keeps_seven(tmp_path):
    # Исходная БД может лежать в той же папке и подходить под шаблон имён копий.
    db = tmp_path / "health_00000000.db"
    _make_db(db)
    out = tmp_path
    for i in range(10):
        (out / f"health_2026010{i % 10}_000000.db").write_bytes(b"x")
    backup.backup_db(str(db), str(out))
    assert db.exists()
    assert len(set(out.glob("health_*.db")) - {db}) == 7


def test_photos_copied_incrementally(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"123")
    (photos / "empty.jpg").write_bytes(b"")          # битый нулевой файл не копируем
    assert backup.backup_photos(str(photos), str(tmp_path / "bk")) == 1
    assert backup.backup_photos(str(photos), str(tmp_path / "bk")) == 0   # повтор ничего не копирует
    (photos / "dishes").mkdir()
    (photos / "dishes" / "a.jpg").write_bytes(b"plate")
    assert backup.backup_photos(str(photos), str(tmp_path / "bk")) == 1
    saved = tmp_path / "bk" / "photos"
    assert (saved / "a.jpg").read_bytes() == b"123"
    assert (saved / "dishes" / "a.jpg").read_bytes() == b"plate"
    (saved / "dishes" / "a.jpg").write_bytes(b"p")
    assert backup.backup_photos(str(photos), str(tmp_path / "bk")) == 1
    assert (saved / "dishes" / "a.jpg").read_bytes() == b"plate"


def test_missing_db_cannot_create_an_empty_source_or_rotate_backups(tmp_path):
    db = tmp_path / "missing.db"
    out = tmp_path / "bk"
    out.mkdir()
    old = [out / f"health_2026010{i}_000000.db" for i in range(1, 8)]
    for path in old:
        path.touch()
    with pytest.raises((FileNotFoundError, sqlite3.OperationalError)):
        backup.backup_db(str(db), str(out))
    assert not db.exists()
    assert sorted(out.iterdir()) == old


def test_failed_restore_check_preserves_previous_snapshots(tmp_path):
    db = tmp_path / "health.db"
    db.touch()  # SQLite открывается, схема дневника отсутствует.
    out = tmp_path / "bk"
    out.mkdir()
    old = [out / f"health_2026010{i}_000000.db" for i in range(1, 8)]
    for path in old:
        path.touch()
    failed = backup.backup_db(str(db), str(out))
    assert not backup.verify_restore(failed)
    assert sorted(out.glob("health_*.db")) == old
