-- Allow catalog provenance in the shared food table.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'bot.foods'::regclass AND conname = 'foods_source_check'
      AND pg_get_constraintdef(oid) LIKE '%green%'
  ) THEN
    ALTER TABLE bot.foods DROP CONSTRAINT IF EXISTS foods_source_check;
    ALTER TABLE bot.foods ADD CONSTRAINT foods_source_check
      CHECK (source IN ('off','usda','user','seed','llm','green'));
  END IF;
END
$$;
