-- Приветственная картинка отправляется один раз, в том числе для уже созданных тем.
ALTER TABLE chat_topics ADD COLUMN welcome_version INTEGER NOT NULL DEFAULT 0;
