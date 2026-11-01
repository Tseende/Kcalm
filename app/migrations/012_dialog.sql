-- 012: живой диалог поддержки.
--   craving_events.summary — одна строка о разговоре для памяти между эпизодами
--                            («что происходило и что помогло»), её пишет модель при закрытии;
--   craving_events.mode    — 'craving' (тяга к еде) или 'talk' (просто поговорить через меню).

ALTER TABLE craving_events ADD COLUMN summary TEXT;
ALTER TABLE craving_events ADD COLUMN mode TEXT NOT NULL DEFAULT 'craving';
