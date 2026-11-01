"""Разовый перенос локальной БД и фото в Turso: python -m app.dbimport [data/health.db]

Нужны TURSO_DATABASE_URL и TURSO_AUTH_TOKEN в .env. Скрипт создаёт схему в облаке (миграции),
копирует строки всех таблиц и кладёт фото в photo_blobs. В облаке не должно быть пользователей:
иначе скрипт остановится и ничего не перезапишет (справочники из миграций заменяются копией).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from app.config import get_config
from app.db import connect, migrate

SKIP = {"_schema_version", "photo_blobs"}
PHOTO_COLUMNS = (("meals", "photo_path"), ("meal_photos", "path"), ("dishes", "photo_path"))


def import_all(src_path: str, dst) -> dict[str, int]:
    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")
        if r[0] not in SKIP]
    existing = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "users" in existing and dst.execute("SELECT 1 FROM users LIMIT 1").fetchone() \
            and "--resume" not in sys.argv:
        raise SystemExit("В облачной БД уже есть пользователи, перенос остановлен "
                         "(повторить прерванный перенос: --resume).")
    try:                                   # порядок таблиц не обязан совпадать с порядком связей
        dst.execute("PRAGMA foreign_keys=OFF")
    except Exception:  # noqa: BLE001
        pass
    counts: dict[str, int] = {}
    for t in tables:
        if t not in existing:
            continue
        cols = [r[1] for r in src.execute(f'PRAGMA table_info("{t}")')]
        dst_cols = {r[1] for r in dst.execute(f'PRAGMA table_info("{t}")').fetchall()}
        cols = [c for c in cols if c in dst_cols]
        rows = [tuple(r[c] for c in cols) for r in src.execute(f'SELECT * FROM "{t}"')]
        if rows:
            marks = ",".join("?" * len(cols))
            names = ",".join(f'"{c}"' for c in cols)
            with dst:
                dst.executemany(f'INSERT OR REPLACE INTO "{t}" ({names}) VALUES ({marks})', rows)
        counts[t] = len(rows)
    photos = 0
    seen: set[str] = set()
    for table, col in PHOTO_COLUMNS:
        try:
            paths = [r[0] for r in src.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL AND "{col}" != ""')]
        except sqlite3.Error:
            continue
        for p in paths:
            f = Path(p)
            if p in seen or not f.is_file():
                continue
            seen.add(p)
            with dst:
                dst.execute("INSERT OR REPLACE INTO photo_blobs(path, data) VALUES (?, ?)", (p, f.read_bytes()))
            photos += 1
    counts["photo_blobs"] = photos
    src.close()
    return counts


def main() -> int:
    cfg = get_config()
    if not cfg.turso_database_url:
        print("Задай TURSO_DATABASE_URL и TURSO_AUTH_TOKEN в .env.")
        return 1
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = args[0] if args else cfg.db_path
    if not Path(src).is_file():
        print(f"Нет файла {src}")
        return 1
    # Реплика назначения — отдельный файл: рабочую локальную БД libsql трогать не должен
    replica = Path(cfg.db_path).with_name("turso_replica.db")
    for suffix in ("", "-info", "-wal", "-shm"):
        Path(str(replica) + suffix).unlink(missing_ok=True)
    dst = connect(replica, url=cfg.turso_database_url, token=cfg.turso_auth_token)
    migrate(dst)
    for table, n in import_all(src, dst).items():
        print(f"{table}: {n}")
    dst.sync()
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
