-- 032: additional plate shapes. Preserve dish ids, defaults, photos and meal-photo links.
-- meal_photos.dish_id has no FK to dishes (see migration 015).
DROP TABLE IF EXISTS dishes_new;

CREATE TABLE dishes_new (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('plate','deep_plate','bowl','salad_bowl','mug','cup','glass','pan','pot','container','jar','thermos','dessert_plate','square_plate','oval_plate','rectangular_plate','pasta_plate','divided_plate','platter','ramekin','other')),
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
