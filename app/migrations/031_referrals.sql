-- 031: реферальная программа (этап 1) и заморозки серии.
-- users.ref_code — личный код приглашения (создаётся лениво), invites_left — сколько друзей ещё можно позвать,
-- streak_freezes — сколько пропущенных дней серии можно закрыть, invited_by — кто пригласил.
ALTER TABLE users ADD COLUMN ref_code TEXT;
ALTER TABLE users ADD COLUMN invited_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN invites_left INTEGER NOT NULL DEFAULT 3;
ALTER TABLE users ADD COLUMN streak_freezes INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ref_code ON users(ref_code);

-- Доступ по приглашению (рядом со списком ALLOWED_TG_IDS). Нет строки — нет доступа.
CREATE TABLE IF NOT EXISTS access_grants (
    tg_id INTEGER PRIMARY KEY,
    inviter_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Один приглашённый — одна строка навсегда (invitee_tg_id UNIQUE). Время в UTC «ГГГГ-ММ-ДД ЧЧ:ММ:СС».
-- invitee_name — first_name из Telegram для списка у пригласившего. rewarded_at — пригласивший получил награду.
-- При удалении данных приглашённого строка обезличивается (invitee_tg_id = -id), счётчики пригласившего целы.
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY,
    inviter_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    invitee_tg_id INTEGER NOT NULL UNIQUE,
    invitee_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    invitee_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    onboarded_at TEXT,
    activated_at TEXT,
    rewarded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_user_id);
CREATE INDEX IF NOT EXISTS idx_referrals_invitee_user ON referrals(invitee_user_id);

-- Журнал наград: kind = freeze | invite, amount со знаком (расход заморозки = -1).
CREATE TABLE IF NOT EXISTS reward_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT NOT NULL,
    referral_id INTEGER REFERENCES referrals(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reward_log_user ON reward_log(user_id);

-- Дни, закрытые заморозкой: для серии это день с записью.
CREATE TABLE IF NOT EXISTS streak_freeze_days (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    local_date TEXT NOT NULL,
    PRIMARY KEY (user_id, local_date)
);
