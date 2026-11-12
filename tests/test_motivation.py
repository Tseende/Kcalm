from datetime import date, timedelta

from app.domain import analytics
from app.services import motivation


def test_night_snack_is_late():
    assert analytics.is_late("01:30", "21:00")
    assert not analytics.is_late("21:00", "21:00")
    assert not analytics.is_late("12:00", "21:00")
    assert analytics.is_late("21:01", "21:00")


def test_days_word():
    assert motivation._days_word(1) == "день"
    assert motivation._days_word(3) == "дня"
    assert motivation._days_word(11) == "дней"
    assert motivation._days_word(21) == "день"


def _db():
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE meals (user_id INT, local_date TEXT, deleted_at TEXT)")
    return db


def test_streak_counts_and_today_gap_does_not_break():
    db = _db()
    today = date(2026, 11, 12)
    for i in (1, 2, 3):
        db.execute("INSERT INTO meals VALUES (1, ?, NULL)", ((today - timedelta(days=i)).isoformat(),))
    assert motivation.logging_streak(db, 1, today) == 3
    db.execute("INSERT INTO meals VALUES (1, ?, NULL)", (today.isoformat(),))
    assert motivation.logging_streak(db, 1, today) == 4
    db.execute("DELETE FROM meals WHERE local_date=?", ((today - timedelta(days=2)).isoformat(),))
    assert motivation.logging_streak(db, 1, today) == 2
