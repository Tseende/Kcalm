-- Свои продукты пользователя лежат в общей таблице foods: source='user', owner_user_id задан.
-- owner_user_id NULL = общий справочник. Удаление мягкое (deleted_at): старые приёмы ссылаются
-- на продукт через meal_items.food_ref_id.
-- UNIQUE(source, external_id) уже есть, поэтому у своих продуктов external_id вида 'u<user>-<uuid>'.
ALTER TABLE foods ADD COLUMN owner_user_id INTEGER;
ALTER TABLE foods ADD COLUMN default_grams REAL;
ALTER TABLE foods ADD COLUMN unit_name TEXT;
ALTER TABLE foods ADD COLUMN unit_grams REAL;
ALTER TABLE foods ADD COLUMN brand TEXT;
ALTER TABLE foods ADD COLUMN deleted_at TEXT;
ALTER TABLE foods ADD COLUMN created_at TEXT;
CREATE INDEX IF NOT EXISTS idx_foods_owner ON foods(owner_user_id);
