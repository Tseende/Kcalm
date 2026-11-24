-- Позволяет обновить названия уже созданных тем без потери переписки.
ALTER TABLE chat_topics ADD COLUMN name TEXT NOT NULL DEFAULT '';
