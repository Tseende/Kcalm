-- Индексы под частые выборки: позиции приёма и поиск продукта по штрихкоду.
CREATE INDEX IF NOT EXISTS idx_meal_items_meal ON meal_items(meal_id);
CREATE INDEX IF NOT EXISTS idx_foods_barcode ON foods(barcode);
