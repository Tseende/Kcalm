-- Избранное можно называть, комментировать и править: свои название и заметка.
ALTER TABLE favorite_meals ADD COLUMN title TEXT;
ALTER TABLE favorite_meals ADD COLUMN note TEXT;
