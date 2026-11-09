# V2 API contract (Mini App ↔ bot)

Single source of truth for the frontend (`app/web/static/`) and the backend
(`app/web/`). Change this file first if the contract has to change.

## Conventions

- Base path: `/api`. Static app is served at `/app/` (index.html), assets at `/app/*`.
- Auth: every request sends `Authorization: tma <Telegram.WebApp.initData>`.
  Server validates the HMAC (key = HMAC_SHA256("WebAppData", bot_token)), `auth_date`
  not older than 24 h, user id in `ALLOWED_TG_IDS` (unless `ALLOW_PUBLIC_BOT`).
  Failure → `401 {"error":"unauthorized"}`. A user that exists in Telegram but has not
  finished onboarding in the bot → `409 {"error":"not_onboarded"}`.
- Dev mode: if `WEB_DEV_TG_ID` is set (non-zero) AND the request comes from
  127.0.0.1/::1, a request without the header is treated as that user. Never on
  other hosts.
- JSON in/out, UTF-8. Dates `YYYY-MM-DD` in the user's local tz, times `HH:MM`.
- Numbers: kcal integers, grams/macros one decimal.
- Errors: `{"error": "<code>", "message": "<human RU text>"}` with 4xx/5xx.
- Uploads: `multipart/form-data`, each image ≤ `MAX_MEDIA_BYTES`, jpeg/png/webp/heic.

## Shared objects

```jsonc
// Item (позиция приёма)
{"id":1,"name":"Гречка отварная","grams":180.0,"kcal":198,"protein":7.4,"fat":2.0,
 "carbs":38.5,"per100":{"kcal":110,"protein":4.1,"fat":1.1,"carbs":21.4}|null,
 "confidence":0.8,"edited":false,
 "source":"seed"|"llm"|"manual"|"user"|"barcode"|...}   // per100.kcal: number with a decimal when entered per portion

// Meal
{"id":10,"date":"2026-11-01","time":"13:20","type":"lunch",   // breakfast|lunch|dinner|snack
 "source":"photo","kcal":540,"kcal_low":470,"kcal_high":610,
 "protein":32.1,"fat":18.0,"carbs":60.2,"confidence":0.7,
 "photos":[{"id":5,"url":"/api/photos/5","dish_id":2|null}],
 "items":[Item], "comment":"..."|null}

// Dish (посуда)
{"id":2,"name":"Глубокая тарелка","kind":"deep_plate",  // plate|deep_plate|bowl|mug|cup|glass|pan|container|other
 "diameter_cm":20.0|null,"depth_cm":5.0|null,"volume_ml":500|null,
 "photo_url":"/api/dishes/2/photo"|null,"is_default":false}

// Product (свой продукт)
{"id":300,"name":"Творог 5%","brand":"Савушкин"|null,
 "kcal":121,"protein":17.0,"fat":5.0,"carbs":1.8,            // per 100 g
 "default_grams":200|null,"unit_name":"пачка"|null,"unit_grams":180|null,
 "barcode":null,"times_used":3}

// Targets
{"kcal":1650,"protein":110,"fat":55,"carbs":170,"water_ml":2100}
```

## Endpoints

| Method | Path | Request | Response |
|---|---|---|---|
| GET | /api/me | — | `{"user":{"tg_id","first_name","sex","height_cm","goal_weight_kg","tz","eating_cutoff"},"targets":Targets,"weight":{"latest":82.4,"trend":82.9,"start":88.0}}` |
| GET | /api/day?date= | date optional (today) | `{"date","targets":Targets,"totals":{"kcal","protein","fat","carbs"},"remaining_kcal","water_ml","meals":[Meal],"sleep":{"duration_min","score"}\|null,"activity":{"steps","active_kcal"}\|null,"weight":82.4\|null}` |
| GET | /api/stats?range=week\|month\|90d | — | `{"range","days":[{"date","kcal","protein","fat","carbs","target_kcal","water_ml","weight","weight_trend","sleep_min","steps"}],"summary":{"avg_kcal","avg_protein","days_logged","days_on_target","weight_change","avg_sleep_min","avg_steps"}}` (nulls where no data) |
| GET | /api/meals/{id} | — | `Meal` |
| POST | /api/meals | multipart: `photo` (1–6 files, repeated), `dish_id` (repeated, same order as photos, empty string = none), `comment`, `time` (HH:MM optional), `date` optional, `text` (when no photos: text description) | `201 Meal` (recognition is synchronous, may take up to ~60 s) |
| PATCH | /api/meals/{id} | `{"time"?:"HH:MM","type"?:...}` | `Meal` |
| DELETE | /api/meals/{id} | — | `204` (soft delete) |
| POST | /api/meals/{id}/refine | `{"comment":"жарил на масле 1 ст.л."}` | `Meal`. Items edited by hand (`edited` and `source:"manual"`, or a database product with confidence 1) are not sent to the model for changes and come back unchanged; the model only sees them as `locked_items` context |
| POST | /api/meals/manual | `{"items":[{"food_id"?:int \| "name":str(1..120),"grams":1..5000,"kcal","protein","fat","carbs","per"?:"100g"\|"portion","save"?:bool}] (1..30),"type"?,"date"?,"time"?}` | `201 Meal`. No LLM. `food_id` = own or shared product (foreign → 404; per100 taken from the database, portion preset is learned); otherwise manual input (`source:"manual"`, all four numbers required; 100 g: kcal 0..900, P/F/C 0..100; portion: kcal 0..10000, P/F/C 0..1000, and per-100 g derived from the portion must stay ≤ 900 / 100). `save:true` stores the input in "my products" (deduplicated). Meal `source:"manual"`, `kcal_low = kcal_high = kcal`, `confidence` = min over items |
| POST | /api/meals/{id}/items | one item of `/api/meals/manual` | `201 Meal`. Adds a product/manual item to a saved meal (foreign meal → 404, at most 30 items). Range: if every item is exact (manual, barcode, own product or confidence ≥ 0.99) it collapses to the total, otherwise the old range is scaled by new total / old total; `confidence` = min over items |
| GET | /api/foods/search | `?q=&limit=` (default 30, max 50) | `{"foods":[{"id","name","brand","kcal","protein","fat","carbs","default_grams","unit_name","unit_grams","own":bool,"source"}]}` per 100 g. Own products first, then the shared reference (`owner IS NULL`); other users' products never appear. `q` shorter than 2 characters: own products only (most used first). No LLM, no network |
| PATCH | /api/items/{id} | `{"grams":150}` or manual edit: any of `{"name"?:str(1..120),"grams"?,"kcal","protein","fat","carbs" (all four together),"per"?:"100g"\|"portion" (default 100g),"food_id"?}`; empty body → 400 `empty_patch` | `Meal`. Only `grams`: recalculated from per100 (as before). Manual numbers are saved as typed (kcal to integer, P/F/C to 0.1, no rounding to 5/10) and both per100 and absolute values are written; `source:"manual"`, `confidence:1`, `food_ref_id` cleared, `edited:true`; meal range/confidence recomputed (same rule as `/items`). Name only: only the name changes. `food_id` (cannot be combined with numbers): replaces name and per100 with the product, grams stay, `food_ref_id` set |
| DELETE | /api/items/{id} | — | `Meal` |
| POST | /api/items/{id}/save_product | `{"name"?}` | `201 Product` |
| GET | /api/products?q= | — | `{"products":[Product]}` |
| POST | /api/products | Product fields without id | `201 Product` |
| PATCH | /api/products/{id} | partial Product | `Product` |
| DELETE | /api/products/{id} | — | `204` |
| POST | /api/products/label | multipart `photo` (1–4 files, one product) | `{"draft":Product-without-id,"confidence":0.9,"notes":["..."]}` (not saved). A barcode read from the photo is checked in Open Food Facts to fill gaps. |
| GET | /api/products/barcode/{code} | | `{"draft":Product-without-id,"existing_id":null\|id,"source":"off"}`; 400 `bad_barcode` (wrong checksum), 404 not found. Open Food Facts first, then own cache. |
| GET | /api/frequent | `?limit=` | `{"items":[{"meal_id","favorite_id":null\|id,"title","names","kcal","count"}]}`: favorites first, then meals eaten ≥2 times |
| POST | /api/favorites | `{"meal_id"}` | 201 `{"id"}` (same composition is not duplicated) |
| DELETE | /api/favorites/{id} | | 204 / 404 |
| POST | /api/favorites/{id}/repeat | | 201 `{"id","kcal"}`: logs the meal again even if the source was deleted |
| GET | /api/dishes | — | `{"dishes":[Dish]}` |
| POST | /api/dishes | multipart: fields of Dish + optional `photo` | `201 Dish` |
| PATCH | /api/dishes/{id} | JSON partial Dish | `Dish` |
| DELETE | /api/dishes/{id} | — | `204` |
| GET | /api/dishes/{id}/photo | — | image |
| POST | /api/water | `{"ml":250}` | `{"water_ml":1250,"goal_ml":2100,"id":7}` |
| DELETE | /api/water/{id} | — | `{"water_ml":1000,"goal_ml":2100}` |
| POST | /api/weight | `{"weight_kg":82.1}` | `{"weight":82.1,"trend":82.6}` |
| POST | /api/screens | multipart `photo` | `{"id":12,"screen":"mifitness_sleep","date","metrics":{"steps":9800,...},"confidence":0.9,"needs_confirm":true\|false,"applied":true\|false}` |
| POST | /api/screens/{id}/confirm | `{"apply":true}` | `{"applied":true}` |
| POST | /api/sleep | `{"date"?,"duration_min"?, "start"?:"23:30","end"?:"07:10","score"?}` | `{"ok":true}` |
| POST | /api/activity | `{"date"?,"steps"?,"active_kcal"?,"active_minutes"?,"workout"?:"бег 30 мин"}` | `{"ok":true}` |
| GET | /api/chat?limit=50 | — | `{"messages":[{"id","role":"user"\|"assistant","text","created_at"}]}` |
| POST | /api/chat | `{"text"?:"...","meal_id"?:int,"mode"?:"talk"\|"craving"}` | `{"messages":[...new messages...],"logged_meal":Meal\|null,"actions":[{"id","label"}]}` — same routing as the bot chat: craving/overeating support dialog (open episode → dialog; a phrase about craving opens one; `mode` is an explicit entry like /talk or /craving, `text` may then be empty and no user message is stored) → water → commands (help, undo, food info, sleep/steps/workout phrases are logged, `добавь посуду: …`) → question to the assistant → food is logged like in the bot chat. `meal_id` (owner's live meal, else 404) makes the assistant talk about that meal. The assistant sees the last 10 chat messages. `actions` are quick-reply buttons (empty list when none), shown under the last assistant bubble |
| POST | /api/chat/action | `{"id":"cr:out:12:resisted"}` | `{"messages":[assistant message],"logged_meal":null,"actions":[...]}` — a button press, same as the bot's callback. `id` is the bot's callback_data: `cr:cat:<event>:<hunger\|emotion\|habit\|tired\|overate>`, `cr:wait:<event>` (15-min follow-up), `cr:out:<event>:<resisted\|ate_planned\|overate>`, `cr:deb:<event>`, `cr:dcat:<event>:<category>`, `cr:less:<event>:<index\|own>`, `cr:rule:<event>`. 400 `bad_action`, 404 `not_found` (foreign/missing event), 409 `episode_closed`. The 15-min follow-up is sent to Telegram and also stored in chat history |
| GET | /api/photos/{id} | — | image (only owner's) |
| GET | /api/export.csv?kind=meals\|body\|water\|sleep\|activity | — | CSV |

## Frontend notes

- Load `https://telegram.org/js/telegram-web-app.js`; call `Telegram.WebApp.ready()`,
  `expand()`; colors from `--tg-theme-*` CSS variables; `BackButton` for sub-screens,
  `MainButton` for the primary action, `HapticFeedback` on success.
- Outside Telegram (no initData) the app still renders and calls the API without the
  header (works only in dev mode on localhost).
