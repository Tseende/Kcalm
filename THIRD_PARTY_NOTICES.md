# THIRD_PARTY_NOTICES

## MIT (код перенесён с сохранением подхода, авторы указаны)

- **fomin-n/nutrition-agent** (MIT) — схема `IngredientEstimate` с диапазоном граммов
  и валидатором, санитарные проверки 4/9/4 и плотности макросов, таблица весов
  ранжирования продуктов. Порт на Python в `app/domain/nutrition.py`,
  `app/domain/matching.py`.
- **DimaPhil/nutrition-tracker** (MIT) — идеи `snapshot_json` (снимок КБЖУ),
  `food_aliases`, `audit_events`, `model_usage_daily`, `photo_sessions`.
  Реализовано в `app/migrations/001_init.sql`, `app/storage/repo.py`.
- **AlexxIT/SmartScaleConnect** (MIT) — контракт API Picooc (login, подпись,
  `bodyIndex/bodyIndexList`). Порт: `app/adapters/picooc.py`; синк —
  `app/services/scale.py`, команда `/sync_scale`. Основной путь всё равно скриншоты.

## Только идеи и формулы (код не копировался)

- **simonoppowa/OpenNutriTracker** (GPL-3.0) — формулы IOM 2005, PAL, taper,
  пороги 1500/1200. Общеизвестные числовые факты из IOM 2005; реализация
  собственная (`app/domain/targets.py`).
- **ignoxx/caloriemate** (без лицензии) — структура vision-промпта: контекстные
  фото как доказательства, полосы неопределённости 5–15/15–30/30–50 %.
  Промпт переписан самостоятельно (`app/prompts/vision.md`).
- **artyomxx/xiaomi-band-ios-export** (без лицензии) — имена таблиц/полей Mi Fitness
  как факты о формате данных (`research/mi_fitness_db.md`). Код чтения реализован
  самостоятельно.

## Сервисы

- Open Food Facts (ODbL), USDA FoodData Central (CC0), OpenRouter — по их условиям.
- API Picooc — неофициальный реверс-инжиниринг, только личное использование.

## Иконки Mini App

- **Tabler Icons** (MIT) — https://github.com/tabler/tabler-icons, пакет `@tabler/icons`
  v3.49.0, набор outline. Copyright (c) 2020-2024 Paweł Kuna. Разметка выбранных иконок
  встроена в `app/web/static/js/icons.js` (без загрузки с CDN во время работы).
  Лицензия MIT: https://github.com/tabler/tabler-icons/blob/main/LICENSE

  ```
  MIT License

  Copyright (c) 2020-2024 Paweł Kuna

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.
  ```
