"""Фаза 0 v2: база на 100 г у позиции, все фото приёма, COALESCE при повторном скриншоте,
миграции 014/015 поверх v13 с данными."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.db import MIGRATIONS_DIR, connect, migrate, user_version
from app.domain.nutrition import FoodRef, IngredientEstimate, calculate_meal
from app.storage import repo


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def _calc(grams=200.0):
    ref = FoodRef(name_ru="гречка", name_en="buckwheat", kcal_100g=110, protein_100g=4.2,
                  fat_100g=1.1, carbs_100g=21.3, source="seed")
    return calculate_meal([(IngredientEstimate(name="buckwheat", grams_min=grams,
                                               grams_max=grams), ref)])


def _meal(db, uid, calc=None, **kw):
    return repo.save_meal(db, uid, "2026-10-28 18:00:00", "2026-10-28", "18:00", "dinner",
                          "text", calc or _calc(), **kw)


def test_per100_written_and_grams_edit_recalculates(db):
    uid = repo.create_user(db, 1)
    mid = _meal(db, uid)
    it = repo.get_meal_items(db, mid)[0]
    assert (it["kcal_100g"], it["protein_100g"], it["fat_100g"], it["carbs_100g"]) == \
        (110, 4.2, 1.1, 21.3)
    repo.edit_item_grams(db, it["id"], 100.0)
    it = repo.get_meal_items(db, mid)[0]
    assert it["kcal"] == 110 and it["protein_g"] == 4.2 and it["carbs_g"] == 21.3
    assert repo.get_meal(db, uid, mid)["kcal_total"] == 110
    assert it["kcal_100g"] == 110            # база не меняется от правки граммов


def test_replace_items_writes_per100(db):
    uid = repo.create_user(db, 1)
    mid = _meal(db, uid)
    repo.replace_meal_items(db, uid, mid, _calc(50))
    it = repo.get_meal_items(db, mid)[0]
    assert it["kcal_100g"] == 110 and it["grams"] == 50


def test_edit_grams_without_per100_falls_back_to_ratio(db):
    uid = repo.create_user(db, 1)
    mid = _meal(db, uid)
    it = repo.get_meal_items(db, mid)[0]
    db.execute("UPDATE meal_items SET kcal_100g=NULL WHERE id=?", (it["id"],))
    repo.edit_item_grams(db, it["id"], 100.0)
    assert repo.get_meal_items(db, mid)[0]["kcal"] == 110


def test_meal_photos_album_in_order_without_duplicates(db):
    uid = repo.create_user(db, 1)
    mid = _meal(db, uid, photo_path="a.jpg", photo_paths=["a.jpg", "b.jpg"])
    rows = repo.meal_photos(db, mid)
    assert [r["path"] for r in rows] == ["a.jpg", "b.jpg"]
    assert [r["position"] for r in rows] == [0, 1]
    assert repo.get_meal(db, uid, mid)["photo_path"] == "a.jpg"
    assert repo.add_meal_photos(db, mid, ["a.jpg", "b.jpg", "c.jpg"], dish_ids=[None, None, 7]) == 1
    rows = repo.meal_photos(db, mid)
    assert [r["path"] for r in rows] == ["a.jpg", "b.jpg", "c.jpg"] and rows[2]["dish_id"] == 7


def test_single_photo_path_gives_one_row(db):
    uid = repo.create_user(db, 1)
    mid = _meal(db, uid, photo_path="x.jpg")
    assert [r["path"] for r in repo.meal_photos(db, mid)] == ["x.jpg"]
    assert repo.meal_photos(db, _meal(db, uid)) == []


def test_second_sleep_screenshot_keeps_earlier_values(db):
    uid = repo.create_user(db, 1)
    repo.save_sleep(db, uid, "2026-10-28", {"duration_min": 420, "deep_min": 90, "score": 80},
                    "screenshot", 0.9)
    repo.save_sleep(db, uid, "2026-10-28", {"duration_min": 430, "rem_min": 100},
                    "screenshot", 0.8)
    r = db.execute("SELECT * FROM sleep_sessions WHERE user_id=?", (uid,)).fetchone()
    assert (r["duration_min"], r["deep_min"], r["rem_min"], r["score"]) == (430, 90, 100, 80)


def test_second_activity_screenshot_keeps_earlier_values(db):
    uid = repo.create_user(db, 1)
    repo.save_activity(db, uid, "2026-10-28", {"steps": 8000, "active_kcal": 300}, "screenshot", 0.9)
    repo.save_activity(db, uid, "2026-10-28", {"steps": 9000, "distance_km": 6.5}, "screenshot", 0.9)
    r = db.execute("SELECT * FROM activity_daily WHERE user_id=?", (uid,)).fetchone()
    assert (r["steps"], r["active_kcal"], r["distance_km"]) == (9000, 300, 6.5)


def test_migrations_014_015_backfill_on_v13_db(tmp_path):
    conn = connect(tmp_path / "v13.db")
    # применяем всё до 013 включительно, как на боевой базе
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        v = int(f.name.split("_", 1)[0])
        if v > 13:
            break
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {v}")
    assert user_version(conn) == 13
    uid = repo.create_user(conn, 5)
    fid = repo.upsert_food(conn, "рис", "rice", 130, 2.7, 0.3, 28, "user", "r1")
    conn.execute("INSERT INTO meals (id, user_id, eaten_at_utc, local_date, local_time, "
                 "meal_type, source, photo_path, kcal_total) VALUES "
                 "(1,?,?,?,?,?,?,?,0),(2,?,?,?,?,?,?,NULL,0)",
                 (uid, "2026-10-01 10:00:00", "2026-10-01", "10:00", "lunch", "photo", "p1.jpg",
                  uid, "2026-10-02 10:00:00", "2026-10-02", "10:00", "lunch", "text"))
    conn.execute("INSERT INTO meal_items (meal_id,name_ru,name_en,grams,kcal,protein_g,fat_g,"
                 "carbs_g,food_ref_id) VALUES (1,'рис','rice',200,260,5.4,0.6,56,?)", (fid,))
    conn.execute("INSERT INTO meal_items (meal_id,name_ru,name_en,grams,kcal,protein_g,fat_g,"
                 "carbs_g) VALUES (2,'суп','soup',250,125,5,5,12.5)")
    conn.execute("INSERT INTO meal_items (meal_id,name_ru,name_en,grams,kcal) "
                 "VALUES (2,'ноль','zero',0,0)")
    conn.commit()
    assert migrate(conn) >= 15
    a, b, z = conn.execute("SELECT * FROM meal_items ORDER BY id").fetchall()
    assert (a["kcal_100g"], a["protein_100g"], a["carbs_100g"]) == (130, 2.7, 28)   # из foods
    assert (b["kcal_100g"], b["protein_100g"], b["fat_100g"], b["carbs_100g"]) == (50, 2, 2, 5)
    assert z["kcal_100g"] is None                                                   # grams = 0
    assert [(r["meal_id"], r["path"]) for r in conn.execute("SELECT * FROM meal_photos")] == \
        [(1, "p1.jpg")]
    conn.close()
