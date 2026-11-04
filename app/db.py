"""SQLite (WAL) без ORM. Миграции — SQL-файлы, версия в PRAGMA user_version.

Облачный режим (Render): задай TURSO_DATABASE_URL и TURSO_AUTH_TOKEN, и `connect()` вернёт
встроенную реплику Turso (libSQL): чтение с локального файла, запись уходит в облако. Диск
Render временный, поэтому настоящая БД живёт в Turso, а локальный файл пересобирается при старте.
Без этих переменных всё работает на обычном локальном SQLite, как раньше.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
log = logging.getLogger(__name__)


class Row:
    """Как sqlite3.Row: доступ по номеру и по имени колонки, keys(), dict(row)."""

    __slots__ = ("_cols", "_vals")

    def __init__(self, cols: dict[str, int], vals: tuple) -> None:
        self._cols = cols
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._vals[self._cols[key]]
            except KeyError:
                raise IndexError("No item with that key") from None
        return self._vals[key]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self) -> int:
        return len(self._vals)

    def __eq__(self, other) -> bool:
        return self._vals == (other._vals if isinstance(other, Row) else other)

    def __hash__(self) -> int:
        return hash(self._vals)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._cols, self._vals))})"

    def keys(self) -> list[str]:
        return list(self._cols)


class _Cursor:
    def __init__(self, cur) -> None:
        self._cur = cur
        desc = cur.description or ()
        self._cols = {d[0]: i for i, d in enumerate(desc)}

    def _wrap(self, vals):
        return None if vals is None else Row(self._cols, tuple(vals))

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    def fetchmany(self, size: int = 1):
        return [self._wrap(r) for r in self._cur.fetchmany(size)]

    def __iter__(self):
        return iter(self.fetchall())


class LibsqlConnection:
    """Обёртка над libsql, совместимая с тем, как приложение пользуется sqlite3.Connection."""

    cloud = True

    def __init__(self, raw, reopen=None) -> None:
        self._raw = raw
        self._reopen = reopen      # фабрика нового соединения: Turso рвёт «простоявший» поток

    # Сбои потока Hrana: запрос не выполнен, можно открыть соединение заново и повторить.
    _STALE = ("stream was idle", "rolled back", "connection closed", "stream error", "stream not found", "expired")

    def _is_stale(self, exc: Exception) -> bool:
        return isinstance(exc, ValueError) and "hrana" in str(exc).lower() \
            and any(m in str(exc).lower() for m in self._STALE)

    def _reconnect(self) -> None:
        old = self._raw
        self._raw = self._reopen()
        try:
            old.close()
        except Exception:  # noqa: BLE001 — старый поток уже мёртв
            log.debug("close stale libsql", exc_info=True)
        log.warning("Turso: соединение пересоздано после обрыва потока")

    def execute(self, sql: str, params=()):
        args = params if isinstance(params, dict) else tuple(params)
        try:
            return _Cursor(self._raw.execute(sql, args))
        except Exception as e:  # noqa: BLE001
            # Чтение повторяем всегда; запись — только вне открытой транзакции, чтобы не продублировать часть её.
            read = sql.lstrip()[:6].upper() in ("SELECT", "PRAGMA")
            if not self._reopen or not self._is_stale(e) or (not read and self.in_transaction_safe()):
                raise
            self._reconnect()
            return _Cursor(self._raw.execute(sql, args))

    def spawn(self) -> "LibsqlConnection":
        """Отдельное соединение для фонового потока: общее соединение между потоками не делим."""
        raw = self._reopen()
        try:
            raw.sync()                      # подтянуть свежие данные реплики
        except Exception:  # noqa: BLE001 — локальный режим без реплики
            log.debug("spawn: sync не нужен", exc_info=True)
        return LibsqlConnection(raw, self._reopen)

    def in_transaction_safe(self) -> bool:
        try:
            return bool(self._raw.in_transaction)
        except Exception:  # noqa: BLE001 — поток мёртв, транзакции нет
            return False

    def executemany(self, sql: str, seq):
        return _Cursor(self._raw.executemany(sql, [tuple(p) for p in seq]))

    def executescript(self, script: str):
        self._raw.executescript(script)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def sync(self) -> None:
        self._raw.sync()

    @property
    def in_transaction(self) -> bool:
        return bool(self._raw.in_transaction)

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:  # noqa: BLE001 — закрытие при остановке не должно падать
            log.debug("close libsql", exc_info=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


def connect(db_path: str | Path, *, url: str = "", token: str = ""):
    """Локальный SQLite или, если задан url Turso, встроенная реплика libSQL."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if url or os.environ.get("HEALTHBOT_TEST_LIBSQL"):   # второе: прогон тестов на libsql без облака
        import libsql

        if url:
            def reopen():
                return libsql.connect(str(p), sync_url=url, auth_token=token)
            raw = reopen()
            raw.sync()                               # подтянуть актуальную БД из облака
        else:
            def reopen():
                return libsql.connect(str(p))
            raw = reopen()
        conn = LibsqlConnection(raw, reopen)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:  # noqa: BLE001
            log.warning("foreign_keys=ON не применился", exc_info=True)
        return conn
    conn = sqlite3.connect(p, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def connect_from_config(cfg):
    return connect(cfg.db_path, url=cfg.turso_database_url, token=cfg.turso_auth_token)


def user_version(conn) -> int:
    if isinstance(conn, LibsqlConnection):
        # В облаке версия лежит в таблице: PRAGMA user_version на удалённой БД ненадёжна.
        conn.execute("CREATE TABLE IF NOT EXISTS _schema_version "
                     "(id INTEGER PRIMARY KEY CHECK (id = 1), v INTEGER NOT NULL)")
        row = conn.execute("SELECT v FROM _schema_version WHERE id = 1").fetchone()
        return int(row[0]) if row else 0
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_version(conn, version: int) -> None:
    if isinstance(conn, LibsqlConnection):
        conn.execute("INSERT INTO _schema_version (id, v) VALUES (1, ?) "
                     "ON CONFLICT(id) DO UPDATE SET v = excluded.v", (version,))
    else:
        conn.execute(f"PRAGMA user_version = {version}")


def migrate(conn, migrations_dir: Path = MIGRATIONS_DIR) -> int:
    """Идемпотентно: применяет только ещё не применённые миграции по порядку.

    Возвращает номер версии после миграции. Каждая миграция — отдельный файл
    NNN_name.sql, версия = числовой префикс имени.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied = user_version(conn)
    for f in files:
        version = int(f.name.split("_", 1)[0])
        if version <= applied:
            continue
        with conn:
            conn.executescript(f.read_text(encoding="utf-8"))
            _set_version(conn, version)
    return user_version(conn)
