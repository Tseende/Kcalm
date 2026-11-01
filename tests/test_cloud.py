"""Облачный режим: обёртка libsql, копии фото в БД, webhook."""
from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

libsql = pytest.importorskip("libsql")

from app import db as dbmod  # noqa: E402
from app.config import Config  # noqa: E402
from app.storage import photos  # noqa: E402


@pytest.fixture
def cloud_db(tmp_path):
    conn = dbmod.LibsqlConnection(libsql.connect(str(tmp_path / "c.db")))
    dbmod.migrate(conn)
    yield conn
    conn.close()


def test_migrate_and_version_in_table(cloud_db):
    latest = max(int(f.name.split("_", 1)[0]) for f in dbmod.MIGRATIONS_DIR.glob("*.sql"))
    assert dbmod.user_version(cloud_db) == latest
    assert dbmod.migrate(cloud_db) == latest          # повторный запуск ничего не ломает


def test_row_behaves_like_sqlite_row(cloud_db):
    cloud_db.execute("CREATE TABLE t(a INTEGER, b TEXT)")
    cur = cloud_db.execute("INSERT INTO t(a, b) VALUES (?, ?)", (1, "x"))
    assert cur.lastrowid == 1
    row = cloud_db.execute("SELECT a, b FROM t").fetchone()
    assert row["a"] == 1 and row[1] == "x" and row.keys() == ["a", "b"]
    assert dict(row) == {"a": 1, "b": "x"}
    a, b = row
    assert (a, b) == (1, "x")
    with pytest.raises(IndexError):
        row["nope"]
    assert cloud_db.execute("SELECT * FROM t WHERE a = 99").fetchone() is None


def test_with_block_rolls_back_on_error(cloud_db):
    cloud_db.execute("CREATE TABLE t(a INTEGER)")
    cloud_db.commit()
    with pytest.raises(RuntimeError):
        with cloud_db:
            cloud_db.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError
    assert cloud_db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_photo_copy_and_restore(cloud_db, tmp_path):
    photos.enable(cloud_db)
    try:
        path = tmp_path / "ph" / "a.jpg"
        photos.write(path, b"\xff\xd8jpegdata")
        path.unlink()
        assert photos.restore(cloud_db) == 1
        assert path.read_bytes() == b"\xff\xd8jpegdata"
        assert photos.restore(cloud_db) == 0          # файл уже на месте
    finally:
        photos.enable(None)


def test_photo_write_without_cloud_is_plain_file(tmp_path):
    photos.enable(None)
    photos.write(tmp_path / "x.jpg", b"1")
    assert (tmp_path / "x.jpg").read_bytes() == b"1"


def test_config_cloud_settings(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    cfg = Config(_env_file=None)
    assert cfg.web_port == 10000
    assert cfg.turso_database_url == "" and cfg.webhook_base_url == ""
    secret = cfg.webhook_secret_value
    assert secret == Config(_env_file=None).webhook_secret_value and len(secret) <= 256
    assert "123:abc" not in secret


@pytest.mark.asyncio
async def test_webhook_route_checks_secret(tmp_path, monkeypatch):
    from aiogram import Bot, Dispatcher
    from app.web.server import WEBHOOK_PATH, build_app

    cfg = Config(_env_file=None, telegram_bot_token="123456:TESTTOKEN", webhook_base_url="https://x.onrender.com")
    db = dbmod.connect(tmp_path / "w.db")
    dbmod.migrate(db)
    bot = Bot("123456:TESTTOKEN")
    app = build_app(db, bot, cfg, Dispatcher())
    async with TestClient(TestServer(app)) as client:
        bad = await client.post(WEBHOOK_PATH, json={"update_id": 1})
        assert bad.status == 401                       # без секрета апдейт не принимается
        ok = await client.post(WEBHOOK_PATH, json={"update_id": 1},
                               headers={"X-Telegram-Bot-Api-Secret-Token": cfg.webhook_secret_value})
        assert ok.status == 200
        assert (await client.get("/healthz")).status == 200
    await bot.session.close()
    db.close()


def test_dbimport_copies_rows_and_photos(tmp_path, monkeypatch):
    from app import dbimport

    monkeypatch.chdir(tmp_path)
    src = dbmod.connect(tmp_path / "src.db")
    dbmod.migrate(src)
    src.execute("INSERT INTO users(tg_id) VALUES (42)")
    (tmp_path / "p.jpg").write_bytes(b"img")
    uid = src.execute("SELECT id FROM users").fetchone()[0]
    src.execute("INSERT INTO meals(user_id, eaten_at_utc, local_date, local_time, meal_type, source, photo_path) VALUES (?, '2026-11-01 09:00', '2026-11-01', '12:00', 'lunch', 'photo', 'p.jpg')", (uid,))
    src.commit()
    src.close()
    dst = dbmod.LibsqlConnection(libsql.connect(str(tmp_path / "dst.db")))
    dbmod.migrate(dst)
    counts = dbimport.import_all(str(tmp_path / "src.db"), dst)
    assert counts["users"] == 1 and counts["meals"] == 1 and counts["photo_blobs"] == 1
    assert dst.execute("SELECT data FROM photo_blobs WHERE path='p.jpg'").fetchone()[0] == b"img"
    with pytest.raises(SystemExit):
        dbimport.import_all(str(tmp_path / "src.db"), dst)       # повторно поверх данных нельзя
    dst.close()
