-- 026: CHECK у dishes.kind не знал salad_bowl, pot, jar, thermos (repo.DISH_KINDS и приложение их предлагают),
-- INSERT с такой посудой падал. SQLite не меняет CHECK на месте, поэтому таблица пересобирается.
-- На dishes никто не ссылается по FK (meal_photos.dish_id без FK, см. 015), id и данные сохраняются.
DROP TABLE IF EXISTS dishes_new;

CREATE TABLE dishes_new (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('plate','deep_plate','bowl','salad_bowl','mug','cup','glass','pan','pot','container','jar','thermos','other')),
    diameter_cm REAL,
    depth_cm REAL,
    volume_ml REAL,
    photo_path TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO dishes_new (id, user_id, name, kind, diameter_cm, depth_cm, volume_ml, photo_path,
                        is_default, deleted_at, created_at)
SELECT id, user_id, name, kind, diameter_cm, depth_cm, volume_ml, photo_path,
       is_default, deleted_at, created_at
FROM dishes;

DROP TABLE dishes;
ALTER TABLE dishes_new RENAME TO dishes;
CREATE INDEX IF NOT EXISTS idx_dishes_user ON dishes(user_id);
