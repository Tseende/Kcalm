"""Бэкап: копия БД проходит проверку восстановления, фото копируются инкрементально."""
from __future__ import annotations

import sqlite3
from pathlib import Path

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
    try:
        assert not backup.verify_restore(garbage)
    except sqlite3.DatabaseError:
        pass                                           # тоже «не прошёл»


def test_rotation_keeps_seven(tmp_path):
    db = tmp_path / "health.db"
    _make_db(db)
    out = tmp_path / "bk"
    out.mkdir()
    for i in range(10):
        (out / f"health_2026010{i % 10}_000000.db").write_bytes(b"x")
    backup.backup_db(str(db), str(out))
    assert len(list(out.glob("health_*.db"))) == 7


def test_photos_copied_incrementally(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"123")
    (photos / "empty.jpg").write_bytes(b"")          # битый нулевой файл не копируем
    assert backup.backup_photos(str(photos), str(tmp_path / "bk")) == 1
    assert backup.backup_photos(str(photos), str(tmp_path / "bk")) == 0   # повтор ничего не копирует
    (photos / "b.jpg").write_bytes(b"456")
    assert backup.backup_photos(str(photos), str(tmp_path / "bk")) == 1
