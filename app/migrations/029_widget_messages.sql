-- 029: сообщения-виджеты Bot API 10.4 (карточка дня и пауза при тяге). Повторяемая миграция: только IF NOT EXISTS.
-- kind: 'day' (одна на пользователя на локальную дату) или 'pause' (ref_id = id эпизода тяги).
CREATE TABLE IF NOT EXISTS widget_messages (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    local_date TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    ref_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_widget_chat_message ON widget_messages(chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_widget_user_kind_date ON widget_messages(user_id, kind, local_date);
