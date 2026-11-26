"""PostgreSQL connection for the bot's shared synchronous repository."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.adapt import Dumper
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import set_json_loads

from app.db import Row

_TOKEN = re.compile(r"""'(?:''|[^'])*'|"(?:""|[^"])*"|\?""")
_INSERT = re.compile(r"^\s*INSERT\s+(?:OR IGNORE\s+)?INTO\s+(\w+)", re.I)
_SCHEMA_VERSION = 41


def connection_options(url: str) -> dict:
    params = conninfo_to_dict(url)
    host = params.get("host", "")
    if host in {"localhost", "127.0.0.1", "::1"}:
        return {"sslmode": params.get("sslmode", "require")}
    default_ca = "system"
    if host.endswith((".pooler.supabase.com", ".supabase.co")):
        default_ca = str(Path(__file__).resolve().parents[1] / "supabase" / "prod-ca-2021.crt")
    return {"sslmode": "verify-full", "sslrootcert": params.get("sslrootcert", default_ca)}


def _bind(sql, params):
    if params:
        return _TOKEN.sub(lambda m: "%s" if m[0] == "?" else m[0], sql.replace("%", "%%"))
    return sql


class _FlagDumper(Dumper):
    # The shared schema stores flags as integers, including values from SQLite.
    def dump(self, value):
        return b"1" if value else b"0"


def _value(value, column):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    # The bot's domain and API already use ISO strings, including UTC timestamps
    # without an offset and HH:MM wall times.
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="minutes" if column == "last_sent_at" else "seconds")
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _row_factory(cursor):
    names = [col.name for col in cursor.description or ()]
    cols = {name: i for i, name in enumerate(names)}
    return lambda values: Row(cols, tuple(_value(value, name) for name, value in zip(names, values)))


class _Result:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount
        self.description = cursor.description

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class PostgresConnection:
    postgres = True

    def __init__(self, url: str):
        self._url = url
        self._raw = None
        self._depth = 0
        self._connect()
        rows = self._raw.execute(
            """SELECT table_name FROM information_schema.columns
               WHERE table_schema='bot' AND column_name='id' AND is_identity='YES'"""
        ).fetchall()
        self._identity_tables = {row[0] for row in rows}
        if not {"users", "meals", "foods", "meal_items"} <= self._identity_tables:
            self.close()
            raise RuntimeError("Initialize the private bot schema before starting PostgreSQL mode")

    def _connect(self):
        self._raw = psycopg.connect(
            self._url, autocommit=True, cursor_factory=psycopg.ClientCursor,
            row_factory=_row_factory, connect_timeout=8, **connection_options(self._url),
        )
        # Set the search path through SQL so it also works through Session Pooler.
        self._raw.execute("SET search_path TO bot, pg_catalog")
        self._raw.execute("SET TIME ZONE 'UTC'")
        self._raw.adapters.register_dumper(bool, _FlagDumper)
        set_json_loads(lambda data: data.decode() if isinstance(data, bytes) else data, self._raw)

    @property
    def schema_version(self):
        return _SCHEMA_VERSION

    @property
    def in_transaction(self):
        return bool(self._depth)

    def spawn(self):
        """The scheduler's worker thread needs its own database session."""
        return PostgresConnection(self._url)

    def execute(self, sql: str, params=()):
        if self._raw.closed and not self._depth:
            self._connect()
        sql = sql.strip().rstrip(";")
        insert = _INSERT.match(sql)
        identity_insert = bool(insert and insert[1].lower() in self._identity_tables)
        if insert and "INSERT OR IGNORE" in insert[0].upper():
            sql = re.sub(r"^INSERT OR IGNORE", "INSERT", sql, flags=re.I) + " ON CONFLICT DO NOTHING"
        if identity_insert:
            sql += " RETURNING id"
        # Shared repository SQL uses these SQLite spellings in a few queries.
        sql = re.sub(r"\bMAX\(0,", "GREATEST(0,", sql)
        sql = re.sub(r"\binstr\(", "strpos(", sql)
        sql = sql.replace(" IS NOT ?", " IS DISTINCT FROM ?")
        sql = _bind(sql, params)
        try:
            cursor = self._raw.execute(sql, tuple(params) if params else None)
        except psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        lastrowid = cursor.fetchone() if identity_insert else None
        return _Result(cursor, lastrowid[0] if lastrowid is not None else None)

    def read_batch(self, statements):
        """Send independent, parameterized SELECTs in one round trip."""
        if self._raw.closed and not self._depth:
            self._connect()
        with self._raw.cursor() as cursor:
            queries = [cursor.mogrify(_bind(sql, params), tuple(params) or None)
                       for sql, params in statements]
            cursor.execute(";\n".join(queries))
            results = [cursor.fetchall()]
            while cursor.nextset():
                results.append(cursor.fetchall())
        if len(results) != len(statements):
            raise RuntimeError("Incorrect number of report result sets")
        return results

    @contextmanager
    def transaction(self):
        if self._raw.closed and not self._depth:
            self._connect()
        with self._raw.transaction():
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1

    def commit(self):
        # Transactions are scoped by transaction(), including legacy helpers
        # that call commit() while composing several repository operations.
        pass

    def rollback(self):
        if self._depth:
            raise RuntimeError("Raise an error to roll back the PostgreSQL transaction")

    def close(self):
        self._raw.close()
