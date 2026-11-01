-- База на 100 г у позиции приёма: нужна, чтобы менять граммы без потерь точности
-- и сохранять позицию как продукт.
ALTER TABLE meal_items ADD COLUMN kcal_100g REAL;
ALTER TABLE meal_items ADD COLUMN protein_100g REAL;
ALTER TABLE meal_items ADD COLUMN fat_100g REAL;
ALTER TABLE meal_items ADD COLUMN carbs_100g REAL;

-- старые строки со ссылкой на справочник: берём значения продукта
UPDATE meal_items SET
    kcal_100g    = (SELECT f.kcal_100g    FROM foods f WHERE f.id = meal_items.food_ref_id),
    protein_100g = (SELECT f.protein_100g FROM foods f WHERE f.id = meal_items.food_ref_id),
    fat_100g     = (SELECT f.fat_100g     FROM foods f WHERE f.id = meal_items.food_ref_id),
    carbs_100g   = (SELECT f.carbs_100g   FROM foods f WHERE f.id = meal_items.food_ref_id)
WHERE food_ref_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM foods f WHERE f.id = meal_items.food_ref_id);

-- остальные: выводим из записанных значений
UPDATE meal_items SET
    kcal_100g    = kcal      * 100.0 / grams,
    protein_100g = protein_g * 100.0 / grams,
    fat_100g     = fat_g     * 100.0 / grams,
    carbs_100g   = carbs_g   * 100.0 / grams
WHERE kcal_100g IS NULL AND grams > 0;
