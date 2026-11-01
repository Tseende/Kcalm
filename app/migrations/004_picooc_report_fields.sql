-- 004: поля, которые есть в отчёте PICOOC, но не влезали в схему.
--   muscle_mass_pct — «Мышцы» в отчёте показаны в ПРОЦЕНТАХ, не в кг;
--   body_tone      — «Тонус тела»;
--   body_score     — «Оценка» (шкала x/10);
--   resting_hr     — «Пульс в состоянии покоя» с весов (не путать с активностью).

ALTER TABLE body_measurements ADD COLUMN muscle_mass_pct REAL;
ALTER TABLE body_measurements ADD COLUMN body_tone REAL;
ALTER TABLE body_measurements ADD COLUMN body_score REAL;
ALTER TABLE body_measurements ADD COLUMN resting_hr INTEGER;
