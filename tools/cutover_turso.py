"""Take the final Turso snapshot while the bot is stopped and verify it in PostgreSQL.

Without --apply, the import is verified in a transaction and rolled back.
For --apply, choose a local protected --backup file and provide the previous
Supabase meal count as an additional guard. The SUPABASE_ADMIN_DB_URL owner DSN, Turso URL
and Turso token are read from the bot's environment. Run before importing GREEN.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.config import get_config
from app.postgres import connection_options

EXCLUDED = {"photo_blobs", "meal_photos", "pending_inputs", "photo_sessions", "_schema_version", "food_barcodes"}
PROJECT = "uxtrrmosccashitsawub"


def fetch(turso_url: str, token: str, statements: list[str]):
    url = f"https://{urlsplit(turso_url).netloc}/v2/pipeline"
    body = {"requests": [{"type": "execute", "stmt": {"sql": query}}
                         for query in statements] + [{"type": "close"}]}
    response = httpx.post(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=45)
    response.raise_for_status()
    results = response.json()["results"]
    if len(results) != len(statements) + 1:
        raise RuntimeError("Incomplete response from Turso")
    if any(result["type"] != "ok" for result in results):
        index, failed = next((i, r) for i, r in enumerate(results) if r["type"] != "ok")
        raise RuntimeError(f"Turso command {index} failed ({failed['error'].get('code')})")
    return [result["response"]["result"] for result in results[:-1]]


def decode(cell):
    kind = cell["type"]
    if kind == "null":
        return None
    if kind == "text":
        return cell["value"]
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    raise ValueError(f"Unexpected source type: {kind}")


def source_snapshot(turso_url, token):
    schema_query = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    tables_result = fetch(turso_url, token, [schema_query])[0]
    tables = sorted({decode(row[0]) for row in tables_result["rows"]} - EXCLUDED)
    if not {"users", "foods", "meals", "meal_items"} <= set(tables):
        raise RuntimeError("The configured Turso database is missing core bot tables")
    if not all(t.isidentifier() and t.isascii() for t in tables):
        raise RuntimeError("Unexpected table name in Turso")
    data = fetch(turso_url, token, ["BEGIN", *(f'SELECT * FROM "{t}"' for t in tables), "COMMIT"])[1:-1]
    snapshot = {}
    for table, result in zip(tables, data, strict=True):
        names = [c["name"] for c in result["cols"]]
        rows = [tuple(None if key in {"photo_path", "file_path"} else decode(value)
                      for key, value in zip(names, row, strict=True)) for row in result["rows"]]
        snapshot[table] = names, rows
    return snapshot


def copy_order(tables: set[str], references):
    pending = set(tables)
    order = []
    while pending:
        ready = sorted(table for table in pending if not any(
            child == table and parent in pending and parent != table for child, parent in references))
        if not ready:
            raise RuntimeError("Cannot resolve PostgreSQL foreign key order")
        order.extend(ready)
        pending.difference_update(ready)
    return order


def backup_target(cur, target: list[str], file: Path):
    os.umask(0o077)
    with gzip.open(file, "xt", encoding="utf8") as output:
        for table in target:
            cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier("bot", table)))
            json.dump({"table": table, "columns": [c.name for c in cur.description],
                       "rows": cur.fetchall()}, output, default=str, ensure_ascii=False)
            output.write("\n")


def refresh(conn, snapshot, *, apply=False, backup=None, expected_meals=None):
    with conn.transaction(force_rollback=not apply), conn.cursor() as cur:
        cur.execute("SET LOCAL TIME ZONE 'UTC'")
        metadata = cur.execute("""SELECT table_name, column_name, is_identity
            FROM information_schema.columns WHERE table_schema='bot'
            ORDER BY table_name, ordinal_position""").fetchall()
        columns = {}
        identities = set()
        for table, column, identity in metadata:
            columns.setdefault(table, set()).add(column)
            if column == "id" and identity == "YES":
                identities.add(table)
        if set(columns) - set(snapshot) - EXCLUDED or set(snapshot) - set(columns):
            raise RuntimeError("The source and destination tables differ; review the schemas")
        for table, (names, _) in snapshot.items():
            if not set(names) <= columns[table]:
                raise RuntimeError(f"Missing destination columns in {table}")
        tables = sorted(columns)
        identifiers = sql.SQL(", ").join(sql.Identifier("bot", table) for table in tables)
        cur.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(identifiers))
        if cur.execute("SELECT EXISTS (SELECT 1 FROM bot.foods WHERE source='green')").fetchone()[0]:
            raise RuntimeError("Run the final Turso refresh before loading GREEN")
        if expected_meals is not None:
            actual = cur.execute("SELECT count(*) FROM bot.meals").fetchone()[0]
            if actual != expected_meals:
                raise RuntimeError("Supabase already has a different meal count; refusing to replace it")
        if apply and backup:
            backup_target(cur, tables, backup)
        cur.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(identifiers))
        refs = cur.execute("""SELECT child.relname, parent.relname FROM pg_constraint fk
             JOIN pg_class child ON child.oid=fk.conrelid JOIN pg_class parent ON parent.oid=fk.confrelid
             JOIN pg_namespace n ON n.oid=child.relnamespace
             WHERE fk.contype='f' AND n.nspname='bot'""").fetchall()
        for table in copy_order(set(snapshot), refs):
            names, rows = snapshot[table]
            if not rows:
                continue
            fields = sql.SQL(", ").join(map(sql.Identifier, names))
            dest = sql.Identifier("bot", table)
            with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(dest, fields)) as bulk:
                for row in rows:
                    bulk.write_row(row)
            if table in identities:
                next_id = 1 + max(row[names.index("id")] for row in rows)
                cur.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN id RESTART WITH {}")
                            .format(dest, sql.Literal(next_id)))
        for table, (_, rows) in snapshot.items():
            actual = cur.execute(sql.SQL("SELECT count(*) FROM {}")
                                 .format(sql.Identifier("bot", table))).fetchone()[0]
            if actual != len(rows):
                raise RuntimeError(f"Verification failed for {table}")
        names, rows = snapshot["meals"]
        fields = ("kcal_total", "protein_g", "fat_g", "carbs_g")
        expected = [sum(row[names.index(field)] for row in rows) for field in fields]
        actual = cur.execute("""SELECT coalesce(sum(kcal_total),0),coalesce(sum(protein_g),0),
            coalesce(sum(fat_g),0),coalesce(sum(carbs_g),0) FROM bot.meals""").fetchone()
        if any(abs(a - b) > 0.000001 for a, b in zip(expected, actual, strict=True)):
            raise RuntimeError("Verification failed for meal nutrition totals")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--expect-target-meals", type=int)
    args = parser.parse_args()
    if args.apply and (args.expect_target_meals is None or not args.backup or args.backup.exists()):
        parser.error("--apply requires --expect-target-meals and a new --backup path")

    cfg = get_config()
    admin_url = cfg.supabase_admin_db_url or cfg.supabase_db_url
    if not (admin_url and cfg.turso_database_url and cfg.turso_auth_token):
        parser.error("SUPABASE_ADMIN_DB_URL, TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required")
    url = admin_url.get_secret_value()
    info = conninfo_to_dict(url)
    host = info.get("host", "")
    local = host in {"localhost", "127.0.0.1", "::1"}
    if not local and not (host == f"db.{PROJECT}.supabase.co" or
                          host.endswith(".pooler.supabase.com") and info.get("user", "").endswith(f".{PROJECT}")):
        parser.error("The Supabase URL must point to weight-loss-bot")
    snapshot = source_snapshot(cfg.turso_database_url, cfg.turso_auth_token)
    with psycopg.connect(url, **connection_options(url)) as conn:
        refresh(conn, snapshot, apply=args.apply, backup=args.backup, expected_meals=args.expect_target_meals)
    for name, (_, rows) in snapshot.items():
        if rows:
            print(f"{name}: {len(rows)}")
    print("Перенос сохранён" if args.apply else "Проверено, транзакция откачена")


if __name__ == "__main__":
    main()
