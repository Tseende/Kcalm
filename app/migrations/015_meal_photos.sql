-- Все фото приёма пищи (альбом), а не только первое. dish_id пока без FK:
-- таблица посуды появится миграцией 016.
CREATE TABLE IF NOT EXISTS meal_photos (
    id INTEGER PRIMARY KEY,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    dish_id INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_meal_photos_meal ON meal_photos(meal_id);

INSERT INTO meal_photos (meal_id, path, position)
SELECT id, photo_path, 0 FROM meals WHERE photo_path IS NOT NULL AND photo_path != '';
