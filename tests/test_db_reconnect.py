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
