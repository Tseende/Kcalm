CREATE TABLE IF NOT EXISTS chat_topics (
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('diary', 'support')),
    thread_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, kind),
    UNIQUE (chat_id, thread_id)
);
ALTER TABLE pending_inputs ADD COLUMN message_thread_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE photo_sessions ADD COLUMN message_thread_id INTEGER NOT NULL DEFAULT 0;
