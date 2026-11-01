-- 009: психологическая поддержка.
--   if_then_rules — личные правила «если … то …» (implementation intentions), с необязательным
--                   временем напоминания;
--   craving_events.lesson — что человек решил сделать иначе в следующий раз (итог разбора);
--   users.care_mode — осторожный режим: без напоминаний о весе и без активного дефицита.

ALTER TABLE users ADD COLUMN care_mode INTEGER NOT NULL DEFAULT 0;
ALTER TABLE craving_events ADD COLUMN lesson TEXT;

CREATE TABLE IF NOT EXISTS if_then_rules (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    if_text TEXT NOT NULL,
    then_text TEXT NOT NULL,
    category TEXT CHECK (category IS NULL OR category IN ('hunger','emotion','habit','tired','overate')),
    remind_time TEXT,
    last_sent_date TEXT,
    created_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rules_user ON if_then_rules(user_id);
