-- 027: свои напоминания (по шаблону или с нуля). Повторяемая миграция: только IF NOT EXISTS.
-- days — цифры дней недели (0=пн … 6=вс), пусто = каждый день. every_min + until_time задают
-- повтор внутри дня (от local_time до until_time). last_sent_at — метка «ГГГГ-ММ-ДД ЧЧ:ММ».
CREATE TABLE IF NOT EXISTS user_reminders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    template TEXT NOT NULL DEFAULT 'custom',
    title TEXT NOT NULL,
    text TEXT,
    local_time TEXT NOT NULL,
    days TEXT,
    every_min INTEGER,
    until_time TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_user_reminders_user ON user_reminders(user_id);
