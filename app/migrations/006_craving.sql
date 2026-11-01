-- 006: ночная поддержка при тяге к еде.
-- craving_events — один эпизод (с итогом); craving_messages — короткая история диалога,
-- чтобы поддержка помнила, о чём шла речь. Это личные данные: уходят в /export.

CREATE TABLE IF NOT EXISTS craving_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_utc TEXT NOT NULL,
    local_date TEXT NOT NULL,
    local_time TEXT NOT NULL,
    trigger_text TEXT,
    category TEXT NOT NULL DEFAULT 'unknown'
        CHECK (category IN ('unknown','hunger','emotion','habit','tired','overate')),
    outcome TEXT NOT NULL DEFAULT 'open'
        CHECK (outcome IN ('open','resisted','ate_planned','overate','dismissed')),
    kcal_so_far INTEGER,
    kcal_target INTEGER,
    risk_flag INTEGER NOT NULL DEFAULT 0,
    followup_at_utc TEXT,
    followup_sent INTEGER NOT NULL DEFAULT 0,
    closed_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_craving_user_date ON craving_events(user_id, local_date);

CREATE TABLE IF NOT EXISTS craving_messages (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES craving_events(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    text TEXT NOT NULL,
    created_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_craving_msg_event ON craving_messages(event_id);
