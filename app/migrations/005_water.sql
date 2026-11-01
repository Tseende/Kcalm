-- 005: учёт выпитой воды. Одна строка = один приём воды (текстом, голосом или командой).
-- Удаление при отмене физическое: запись воды не несёт ценности для истории.

CREATE TABLE IF NOT EXISTS water_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    logged_at_utc TEXT NOT NULL,
    local_date TEXT NOT NULL,
    local_time TEXT NOT NULL,
    ml INTEGER NOT NULL CHECK (ml BETWEEN 10 AND 3000),
    source TEXT NOT NULL CHECK (source IN ('text','voice','command')),
    raw_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_water_user_date ON water_log(user_id, local_date);
