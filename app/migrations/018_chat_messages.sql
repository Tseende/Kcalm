-- История чата Mini App (веб-ассистент). Раньше таблицу лениво создавал веб-сервер.
CREATE TABLE IF NOT EXISTS chat_messages(
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    meal_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages(user_id, id);
