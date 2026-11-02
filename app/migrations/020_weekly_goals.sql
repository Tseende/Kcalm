-- Одна мини-цель на календарную неделю (пн-вс); прогресс считается из записей, вручную не отмечается.
CREATE TABLE IF NOT EXISTS weekly_goals(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_start TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_days INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, week_start));
