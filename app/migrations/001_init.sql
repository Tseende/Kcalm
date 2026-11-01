-- 001_init: базовая схема. timestamps в UTC, отчёты — по local_date/local_time.
-- Ккал — целые (REAL только для граммов и процентов).

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    tg_id INTEGER NOT NULL UNIQUE,
    sex TEXT NOT NULL DEFAULT 'male' CHECK (sex IN ('male','female')),
    birth_date TEXT,
    height_cm REAL,
    start_weight_kg REAL,
    tz TEXT NOT NULL DEFAULT 'Europe/Minsk',
    eating_cutoff TEXT NOT NULL DEFAULT '21:00',
    activity_pa REAL NOT NULL DEFAULT 1.12,
    goal_weight_kg REAL,
    target_rate_kg_week REAL NOT NULL DEFAULT -0.5,
    onboarded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS foods (
    id INTEGER PRIMARY KEY,
    name_ru TEXT NOT NULL,
    name_en TEXT NOT NULL,
    kcal_100g REAL NOT NULL,
    protein_100g REAL NOT NULL,
    fat_100g REAL NOT NULL,
    carbs_100g REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('off','usda','user','seed','llm')),
    barcode TEXT,
    external_id TEXT,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_foods_name_en ON foods(name_en);
CREATE INDEX IF NOT EXISTS idx_foods_name_ru ON foods(name_ru);

CREATE TABLE IF NOT EXISTS food_aliases (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    food_id INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    alias_text TEXT NOT NULL,
    UNIQUE(user_id, alias_text)
);

CREATE TABLE IF NOT EXISTS user_foods (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    food_id INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    default_grams REAL,
    times_used INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    eaten_at_utc TEXT NOT NULL,
    local_date TEXT NOT NULL,
    local_time TEXT NOT NULL,
    meal_type TEXT NOT NULL CHECK (meal_type IN ('breakfast','lunch','dinner','snack')),
    source TEXT NOT NULL CHECK (source IN ('photo','voice','text','combo','manual')),
    photo_path TEXT,
    transcript TEXT,
    kcal_total INTEGER NOT NULL DEFAULT 0,
    protein_g REAL NOT NULL DEFAULT 0,
    fat_g REAL NOT NULL DEFAULT 0,
    carbs_g REAL NOT NULL DEFAULT 0,
    kcal_low INTEGER NOT NULL DEFAULT 0,
    kcal_high INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.5,
    method TEXT NOT NULL DEFAULT 'db' CHECK (method IN ('db','mixed','llm_prior')),
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_meals_user_date ON meals(user_id, local_date);

CREATE TABLE IF NOT EXISTS meal_items (
    id INTEGER PRIMARY KEY,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    name_ru TEXT NOT NULL,
    name_en TEXT NOT NULL,
    grams REAL NOT NULL,
    kcal INTEGER NOT NULL,
    protein_g REAL NOT NULL DEFAULT 0,
    fat_g REAL NOT NULL DEFAULT 0,
    carbs_g REAL NOT NULL DEFAULT 0,
    food_ref_id INTEGER REFERENCES foods(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'seed',
    confidence REAL NOT NULL DEFAULT 0.5,
    edited_by_user INTEGER NOT NULL DEFAULT 0,
    snapshot_json TEXT
);

CREATE TABLE IF NOT EXISTS meal_edits (
    id INTEGER PRIMARY KEY,
    meal_item_id INTEGER NOT NULL REFERENCES meal_items(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS body_measurements (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    measured_at_utc TEXT,
    local_date TEXT NOT NULL,
    weight_kg REAL, bmi REAL, body_fat_pct REAL, fat_mass_kg REAL,
    muscle_mass_kg REAL, skeletal_muscle_pct REAL, body_water_pct REAL,
    bone_mass_kg REAL, protein_pct REAL, visceral_fat INTEGER,
    bmr_kcal INTEGER, metabolic_age INTEGER,
    fasting INTEGER,
    source TEXT NOT NULL CHECK (source IN ('screenshot','csv','manual')),
    raw_observation_id INTEGER REFERENCES raw_observations(id) ON DELETE SET NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    UNIQUE(user_id, local_date, source, weight_kg)
);
CREATE INDEX IF NOT EXISTS idx_body_user_date ON body_measurements(user_id, local_date);

CREATE TABLE IF NOT EXISTS activity_daily (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    local_date TEXT NOT NULL,
    steps INTEGER, distance_km REAL, active_kcal INTEGER,
    resting_hr INTEGER, avg_hr INTEGER, active_minutes INTEGER,
    workouts_json TEXT,
    source TEXT NOT NULL,
    raw_observation_id INTEGER REFERENCES raw_observations(id) ON DELETE SET NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    UNIQUE(user_id, local_date, source)
);

CREATE TABLE IF NOT EXISTS sleep_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wake_date TEXT NOT NULL,
    sleep_start_utc TEXT, sleep_end_utc TEXT,
    duration_min INTEGER, deep_min INTEGER, light_min INTEGER,
    rem_min INTEGER, awake_min INTEGER, score INTEGER,
    source TEXT NOT NULL,
    raw_observation_id INTEGER REFERENCES raw_observations(id) ON DELETE SET NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    UNIQUE(user_id, wake_date, source)
);

CREATE TABLE IF NOT EXISTS targets_history (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    valid_from TEXT NOT NULL,
    kcal_target INTEGER NOT NULL,
    protein_g REAL, fat_g REAL, carbs_g REAL,
    tdee_est INTEGER,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_observations (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    file_path TEXT,
    file_hash TEXT,
    parsed_json TEXT,
    model TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_raw_hash ON raw_observations(file_hash);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER, tokens_out INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    ok INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS model_usage_daily (
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, model)
);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    shown_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pending_inputs (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photo_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('awaiting_caption','ready')),
    context_json TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    local_time TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sent_at TEXT,
    UNIQUE(user_id, kind)
);

CREATE TABLE IF NOT EXISTS recent_files (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_recent_hash ON recent_files(user_id, file_hash, created_at);
