-- 008: напоминание о взвешивании натощак. days — дни недели через цифры (0=пн … 6=вс).
ALTER TABLE reminders ADD COLUMN days TEXT;

INSERT OR IGNORE INTO reminders (user_id, kind, local_time, days)
SELECT id, 'weigh', '07:30', '0123456' FROM users WHERE onboarded = 1;
