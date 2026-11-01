-- Посуда пользователя: известный размер тарелки или кружки служит масштабом для оценки порции.
-- Удаление мягкое (deleted_at): старые приёмы продолжают ссылаться на посуду через meal_photos.dish_id.
CREATE TABLE IF NOT EXISTS dishes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('plate','deep_plate','bowl','mug','cup','glass','pan','container','other')),
    diameter_cm REAL,
    depth_cm REAL,
    volume_ml REAL,
    photo_path TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dishes_user ON dishes(user_id);
