"""Execute the actual JS matcher: snack flavours must not become ingredients."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_food_card_matching_and_assets():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for frontend matcher checks')
    cases = {
        'Чипсы со вкусом сыра': 'chips',
        'Чипсы рифлёные с луком': 'ridgedchips',
        'Рифленые чипсы со сметаной': 'ridgedchips',
        'Начос сырные': 'nachos',
        'Кукурузные чипсы': 'nachos',
        'Сухарики ржаные с чесноком': 'croutons',
        'Кириешки со вкусом бекона': 'croutons',
        'Крекер с сыром': 'crackers',
        'Соломка солёная': 'pretzelsticks',
        'Арахис в хрустящей оболочке': 'coatedpeanuts',
        'Луковые кольца': 'onionrings',
        'Кукурузные палочки': 'cornpuffs',
        'Попкорн карамельный': 'popcorn',
        'Драники со сметаной': 'draniki',
        'Куриные стрипсы': 'chickenstrips',
        'Картофель по-деревенски': 'potatowedges',
        'Котлета с картофельным пюре': 'cutletmashed',
        'Гречка с курицей': 'buckwheatchicken',
        'Макароны по-флотски': 'pastameat',
        'Паста с фаршем': 'pastameat',
        'Тефтели в томатном соусе': 'meatballs',
        'Wavy potato chips': 'ridgedchips',
        'Rye croutons': 'croutons',
        'Potato wedges': 'potatowedges',
        'Chicken tenders': 'chickenstrips',
        'Macaroni with ground beef': 'pastameat',
        'Салат Цезарь с сухариками': 'salad',
        'Суп с фрикадельками': 'soup',
        'Борщ': 'borscht',
        'Плов': 'plov',
        'Наггетсы': 'nuggets',
        'Хлеб ржаной': 'ryebread',
        'Сыр': 'cheese',
        'Арахис': 'peanut',
        'Картофель': 'potato',
        'Майонез Провансаль': 'mayo',
        'Бургер-соус майонезный': 'burgersauce',
        'Соус для бургеров': 'burgersauce',
        'Burger sauce': 'burgersauce',
        'Соус сырный': 'cheesesauce',
        'Соус барбекю': 'barbecuesauce',
        'Пицца с соусом барбекю': 'pizza',
        'Молочный напиток Савушкин Какао 2,5%': 'milkcocoa',
        'Напиток ультрапастеризованный молочный с какао': 'milkcocoa',
        'Савушкин какао': 'milkcocoa',
        'Chocolate milk': 'milkcocoa',
        'Молочный коктейль ванильный': 'milkdrink',
        'Кисломолочный напиток «Снежок»': 'drinkyogurt',
        'Какао-порошок': None,
        'Пиво светлое в банке': 'beercan',
        'Пиво тёмное ж/б': 'beercan_dark',
        'Пиво светлое в стекле': 'beerbottle',
        'Пиво безалкогольное': 'beerbottle',
        'Тёмное пиво в бутылке': 'beerbottle_dark',
        'Stout in a can': 'beercan_dark',
        'Сухарики к пиву': 'croutons',
        'Сушёные яблоки': 'driedapple',
        'Яблоко сушеное': 'driedapple',
        'Смесь сухофруктов': 'driedfruit',
        'Сушёные абрикосы': 'driedfruit',
        'Чернослив': 'driedfruit',
        'Dried fruit mix': 'driedfruit',
        'Сыр копчёный косичка': 'braidedcheese',
        'Сыр чечил': 'braidedcheese',
        'Копчёный сыр': 'smokedcheese',
        'Клюква': 'cranberry',
        'Frozen cranberries': 'cranberry',
        'Клюква вяленая': 'driedcranberry',
        'Кальмар сушёный полосками': 'driedsquid',
        'Сушеные кольца кальмара': 'driedsquid',
        'Dried squid': 'driedsquid',
    }
    script = r'''
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
globalThis.window = { addEventListener() {} };
globalThis.location = { search: '', hostname: 'localhost', protocol: 'http:' };
const url = pathToFileURL(process.argv[1]);
const { foodPicName, foodPicKey, foodGroupOf, FOOD_GROUPS: groups, FOOD_ICONS: keys } = await import(url);
const { TILES } = await import(new URL('./food_atlas.js', url));
const atlas = TILES.flatMap(tile => tile.keys);
if (TILES.length !== 4 || new Set(atlas).size !== atlas.length) throw new Error('Invalid atlas partition');
for (const tile of TILES) {
  if (!fs.existsSync(new URL('../' + tile.asset, url))) throw new Error('Missing tile: ' + tile.asset);
}
const cases = JSON.parse(process.argv[2]);
for (const [name, expected] of Object.entries(cases)) {
  const actual = foodPicName(name);
  if (actual !== expected) throw new Error(`${name}: ${actual} != ${expected}`);
}
if (foodPicKey('Какао 2,5%', null, 'Савушкин продукт') !== 'milkcocoa') throw new Error('Missing brand match');
if (foodPicKey('Какао-порошок', null, 'Савушкин') !== null) throw new Error('Powder matched as drink');
if (foodPicKey('Пиво светлое', 'beercan_dark') !== 'beercan_dark') throw new Error('Manual icon lost');
if (foodGroupOf('Пиво светлое') !== 'Пиво') throw new Error('Beer must have a product group');
for (const name of ['Сушёные яблоки', 'Вяленая клюква', 'Сухофрукты', 'Изюм', 'Финики']) {
  if (foodGroupOf(name) !== 'Сухофрукты') throw new Error(`Missing dried fruit group: ${name}`);
}
if (foodGroupOf('Копчёный сыр косичка') !== 'Молочное и яйца') throw new Error('Braided cheese must stay in dairy');
for (const key of keys) if (!atlas.includes(key)) throw new Error('Missing sprite cell: ' + key);
for (const key of keys) {
  if (!fs.existsSync(process.argv[3] + '/' + key + '.webp')) throw new Error('Missing asset: ' + key);
}
const snacks = groups.find(([name]) => name === 'Снеки')[1];
for (const key of ['chips', 'ridgedchips', 'croutons', 'nachos', 'popcorn', 'driedsquid']) {
  if (!snacks.includes(key)) throw new Error('Missing snack: ' + key);
}
if (new Set(groups.flatMap(([, keys]) => keys)).size !== keys.length) throw new Error('Ungrouped icon');
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, str(ROOT / 'app/web/static/js/pics.js'),
                             json.dumps(cases, ensure_ascii=False), str(ROOT / 'app/web/static/img/food')],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
