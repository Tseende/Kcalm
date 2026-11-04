"""LibsqlConnection переживает обрыв потока Turso: переоткрывает соединение и повторяет запрос."""
import pytest

from app.db import LibsqlConnection


class FakeRaw:
    def __init__(self, fail_times=0, msg="Hrana: `stream error: interactive transaction was rolled back because the stream was idle for too long`", in_tx=False):
        self.fail_times, self.msg, self.in_transaction, self.closed, self.calls = fail_times, msg, in_tx, False, 0

    def execute(self, sql, params=()):
        self.calls += 1
        if self.fail_times:
            self.fail_times -= 1
            raise ValueError(self.msg)
        return type("C", (), {"description": (("a",),), "fetchall": lambda s: [(1,)], "lastrowid": 1, "rowcount": 1})()

    def close(self):
        self.closed = True


def test_read_retried_on_new_connection():
    bad, good = FakeRaw(fail_times=1), FakeRaw()
    conn = LibsqlConnection(bad, lambda: good)
    assert conn.execute("SELECT 1").fetchall()[0][0] == 1
    assert bad.closed and conn._raw is good


def test_write_retried_outside_transaction():
    bad, good = FakeRaw(fail_times=1), FakeRaw()
    conn = LibsqlConnection(bad, lambda: good)
    conn.execute("INSERT INTO t VALUES (1)")
    assert good.calls == 1


def test_write_inside_transaction_not_retried():
    bad = FakeRaw(fail_times=1, in_tx=True)
    conn = LibsqlConnection(bad, lambda: FakeRaw())
    with pytest.raises(ValueError):
        conn.execute("INSERT INTO t VALUES (1)")


def test_other_errors_propagate():
    bad = FakeRaw(fail_times=1, msg="no such table: x")
    conn = LibsqlConnection(bad, lambda: FakeRaw())
    with pytest.raises(ValueError):
        conn.execute("SELECT * FROM x")


class TxRaw(FakeRaw):
    """Как libsql: запись открывает транзакцию, commit её закрывает; поток можно «убить»."""

    def __init__(self, dead=False):
        super().__init__()
        self.dead, self.commits = dead, 0

    def execute(self, sql, params=()):
        if self.dead:
            raise ValueError('Hrana: `api error: `status=404 Not Found, body={"error":"stream not found: x"}``')
        if not sql.lstrip().upper().startswith(("SELECT", "PRAGMA")):
            self.in_transaction = True
        return super().execute(sql, params)

    def commit(self):
        if self.dead:
            raise ValueError('Hrana: `api error: `status=404 Not Found, body={"error":"stream not found: x"}``')
        self.commits += 1
        self.in_transaction = False


def _age(conn, sec=60):
    conn._last -= sec


def test_orphan_transaction_committed_before_next_write():
    raw = TxRaw()
    conn = LibsqlConnection(raw, lambda: TxRaw())
    conn.execute("UPDATE t SET a=1 WHERE 0")          # записал и не закоммитил
    _age(conn)
    conn.execute("INSERT INTO t VALUES (1)")
    assert raw.commits == 1 and conn._raw is raw      # старая транзакция закрыта, запись прошла


def test_orphan_transaction_on_dead_stream_does_not_break_write():
    """Было на Render: поток с забытой транзакцией умер, и запись еды падала с ошибкой."""
    raw, fresh = TxRaw(), TxRaw()
    conn = LibsqlConnection(raw, lambda: fresh)
    conn.execute("UPDATE t SET a=1 WHERE 0")
    raw.dead = True
    _age(conn)
    conn.execute("INSERT INTO meals VALUES (1)")
    assert conn._raw is fresh and fresh.calls == 1


def test_recent_transaction_left_alone():
    raw = TxRaw()
    conn = LibsqlConnection(raw, lambda: TxRaw())
    conn.execute("INSERT INTO t VALUES (1)")
    conn.execute("INSERT INTO t VALUES (2)")          # та же транзакция, без паузы
    assert raw.commits == 0


def test_explicit_with_block_not_committed_midway():
    raw = TxRaw()
    conn = LibsqlConnection(raw, lambda: TxRaw())
    with conn:
        conn.execute("INSERT INTO t VALUES (1)")
        _age(conn)
        conn.execute("INSERT INTO t VALUES (2)")
        assert raw.commits == 0
    assert raw.commits == 1
