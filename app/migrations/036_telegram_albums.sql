CREATE TABLE meal_telegram_albums (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    media_group_id TEXT NOT NULL,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, media_group_id)
);
CREATE INDEX idx_meal_telegram_albums_meal ON meal_telegram_albums(meal_id);
