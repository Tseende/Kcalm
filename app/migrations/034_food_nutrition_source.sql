ALTER TABLE foods ADD COLUMN nutrition_source TEXT CHECK (nutrition_source IN ('label', 'off'));
