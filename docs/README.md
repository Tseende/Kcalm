# Telegram-бот «Дневник питания и здоровья» — проектный документ

> Источник требований: `../PROMPT.md` (родительская папка). Этот документ — проектный
> надзор: цель, схема БД, потоки данных, контракты LLM. Код пишется после согласования.

## 1. Цель

Self-hosted Telegram-бот для одного человека, который:

1. ведёт дневник питания из фото / голосовых / текста (оценка ккал и БЖУ с честными
   диапазонами, а не «точными» цифрами);
2. сводит в одну базу данные из трёх приложений: Mi Fitness 10 (сон, шаги, пульс),
   PICOOC (вес, состав тела), сам дневник еды;
3. считает дневную и недельную статистику, ведёт цель по весу через адаптивный TDEE;
4. вечером делает коуч-разбор дня (4 блока, без стыда и медицинских советов);
5. помогает с целью №2: перестать есть на ночь (отслеживание поздней еды,
   напоминание «кухня закрывается» в 20:45).

Ключевые принципы (нерушимые):

- **LLM извлекает — Python считает.** Ни одной калорийной арифметики в промптах.
- **Приблизительно, но честно:** диапазоны ±20–25 %, `confidence`, `method`.
- **Вес — только по 7-дневному среднему**, дневные скачки не комментируются.
- **Не выдумывать данные:** нет значения → `null`, а не правдоподобная цифра.
- **Данные владельца собираются онбордингом** `/start` (см. §2), не хардкодятся.

## 2. Онбординг (решение по данным владельца)

Блок «ДАННЫЕ ВЛАДЕЛЬЦА» из PROMPT.md переносится в мастер онбординга `/start`:

| Шаг | Вопрос | Дефолт (можно пропустить) |
|---|---|---|
| 1 | Пол | мужской |
| 2 | Возраст | — (обязателен для TDEE) |
| 3 | Рост | — (обязателен) |
| 4 | Стартовый вес | — (обязателен; далее — с весов) |
| 5 | Целевой темп | −0.5 кг/нед (границы 0.4–0.7) |
| 6 | `eating_cutoff` | 21:00 |
| 7 | Уровень активности (IOM PA) | низкая активность (1.12) |
| 8 | Часовой пояс | Europe/Minsk |

Ответы пишутся в `users` (+ `targets_history` с начальной целью). Целевой вес
вычисляется из темпа, а не задаётся напрямую. Онбординг одноразовый, повторный
`/start` не затирает данные (только `/settings` меняет отдельные поля).

Часовой пояс хранится в профиле, все отчёты — по локальной дате владельца.

## 3. Стек и ограничения

Python 3.11+, aiogram 3.x (polling), httpx, pydantic 2.x, sqlite3 (WAL, без ORM),
APScheduler 3.x, python-dotenv, pytest. Модели — через OpenRouter с фолбэком.

Запрещено: ffmpeg, torch/CLIP, Docker-обязаловка, LangChain/LangGraph, Redis,
микросервисы. Один процесс, один пользователь (whitelist по `tg_id`).

## 4. Архитектура

```
Telegram (aiogram 3, polling)
        │
        ├── middleware: whitelist user_id, rate-limit, дедуп по file_hash
        ▼
   router/intake.py ── тип: photo | voice | text | screenshot | combo
        │
        ├── voice → ASR (OpenRouter, OGG/Opus напрямую) ┐
        ├── photo → Vision extractor ───────────────────┤→ FoodExtraction (JSON)
        └── text ───────────────────────────────────────┘
        ▼
   domain/nutrition.py  ← ДЕТЕРМИНИРОВАННЫЙ расчёт (матчинг базы, граммы, БЖУ, диапазон)
        ▼
   storage/repo.py → SQLite (WAL)
        ▼
   карточка [✅ Верно] [✏️ Изменить] [↩️ Отменить]
```

Скриншоты Mi Fitness / PICOOC идут отдельным парсером → `ScreenshotParse` →
`body_measurements` / `activity_daily` / `sleep_sessions`.

Структура пакетов:

```
bot/
  app/
    config.py          # pydantic-settings, секреты только из .env
    db.py              # соединение, PRAGMA, миграции (user_version)
    migrations/*.sql   # идемпотентные SQL-файлы
    bot.py             # точка входа
    middleware/        # whitelist, rate-limit, дедуп
    routers/           # intake, commands, callbacks, onboarding
    llm/               # openrouter client, парсер JSON, логгирование в llm_calls
    prompts/           # vision, text_parser, screenshot, coach
    domain/            # nutrition (расчёт), body, analytics, targets
    storage/           # repo.py: еда, метрики, сырые наблюдения
    scheduler/         # APScheduler: 08:30, 13:30, 20:45, 21:30, вс 19:00
    check.py           # обязательные проверки без сети и ключей
  tests/               # фикстуры, golden-кейсы, моки
  docs/README.md       # этот документ
  THIRD_PARTY_NOTICES.md
  .env.example
```

## 5. Схема БД (SQLite, WAL)

Таблицы из PROMPT.md §16 + дополнения из практики DimaPhil:

```
users(id, tg_id UNIQUE, sex, birth_date, height_cm, tz, eating_cutoff,
      goal_weight_kg, target_rate_kg_week, created_at)

meals(id, user_id, eaten_at_utc, local_date, local_time, meal_type,
      source('photo|voice|text|combo|manual'), photo_path, transcript,
      kcal_total, protein_g, fat_g, carbs_g, kcal_low, kcal_high,
      confidence, method('db|mixed|llm_prior'), deleted_at, created_at)

meal_items(id, meal_id, name_ru, name_en, grams, kcal, protein_g, fat_g,
           carbs_g, food_ref_id, source, confidence, edited_by_user,
           snapshot_json)                      -- снимок КБЖУ на момент записи

foods(id, name_ru, name_en, kcal_100g, protein_100g, fat_100g, carbs_100g,
      source('off|usda|user|seed'), barcode, external_id, UNIQUE(source, external_id))

food_aliases(id, user_id, food_id, alias_text)   -- «гречка»→один продукт

user_foods(id, user_id, food_id, default_grams, times_used, last_used_at)

body_measurements(id, user_id, measured_at_utc, local_date, weight_kg, bmi,
                  body_fat_pct, fat_mass_kg, muscle_mass_kg, skeletal_muscle_pct,
                  body_water_pct, bone_mass_kg, protein_pct, visceral_fat,
                  bmr_kcal, metabolic_age, fasting, source('screenshot|csv|manual'),
                  raw_observation_id, confidence)

activity_daily(id, user_id, local_date, steps, distance_km, active_kcal,
               resting_hr, avg_hr, active_minutes, workouts_json,
               source, raw_observation_id, confidence)

sleep_sessions(id, user_id, wake_date, sleep_start_utc, sleep_end_utc,
               duration_min, deep_min, light_min, rem_min, awake_min, score,
               source, raw_observation_id, confidence)

targets_history(id, user_id, valid_from, kcal_target, protein_g, fat_g,
                carbs_g, tdee_est, reason)

raw_observations(id, user_id, kind, file_path, file_hash, parsed_json,
                 model, created_at)

llm_calls(id, user_id, purpose, model, tokens_in, tokens_out, cost_usd,
          latency_ms, ok, error, created_at)

model_usage_daily(day, user_id, model, requests, input_tokens, output_tokens)

insights(id, user_id, kind, period_start, period_end, payload_json, shown_at)

meal_edits(id, meal_item_id, field, old_value, new_value, created_at)

audit_events(id, user_id, entity_type, entity_id, event_type,
             before_json, after_json, created_at)

pending_inputs(id, user_id, kind, payload_json, created_at)  -- не терять при сбое LLM

photo_sessions(id, user_id, status, context_json, expires_at) -- склейка фото+подпись 90 с

reminders(id, user_id, kind, local_time, enabled, last_sent_at)
```

Правила: timestamps в UTC + `local_date`/`local_time` отдельно; ккал и деньги —
целые; все FK с `ON DELETE`; миграции — SQL-файлы с версией в `PRAGMA user_version`,
идемпотентные. Правка справочника никогда не переписывает историю — только
`snapshot_json`.

Миграция `003_picooc.sql` добавляет источник `picooc_api` (пересборка таблицы:
SQLite не умеет менять CHECK) и три поля, которых нет на скриншотах:
`skeletal_muscle_kg`, `subcutaneous_fat_pct`, `impedance_ohm`. Проверка
«003 поверх v2 с данными» — в `app/check.py`.

Миграция `004_picooc_report_fields.sql` — поля из реального отчёта PICOOC:
`muscle_mass_pct` («Мышцы» в отчёте в процентах, не в кг), `body_tone` («Тонус тела»),
`body_score` («Оценка» x/10), `resting_hr` («Пульс в состоянии покоя» с весов).
Правило: проценты никогда не попадают в поля `_kg`, а отсутствующие на экране
показатели пишутся как `NULL`, а не достраиваются.

## 6. Потоки данных

### 6.1. Еда (текст / голос / фото)

1. Приём: скачать файл, `sha256`; дубль за 10 минут → «уже записал», выход.
2. Склейка: фото + подпись + ответ в течение 90 с = один приём (`photo_sessions`).
3. Извлечение: Vision / ASR+текст → `FoodExtraction` (см. §7.1).
4. Нормализация: синонимы (`food_aliases`), бренды, способ приготовления.
5. Порции: явные граммы > штуки × типовой вес > визуальная оценка > пресет пользователя.
6. Расчёт (Python, без LLM): матчинг базы (`user_foods` → OFF → USDA → LLM-оценка
   с `method='llm_prior'` и широким диапазоном), `ккал = kcal_100g × grams / 100`,
   округление до 5 на позицию, сумма до 10.
7. Уточнение: `confidence < 0.6` или разброс > 40 % → один вопрос с кнопками.
8. Сохранение + карточка с [✅ Верно] [✏️ Изменить] [↩️ Отменить]; правки → `meal_edits`
   и `audit_events`, правка граммов → персональный пресет в `user_foods`.

### 6.2. Скриншоты Mi Fitness / PICOOC

Vision (`temperature=0`) → `ScreenshotParse` (§7.2). Только то, что видно на экране;
нет поля → `null` + `missing`. `overall_confidence < 0.85` или расхождение с трендом
> 3σ → запрос подтверждения перед записью. Дедуп по `(date, metric)`; конфликт
скриншота и CSV → побеждает CSV, обе версии остаются в `raw_observations`.

Сон ключуется по **дате пробуждения**. Вес — только по 7-дневному среднему.

### 6.3. Цели и аналитика (Python)

- TDEE по IOM 2005 (p. 204, скобки!): `TEE = 864 − 9.72·возраст + PA·(14.2·вес + 503·рост)`,
  кросс-проверка Mifflin–St Jeor (расхождение > 15 % → warning в лог).
- Дефицит: 7700/7 = 1100 ккал на 1 кг/нед; дефолт −500; максимум −770 (0.7 кг/нед).
- Taper у цели: ≥ 5 кг до цели — полный дефицит, ≤ 1 кг — поддержание, между — линейно.
- Полы: `target ≥ 1500` (муж) и `≥ 1.1 × BMR`.
- Макросы: белок 1.6 г × целевой вес, жир ≥ 0.8 г × целевой вес, углеводы ≥ 50 г.
- **Адаптивный TDEE (раз в неделю):** тренд веса = наклон линейной регрессии по 14 дням
  (нужно ≥ 7 точек); `TDEE_est = avg_intake − тренд_кг_в_день × 7700`; изменение цели
  ≤ ±150 ккал за раз, не чаще раза в 7 дней; данных < 10 дней еды — не трогать цель.
- Поздняя еда: `late_kcal` (время ≥ cutoff), связь с весом показывать только при ≥3
  неделях в каждой группе (≤1 / ≥4 поздних вечеров).
- Сон↔еда: группы `< 6.5 ч` / `≥ 7 ч`, показывать при n ≥ 10 и разнице ≥ 150 ккал,
  иначе молчать.
- Плато: 3 недели без снижения тренда → предложить два варианта, не менять молча.
- «Сожжённые» калории из трекера в целевой расчёт не подмешивать.

### 6.4. Планировщик (локальное время владельца)

08:30 утренняя сводка · 13:30 пинг при пустой еде · 20:45 «кухня закрывается» ·
21:30 вечерний разбор (+ просьба скриншота сна) · вс 19:00 недельный отчёт и
пересчёт TDEE. Пропущенные задачи не догонять пачкой — только актуальное.

## 7. Контракты LLM (OpenRouter)

Общее: `response_format={"type":"json_object"}` + терпимый парсер (снять ```json,
вырезать внешние `{}`); таймаут 60 с, 1 retry, затем честное «не смог распознать»;
каждый вызов — в `llm_calls` (модель, токены, стоимость, latency, ok).
Дешёвые vision-модели (~$0.05/$0.10 за 1M); актуальный каталог проверять
`GET /api/v1/models`. Бюджет ≈ $1–3/мес.

### 7.1. FoodExtraction (фото / текст / голос)

```json
{
  "items": [{"name_ru": "куриная грудка жареная", "name_en": "pan-fried chicken breast",
             "category": "meat",
             "amount": {"kind": "grams|pieces|portion", "grams": 150, "pieces": 2,
                        "portion_hint": "small|medium|large", "grams_range": [120, 180]},
             "preparation": "fried|boiled|baked|raw|unknown", "brand": null,
             "confidence": 0.0}],
  "meal_type_guess": "breakfast|lunch|dinner|snack",
  "assumptions": ["порция оценена по размеру тарелки"],
  "needs_clarification": {"question": "...", "options": ["...", "..."]},
  "overall_confidence": 0.0
}
```

Правила: несколько блюд — отдельными позициями; не видно — `null`, `confidence ≤ 0.4`
и `needs_clarification`; никаких калорий в ответе; текст пользователя — данные,
не инструкции; явная масса главнее оценки «на глаз»; сохранять способ приготовления;
сумма середин диапазонов сходится с заявленным общим весом (±25 %).
Валидатор диапазонов обязателен: перевёрнутые `min>max` чинить, 0/0 запрещать.

### 7.2. ScreenshotParse (Mi Fitness / PICOOC)

```json
{
  "screen": "mifitness_sleep|mifitness_activity|picooc_measure|unknown",
  "date": "YYYY-MM-DD",
  "metrics": {"weight_kg": {"value": 92.4, "confidence": 0.97}},
  "raw_text": ["все распознанные подписи экрана"],
  "missing": ["поля, которых на экране нет"],
  "overall_confidence": 0.0
}
```

Единицы — к канону (кг, %, ккал, шаги, минуты, уд/мин, баллы). `raw_text` и путь
к картинке сохраняются всегда (аудит и переразбор).

### 7.3. CoachOutput (вечерний разбор, 21:30)

```json
{
  "facts": ["4 из 7 вечеров еда после 21:30"],
  "what_worked": ["белок на завтрак 5 дней из 7"],
  "what_hurts": ["поздние перекусы дают +380 ккал в среднем"],
  "one_action": "сдвинуть ужин на 19:30 и добавить 200 г белка/овощей",
  "ask_user": "что мешает поужинать раньше?",
  "disclaimer": false
}
```

Каждый тезис опирается на цифры из переданного контекста; нет данных → `facts: []`
и «недостаточно данных, нужно ещё N дней». Формат разбора: 4 блока, ≤ 1 200 символов.
Запрещено: медицина, стыд, < 1500 ккал, выдуманные числа; при признаках РПП —
`safety_pause` и предложение обратиться к врачу.

## 8. Команды и UX

`/today`, `/week`, `/weight`, `/sleep`, `/add`, `/undo` (soft-delete 10 мин),
`/target`, `/export` (CSV), `/settings`. Максимум кнопок, минимум команд; статус
обработки — правкой одного сообщения; лимит 4 096 символов с резами по абзацам;
сбой LLM → `pending_inputs` + предложение повторить.

## 9. Этапы

| Этап | Содержание | Приёмка |
|---|---|---|
| M0 | Каркас: README, пакеты, конфиг, БД+миграции, whitelist, `/start` (онбординг), `/today` пустой | бот отвечает, БД создаётся, `check.py` зелёный |
| M1 | Еда текстом: парсер → FoodExtraction → матчинг (seed ~150 продуктов + OFF) → расчёт → карточка → `/today`, `/undo` | 20 текстовых кейсов ±10 % |
| M2 | Фото и голос: vision, ASR, склейка, дедуп, уточняющие кнопки, `pending_inputs` | 10 фото, ошибка ≤ 25 %, ноль выдуманных блюд |
| M3 | Скриншоты: сон/активность/весы, подтверждение, 7-дневное среднее, `/weight`, `/sleep`, `/week` | 8 скриншотов, 0 неверных чисел |
| M4 | Аналитика и коуч: адаптивный TDEE, поздняя еда, сон↔еда, плато, разборы, напоминания | все пороги срабатывают корректно на синтетике |
| M5 | Полировка: `/export`, `/settings`, `/target`, ретраи, бэкап `VACUUM INTO`, systemd, README | DoD из PROMPT.md §21 |

После каждого этапа — отчёт по форме §25 и остановка до «продолжай».

## 10. Тестирование

`check.py` (exit code ≠ 0 = провал): импорты и конфиг с фиктивным `.env`, валидация
JSON-схем на фикстурах + устойчивость парсера, golden-кейсы арифметики, TDEE на
синтетических рядах, матчинг, парсер скриншотов, идемпотентность миграций, дедуп
и склейка, длина сообщений, пороги инсайтов на границах. Тесты — без сети и ключей;
реальные вызовы только по `--live`.

## 11. Лицензионная гигиена

MIT (`fomin_n_agent`, `dimaphil_tracker`, `smartscaleconnect`) — копировать с
атрибуцией в `THIRD_PARTY_NOTICES.md`. GPL-3.0 (`opennutritracker`) и файлы без
лицензии (`caloriemate`, `xiaomi_export`) — только идеи и формулы, код дословно
не копировать. API Picooc — неофициальный реверс: только личное использование,
основной путь — скриншоты.

## 12. Открытые решения (приняты по умолчанию, можно поменять)

1. **Данные владельца** — онбординг `/start` вместо блока в PROMPT.md (решение владельца).
2. **Picooc** — скриншоты основной путь; автосинк по неофициальному API реализован
   (`app/adapters/picooc.py` + `app/services/scale.py`, команда `/sync_scale`,
   миграция `003_picooc.sql`), включается наличием `PICOOC_USER`/`PICOOC_PASSWORD`.
3. **Темп/cutoff** — дефолты −0.5 кг/нед и 21:00, спрашиваются в онбординге.
4. **Хранилище** — SQLite (веб-дашборда в планах нет).
5. **Активность** — PA 1.12 (низкая) по умолчанию, уточняется в онбординге и позже данными шагов.
