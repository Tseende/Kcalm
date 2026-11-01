-- 007: напоминание про воду (16:00, шлётся только если выпито меньше половины ориентира).
INSERT OR IGNORE INTO reminders (user_id, kind, local_time)
SELECT id, 'water', '16:00' FROM users WHERE onboarded = 1;
