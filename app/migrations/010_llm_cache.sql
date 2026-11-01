-- 010: сколько токенов запроса пришло из кеша провайдера (DeepSeek берёт за них в разы меньше)
ALTER TABLE llm_calls ADD COLUMN tokens_cached INTEGER;
