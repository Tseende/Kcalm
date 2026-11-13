-- Мои блюда: категория для списка (breakfast, lunch, dinner, snack, drink). NULL = по типу исходного приёма.
ALTER TABLE favorite_meals ADD COLUMN category TEXT;
