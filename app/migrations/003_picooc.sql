-- 003: автосинк весов Picooc по неофициальному API.
--   1) источник 'picooc_api' в CHECK (SQLite не умеет менять CHECK → пересборка таблицы);
--   2) три поля, которые Picooc отдаёт, а скриншоты — нет.
-- Таблица body_measurements никем не referenced, поэтому пересборка безопасна.

CREATE TABLE body_measurements_new (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    measured_at_utc TEXT,
    local_date TEXT NOT NULL,
    weight_kg REAL, bmi REAL, body_fat_pct REAL, fat_mass_kg REAL,
    muscle_mass_kg REAL, skeletal_muscle_pct REAL, body_water_pct REAL,
    bone_mass_kg REAL, protein_pct REAL, visceral_fat INTEGER,
    bmr_kcal INTEGER, metabolic_age INTEGER,
    fasting INTEGER,
    skeletal_muscle_kg REAL,
    subcutaneous_fat_pct REAL,
    impedance_ohm REAL,
    source TEXT NOT NULL CHECK (source IN ('screenshot','csv','manual','picooc_api')),
    raw_observation_id INTEGER REFERENCES raw_observations(id) ON DELETE SET NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    UNIQUE(user_id, local_date, source, weight_kg)
);

INSERT INTO body_measurements_new (
    id, user_id, measured_at_utc, local_date, weight_kg, bmi, body_fat_pct, fat_mass_kg,
    muscle_mass_kg, skeletal_muscle_pct, body_water_pct, bone_mass_kg, protein_pct,
    visceral_fat, bmr_kcal, metabolic_age, fasting, source, raw_observation_id, confidence
)
SELECT id, user_id, measured_at_utc, local_date, weight_kg, bmi, body_fat_pct, fat_mass_kg,
       muscle_mass_kg, skeletal_muscle_pct, body_water_pct, bone_mass_kg, protein_pct,
       visceral_fat, bmr_kcal, metabolic_age, fasting, source, raw_observation_id, confidence
FROM body_measurements;

DROP TABLE body_measurements;
ALTER TABLE body_measurements_new RENAME TO body_measurements;

CREATE INDEX IF NOT EXISTS idx_body_user_date ON body_measurements(user_id, local_date);
