-- Избранные блюда: образец приёма пищи, который записывается в один тап.
-- sig — подпись состава (названия позиций), по ней избранное не двоится.
CREATE TABLE IF NOT EXISTS favorite_meals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    meal_id    INTEGER NOT NULL REFERENCES meals(id),
    sig        TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, sig)
);
CREATE INDEX IF NOT EXISTS idx_favorite_meals_user ON favorite_meals(user_id);
