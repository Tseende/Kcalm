"""Свои продукты находятся по падежам и опечаткам в комментариях к еде."""
import pytest

from app import db as appdb
from app.domain import matching
from app.storage import repo


@pytest.fixture()
def conn(tmp_path):
    c = appdb.connect(tmp_path / "t.db")
    appdb.migrate(c)
    c.execute("INSERT INTO users (tg_id) VALUES (1)")
    c.commit()
    uid = c.execute("SELECT id FROM users").fetchone()["id"]
    pid = repo.create_product(c, uid, "Форшмак", 196, 22, 12, 0, brand="Океан в подарок")
    c.execute("INSERT INTO food_aliases (user_id, food_id, alias_text) VALUES (?,?,?)",
              (uid, pid, "форшмак лососевый"))
    bid = repo.create_product(c, uid, "Бургер-соус майонезный", 290, 0.5, 28, 10, brand="Delikat'e")
    c.execute("INSERT INTO food_aliases (user_id, food_id, alias_text) VALUES (?,?,?)",
              (uid, bid, "бургерный соус"))
    c.commit()
    return c, uid


@pytest.mark.parametrize("query,name", [
    ("фаршмака", "Форшмак"),
    ("фаршмак лососевый", "Форшмак"),
    ("форшмака лососевого", "Форшмак"),
    ("бургерного соуса", "Бургер-соус майонезный"),
    ("соуса бургерного", "Бургер-соус майонезный"),
])
def test_own_product_found_in_any_form(conn, query, name):
    conn, uid = conn
    ref = matching.find_food(conn, "", query, user_id=uid)
    assert ref is not None and ref.name_ru == name and ref.source == "user"


def test_stem_and_tokens_match():
    assert matching.tokens_match("соуса", "соус")
    assert matching.tokens_match("лососевого", "лососевый")
    assert matching.tokens_match("фаршмака", "форшмак")
    assert not matching.tokens_match("сыр", "соус")
    assert not matching.tokens_match("чай", "чайка")


def test_other_users_product_invisible(conn):
    conn, _ = conn
    conn.execute("INSERT INTO users (tg_id) VALUES (2)")
    conn.commit()
    other = conn.execute("SELECT id FROM users WHERE tg_id=2").fetchone()["id"]
    ref = matching.find_food(conn, "", "фаршмак", user_id=other)
    assert ref is None or ref.source != "user"


@pytest.mark.parametrize("query", ["соус", "майонез", "паштет", "соус сырный"])
def test_generic_word_does_not_pick_own_product(conn, query):
    conn, uid = conn
    ref = matching.find_food(conn, "", query, user_id=uid)
    assert ref is None or ref.source != "user"
