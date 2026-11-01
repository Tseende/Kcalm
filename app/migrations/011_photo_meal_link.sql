-- Фото распознаётся сразу, а подпись в течение окна правит ТОТ ЖЕ приём пищи:
-- сессия помнит, какая запись создана из фото.
ALTER TABLE photo_sessions ADD COLUMN meal_id INTEGER REFERENCES meals(id) ON DELETE SET NULL;
