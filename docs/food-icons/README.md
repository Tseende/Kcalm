# Карточки снеков и домашних блюд

Добавлено 15 самостоятельных изображений без замены существующих.
Финальные файлы: `app/web/static/img/food/<key>.webp`, 112×112, с прозрачностью.
Ручной выбор: «Мои продукты» → продукт → значок. Для снеков выделена отдельная группа.
Автоматический выбор: `app/web/static/js/pics.js`.

Изображения созданы встроенным `image_gen`, по одному запросу на изображение.
Общий промпт каждого запроса (в `{subject}` подставляется строка из таблицы):

> Use case: stylized-concept. Asset type: single food diary app icon. Subject: {subject} Style: polished Apple emoji-like 3D food illustration, softly shaded realistic appetizing materials, warm natural food colors, crisp silhouette readable at 48 pixels, three-quarter view slightly from above, centered square composition with about 10% transparent padding. One isolated food composition only. Actual transparent background. No text, no labels, no logos, no border, no watermark. Match the visual style of a small glossy food emoji, not a photograph, no environment.

| Key | Subject |
| --- | --- |
| ridgedchips | A small pile of golden deeply ridged potato chips, a plain orange snack bag behind them. |
| nachos | A small pile of golden triangular corn tortilla chips with a tiny bowl of cheese dip. |
| croutons | A pile of dark rye bread snack croutons, short rectangular crunchy brown sticks with seasoning. |
| crackers | A stack of square golden salted crackers with scalloped edges and tiny holes. |
| pretzelsticks | A small bundle of thin brown salted pretzel bread sticks, visible white salt crystals. |
| coatedpeanuts | A small bowl of crunchy coated peanuts, some round reddish golden shells and a broken one showing a peanut. |
| onionrings | A pile of golden fried battered onion rings, clearly hollow circular rings. |
| cornpuffs | A small pile of bright golden airy curved corn puff snacks. |
| draniki | Three golden brown Belarusian potato pancakes on a small ceramic plate with a dollop of sour cream and a tiny sprig of dill. |
| chickenstrips | Four golden crispy breaded chicken tender strips, one cut showing white meat, on a small plate. |
| potatowedges | Golden roasted skin-on potato wedges with light herb seasoning on a small plate. |
| cutletmashed | One browned oval meat cutlet beside a mound of creamy mashed potatoes on a small plate. |
| buckwheatchicken | Cooked brown buckwheat groats with pieces of cooked chicken breast on a small plate. |
| pastameat | A small plate of short macaroni pasta mixed with browned minced meat, Russian macaroni po-flotski. |
| meatballs | Three round meatballs in glossy tomato sauce on a small ceramic plate. |

`tools/prepare_food_icon.py` приводит прозрачный PNG к размеру и формату существующего набора,
сохраняя альфа-канал. Исходные генерации остаются в каталоге Codex `generated_images`.
Галерея для просмотра: `docs/food-cards-preview.html`.

Проверка: `python -m pytest tests/test_food_pics.py -q`.
Тест запускает настоящий JS-подбор на Node.js, проверяет названия с добавками,
составные блюда, существующие картинки и наличие файлов для всех значков.

Изображение — только визуальное обозначение. Данные о составе и калорийности
в рамках этой задачи не меняются и по изображению не назначаются.
