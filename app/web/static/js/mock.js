const MOCK_DRINKS = [{"id": "cola_zero", "name": "Кола без сахара", "emoji": "🥤", "color": "gray", "kcal_100": 0, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "cola", "name": "Кола", "emoji": "🥤", "color": "red", "kcal_100": 42.4, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "fanta", "name": "Фанта", "emoji": "🍊", "color": "orange", "kcal_100": 40, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "tea", "name": "Чай", "emoji": "🍵", "color": "green", "kcal_100": 0, "sizes": [200, 300, 400], "sugar": true, "hydrates": true}, {"id": "americano", "name": "Американо", "emoji": "☕", "color": "indigo", "kcal_100": 0.4, "sizes": [200, 300], "sugar": true, "hydrates": true}, {"id": "cappuccino", "name": "Капучино", "emoji": "☕", "color": "orange", "kcal_100": 39.0, "sizes": [180, 250, 350], "sugar": true, "hydrates": true}, {"id": "flat_white", "name": "Флэт уайт", "emoji": "☕", "color": "purple", "kcal_100": 52.6, "sizes": [160, 200], "sugar": true, "hydrates": true}, {"id": "latte", "name": "Латте", "emoji": "🥛", "color": "blue", "kcal_100": 42.4, "sizes": [250, 350], "sugar": true, "hydrates": true}, {"id": "juice", "name": "Сок", "emoji": "🧃", "color": "yellow", "kcal_100": 44.9, "sizes": [200, 250], "sugar": false, "hydrates": false}, {"id": "smoothie", "name": "Смузи", "emoji": "🥤", "color": "pink", "kcal_100": 52, "sizes": [250, 350, 450], "sugar": false, "hydrates": false}, {"id": "shake", "name": "Протеиновый коктейль", "emoji": "🥛", "color": "teal", "kcal_100": 62, "sizes": [250, 300, 400], "sugar": false, "hydrates": false}, {"id": "energy", "name": "Энергетик", "emoji": "⚡", "color": "yellow", "kcal_100": 44, "sizes": [250, 330, 450], "sugar": false, "hydrates": false}, {"id": "beer", "name": "Пиво", "emoji": "🍺", "color": "orange", "kcal_100": 42.9, "sizes": [330, 500, 568], "sugar": false, "hydrates": false}, {"id": "beer0", "name": "Пиво безалкогольное", "emoji": "🍺", "color": "gray", "kcal_100": 21.2, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "wine", "name": "Вино", "emoji": "🍷", "color": "red", "kcal_100": 77.0, "sizes": [125, 150, 200], "sugar": false, "hydrates": false}];
// Fixture data + simulated API for previewing the UI without the backend (?mock=1).
const pad2 = (n) => String(n).padStart(2, '0');
const iso = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rng(seed) {
  let a = seed;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = rng(42);
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];

function svgPhoto(c1, c2, emoji) {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 240'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='${c1}'/><stop offset='1' stop-color='${c2}'/></linearGradient></defs><rect width='320' height='240' fill='url(#g)'/><circle cx='160' cy='125' r='80' fill='rgba(255,255,255,.88)'/><circle cx='160' cy='125' r='62' fill='rgba(0,0,0,.06)'/><text x='160' y='148' font-size='60' text-anchor='middle'>${emoji}</text></svg>`;
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
}
const PHOTOS = [
  svgPhoto('#f59e0b', '#fde68a', '🍳'),
  svgPhoto('#10b981', '#a7f3d0', '🥗'),
  svgPhoto('#ef4444', '#fecaca', '🍲'),
  svgPhoto('#3b82f6', '#bfdbfe', '🐟'),
  svgPhoto('#8b5cf6', '#ddd6fe', '🥣'),
];

// name -> [per100 kcal, P, F, C]
const FOODS = {
  oats: ['Овсяная каша на молоке', 102, 3.4, 3.4, 15],
  egg: ['Яйцо варёное', 157, 12.7, 11.5, 0.7],
  bread: ['Хлеб цельнозерновой', 247, 13, 3.4, 41],
  tomato: ['Салат из огурцов и помидоров', 62, 1.2, 4.5, 4.4],
  chicken: ['Куриная грудка запечённая', 165, 31, 3.6, 0],
  buckwheat: ['Гречка отварная', 110, 4.1, 1.1, 21.4],
  salmon: ['Лосось запечённый', 208, 20, 13, 0],
  rice: ['Рис отварной', 130, 2.7, 0.3, 28],
  cottage: ['Творог 5%', 121, 17, 5, 1.8],
  apple: ['Яблоко', 52, 0.3, 0.2, 14],
  banana: ['Банан', 89, 1.1, 0.3, 22.8],
  borscht: ['Борщ', 49, 2.1, 2.4, 5],
  oil: ['Масло оливковое', 884, 0, 100, 0],
  americano: ['Американо', 0.4, 0.1, 0, 0],
  juice: ['Сок', 45, 0.5, 0.1, 10.5],
  wine: ['Вино красное', 85, 0.1, 0, 2.6],
  sausage: ['куриные сосиски', 220, 12, 18, 2],
};
const TEMPLATES = {
  breakfast: [[['oats', 250], ['banana', 120]], [['egg', 110], ['bread', 60], ['tomato', 100]], [['cottage', 200], ['apple', 150]]],
  lunch: [[['chicken', 150], ['buckwheat', 180], ['tomato', 150]], [['salmon', 140], ['rice', 160], ['tomato', 120]], [['borscht', 300], ['bread', 40]]],
  dinner: [[['salmon', 150], ['tomato', 200]], [['chicken', 130], ['rice', 100], ['tomato', 150]], [['cottage', 220], ['banana', 100]]],
  snack: [[['apple', 180]], [['cottage', 150]], [['banana', 110]], [['americano', 200]], [['juice', 250]], [['wine', 150]]],
};
const TIMES = { breakfast: ['08:05', '08:40', '09:10'], lunch: ['13:00', '13:20', '14:05'], dinner: ['19:00', '19:35', '20:10'], snack: ['11:15', '16:45', '17:20'] };

let seq = 1000;
const nid = () => ++seq;
const r1 = (v) => Math.round(v * 10) / 10;

function makeItem(key, grams) {
  const f = FOODS[key];
  const per100 = { kcal: f[1], protein: f[2], fat: f[3], carbs: f[4] };
  return calcItem({ id: nid(), name: f[0], grams, per100, confidence: 0.6 + Math.round(rnd() * 3) / 10, edited: false });
}
function calcItem(it) {
  const k = it.grams / 100;
  it.kcal = Math.round(it.per100.kcal * k);
  it.protein = r1(it.per100.protein * k);
  it.fat = r1(it.per100.fat * k);
  it.carbs = r1(it.per100.carbs * k);
  return it;
}
function recalcMeal(m) {
  const t = m.items.reduce((a, i) => ({ kcal: a.kcal + i.kcal, protein: a.protein + i.protein, fat: a.fat + i.fat, carbs: a.carbs + i.carbs }), { kcal: 0, protein: 0, fat: 0, carbs: 0 });
  m.kcal = Math.round(t.kcal);
  m.kcal_low = Math.round(t.kcal * 0.85);
  m.kcal_high = Math.round(t.kcal * 1.15);
  m.protein = r1(t.protein);
  m.fat = r1(t.fat);
  m.carbs = r1(t.carbs);
  return m;
}

const TARGETS = { kcal: 1650, protein: 110, fat: 55, carbs: 170, water_ml: 2100 };
const prof = { sex: 'male', birth_year: 1990, height_cm: 178, goal_weight_kg: 76, activity: 2, rate_kg_week: 0.5, tz: 'Europe/Moscow', eating_cutoff: '21:00' };
const mockProfile = () => ({ ...prof, age: new Date().getFullYear() - prof.birth_year, weight_kg: 82.4 });
const rems = [
  { kind: 'morning', title: 'Утро', sub: 'План на день', time: '08:30', enabled: true },
  { kind: 'midday', title: 'День', sub: 'Как идёт питание', time: '13:30', enabled: true },
  { kind: 'evening', title: 'Вечер', sub: 'Итоги дня', time: '21:30', enabled: true },
  { kind: 'weekly', title: 'Итоги недели', sub: 'По воскресеньям', time: '19:00', enabled: true },
  { kind: 'water', title: 'Вода', sub: 'Напомнить выпить воды', time: '16:00', enabled: false },
  { kind: 'weigh', title: 'Взвешивание', sub: 'Записать вес', time: '07:30', enabled: true },
];
const meals = new Map();
const water = new Map(); // date -> [{id, ml}]
const body = new Map(); // date -> {weight, sleep_min, score, steps, active_kcal}
let products = [];
let dishes = [];

function makeMeal(date, type, tplIdx, withPhotos, time) {
  const tpl = TEMPLATES[type][tplIdx % TEMPLATES[type].length];
  const nPh = withPhotos ? (type === 'lunch' && date === iso(new Date()) ? 2 : 1) : 0;
  const m = {
    id: nid(), date, time: time || TIMES[type][tplIdx % 3], type, source: nPh ? 'photo' : 'text',
    confidence: 0.55 + Math.round(rnd() * 4) / 10,
    photos: Array.from({ length: nPh }, (_, i) => ({ id: nid(), url: PHOTOS[(tplIdx + i + type.length) % PHOTOS.length], dish_id: i === 0 && tplIdx % 2 ? 2 : null })),
    items: tpl.map(([k, g]) => makeItem(k, Math.round(g * (0.9 + rnd() * 0.25)))),
    comment: nPh && tplIdx === 1 ? 'на подсолнечном масле, без соуса' : null,
  };
  recalcMeal(m);
  meals.set(m.id, m);
  return m;
}

function init() {
  const today = new Date();
  const T = iso(today);
  let w = 88.0;
  let trend = null;
  for (let i = 89; i >= 0; i--) {
    const d = iso(addDays(today, -i));
    // meals
    if (i === 0) {
      makeMeal(d, 'breakfast', 1, true, '08:15');
      makeMeal(d, 'lunch', 0, true, '13:20');
      makeMeal(d, 'snack', 4, false, '11:09');
      makeMeal(d, 'snack', 3, false, '14:12');
    } else if (rnd() > 0.04) {
      makeMeal(d, 'breakfast', Math.floor(rnd() * 3), rnd() > 0.3);
      makeMeal(d, 'lunch', Math.floor(rnd() * 3), rnd() > 0.3);
      makeMeal(d, 'dinner', Math.floor(rnd() * 3), rnd() > 0.3);
      if (rnd() > 0.5) makeMeal(d, 'snack', Math.floor(rnd() * 3), false);
    }
    // water
    const wm = i === 0 ? [250, 300] : Array.from({ length: 4 + Math.floor(rnd() * 4) }, () => pick([200, 250, 300, 500]));
    water.set(d, wm.map((ml) => ({ id: nid(), ml })));
    // body
    w -= 0.0627 + (rnd() - 0.5) * 0.12;
    const noisy = r1(w + (rnd() - 0.5) * 0.8);
    const hasW = i === 0 || rnd() > 0.4;
    const b = {
      weight: hasW ? (i === 0 ? 82.4 : noisy) : null,
      sleep_min: i === 0 ? 442 : rnd() > 0.12 ? Math.round(380 + rnd() * 120) : null,
      score: null, steps: i === 0 ? 6240 : rnd() > 0.1 ? Math.round(3500 + rnd() * 9500) : null, active_kcal: Math.round(180 + rnd() * 380),
    };
    b.score = b.sleep_min ? Math.round(62 + rnd() * 33) : null;
    if (b.weight !== null) trend = trend === null ? b.weight : trend * 0.8 + b.weight * 0.2;
    b.trend = trend === null ? null : r1(trend);
    body.set(d, b);
  }
  products = [
    { id: 300, name: 'Творог 5%', brand: 'Савушкин', kcal: 121, protein: 17, fat: 5, carbs: 1.8, default_grams: 200, unit_name: 'пачка', unit_grams: 180, barcode: null, times_used: 3 },
    { id: 301, name: 'Гречка ядрица (сухая)', brand: null, kcal: 313, protein: 12.6, fat: 3.3, carbs: 62.1, default_grams: 60, unit_name: null, unit_grams: null, barcode: null, times_used: 9 },
    { id: 302, name: 'Протеиновый батончик', brand: 'Bombbar', kcal: 340, protein: 36, fat: 10, carbs: 28, default_grams: 60, unit_name: 'батончик', unit_grams: 60, barcode: null, times_used: 1 },
    { id: 303, name: 'Йогурт греческий 2%', brand: 'Teos', kcal: 73, protein: 9, fat: 2, carbs: 4.5, default_grams: 140, unit_name: 'баночка', unit_grams: 140, barcode: null, times_used: 5 },
  ];
  dishes = [
    { id: 1, name: 'Тарелка 26 см', kind: 'plate', diameter_cm: 26, depth_cm: 2, volume_ml: null, photo_url: null, is_default: false },
    { id: 2, name: 'Глубокая тарелка', kind: 'deep_plate', diameter_cm: 20, depth_cm: 5, volume_ml: 500, photo_url: PHOTOS[4], is_default: true },
    { id: 3, name: 'Любимая кружка', kind: 'mug', diameter_cm: 8.5, depth_cm: 9.5, volume_ml: 300, photo_url: null, is_default: false },
  ];
  void T;
}
init();

const dayMeals = (date) => [...meals.values()].filter((m) => m.date === date && !m.deleted).sort((a, b) => a.time.localeCompare(b.time));
const waterOf = (date) => (water.get(date) || []).reduce((a, e) => a + e.ml, 0);
const todayISO = () => iso(new Date());

function dayTotals(date) {
  return dayMeals(date).reduce((a, m) => ({ kcal: a.kcal + m.kcal, protein: a.protein + m.protein, fat: a.fat + m.fat, carbs: a.carbs + m.carbs }), { kcal: 0, protein: 0, fat: 0, carbs: 0 });
}

function getDay(date) {
  const ms = dayMeals(date);
  const t = dayTotals(date);
  const b = body.get(date) || {};
  return {
    date, targets: TARGETS,
    totals: { kcal: Math.round(t.kcal), protein: r1(t.protein), fat: r1(t.fat), carbs: r1(t.carbs) },
    remaining_kcal: TARGETS.kcal - Math.round(t.kcal),
    water_ml: waterOf(date), meals: ms,
    sleep: b.sleep_min ? { duration_min: b.sleep_min, score: b.score } : null,
    activity: b.steps ? { steps: b.steps, active_kcal: b.active_kcal } : null,
    weight: b.weight ?? null,
    weight_last: b.weight != null ? { kg: b.weight, date } : { kg: 82.4, date: iso(addDays(date, -3)) },
  };
}

function getStats(range) {
  const n = range === 'week' ? 7 : range === 'month' ? 30 : 90;
  const today = new Date();
  const days = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = iso(addDays(today, -i));
    const ms = dayMeals(d);
    const t = dayTotals(d);
    const b = body.get(d) || {};
    days.push({
      date: d, kcal: ms.length ? Math.round(t.kcal) : null, protein: ms.length ? r1(t.protein) : null, fat: ms.length ? r1(t.fat) : null, carbs: ms.length ? r1(t.carbs) : null,
      target_kcal: TARGETS.kcal, water_ml: waterOf(d) || null, weight: b.weight ?? null, weight_trend: b.trend ?? null, sleep_min: b.sleep_min ?? null, steps: b.steps ?? null,
    });
  }
  const avg = (key) => { const v = days.map((x) => x[key]).filter((x) => x !== null); return v.length ? v.reduce((a, c) => a + c, 0) / v.length : null; };
  const logged = days.filter((d) => d.kcal !== null);
  const trends = days.map((d) => d.weight_trend).filter((x) => x !== null);
  return {
    range, days,
    summary: {
      avg_kcal: avg('kcal') === null ? null : Math.round(avg('kcal')), avg_protein: avg('protein') === null ? null : r1(avg('protein')),
      days_logged: logged.length,
      days_on_target: logged.filter((d) => d.kcal <= TARGETS.kcal * 1.05 && d.kcal >= TARGETS.kcal * 0.8).length,
      weight_change: trends.length > 1 ? r1(trends[trends.length - 1] - trends[0]) : null,
      avg_sleep_min: avg('sleep_min') === null ? null : Math.round(avg('sleep_min')),
      avg_steps: avg('steps') === null ? null : Math.round(avg('steps')),
      avg_water: avg('water_ml') === null ? null : Math.round(avg('water_ml')),
    },
    prev_summary: { avg_kcal: 1810, avg_protein: 84, days_logged: Math.max(1, logged.length - 1), days_on_target: Math.max(0, logged.length - 3), weight_change: -0.4, avg_sleep_min: 395, avg_steps: 6100, avg_water: 1500 },
    goals: { protein_g: TARGETS.protein, water_ml: TARGETS.water_ml, steps: 8000, sleep_min: 420 },
  };
}

function periodMock(period) {
  const n = period === 'week' ? 7 : 30;
  const today = new Date();
  const pts = [];
  for (let i = n - 1; i >= 0; i -= (n > 7 ? 3 : 1)) { const d = iso(addDays(today, -i)); pts.push({ date: d, v: r1(86.4 - (n - i) * (n > 7 ? 0.06 : 0.1)) }); }
  return {
    period, from: iso(addDays(today, -(n - 1))), to: iso(today), days: n, logged_days: n - 1, care: false,
    headline: 'Есть что поправить',
    focus: { icon: '🥩', title: 'Белок', text: 'В среднем 84 из 110 г, не хватает около 26 г в день. Добавь белок в каждый приём: яйца или творог утром, курица или рыба в обед, кефир вечером.' },
    metrics: [
      { key: 'kcal', label: 'Калории', value: '1 720 ккал', goal: 'норма 1 650', status: 'good', note: 'в норме 5 из 6 дн.', delta: { text: '−90 к прошлому', dir: 'good' } },
      { key: 'protein', label: 'Белок', value: '84 г', goal: 'цель 110 г', status: 'bad', note: 'цель достигнута 1 из 6 дн.', delta: { text: '+6 г к прошлому', dir: 'good' } },
      { key: 'water', label: 'Вода', value: '1 540 мл', goal: 'цель 2 100', status: 'ok', note: 'цель достигнута 2 из 7 дн.', delta: { text: 'как раньше', dir: 'flat' } },
      { key: 'steps', label: 'Шаги', value: '7 120', goal: 'цель 8 000', status: 'ok', note: '3 дн. с целью', delta: { text: '+1 020 к прошлому', dir: 'good' } },
      { key: 'sleep', label: 'Сон', value: '6 ч 20 м', goal: 'цель 7+ ч', status: 'bad', note: 'меньше 6 ч: 2 дня', delta: { text: '−15 мин к прошлому', dir: 'bad' } },
      { key: 'logged', label: 'Дней с едой', value: `${n - 1} из ${n}`, goal: '', status: 'good', note: '', delta: null },
    ],
    weight: { start: 86.0, now: 85.3, change: -0.7, per_week: -0.5, weighins: 5, goal: 76, remaining: 9.3, eta_weeks: 19, expected_change: -0.6, points: pts },
    good: ['Калории в целевом коридоре', 'Шагов стало больше на 1 020 в день', 'Тренд веса −0,7 кг за период'],
    patterns: [{ icon: '😴', title: 'Сон и аппетит', text: 'После коротких ночей (меньше 6,5 ч) ты ешь примерно на 280 ккал больше, чем после полноценных. Недосып усиливает голод: ранний отбой сработает лучше силы воли.' }],
    tips: [
      { icon: '🥩', title: 'Белок', text: 'В среднем 84 из 110 г, не хватает около 26 г в день.' },
      { icon: '😴', title: 'Сон', text: 'Сон в среднем 6 ч 20 мин. Сдвинь отбой на 30 минут раньше.' },
    ],
  };
}

class FormLike {
  constructor(fd) { this.fd = fd; }
  get(k) { return this.fd.get(k); }
  all(k) { return this.fd.getAll(k); }
}
const dishOut = (d) => ({ ...d });
const itemMeal = (itemId) => [...meals.values()].find((m) => m.items.some((i) => i.id === itemId));
const err = (status, error, message) => { const e = new Error(message); e.status = status; e.code = error; return Object.assign(e, { name: 'ApiError' }); };
const notFound = () => err(404, 'not_found', 'Не найдено.');

function guessType(time) {
  const h = parseInt((time || '12:00').slice(0, 2), 10);
  return h < 11 ? 'breakfast' : h < 16 ? 'lunch' : h < 21 ? 'dinner' : 'snack';
}
function nowHHMM() { const d = new Date(); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`; }

// Weekly mini-goal demo state
const WG_KINDS = {
  protein: { icon: '🥩', title: 'Белок в норме', hint: 'Белок в каждый приём пищи: яйца или творог, курица или рыба, кефир.' },
  water: { icon: '💧', title: 'Вода в норме', hint: 'Стакан после пробуждения, перед едой и в 16:00. Чай и кофе тоже считаются.' },
  late: { icon: '🌙', title: 'Без поздней еды', hint: 'Ужин на час раньше и с белком, так вечером меньше тянет.' },
};
const mockFavs = {}; // meal_id -> favorite_id
const customFavs = [];   // избранное, созданное с нуля
const favEdits = {};     // правки полей избранного по id
const FREQ = [
  { meal_id: 1, title: 'Овсянка с бананом +1', names: ['Овсянка с бананом', 'Кофе с молоком'], kcal: 410, protein: 16, fat: 11, carbs: 62, count: 9 },
  { meal_id: 2, title: 'Гречка +1', names: ['Гречка', 'Куриная грудка'], kcal: 520, protein: 41, fat: 12, carbs: 58, count: 6 },
  { meal_id: 3, title: 'Творог 5%', names: ['Творог 5%'], kcal: 180, protein: 30, fat: 5, carbs: 3, count: 4 },
  { meal_id: 4, title: 'Капучино', names: ['Капучино'], kcal: 98, protein: 5, fat: 5, carbs: 8, count: 3 },
];
function favList() {
  const starred = FREQ.filter((f) => mockFavs[f.meal_id]).map((f) => ({ ...f, id: mockFavs[f.meal_id], note: null, photo_url: null }));
  return [...customFavs, ...starred].map((f) => ({ ...f, ...(favEdits[f.id] || {}) }));
}
let weekGoal = null;
function weekGoalMock() {
  const recap = { week_start: '2026-10-26', good: ['Белок держится у цели', 'Воды хватает'], goal: { kind: 'water', icon: '💧', title: 'Вода в норме', done: 5, target: 4, achieved: true } };
  if (!weekGoal) {
    return { week_start: '2026-11-02', goal: null, recap, options: [
      { kind: 'protein', ...WG_KINDS.protein, target: 4, hits: 2 },
      { kind: 'water', ...WG_KINDS.water, target: 5, hits: 3 },
      { kind: 'late', ...WG_KINDS.late, target: 4, hits: 2 }] };
  }
  const k = WG_KINDS[weekGoal.kind];
  const states = ['ok', 'miss', 'ok', 'ok', 'ok', 'future', 'future'];
  const done = Math.min(weekGoal.target, 4);
  const achieved = done >= weekGoal.target;
  return { week_start: '2026-11-02', options: [], recap, goal: { kind: weekGoal.kind, ...k, target: weekGoal.target, done, achieved, possible: true,
    cells: states.map((state, i) => ({ date: '2026-11-0' + (2 + i), state })), message: achieved ? 'Цель недели выполнена. Это уже привычка, а не случайность.' : `Осталось ${weekGoal.target - done} дня из запланированных.` } };
}

// ?onb=1: приложение открывается как у нового человека (онбординг); после POST /onboarding — обычные данные.
let onbPending = /[?&]onb=1\b/.test(location.search);

function mockOnboarding(b) {
  const age = new Date().getFullYear() - b.birth_year;
  if (age < 18) { const e = new Error('Бот считает дефицит калорий и рассчитан на взрослых (18+).'); e.name = 'ApiError'; e.status = 400; e.code = 'underage'; throw e; }
  const bmr = 10 * b.weight_kg + 6.25 * b.height_cm - 5 * age + (b.sex === 'male' ? 5 : -161);
  const pal = [1.25, 1.5, 1.75, 2.2][(b.activity || 2) - 1];
  const rate = b.care_mode ? Math.min(b.rate_kg_week, 0.25) : b.rate_kg_week;
  const kcal = Math.round((bmr * pal - rate * 1100) / 10) * 10;
  const protein = Math.round(1.6 * b.goal_weight_kg), fat = Math.round(0.8 * b.goal_weight_kg);
  const carbs = Math.max(50, Math.round((kcal - protein * 4 - fat * 9) / 4));
  const lose = rate > 0 && b.goal_weight_kg < b.weight_kg;
  const goalDate = lose ? iso(addDays(new Date(), Math.ceil(((b.weight_kg - b.goal_weight_kg) / rate) * 7))) : null;
  Object.assign(TARGETS, { kcal, protein, fat, carbs });
  Object.assign(prof, { sex: b.sex, birth_year: b.birth_year, height_cm: b.height_cm, goal_weight_kg: b.goal_weight_kg, activity: b.activity, rate_kg_week: rate });
  onbPending = false;
  return { profile: mockProfile(), targets: { kcal, protein, fat, carbs, water_ml: Math.round(b.weight_kg * 30 / 50) * 50 }, notes: [], goal_date: goalDate };
}

const routes = [
  ['GET', /^\/me$/, () => onbPending ? { onboarded: false, user: { first_name: 'Аня' } } : ({
    onboarded: true,
    user: { tg_id: 1, first_name: 'Жан', sex: 'm', height_cm: 178, goal_weight_kg: 76, tz: 'Europe/Moscow', eating_cutoff: '21:00' },
    targets: TARGETS, weight: { latest: 82.4, trend: body.get(todayISO()).trend, start: 88.0 },
  })],
  ['POST', /^\/onboarding$/, (m, q, b) => mockOnboarding(b)],
  ['GET', /^\/profile$/, () => ({ profile: mockProfile(), targets: TARGETS, first_name: 'Жан' })],
  ['PATCH', /^\/profile$/, (m, q, b) => {
    const old = TARGETS.kcal;
    const { recalc, ...rest } = b || {};
    Object.assign(prof, rest);
    const affects = recalc || ['sex', 'birth_year', 'height_cm', 'goal_weight_kg', 'activity', 'rate_kg_week'].some((k) => k in rest);
    if (affects) {
      const age = new Date().getFullYear() - (prof.birth_year || 1990);
      const bmr = 10 * 82.4 + 6.25 * prof.height_cm - 5 * age + (prof.sex === 'male' ? 5 : -161);
      TARGETS.kcal = Math.round((bmr * [1.2, 1.375, 1.55, 1.725][prof.activity - 1] - prof.rate_kg_week * 1100) / 10) * 10;
    }
    return { profile: mockProfile(), targets: TARGETS, result: { changed: true, target: affects ? { old, new: TARGETS.kcal, notes: [] } : null } };
  }],
  ['PUT', /^\/targets$/, (m, q, b) => {
    Object.assign(TARGETS, { kcal: b.kcal, protein: b.protein, fat: b.fat, carbs: b.carbs });
    return { profile: mockProfile(), targets: TARGETS, result: { kcal: b.kcal, raised: false } };
  }],
  ['GET', /^\/reminders$/, () => ({ reminders: rems })],
  ['PUT', /^\/reminders\/(\w+)$/, (m, q, b) => { Object.assign(rems.find((r) => r.kind === m[1]), b); return { reminders: rems }; }],
  ['DELETE', /^\/me$/, (m, q, b) => { if (!b || b.confirm !== 'УДАЛИТЬ') throw Object.assign(new Error('Для удаления нужно подтверждение.'), { status: 400 }); return { deleted: true }; }],
  ['GET', /^\/day$/, (m, q) => getDay(q.date || todayISO())],
  ['GET', /^\/stats$/, (m, q) => getStats(q.range || 'week')],
  ['GET', /^\/meals\/(\d+)$/, (m) => { const x = meals.get(+m[1]); if (!x) throw notFound(); x.favorite_id = mockFavs[x.id] || null; return x; }],
  ['POST', /^\/meals$/, async (m, q, b) => {
    await sleep(1800);
    const f = new FormLike(b);
    const files = f.all('photo').filter((x) => x && x.size !== undefined);
    const dishIds = f.all('dish_id');
    const time = f.get('time') || nowHHMM();
    const type = guessType(time);
    const mm = makeMeal(f.get('date') || todayISO(), type, Math.floor(rnd() * 3), false, time);
    if (files.length) {
      mm.source = 'photo';
      mm.photos = files.map((file, i) => ({ id: nid(), url: URL.createObjectURL(file), dish_id: dishIds[i] ? +dishIds[i] : null }));
    }
    mm.comment = f.get('comment') || f.get('text') || null;
    if (/масл/i.test(mm.comment || '')) { mm.items.push(makeItem('oil', 10)); recalcMeal(mm); }
    return mm;
  }, 201],
  ['PATCH', /^\/meals\/(\d+)$/, (m, q, b) => { const x = meals.get(+m[1]); if (!x) throw notFound(); Object.assign(x, b); return x; }],
  ['DELETE', /^\/meals\/(\d+)$/, (m) => { const x = meals.get(+m[1]); if (x) x.deleted = true; return null; }],
  ['POST', /^\/meals\/(\d+)\/refine$/, async (m, q, b) => {
    await sleep(1200);
    const x = meals.get(+m[1]); if (!x) throw notFound();
    x.comment = b.comment;
    if (/масл/i.test(b.comment || '') && !x.items.some((i) => i.name.includes('Масло'))) x.items.push(makeItem('oil', 10));
    x.items.forEach((i) => { i.edited = true; });
    return recalcMeal(x);
  }],
  ['PATCH', /^\/items\/(\d+)$/, async (m, q, b) => {
    // Разброс задержки (до ~1.2 с; ?slow=1 — до 3 с): так видно, что ответы не перезаписывают поле.
    await sleep(80 + Math.random() * (/[?&]slow=1\b/.test(location.search) ? 3000 : 1100));
    const x = itemMeal(+m[1]); if (!x) throw notFound();
    const it = x.items.find((i) => i.id === +m[1]);
    if (!(b.grams >= 1 && b.grams <= 5000)) throw err(422, 'validation', 'Граммы: от 1 до 5000.');
    it.grams = b.grams; it.edited = true; calcItem(it);
    return recalcMeal(x);
  }],
  ['POST', /^\/items\/(\d+)\/barcode$/, async (m, q, b) => {
    await sleep(400);
    const x = itemMeal(+m[1]); if (!x) throw notFound();
    if (String(b.code).endsWith('0000')) throw err(404, 'not_found', 'В базе такого штрихкода нет');
    const it = x.items.find((i) => i.id === +m[1]);
    it.name = 'Сосиски куриные, Папа может'; it.per100 = { kcal: 214, protein: 11.5, fat: 18.3, carbs: 1.6 };
    it.edited = true; calcItem(it);
    return recalcMeal(x);
  }],
  ['DELETE', /^\/items\/(\d+)$/, (m) => {
    const x = itemMeal(+m[1]); if (!x) throw notFound();
    x.items = x.items.filter((i) => i.id !== +m[1]);
    return recalcMeal(x);
  }],
  ['POST', /^\/items\/(\d+)\/save_product$/, (m, q, b) => {
    const x = itemMeal(+m[1]); if (!x) throw notFound();
    const it = x.items.find((i) => i.id === +m[1]);
    const p = { id: nid(), name: (b && b.name) || it.name, brand: null, ...it.per100, default_grams: it.grams, unit_name: null, unit_grams: null, barcode: null, times_used: 1 };
    products.unshift(p);
    return p;
  }, 201],
  ['GET', /^\/products$/, (m, q) => ({ products: products.filter((p) => !q.q || (p.name + ' ' + (p.brand || '')).toLowerCase().includes(q.q.toLowerCase())) })],
  ['POST', /^\/products$/, (m, q, b) => { const p = { id: nid(), brand: null, default_grams: null, unit_name: null, unit_grams: null, barcode: null, times_used: 0, ...b }; products.unshift(p); return p; }, 201],
  ['PATCH', /^\/products\/(\d+)$/, (m, q, b) => { const p = products.find((x) => x.id === +m[1]); if (!p) throw notFound(); return Object.assign(p, b); }],
  ['DELETE', /^\/products\/(\d+)$/, (m) => { products = products.filter((x) => x.id !== +m[1]); return null; }],
  ['POST', /^\/products\/label$/, async () => {
    await sleep(1500);
    return {
      draft: { name: 'Кефир 2,5%', brand: 'Простоквашино', kcal: 53, protein: 2.9, fat: 2.5, carbs: 4, default_grams: 250, unit_name: 'стакан', unit_grams: 250, barcode: null },
      confidence: 0.86, notes: ['Значения указаны на 100 мл, принята плотность 1 г/мл.', 'Вес упаковки не найден.'],
    };
  }],
  ['GET', /^\/products\/barcode\/(\d+)$/, async (m) => {
    await sleep(700);
    if (m[1] === '0000000000000') throw notFound();
    return { source: 'off', existing_id: null, draft: { name: 'Кефир 2,5%', brand: 'Простоквашино', kcal: 53, protein: 2.9, fat: 2.5, carbs: 4, default_grams: 250, unit_name: 'стакан', unit_grams: 250, barcode: m[1] } };
  }],
  ['GET', /^\/dishes$/, () => ({ dishes: dishes.map(dishOut) })],
  ['POST', /^\/dishes$/, (m, q, b) => {
    const f = new FormLike(b);
    const num = (k) => (f.get(k) ? +f.get(k) : null);
    const file = f.get('photo');
    const d = { id: nid(), name: f.get('name'), kind: f.get('kind') || 'other', diameter_cm: num('diameter_cm'), depth_cm: num('depth_cm'), volume_ml: num('volume_ml'), photo_url: file && file.size ? URL.createObjectURL(file) : null, is_default: f.get('is_default') === 'true' };
    if (d.is_default) dishes.forEach((x) => { x.is_default = false; });
    dishes.push(d);
    return d;
  }, 201],
  ['PATCH', /^\/dishes\/(\d+)$/, (m, q, b) => {
    const d = dishes.find((x) => x.id === +m[1]); if (!d) throw notFound();
    if (b.is_default) dishes.forEach((x) => { x.is_default = false; });
    return Object.assign(d, b);
  }],
  ['DELETE', /^\/dishes\/(\d+)$/, (m) => { dishes = dishes.filter((x) => x.id !== +m[1]); return null; }],
  ['POST', /^\/water$/, (m, q, b) => {
    const T = todayISO(); const e = { id: nid(), ml: b.ml };
    water.set(T, [...(water.get(T) || []), e]);
    return { water_ml: waterOf(T), goal_ml: TARGETS.water_ml, id: e.id };
  }],
  ['GET', /^\/drinks$/, () => ({ drinks: MOCK_DRINKS })],
  ['POST', /^\/drinks$/, (m, q, b) => {
    const T = todayISO();
    const d = MOCK_DRINKS.find((x) => x.id === b.drink);
    if (!d) throw Object.assign(new Error('Неизвестный напиток.'), { status: 400 });
    if (d.hydrates) water.set(T, [...(water.get(T) || []), { id: nid(), ml: b.ml }]);
    const kcal = Math.round(d.kcal_100 * b.ml / 100 + (d.sugar ? (b.sugar_tsp || 0) * 20 : 0));
    return { id: nid(), kcal, water_ml: waterOf(T), goal_ml: TARGETS.water_ml };
  }, 201],
  ['DELETE', /^\/water\/(\d+)$/, (m) => {
    for (const [d, list] of water) water.set(d, list.filter((e) => e.id !== +m[1]));
    return { water_ml: waterOf(todayISO()), goal_ml: TARGETS.water_ml };
  }],
  ['POST', /^\/weight$/, (m, q, b) => {
    const bd = body.get(b.date || todayISO()); if (!bd) return { weight: b.weight_kg, trend: b.weight_kg }; bd.weight = b.weight_kg; bd.trend = r1((bd.trend || b.weight_kg) * 0.8 + b.weight_kg * 0.2);
    return { weight: b.weight_kg, trend: bd.trend };
  }],
  ['POST', /^\/screens$/, async () => {
    await sleep(1400);
    const risky = rnd() > 0.5;
    return { id: nid(), screen: 'mifitness_sleep', date: todayISO(), metrics: { sleep_duration_min: 431, sleep_deep_min: 96, sleep_rem_min: 88, sleep_score: 78, steps: 9800, active_kcal: 410 }, confidence: risky ? 0.72 : 0.93, needs_confirm: risky, reasons: risky ? ['дата не распознана — ставлю сегодняшнюю', 'уверенность 72%'] : [], applied: risky ? null : 'sleep' };
  }],
  ['POST', /^\/screens\/(\d+)\/confirm$/, (m, q, b) => ({ applied: !!(b && b.apply) })],
  ['POST', /^\/sleep$/, (m, q, b) => {
    const bd = body.get(b.date || todayISO());
    if (bd) {
      if (b.duration_min) bd.sleep_min = b.duration_min;
      else if (b.start && b.end) { const [sh, sm] = b.start.split(':').map(Number); const [eh, em] = b.end.split(':').map(Number); let d = (eh * 60 + em) - (sh * 60 + sm); if (d <= 0) d += 1440; bd.sleep_min = d; }
      if (b.score) bd.score = b.score;
    }
    return { ok: true };
  }],
  ['POST', /^\/activity$/, (m, q, b) => { const bd = body.get(b.date || todayISO()); if (bd) { if (b.steps) bd.steps = b.steps; if (b.active_kcal) bd.active_kcal = b.active_kcal; } return { ok: true }; }],
  ['GET', /^\/frequent$/, () => ({ items: [
    ...favList().map((f) => ({ meal_id: f.meal_id, favorite_id: f.id, title: f.title, names: f.names, kcal: f.kcal, count: 0 })),
    ...FREQ.filter((f) => !mockFavs[f.meal_id]).map((f) => ({ meal_id: f.meal_id, favorite_id: null, title: f.title, names: f.names, kcal: f.kcal, count: f.count }))] })],
  ['GET', /^\/favorites$/, () => ({ items: favList() })],
  ['GET', /^\/favorites\/(\d+)$/, (m) => { const f = favList().find((x) => x.id === +m[1]); if (!f) throw notFound(); return f; }],
  ['POST', /^\/favorites\/custom$/, (m, q, b) => {
    const id = 500 + customFavs.length + 1;
    customFavs.unshift({ id, meal_id: id, title: b.title, note: b.note || null, kcal: Math.round(b.kcal), protein: +b.protein || 0, fat: +b.fat || 0, carbs: +b.carbs || 0, names: [b.title], photo_url: null });
    return { id };
  }, 201],
  ['PATCH', /^\/favorites\/(\d+)$/, (m, q, b) => {
    const f = favList().find((x) => x.id === +m[1]);
    if (!f) throw notFound();
    favEdits[f.id] = { ...(favEdits[f.id] || {}), ...b };
    return favList().find((x) => x.id === +m[1]);
  }],
  ['POST', /^\/favorites$/, (m, q, b) => { mockFavs[b.meal_id] = 100 + Number(b.meal_id); return { id: mockFavs[b.meal_id] }; }, 201],
  ['DELETE', /^\/favorites\/(\d+)$/, (m) => {
    for (const k of Object.keys(mockFavs)) if (mockFavs[k] === Number(m[1])) delete mockFavs[k];
    const i = customFavs.findIndex((x) => x.id === Number(m[1])); if (i >= 0) customFavs.splice(i, 1);
    return null;
  }],
  ['POST', /^\/meals\/product$/, (m, q, b) => ({ id: 9002, kcal: Math.round(b.kcal * b.grams / 100) }), 201],
  ['POST', /^\/favorites\/(\d+)\/repeat$/, () => ({ id: 9001, kcal: 410 })],
  ['POST', /^\/meals\/(\d+)\/repeat$/, () => ({ id: 9001, kcal: 410 })],
  ['GET', /^\/week-goal$/, () => weekGoalMock()],
  ['PUT', /^\/week-goal$/, (m, q, b) => { weekGoal = { kind: b.kind, target: b.target }; return weekGoalMock(); }],
  ['DELETE', /^\/week-goal$/, () => { weekGoal = null; return weekGoalMock(); }],
  ['GET', /^\/analysis$/, (m, q) => (q.period && q.period !== 'day' ? periodMock(q.period) : {
    date: q.date || todayISO(),
    closed: true,
    headline: 'В целом неплохо',
    incomplete: false,
    rows: [
      { label: 'Калории', value: '1 580 ккал', goal: '1 650', status: 'good' },
      { label: 'Белок', value: '92 г', goal: '120 г', status: 'ok' },
      { label: 'Вода', value: '900 мл', goal: '2 000 мл', status: 'bad' },
      { label: 'Шаги', value: '9 400', goal: '8 000', status: 'good' },
      { label: 'Сон', value: '6 ч 40 мин', goal: '7+ ч', status: 'ok' },
    ],
    good: ['Калории в цели', 'Шаги в плане (9 400)'],
    tips: [
      { icon: '💧', title: 'Вода', text: 'Воды мало: 900 из 2 000 мл. Поставьте стакан на виду и пейте по паре глотков каждый час. Часто «голод» вечером оказывается жаждой и усталостью.' },
      { icon: '🥩', title: 'Белок', text: 'Белок держит сытость и бережёт мышцы. Сегодня 92 из 120 г: добавьте ещё около 28 г, например 150 г творога.' },
    ],
  })],
];

export async function mockRequest(method, path, query, body) {
  await sleep(120 + Math.random() * 160);
  for (const [mt, re, fn, status] of routes) {
    if (mt !== method) continue;
    const m = path.match(re);
    if (!m) continue;
    try {
      const res = await fn(m, query || {}, body);
      void status;
      return res === undefined ? null : JSON.parse(JSON.stringify(res, (k, v) => v)); // detach from store
    } catch (e) {
      if (e.name === 'ApiError') {
        const { ApiError } = await import('./api.js');
        throw new ApiError(e.status, e.code, e.message);
      }
      throw e;
    }
  }
  const { ApiError } = await import('./api.js');
  throw new ApiError(404, 'not_found', 'Не найдено.');
}

export function mockCsv(kind) {
  const rows = [];
  if (kind === 'meals') {
    rows.push('date,time,type,kcal,protein,fat,carbs');
    [...meals.values()].filter((m) => !m.deleted).sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time))
      .forEach((m) => rows.push([m.date, m.time, m.type, m.kcal, m.protein, m.fat, m.carbs].join(',')));
  } else {
    rows.push('date,value');
    for (const [d, b] of body) {
      const v = kind === 'body' ? b.weight : kind === 'sleep' ? b.sleep_min : kind === 'activity' ? b.steps : waterOf(d);
      if (v !== null && v !== undefined) rows.push(`${d},${v}`);
    }
  }
  return rows.join('\n');
}
