import { lang } from './i18n.js';
const L = (ru, en) => (lang() === 'en' ? en : ru);
const MOCK_DRINKS = [{"id": "cola_zero", "name": L("Кола без сахара", "Cola Zero"), "emoji": "🥤", "color": "gray", "kcal_100": 0, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "cola", "name": L("Кола", "Cola"), "emoji": "🥤", "color": "red", "kcal_100": 42.4, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "fanta", "name": L("Фанта", "Fanta"), "emoji": "🍊", "color": "orange", "kcal_100": 40, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "tea", "name": L("Чай", "Tea"), "emoji": "🍵", "color": "green", "kcal_100": 0, "sizes": [200, 300, 400], "sugar": true, "hydrates": true}, {"id": "americano", "name": L("Американо", "Americano"), "emoji": "☕", "color": "indigo", "kcal_100": 0.4, "sizes": [200, 300], "sugar": true, "hydrates": true}, {"id": "cappuccino", "name": L("Капучино", "Cappuccino"), "emoji": "☕", "color": "orange", "kcal_100": 39.0, "sizes": [180, 250, 350], "sugar": true, "hydrates": true}, {"id": "flat_white", "name": L("Флэт уайт", "Flat white"), "emoji": "☕", "color": "purple", "kcal_100": 52.6, "sizes": [160, 200], "sugar": true, "hydrates": true}, {"id": "latte", "name": L("Латте", "Latte"), "emoji": "🥛", "color": "blue", "kcal_100": 42.4, "sizes": [250, 350], "sugar": true, "hydrates": true}, {"id": "juice", "name": L("Сок", "Juice"), "emoji": "🧃", "color": "yellow", "kcal_100": 44.9, "sizes": [200, 250], "sugar": false, "hydrates": false}, {"id": "smoothie", "name": L("Смузи", "Smoothie"), "emoji": "🥤", "color": "pink", "kcal_100": 52, "sizes": [250, 350, 450], "sugar": false, "hydrates": false}, {"id": "shake", "name": L("Протеиновый коктейль", "Protein shake"), "emoji": "🥛", "color": "teal", "kcal_100": 62, "sizes": [250, 300, 400], "sugar": false, "hydrates": false}, {"id": "energy", "name": L("Энергетик", "Energy drink"), "emoji": "⚡", "color": "yellow", "kcal_100": 44, "sizes": [250, 330, 450], "sugar": false, "hydrates": false}, {"id": "beer", "name": L("Пиво", "Beer"), "emoji": "🍺", "color": "orange", "kcal_100": 42.9, "sizes": [330, 500, 568], "sugar": false, "hydrates": false}, {"id": "beer0", "name": L("Пиво безалкогольное", "Non-alcoholic beer"), "emoji": "🍺", "color": "gray", "kcal_100": 21.2, "sizes": [330, 500], "sugar": false, "hydrates": false}, {"id": "wine", "name": L("Вино", "Wine"), "emoji": "🍷", "color": "red", "kcal_100": 77.0, "sizes": [125, 150, 200], "sugar": false, "hydrates": false}];
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
  oats: [L('Овсяная каша на молоке', "Oatmeal with milk"), 102, 3.4, 3.4, 15],
  egg: [L('Яйцо варёное', "Boiled egg"), 157, 12.7, 11.5, 0.7],
  bread: [L('Хлеб цельнозерновой', "Whole-grain bread"), 247, 13, 3.4, 41],
  tomato: [L('Салат из огурцов и помидоров', "Cucumber and tomato salad"), 62, 1.2, 4.5, 4.4],
  chicken: [L('Куриная грудка запечённая', "Baked chicken breast"), 165, 31, 3.6, 0],
  buckwheat: [L('Гречка отварная', "Boiled buckwheat"), 110, 4.1, 1.1, 21.4],
  salmon: [L('Лосось запечённый', "Baked salmon"), 208, 20, 13, 0],
  rice: [L('Рис отварной', "Boiled rice"), 130, 2.7, 0.3, 28],
  cottage: [L('Творог 5%', "Cottage cheese 5%"), 121, 17, 5, 1.8],
  apple: [L('Яблоко', "Apple"), 52, 0.3, 0.2, 14],
  banana: [L('Банан', "Banana"), 89, 1.1, 0.3, 22.8],
  borscht: [L('Борщ', "Borscht"), 49, 2.1, 2.4, 5],
  oil: [L('Масло оливковое', "Olive oil"), 884, 0, 100, 0],
  americano: [L('Американо', "Americano"), 0.4, 0.1, 0, 0],
  juice: [L('Сок', "Juice"), 45, 0.5, 0.1, 10.5],
  wine: [L('Вино красное', "Red wine"), 85, 0.1, 0, 2.6],
  sausage: [L('куриные сосиски', "chicken sausages"), 220, 12, 18, 2],
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
  const exact = m.items.length > 0 && m.items.every((i) => i.source === 'manual' || i.source === 'user' || i.source === 'seed' && i.confidence >= 0.99);
  m.kcal_low = Math.round(t.kcal * (exact ? 1 : 0.85));
  m.kcal_high = Math.round(t.kcal * (exact ? 1 : 1.15));
  if (m.items.length) m.confidence = Math.min(...m.items.map((i) => i.confidence));
  m.protein = r1(t.protein);
  m.fat = r1(t.fat);
  m.carbs = r1(t.carbs);
  return m;
}

const TARGETS = { kcal: 1650, protein: 110, fat: 55, carbs: 170, water_ml: 2100 };
const prof = { sex: 'male', birth_year: 1990, height_cm: 178, goal_weight_kg: 76, activity: 2, rate_kg_week: 0.5, tz: 'Europe/Moscow', eating_cutoff: '21:00' };
const mockProfile = () => ({ ...prof, age: new Date().getFullYear() - prof.birth_year, weight_kg: 82.4 });
const rems = [
  { kind: 'morning', title: L('Утро', "Morning"), sub: L('План на день', "Plan for the day"), time: '08:30', enabled: true },
  { kind: 'midday', title: L('День', "Midday"), sub: L('Как идёт питание', "How eating is going"), time: '13:30', enabled: true },
  { kind: 'evening', title: L('Вечер', "Evening"), sub: L('Итоги дня', "Daily summary"), time: '21:30', enabled: true },
  { kind: 'weekly', title: L('Итоги недели', "Weekly summary"), sub: L('По воскресеньям', "On Sundays"), time: '19:00', enabled: true },
  { kind: 'water', title: L('Вода', "Water"), sub: L('Напомнить выпить воды', "Reminder to drink water"), time: '16:00', enabled: false },
  { kind: 'weigh', title: L('Взвешивание', "Weigh-in"), sub: L('Записать вес', "Log your weight"), time: '07:30', enabled: true },
  { kind: 'kitchen_close', title: L('Кухня закрывается', "Kitchen closes"), sub: L('За 15 минут до «Не есть после»', "15 minutes before “Don't eat after”"), time: '20:45', enabled: true, fixed_time: true },
];
const REM_TEMPLATES = [
  { key: 'breakfast', title: L('Завтрак', "Breakfast"), time: '08:30', text: L('Время завтрака. Запиши, что на тарелке: хватит пары слов или фото.', "Breakfast time. Log what is on your plate: a few words or a photo is enough.") },
  { key: 'lunch', title: L('Обед', "Lunch"), time: '13:00', text: L('Пора пообедать. Запиши обед: хватит пары слов или фото.', "Time for lunch. Log it: a few words or a photo is enough.") },
  { key: 'dinner', title: L('Ужин', "Dinner"), time: '19:00', text: L('Время ужина. Запиши, что на тарелке: пары слов хватит.', "Dinner time. Log what is on your plate: a few words are enough.") },
  { key: 'snack', title: L('Перекус', "Snack"), time: '16:30', text: L('Время перекуса. Если что-то перекушено, запиши: хватит пары слов.', "Snack time. If you had something, log it: a few words are enough.") },
  { key: 'water', title: L('Вода', "Water"), time: '10:00', every_min: 120, until_time: '20:00', text: L('Глоток воды сейчас не помешает. Выпей стакан и отметь в приложении.', "A sip of water would be nice right now. Have a glass and mark it in the app.") },
  { key: 'steps', title: L('Внести шаги', "Log steps"), time: '21:30', text: L('Загляни в счётчик шагов и внеси итог за день.', "Check your step counter and log the daily total.") },
  { key: 'sleep', title: L('Записать сон', "Log sleep"), time: '09:00', text: L('Запиши сон за ночь: так видно, как идёт восстановление.', "Log last night's sleep so you can see how recovery is going.") },
  { key: 'weigh', title: L('Взвеситься', "Weigh in"), time: '07:30', text: L('Утреннее взвешивание: после туалета, до еды и воды. Одна цифра ничего не значит, смотрим на тренд.', "Morning weigh-in: after the bathroom, before food and water. One number means little, we look at the trend.") },
  { key: 'walk', title: L('Прогулка', "Walk"), time: '18:00', text: L('Время прогулки. Даже 15 минут на свежем воздухе помогают.', "Time for a walk. Even 15 minutes of fresh air helps.") },
  { key: 'vitamins', title: L('Витамины / таблетки', "Vitamins / pills"), time: '09:00', text: L('Время витаминов и таблеток.', "Time for vitamins and pills.") },
  { key: 'bedtime', title: L('Пора готовиться ко сну', "Time to wind down"), time: '22:30', text: L('Пора готовиться ко сну: приглуши свет и отложи дела на завтра.', "Time to wind down: dim the lights and leave tomorrow's tasks for tomorrow.") },
  { key: 'screen_off', title: L('Отложить телефон', "Put the phone away"), time: '23:00', text: L('Пора отложить телефон: так проще уснуть.', "Time to put the phone away: it makes falling asleep easier.") },
].map((t) => ({ every_min: null, until_time: null, available: true, ...t }));
let customRems = [{ id: 1, template: 'water', title: L('Вода', "Water"), text: L('Глоток воды сейчас не помешает.', "A sip of water would be nice right now."), time: '10:00', days: '', every_min: 120, until_time: '20:00', enabled: true }];
let remSeq = 2;
const remsPayload = () => ({ reminders: rems, custom: [...customRems].sort((a, b) => a.time.localeCompare(b.time) || a.id - b.id), templates: REM_TEMPLATES });
const badField = (msg) => Object.assign(new Error(msg), { status: 400 });
function cleanRem(cur, b) {
  const o = { ...cur };
  if ('title' in b) { o.title = String(b.title || '').trim(); if (!o.title) throw badField(L('Напишите название напоминания.', "Enter a title for the reminder.")); }
  if ('text' in b) { o.text = String(b.text || '').trim(); }
  if ('time' in b) { if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(b.time || '')) throw badField(L('Время: нужно в формате ЧЧ:ММ.', "Time must be in HH:MM format.")); o.time = b.time; }
  if ('days' in b) { const d = [...new Set(String(b.days || '').split(''))].sort().join(''); o.days = d === '0123456' ? '' : d; }
  if ('every_min' in b) o.every_min = b.every_min || null;
  if ('until_time' in b) o.until_time = b.until_time || null;
  if ('enabled' in b) o.enabled = !!b.enabled;
  if (!o.every_min) { o.every_min = null; o.until_time = null; }
  else if (!o.until_time) throw badField(L('Укажите, до какого времени повторять.', "Set the time to repeat until."));
  else if (o.until_time <= o.time) throw badField(L('Повтор «до» должен быть позже времени начала.', "The “until” time must be later than the start time."));
  if (!o.text) throw badField(L('Напишите текст сообщения.', "Enter the message text."));
  return o;
}
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
    comment: nPh && tplIdx === 1 ? L('на подсолнечном масле, без соуса', "in sunflower oil, no sauce") : null,
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
    { id: 300, name: L('Творог 5%', "Cottage cheese 5%"), brand: L('Савушкин', "Savushkin"), kcal: 121, protein: 17, fat: 5, carbs: 1.8, default_grams: 200, unit_name: L('пачка', "pack"), unit_grams: 180, barcode: null, times_used: 3 },
    { id: 301, name: L('Гречка ядрица (сухая)', "Buckwheat groats (dry)"), brand: null, kcal: 313, protein: 12.6, fat: 3.3, carbs: 62.1, default_grams: 60, unit_name: null, unit_grams: null, barcode: null, times_used: 9 },
    { id: 302, name: L('Протеиновый батончик', "Protein bar"), brand: 'Bombbar', kcal: 340, protein: 36, fat: 10, carbs: 28, default_grams: 60, unit_name: L('батончик', "bar"), unit_grams: 60, barcode: null, times_used: 1 },
    { id: 303, name: L('Йогурт греческий 2%', "Greek yogurt 2%"), brand: 'Teos', kcal: 73, protein: 9, fat: 2, carbs: 4.5, default_grams: 140, unit_name: L('баночка', "jar"), unit_grams: 140, barcode: null, times_used: 5 },
  ];
  dishes = [
    { id: 1, name: L('Тарелка 26 см', "26 cm plate"), kind: 'plate', diameter_cm: 26, depth_cm: 2, volume_ml: null, photo_url: null, is_default: false },
    { id: 2, name: L('Глубокая тарелка', "Deep plate"), kind: 'deep_plate', diameter_cm: 20, depth_cm: 5, volume_ml: 500, photo_url: PHOTOS[4], is_default: true },
    { id: 3, name: L('Любимая кружка', "Favorite mug"), kind: 'mug', diameter_cm: 8.5, depth_cm: 9.5, volume_ml: 300, photo_url: null, is_default: false },
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
    steps_goal: 8000,
    activity_hint: date === todayISO() && prof.activity < 3 ? { avg_steps: 11200, days: 16, current: prof.activity, suggested: 3 } : null,
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
    headline: L('Есть что поправить', "A few things to adjust"),
    focus: { icon: '🥩', title: L('Белок', "Protein"), text: L('В среднем 84 из 110 г, не хватает около 26 г в день. Добавь белок в каждый приём: яйца или творог утром, курица или рыба в обед, кефир вечером.', "On average 84 of 110 g, about 26 g short per day. Add protein to every meal: eggs or cottage cheese in the morning, chicken or fish at lunch, kefir in the evening."), action: L('Завтра: творог или яйца на завтрак', "Tomorrow: cottage cheese or eggs for breakfast") },
    metrics: [
      { key: 'kcal', label: L('Калории', "Calories"), value: L('1 720 ккал', "1,720 kcal"), goal: L('норма 1 650', "target 1,650"), status: 'good', note: L('в норме 5 из 6 дн.', "on target 5 of 6 days"), delta: { text: L('−90 к прошлому', "−90 vs previous"), dir: 'good' } },
      { key: 'protein', label: L('Белок', "Protein"), value: L('84 г', "84 g"), goal: L('цель 110 г', "goal 110 g"), status: 'bad', note: L('цель достигнута 1 из 6 дн.', "goal reached 1 of 6 days"), delta: { text: L('+6 г к прошлому', "+6 g vs previous"), dir: 'good' } },
      { key: 'water', label: L('Вода', "Water"), value: L('1 540 мл', "1,540 ml"), goal: L('цель 2 100', "goal 2,100"), status: 'ok', note: L('цель достигнута 2 из 7 дн.', "goal reached 2 of 7 days"), delta: { text: L('как раньше', "same as before"), dir: 'flat' } },
      { key: 'steps', label: L('Шаги', "Steps"), value: L('7 120', "7,120"), goal: L('цель 8 000', "goal 8,000"), status: 'ok', note: L('3 дн. с целью', "3 days at goal"), delta: { text: L('+1 020 к прошлому', "+1,020 vs previous"), dir: 'good' } },
      { key: 'sleep', label: L('Сон', "Sleep"), value: L('6 ч 20 м', "6 h 20 m"), goal: L('цель 7+ ч', "goal 7+ h"), status: 'bad', note: L('меньше 6 ч: 2 дня', "under 6 h: 2 days"), delta: { text: L('−15 мин к прошлому', "−15 min vs previous"), dir: 'bad' } },
      { key: 'rhythm', label: L('Режим', "Routine"), value: L('2,6 в день', "2.6 per day"), goal: L('цель 3+ приёма', "goal 3+ meals"), status: 'ok', note: L('первый приём ±2 ч', "first meal ±2 h"), delta: null },
      { key: 'logged', label: L('Дней с едой', "Days logged"), value: L(`${n - 1} из ${n}`, `${n - 1} of ${n}`), goal: '', status: 'good', note: '', delta: null },
    ],
    weight: { start: 86.0, now: 85.3, change: -0.7, per_week: -0.5, weighins: 5, goal: 76, remaining: 9.3, eta_weeks: 19, expected_change: -0.6, points: pts },
    good: [L('Калории в целевом коридоре', "Calories within the target range"), L('Шагов стало больше на 1 020 в день', "1,020 more steps per day"), L('Тренд веса −0,7 кг за период', "Weight trend −0.7 kg over the period")],
    patterns: [{ icon: '😴', title: L('Сон и аппетит', "Sleep and appetite"), text: L('После коротких ночей (меньше 6,5 ч) ты ешь примерно на 280 ккал больше, чем после полноценных. Недосып усиливает голод: ранний отбой сработает лучше силы воли.', "After short nights (under 6.5 h) you eat about 280 kcal more than after full ones. Lack of sleep boosts hunger: an early bedtime works better than willpower.") }],
    tips: [
      { icon: '🥩', title: L('Белок', "Protein"), text: L('В среднем 84 из 110 г, не хватает около 26 г в день.', "On average 84 of 110 g, about 26 g short per day.") },
      { icon: '😴', title: L('Сон', "Sleep"), text: L('Сон в среднем 6 ч 20 мин. Сдвинь отбой на 30 минут раньше.', "Sleep averages 6 h 20 min. Move bedtime 30 minutes earlier."), action: L('Завтра: отбой на 30 минут раньше', "Tomorrow: bedtime 30 minutes earlier") },
      { icon: '🕐', title: L('Режим питания', "Eating routine"), text: L('В среднем 2,6 приёма пищи в день, в 3 дня из 6 было всего 1–2. Крупные порции и длинные паузы хуже держат сытость.', "On average 2.6 meals a day, and on 3 of 6 days only 1–2. Big portions and long gaps keep you full less well."), action: L('Завтра: три приёма пищи, в каждом белок', "Tomorrow: three meals, each with protein") },
      { icon: '🚶', title: L('Движение', "Movement"), text: L('Шагов в среднем 7 120, до цели 880 в день: это около 10 минут неспешной ходьбы.', "Steps average 7,120, 880 a day short of the goal: about 10 minutes of easy walking."), action: L('Завтра: 10 минут ходьбы после ужина', "Tomorrow: a 10-minute walk after dinner") },
    ],
  };
}

class FormLike {
  constructor(fd) { this.fd = fd; }
  get(k) { return this.fd.get(k); }
  all(k) { return this.fd.getAll(k); }
}
const dishOut = (d) => ({ ...d });
// Общий справочник в моке: еда из FOODS + свои продукты.
function mockFoods() {
  const own = products.map((p) => ({ id: p.id, name: p.name, brand: p.brand || null, kcal: p.kcal, protein: p.protein, fat: p.fat, carbs: p.carbs, default_grams: p.default_grams || null, unit_name: p.unit_name || null, unit_grams: p.unit_grams || null, own: true, source: 'user' }));
  const shared = Object.values(FOODS).map((f, i) => ({ id: 9000 + i, name: f[0], brand: null, kcal: f[1], protein: f[2], fat: f[3], carbs: f[4], default_grams: null, unit_name: null, unit_grams: null, own: false, source: 'seed' }));
  return [...own, ...shared];
}
// Позиция ручного ввода или продукта из базы (как POST /meals/manual и /meals/{id}/items).
function mockManualItem(x) {
  if (!(x.grams >= 1 && x.grams <= 5000)) throw err(400, 'bad_field', L('Граммы: от 1 до 5000.', "Grams: from 1 to 5000."));
  let per100; let name; let source = 'manual';
  if (x.food_id != null) {
    const f = mockFoods().find((y) => y.id === +x.food_id); if (!f) throw notFound();
    per100 = { kcal: f.kcal, protein: f.protein, fat: f.fat, carbs: f.carbs }; name = f.name; source = f.own ? 'user' : 'seed';
  } else {
    name = String(x.name || '').trim();
    if (!name || name.length > 120) throw err(400, 'bad_field', L('Название: от 1 до 120 символов.', "Name: 1 to 120 characters."));
    if (!['kcal', 'protein', 'fat', 'carbs'].every((k) => x[k] != null)) throw err(400, 'bad_field', L('Нужны все четыре значения.', "All four values are required."));
    const k = x.per === 'portion' ? 100 / x.grams : 1;
    per100 = { kcal: r1(x.kcal * k), protein: r1(x.protein * k), fat: r1(x.fat * k), carbs: r1(x.carbs * k) };
    if (x.save) products.unshift({ id: nid(), name, brand: null, ...per100, default_grams: x.grams, unit_name: null, unit_grams: null, barcode: null, times_used: 1 });
  }
  return calcItem({ id: nid(), name, grams: x.grams, per100, confidence: 1, edited: source === 'manual', source });
}
const itemMeal = (itemId) => [...meals.values()].find((m) => m.items.some((i) => i.id === itemId));
const err = (status, error, message) => { const e = new Error(message); e.status = status; e.code = error; return Object.assign(e, { name: 'ApiError' }); };
const notFound = () => err(404, 'not_found', L('Не найдено.', "Not found."));

function guessType(time) {
  const h = parseInt((time || '12:00').slice(0, 2), 10);
  return h < 11 ? 'breakfast' : h < 16 ? 'lunch' : h < 21 ? 'dinner' : 'snack';
}
function nowHHMM() { const d = new Date(); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`; }

// Weekly mini-goal demo state
const WG_KINDS = {
  protein: { icon: '🥩', title: L('Белок в норме', "Protein on target"), hint: L('Белок в каждый приём пищи: яйца или творог, курица или рыба, кефир.', "Protein at every meal: eggs or cottage cheese, chicken or fish, kefir.") },
  water: { icon: '💧', title: L('Вода в норме', "Water on target"), hint: L('Стакан после пробуждения, перед едой и в 16:00. Чай и кофе тоже считаются.', "A glass after waking up, before meals and at 4 pm. Tea and coffee count too.") },
  late: { icon: '🌙', title: L('Без поздней еды', "No late eating"), hint: L('Ужин на час раньше и с белком, так вечером меньше тянет.', "Dinner an hour earlier and with protein, so evening cravings are weaker.") },
};
const mockFavs = { 1: 101, 2: 102, 3: 103, 4: 104 }; // meal_id -> favorite_id (в демо избранное уже есть)
const favUse = {};       // favorite_id -> сколько раз записали (порядок в списке)
const customFavs = [     // избранное, созданное с нуля
  { id: 502, meal_id: 502, title: L('Тостовый хлеб +3', "Toast +3"), note: null, kcal: 380, protein: 24, fat: 16, carbs: 34, names: [L('Тостовый хлеб', "Toast"), L('Сыр', "Cheese"), L('Ветчина', "Ham"), L('Огурец', "Cucumber")], photo_url: '/api/favorites/502/photo' },   // фото нет: проверка запасной картинки
  { id: 501, meal_id: 501, title: L('Мой омлет', "My omelet"), note: L('на сливочном масле', "in butter"), kcal: 320, protein: 20, fat: 24, carbs: 3, names: [L('Омлет', "Omelet")], photo_url: null },
];
const favEdits = {};     // правки полей избранного по id
const FREQ = [
  { meal_id: 1, title: L('Овсянка с бананом +1', "Oatmeal with banana +1"), names: [L('Овсянка с бананом', "Oatmeal with banana"), L('Кофе с молоком', "Coffee with milk")], kcal: 410, protein: 16, fat: 11, carbs: 62, count: 9 },
  { meal_id: 2, title: L('Гречка +1', "Buckwheat +1"), names: [L('Гречка', "Buckwheat"), L('Куриная грудка', "Chicken breast")], kcal: 520, protein: 41, fat: 12, carbs: 58, count: 6 },
  { meal_id: 3, title: L('Творог 5%', "Cottage cheese 5%"), names: [L('Творог 5%', "Cottage cheese 5%")], kcal: 180, protein: 30, fat: 5, carbs: 3, count: 4 },
  { meal_id: 4, title: L('Капучино', "Cappuccino"), names: [L('Капучино', "Cappuccino")], kcal: 98, protein: 5, fat: 5, carbs: 8, count: 3 },
];
function favList() {
  const starred = FREQ.filter((f) => mockFavs[f.meal_id]).map((f) => ({ ...f, id: mockFavs[f.meal_id], note: null, photo_url: null }));
  return [...customFavs, ...starred].map((f) => ({ ...f, meal_type: f.meal_type || 'breakfast', use_count: favUse[f.id] || 0, ...(favEdits[f.id] || {}) }))
    .sort((a, b) => b.use_count - a.use_count);
}
let weekGoal = null;
function weekGoalMock() {
  const recap = { week_start: '2026-10-26', good: [L('Белок держится у цели', "Protein stays near the goal"), L('Воды хватает', "Water is enough")], goal: { kind: 'water', icon: '💧', title: L('Вода в норме', "Water on target"), done: 5, target: 4, achieved: true } };
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
    cells: states.map((state, i) => ({ date: '2026-11-0' + (2 + i), state })), message: achieved ? L('Цель недели выполнена. Это уже привычка, а не случайность.', "Weekly goal reached. That is a habit now, not a coincidence.") : L(`Осталось ${weekGoal.target - done} дня из запланированных.`, `${weekGoal.target - done} more day(s) to go.`) } };
}

// ?onb=1: приложение открывается как у нового человека (онбординг); после POST /onboarding — обычные данные.
let onbPending = /[?&]onb=1\b/.test(location.search);

function mockOnboarding(b) {
  const age = new Date().getFullYear() - b.birth_year;
  if (age < 18) { const e = new Error(L('Бот считает дефицит калорий и рассчитан на взрослых (18+).', "The bot works with a calorie deficit and is built for adults (18+).")); e.name = 'ApiError'; e.status = 400; e.code = 'underage'; throw e; }
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
  ['GET', /^\/me$/, () => onbPending ? { onboarded: false, user: { first_name: L('Аня', "Anna") } } : ({
    onboarded: true,
    user: { tg_id: 1, first_name: L('Жан', "Jean"), sex: 'm', height_cm: 178, goal_weight_kg: 76, tz: 'Europe/Moscow', eating_cutoff: '21:00' },
    targets: TARGETS, weight: { latest: 82.4, trend: body.get(todayISO()).trend, start: 88.0 },
  })],
  ['POST', /^\/onboarding$/, (m, q, b) => mockOnboarding(b)],
  ['GET', /^\/profile$/, () => ({ profile: mockProfile(), targets: TARGETS, first_name: L('Жан', "Jean") })],
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
  ['GET', /^\/reminders$/, () => remsPayload()],
  ['PUT', /^\/reminders\/(\w+)$/, (m, q, b) => { const r = rems.find((x) => x.kind === m[1]); if (!r) throw notFound(); if ('enabled' in b) r.enabled = !!b.enabled; if (b.time && !r.fixed_time) r.time = b.time; return remsPayload(); }],
  ['POST', /^\/user-reminders$/, (m, q, b) => {
    if (customRems.length >= 20) throw Object.assign(new Error(L('Можно создать не больше 20 напоминаний.', "You can create up to 20 reminders.")), { status: 409 });
    const t = REM_TEMPLATES.find((x) => x.key === b.template);
    const base = t ? { template: t.key, title: t.title, text: t.text, time: t.time, days: '', every_min: t.every_min, until_time: t.until_time, enabled: true } : { template: 'custom', days: '', every_min: null, until_time: null, enabled: true };
    const item = { id: remSeq++, ...cleanRem(base, b) };
    customRems.push(item);
    return { reminder: item, ...remsPayload() };
  }],
  ['PATCH', /^\/user-reminders\/(\d+)$/, (m, q, b) => { const i = customRems.findIndex((x) => x.id === +m[1]); if (i < 0) throw notFound(); customRems[i] = cleanRem(customRems[i], b); return { reminder: customRems[i], ...remsPayload() }; }],
  ['DELETE', /^\/user-reminders\/(\d+)$/, (m) => { const n = customRems.length; customRems = customRems.filter((x) => x.id !== +m[1]); if (customRems.length === n) throw notFound(); return remsPayload(); }],
  ['DELETE', /^\/me$/, (m, q, b) => { if (!b || b.confirm !== 'УДАЛИТЬ') throw Object.assign(new Error(L('Для удаления нужно подтверждение.', "Confirmation is required to delete.")), { status: 400 }); return { deleted: true }; }],
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
    if (/масл|butter|oil/i.test(mm.comment || '')) { mm.items.push(makeItem('oil', 10)); recalcMeal(mm); }
    return mm;
  }, 201],
  ['PATCH', /^\/meals\/(\d+)$/, (m, q, b) => { const x = meals.get(+m[1]); if (!x) throw notFound(); Object.assign(x, b); return x; }],
  ['DELETE', /^\/meals\/(\d+)$/, (m) => { const x = meals.get(+m[1]); if (x) x.deleted = true; return null; }],
  ['POST', /^\/meals\/(\d+)\/refine$/, async (m, q, b) => {
    await sleep(1200);
    const x = meals.get(+m[1]); if (!x) throw notFound();
    x.comment = b.comment;
    if (/масл|butter|oil/i.test(b.comment || '') && !x.items.some((i) => /масло|oil/i.test(i.name))) x.items.push(makeItem('oil', 10));
    x.items.forEach((i) => { i.edited = true; });
    return recalcMeal(x);
  }],
  ['PATCH', /^\/items\/(\d+)$/, async (m, q, b) => {
    // Разброс задержки (до ~1.2 с; ?slow=1 — до 3 с): так видно, что ответы не перезаписывают поле.
    await sleep(80 + Math.random() * (/[?&]slow=1\b/.test(location.search) ? 3000 : 1100));
    const x = itemMeal(+m[1]); if (!x) throw notFound();
    const it = x.items.find((i) => i.id === +m[1]);
    const keys = ['grams', 'name', 'per', 'food_id', 'kcal', 'protein', 'fat', 'carbs'];
    if (!keys.some((k) => k in b)) throw err(400, 'empty_patch', L('Нечего менять.', "Nothing to change."));
    if ('grams' in b && !(b.grams >= 1 && b.grams <= 5000)) throw err(422, 'validation', L('Граммы: от 1 до 5000.', "Grams: from 1 to 5000."));
    if (Object.keys(b).every((k) => k === 'grams')) { it.grams = b.grams; it.edited = true; calcItem(it); return recalcMeal(x); }
    if ('name' in b) { const n = String(b.name || '').trim(); if (!n || n.length > 120) throw err(400, 'bad_field', L('Название: от 1 до 120 символов.', "Name: 1 to 120 characters.")); it.name = n; }
    if ('grams' in b) it.grams = b.grams;
    if (b.food_id != null) {
      const f = mockFoods().find((y) => y.id === +b.food_id); if (!f) throw notFound();
      it.name = f.name; it.per100 = { kcal: f.kcal, protein: f.protein, fat: f.fat, carbs: f.carbs }; it.source = f.own ? 'user' : 'seed'; it.confidence = 1;
    } else if (['kcal', 'protein', 'fat', 'carbs'].some((k) => b[k] != null)) {
      if (!['kcal', 'protein', 'fat', 'carbs'].every((k) => b[k] != null)) throw err(400, 'bad_field', L('Нужны все четыре значения.', "All four values are required."));
      const k = b.per === 'portion' ? 100 / it.grams : 1;
      it.per100 = { kcal: r1(b.kcal * k), protein: r1(b.protein * k), fat: r1(b.fat * k), carbs: r1(b.carbs * k) };
      it.source = 'manual'; it.confidence = 1;
    }
    it.edited = true; calcItem(it);
    return recalcMeal(x);
  }],
  ['POST', /^\/items\/(\d+)\/barcode$/, async (m, q, b) => {
    await sleep(400);
    const x = itemMeal(+m[1]); if (!x) throw notFound();
    if (String(b.code).endsWith('0000')) throw err(404, 'not_found', L('В базе такого штрихкода нет', "This barcode is not in the database"));
    const it = x.items.find((i) => i.id === +m[1]);
    it.name = L('Сосиски куриные, Папа может', "Chicken sausages, Papa Can"); it.per100 = { kcal: 214, protein: 11.5, fat: 18.3, carbs: 1.6 };
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
  ['GET', /^\/foods\/search$/, (m, q) => {
    const needle = String(q.q || '').trim().toLowerCase();
    const all = mockFoods();
    const list = needle.length < 2 ? all.filter((f) => f.own) : all.filter((f) => (f.name + ' ' + (f.brand || '')).toLowerCase().includes(needle));
    return { foods: list.slice(0, +q.limit || 30) };
  }],
  ['POST', /^\/meals\/manual$/, (m, q, b) => {
    if (!Array.isArray(b.items) || !b.items.length || b.items.length > 30) throw err(400, 'bad_field', L('Нужна хотя бы одна позиция.', "At least one item is required."));
    const time = b.time || nowHHMM();
    const mm = { id: nid(), date: b.date || todayISO(), time, type: b.type || guessType(time), source: 'manual', confidence: 1, photos: [], items: [], comment: null };
    b.items.forEach((x) => mm.items.push(mockManualItem(x)));
    meals.set(mm.id, mm);
    return recalcMeal(mm);
  }, 201],
  ['POST', /^\/meals\/(\d+)\/items$/, (m, q, b) => {
    const x = meals.get(+m[1]); if (!x) throw notFound();
    x.items.push(mockManualItem(b));
    return recalcMeal(x);
  }, 201],
  ['GET', /^\/products$/, (m, q) => ({ products: products.filter((p) => !q.q || (p.name + ' ' + (p.brand || '')).toLowerCase().includes(q.q.toLowerCase())) })],
  ['POST', /^\/products$/, (m, q, b) => { const p = { id: nid(), brand: null, default_grams: null, unit_name: null, unit_grams: null, barcode: null, times_used: 0, ...b }; products.unshift(p); return p; }, 201],
  ['PATCH', /^\/products\/(\d+)$/, (m, q, b) => { const p = products.find((x) => x.id === +m[1]); if (!p) throw notFound(); return Object.assign(p, b); }],
  ['DELETE', /^\/products\/(\d+)$/, (m) => { products = products.filter((x) => x.id !== +m[1]); return null; }],
  ['POST', /^\/products\/label$/, async () => {
    await sleep(1500);
    return {
      draft: { name: L('Кефир 2,5%', "Kefir 2.5%"), brand: L('Простоквашино', "Prostokvashino"), kcal: 53, protein: 2.9, fat: 2.5, carbs: 4, default_grams: 250, unit_name: L('стакан', "glass"), unit_grams: 250, barcode: null },
      confidence: 0.86, notes: [L('Значения указаны на 100 мл, принята плотность 1 г/мл.', "Values are given per 100 ml, a density of 1 g/ml is assumed."), L('Вес упаковки не найден.', "Package weight not found.")],
    };
  }],
  ['GET', /^\/products\/barcode\/(\d+)$/, async (m) => {
    await sleep(700);
    if (m[1] === '0000000000000') throw notFound();
    return { source: 'off', existing_id: null, draft: { name: L('Кефир 2,5%', "Kefir 2.5%"), brand: L('Простоквашино', "Prostokvashino"), kcal: 53, protein: 2.9, fat: 2.5, carbs: 4, default_grams: 250, unit_name: L('стакан', "glass"), unit_grams: 250, barcode: m[1] } };
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
  ['GET', /^\/drinks$/, () => ({ drinks: MOCK_DRINKS.map((d) => ({ protein: 0, fat: 0, carbs: Math.round(d.kcal_100 / 4 * 10) / 10, alcohol: ['beer', 'wine'].includes(d.id), ...d })) })],
  ['POST', /^\/drinks$/, (m, q, b) => {
    const T = todayISO();
    const d = MOCK_DRINKS.find((x) => x.id === b.drink);
    if (!d) throw Object.assign(new Error(L('Неизвестный напиток.', "Unknown drink.")), { status: 400 });
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
    return { id: nid(), screen: 'mifitness_sleep', date: todayISO(), metrics: { sleep_duration_min: 431, sleep_deep_min: 96, sleep_rem_min: 88, sleep_score: 78, steps: 9800, active_kcal: 410 }, confidence: risky ? 0.72 : 0.93, needs_confirm: risky, reasons: risky ? [L('дата не распознана — ставлю сегодняшнюю', "date not recognized, using today"), L('уверенность 72%', "confidence 72%")] : [], applied: risky ? null : 'sleep' };
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
    ...favList().map((f) => ({ meal_id: f.meal_id, favorite_id: f.id, title: f.title, names: f.names, kcal: f.kcal, count: 0, photo_url: f.photo_url || null })),
    ...FREQ.filter((f) => !mockFavs[f.meal_id]).map((f) => ({ meal_id: f.meal_id, favorite_id: null, title: f.title, names: f.names, kcal: f.kcal, count: f.count }))] })],
  ['GET', /^\/favorites$/, () => ({ items: favList() })],
  ['GET', /^\/favorites\/(\d+)$/, (m) => { const f = favList().find((x) => x.id === +m[1]); if (!f) throw notFound(); return f; }],
  ['POST', /^\/favorites\/custom$/, (m, q, b) => {
    const id = Math.max(500, ...customFavs.map((x) => x.id)) + 1;
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
  ['POST', /^\/favorites\/(\d+)\/repeat$/, (m, q, b) => {
    const f = favList().find((x) => x.id === +m[1]);
    if (!f) throw notFound();
    const k = b && b.factor !== undefined ? Number(b.factor) : 1;
    if (!(k >= 0.25 && k <= 4)) throw err(400, 'bad_field', L('Поле factor: допустимо от 0,25 до 4.', "Field factor: allowed range is 0.25 to 4."));
    favUse[f.id] = (favUse[f.id] || 0) + 1;
    return { id: 9001, kcal: Math.round(f.kcal * k) };
  }, 201],
  ['POST', /^\/meals\/(\d+)\/repeat$/, () => ({ id: 9001, kcal: 410 })],
  ['GET', /^\/week-goal$/, () => weekGoalMock()],
  ['PUT', /^\/week-goal$/, (m, q, b) => { weekGoal = { kind: b.kind, target: b.target }; return weekGoalMock(); }],
  ['DELETE', /^\/week-goal$/, () => { weekGoal = null; return weekGoalMock(); }],
  ['GET', /^\/analysis$/, (m, q) => (q.period && q.period !== 'day' ? periodMock(q.period) : {
    date: q.date || todayISO(),
    closed: true,
    headline: L('В целом неплохо', "Pretty good overall"),
    incomplete: false,
    rows: [
      { label: L('Калории', "Calories"), value: L('1 580 ккал', "1,580 kcal"), goal: L('1 650', "1,650"), status: 'good' },
      { label: L('Белок', "Protein"), value: L('92 г', "92 g"), goal: L('120 г', "120 g"), status: 'ok' },
      { label: L('Вода', "Water"), value: L('900 мл', "900 ml"), goal: L('2 000 мл', "2,000 ml"), status: 'bad' },
      { label: L('Шаги', "Steps"), value: L('9 400', "9,400"), goal: L('8 000', "8,000"), status: 'good' },
      { label: L('Сон', "Sleep"), value: L('6 ч 40 мин', "6 h 40 min"), goal: L('7+ ч', "7+ h"), status: 'ok' },
    ],
    good: [L('Калории в цели', "Calories on target"), L('Шаги в плане (9 400)', "Steps on plan (9,400)")],
    tips: [
      { icon: '💧', title: L('Вода', "Water"), text: L('Воды мало: 900 из 2 000 мл. Поставьте стакан на виду и пейте по паре глотков каждый час. Часто «голод» вечером оказывается жаждой и усталостью.', "Water is low: 900 of 2,000 ml. Keep a glass in sight and take a few sips every hour. Evening “hunger” is often thirst or tiredness.") },
      { icon: '🥩', title: L('Белок', "Protein"), text: L('Белок держит сытость и бережёт мышцы. Сегодня 92 из 120 г: добавьте ещё около 28 г, например 150 г творога.', "Protein keeps you full and protects muscle. Today 92 of 120 g: add about 28 g more, for example 150 g of cottage cheese.") },
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
  throw new ApiError(404, 'not_found', L('Не найдено.', "Not found."));
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
