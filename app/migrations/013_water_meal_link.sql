-- вода из чая и кофе в приёме пищи: привязка к записи, чтобы правки и отмена шли вместе
ALTER TABLE water_log ADD COLUMN meal_id INTEGER REFERENCES meals(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_water_meal ON water_log(meal_id);
