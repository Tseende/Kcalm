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
    }
    script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/\bexport /g, '');
const ctx = { t: x => x };
vm.createContext(ctx);
vm.runInContext(source, ctx);
const cases = JSON.parse(process.argv[2]);
for (const [name, expected] of Object.entries(cases)) {
  const actual = ctx.foodPicName(name);
  if (actual !== expected) throw new Error(`${name}: ${actual} != ${expected}`);
}
const groups = vm.runInContext('FOOD_GROUPS', ctx);
const keys = vm.runInContext('FOOD_ICONS', ctx);
for (const key of keys) {
  if (!fs.existsSync(process.argv[3] + '/' + key + '.webp')) throw new Error('Missing asset: ' + key);
}
const snacks = groups.find(([name]) => name === 'Снеки')[1];
for (const key of ['chips', 'ridgedchips', 'croutons', 'nachos', 'popcorn']) {
  if (!snacks.includes(key)) throw new Error('Missing snack: ' + key);
}
if (new Set(groups.flatMap(([, keys]) => keys)).size !== keys.length) throw new Error('Ungrouped icon');
'''
    result = subprocess.run([node, '-e', script, str(ROOT / 'app/web/static/js/pics.js'),
                             json.dumps(cases, ensure_ascii=False), str(ROOT / 'app/web/static/img/food')],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
