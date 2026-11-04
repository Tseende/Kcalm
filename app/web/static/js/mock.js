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
};
const TEMPLATES = {
  breakfast: [[['oats', 250], ['banana', 120]], [['egg', 110], ['bread', 60], ['tomato', 100]], [['cottage', 200], ['apple', 150]]],
  lunch: [[['chicken', 150], ['buckwheat', 180], ['tomato', 150]], [['salmon', 140], ['rice', 160], ['tomato', 120]], [['borscht', 300], ['bread', 40]]],
  dinner: [[['salmon', 150], ['tomato', 200]], [['chicken', 130], ['rice', 100], ['tomato', 150]], [['cottage', 220], ['banana', 100]]],
  snack: [[['apple', 180]], [['cottage', 150]], [['banana', 110]]],
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
      makeMeal(d, 'snack', 1, false, '16:45');
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
    },
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

const routes = [
  ['GET', /^\/me$/, () => ({
    user: { tg_id: 1, first_name: 'Жан', sex: 'm', height_cm: 178, goal_weight_kg: 76, tz: 'Europe/Moscow', eating_cutoff: '21:00' },
    targets: TARGETS, weight: { latest: 82.4, trend: body.get(todayISO()).trend, start: 88.0 },
  })],
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
    await sleep(120);
    const x = itemMeal(+m[1]); if (!x) throw notFound();
    const it = x.items.find((i) => i.id === +m[1]);
    it.grams = b.grams; it.edited = true; calcItem(it);
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
  ['GET', /^\/drinks$/, () => ({ drinks: [{"id": "cola_zero", "name": "Кола без сахара", "emoji": "🥤", "color": "gray", "kcal_100": 0, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "cola", "name": "Кола", "emoji": "🥤", "color": "red", "kcal_100": 42.4, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "fanta", "name": "Фанта", "emoji": "🍊", "color": "orange", "kcal_100": 40, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "tea", "name": "Чай", "emoji": "🍵", "color": "green", "kcal_100": 0, "sizes": [200, 300, 400], "sugar": true, "hydrates": true}, {"id": "americano", "name": "Американо", "emoji": "☕", "color": "indigo", "kcal_100": 0.4, "sizes": [200, 300], "sugar": true, "hydrates": true}, {"id": "cappuccino", "name": "Капучино", "emoji": "☕", "color": "orange", "kcal_100": 39.0, "sizes": [180, 250, 350], "sugar": true, "hydrates": true}, {"id": "flat_white", "name": "Флэт уайт", "emoji": "☕", "color": "purple", "kcal_100": 52.6, "sizes": [160, 200], "sugar": true, "hydrates": true}, {"id": "latte", "name": "Латте", "emoji": "🥛", "color": "blue", "kcal_100": 42.4, "sizes": [250, 350], "sugar": true, "hydrates": true}, {"id": "juice", "name": "Сок", "emoji": "🧃", "color": "yellow", "kcal_100": 44.9, "sizes": [200, 250], "sugar": false, "hydrates": false}] })],
  ['POST', /^\/drinks$/, (m, q, b) => {
    const T = todayISO();
    const d = [{"id": "cola_zero", "name": "Кола без сахара", "emoji": "🥤", "color": "gray", "kcal_100": 0, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "cola", "name": "Кола", "emoji": "🥤", "color": "red", "kcal_100": 42.4, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "fanta", "name": "Фанта", "emoji": "🍊", "color": "orange", "kcal_100": 40, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "tea", "name": "Чай", "emoji": "🍵", "color": "green", "kcal_100": 0, "sizes": [200, 300, 400], "sugar": true, "hydrates": true}, {"id": "americano", "name": "Американо", "emoji": "☕", "color": "indigo", "kcal_100": 0.4, "sizes": [200, 300], "sugar": true, "hydrates": true}, {"id": "cappuccino", "name": "Капучино", "emoji": "☕", "color": "orange", "kcal_100": 39.0, "sizes": [180, 250, 350], "sugar": true, "hydrates": true}, {"id": "flat_white", "name": "Флэт уайт", "emoji": "☕", "color": "purple", "kcal_100": 52.6, "sizes": [160, 200], "sugar": true, "hydrates": true}, {"id": "latte", "name": "Латте", "emoji": "🥛", "color": "blue", "kcal_100": 42.4, "sizes": [250, 350], "sugar": true, "hydrates": true}, {"id": "juice", "name": "Сок", "emoji": "🧃", "color": "yellow", "kcal_100": 44.9, "sizes": [200, 250], "sugar": false, "hydrates": false}].find((x) => x.id === b.drink);
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
    const bd = body.get(todayISO()); bd.weight = b.weight_kg; bd.trend = r1((bd.trend || b.weight_kg) * 0.8 + b.weight_kg * 0.2);
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
    { meal_id: 1, favorite_id: mockFavs[1] || null, title: 'Овсянка с бананом +1', names: ['Овсянка с бананом', 'Кофе с молоком'], kcal: 410, count: 9 },
    { meal_id: 2, favorite_id: mockFavs[2] || null, title: 'Гречка +1', names: ['Гречка', 'Куриная грудка'], kcal: 520, count: 6 },
    { meal_id: 3, favorite_id: mockFavs[3] || null, title: 'Творог 5%', names: ['Творог 5%'], kcal: 180, count: 4 },
    { meal_id: 4, favorite_id: mockFavs[4] || null, title: 'Капучино', names: ['Капучино'], kcal: 98, count: 3 }]
    .sort((a, b) => (b.favorite_id ? 1 : 0) - (a.favorite_id ? 1 : 0)) })],
  ['POST', /^\/favorites$/, (m, q, b) => { mockFavs[b.meal_id] = 100 + Number(b.meal_id); return { id: mockFavs[b.meal_id] }; }],
  ['DELETE', /^\/favorites\/(\d+)$/, (m) => { for (const k of Object.keys(mockFavs)) if (mockFavs[k] === Number(m[1])) delete mockFavs[k]; return null; }],
  ['POST', /^\/favorites\/(\d+)\/repeat$/, () => ({ id: 9001, kcal: 410 })],
  ['POST', /^\/meals\/(\d+)\/repeat$/, () => ({ id: 9001, kcal: 410 })],
  ['GET', /^\/week-goal$/, () => weekGoalMock()],
  ['PUT', /^\/week-goal$/, (m, q, b) => { weekGoal = { kind: b.kind, target: b.target }; return weekGoalMock(); }],
  ['DELETE', /^\/week-goal$/, () => { weekGoal = null; return weekGoalMock(); }],
  ['GET', /^\/analysis$/, (m, q) => ({
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
