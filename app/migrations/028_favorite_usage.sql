-- Избранное: сколько раз и когда записывали в дневник (порядок в списке: частые и свежие выше).
ALTER TABLE favorite_meals ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE favorite_meals ADD COLUMN last_used_at TEXT;
