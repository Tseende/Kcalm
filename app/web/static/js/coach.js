// Одноразовые подсказки по интерфейсу («coach marks»): короткий тур на каждом экране.
// Подсветка нужного элемента, пузырь со стрелкой, «1 из 3», «Дальше» / «Понятно» / «Пропустить».
// Каждая подсказка показывается один раз за всё время: id хранятся в localStorage `coach_seen`
// и (если есть Telegram CloudStorage, версия 6.9+) дублируются в облаке, чтобы пережить переустановку.
// Запускается из main.js после каждого render(): coach.maybeShow({ path }).
import { tg, isMock } from './api.js';
import { h, haptic } from './ui.js';

const KEY = 'coach_seen';
const MAX_STEPS = 4;          // шагов в одном туре; остальное покажется при следующем заходе
const WAIT_MS = 3000;         // сколько ждём появления цели
const SETTLE_MS = 500;        // экран должен «устояться» перед показом

// ------------------------------------------------------------------ реестр подсказок
const q = (s) => document.querySelector(s);
const rowByTitle = (t) => {
  const el = [...document.querySelectorAll('#view .row .row-title')].find((x) => x.textContent.trim() === t);
  return el ? el.closest('.row') : null;
};

// route: имя экрана (см. ROUTES). target: селектор или функция. Порядок в массиве = порядок в туре.
// when: доп. условие. round: круглая подсветка. pad: поля подсветки. placement: auto | above | below.
export const TIPS = [
  { id: 'today.ring', route: 'today', target: '.today .kcal-card', pad: 4, radius: 22,
    title: 'Сколько ещё можно сегодня', text: 'Кольцо показывает, сколько калорий осталось до нормы. Ниже — белки, жиры и углеводы: сколько ещё влезает в день.' },
  { id: 'today.date', route: 'today', target: '.today .date-switch', pad: 2,
    title: 'Дни переключаются тут', text: 'Стрелки или свайп влево-вправо по этой строке — вчера, позавчера. Если смотришь прошлый день, тап по дате вернёт на сегодня.' },
  { id: 'today.water', route: 'today', target: '.water-card .quick',
    title: 'Вода в одно касание', text: 'Стакан воды — одно нажатие: +200, +300 или +500 мл. Нажато по ошибке? Кнопка со стрелкой отменит последнюю порцию.' },
  { id: 'today.plus', route: 'today', target: '.tab-plus .plus-circle', round: true, pad: 7,
    title: 'Начни с «+»', text: 'Нажми, чтобы записать еду: сфотографируй тарелку или опиши словами. Там же вода, вес, сон и активность.' },
  { id: 'today.meal', route: 'today', target: () => q('.today .meal-row'),
    title: 'Приём можно поправить', text: 'Открой любой приём: там можно изменить граммы у блюда, и калории пересчитаются сами.' },
  { id: 'today.often', route: 'today', target: () => q('.today .often:not([hidden])'),
    title: 'Повторить как обычно', text: 'Блюда, которые встречаются у тебя не раз, собраны здесь. Тап по карточке записывает приём сразу, отменить можно в плашке сверху.' },

  { id: 'meal.grams', route: 'meal', target: '.item .stepper',
    title: 'Граммы можно поправить', text: 'Плюс и минус меняют вес на 10 г. Нажми на само число, чтобы вписать точнее. Калории и БЖУ пересчитаются.' },
  { id: 'meal.actions', route: 'meal', target: '.item .item-actions',
    title: 'Три значка у блюда', text: 'Штрихкод уточняет продукт по упаковке, закладка сохраняет его в «Мои продукты», корзина убирает позицию.' },
  { id: 'meal.refine', route: 'meal', target: () => q('.meal-actions .act[title="Уточнить"]'), pad: 4, radius: 16,
    title: 'Уточни словами', text: 'Напиши детали: «на сливочном масле», «соус сметанный». Бот пересчитает позиции.' },
  { id: 'meal.fav', route: 'meal', target: () => q('.meal-actions .act[aria-pressed]'), pad: 4, radius: 16,
    title: 'Звезда — в избранное', text: 'Любимое блюдо потом можно записать одним касанием прямо при добавлении приёма.' },

  { id: 'addmeal.photo', route: 'addmeal', target: '.capture .cap-grid',
    title: 'Фото — самый быстрый путь', text: 'Снимай или выбирай из галереи. До 6 фото на блюдо: тарелка, упаковка, этикетка. Так оценка точнее.' },
  { id: 'addmeal.text', route: 'addmeal', target: '.compose-keep',
    title: 'Или просто напиши', text: 'Опиши словами, что было на тарелке, с примерным весом: «овсянка 250 г, банан». Фото не обязательно.' },
  { id: 'addmeal.quick', route: 'addmeal', target: () => { const el = q('#view .quick-list'); return el && !el.closest('[hidden]') ? el.parentElement : null; },
    title: 'Избранное и частые', text: 'Тап по строке записывает такой же приём сразу. Звёздочка закрепляет любимые наверху списка.' },

  { id: 'stats.range', route: 'stats', target: '#view .seg',
    title: 'Выбери период', text: 'Неделя, месяц или 90 дней: графики и цифры ниже пересчитаются под выбранный срок.' },
  { id: 'stats.heat', route: 'stats', target: '.st-heat .hm-wrap',
    title: 'Дни в норме', text: 'Одна клетка — один день, цвет показывает, удалось ли уложиться в норму. Тапни по клетке, чтобы увидеть дату и калории.' },

  { id: 'analysis.period', route: 'analysis', target: '#view .seg',
    title: 'День, неделя или месяц', text: 'Разбор и советы подстроятся под период. За день видно итог по записям, за неделю и месяц — тренд.' },
  { id: 'analysis.focus', route: 'analysis', target: '.an-focus',
    title: 'Главное — одно', text: 'Не нужно править всё сразу. Начни с этого совета, остальные подождут.' },

  { id: 'menu.products', route: 'menu', target: () => rowByTitle('Мои продукты'),
    title: 'Свои продукты', text: 'Сохраняй то, что ешь часто: любимый йогурт, домашний соус. Потом их можно добавить к приёму в пару касаний.' },
  { id: 'menu.dishes', route: 'menu', target: () => rowByTitle('Моя посуда'),
    title: 'Посуда помогает считать порции', text: 'Добавь свою тарелку или миску: размер порции по фото оценится точнее. Выбрать посуду можно при добавлении приёма.' },

  { id: 'drink.pick', route: 'drink', target: '.drink-grid', maxH: 290,
    title: 'Сначала напиток', text: 'Выбери, что пьёшь. Чай и кофе засчитываются в воду, у сладких можно добавить сахар.' },
  { id: 'drink.size', route: 'drink', target: () => q('.drink-label + .chips'),
    title: 'Потом объём', text: 'Тапни готовый размер или впиши свой. Кнопка «Добавить» внизу сохранит напиток.' },
];

const ROUTES = [
  [/^\/today$/, 'today'], [/^\/stats$/, 'stats'], [/^\/analysis$/, 'analysis'], [/^\/menu$/, 'menu'],
  [/^\/meal\/\d+$/, 'meal'], [/^\/add\/meal$/, 'addmeal'], [/^\/add\/(drink|water)$/, 'drink'],
];
const routeName = (path) => { const m = ROUTES.find(([re]) => re.test(path)); return m ? m[1] : null; };

// ------------------------------------------------------------------ что уже показано
const lsGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* без хранилища */ } };
const lsDel = (k) => { try { localStorage.removeItem(k); } catch (e) { /* без хранилища */ } };

// g — «поколение»: сброс увеличивает его, и более новое поколение целиком вытесняет старое (в т.ч. из облака других устройств).
function parseState(raw) {
  let g = 0; let ids = [];
  try {
    const v = typeof raw === 'string' ? JSON.parse(raw || 'null') : raw;
    if (Array.isArray(v)) ids = v;
    else if (v && typeof v === 'object') { g = Number(v.g) || 0; ids = Array.isArray(v.ids) ? v.ids : []; }
  } catch (e) { /* битое значение = пусто */ }
  return { g, ids: new Set(ids.filter((x) => typeof x === 'string')) };
}
let st = parseState(lsGet(KEY));
const ser = () => JSON.stringify({ g: st.g, ids: [...st.ids] });

const cloud = () => {
  try { return tg && tg.CloudStorage && tg.isVersionAtLeast && tg.isVersionAtLeast('6.9') ? tg.CloudStorage : null; } catch (e) { return null; }
};
let pushTimer = 0;
function pushCloud(now) {
  const c = cloud();
  if (!c) return;
  clearTimeout(pushTimer);
  const go = () => { try { c.setItem(KEY, ser(), () => {}); } catch (e) { /* старый клиент */ } };
  if (now) go(); else pushTimer = setTimeout(go, 500);
}
function save() { lsSet(KEY, ser()); }

function mergeCloud(o) {
  let changed = false;
  if (o.g > st.g) { st = o; changed = true; save(); return; }
  if (o.g === st.g) {
    for (const id of o.ids) if (!st.ids.has(id)) { st.ids.add(id); changed = true; }
    if (changed) save();
    if (st.ids.size > o.ids.size) pushCloud();      // локально есть то, чего нет в облаке
  } else pushCloud(true);                           // облако отстало по поколению
}

export function isSeen(id) {
  return st.ids.has(id) || (id === 'today.plus' && lsGet('onb_hint_done') === '1');
}
function markSeen(id) {
  if (st.ids.has(id)) return;
  st.ids.add(id);
  save();
  pushCloud();
  if (id === 'today.plus') { lsSet('onb_hint_done', '1'); lsDel('onb_hint_pending'); }   // совместимость со старой подсказкой над «+»
}

/** Сбросить показанное (локально и в облаке): подсказки появятся снова по мере захода на экраны. */
export function reset() {
  st = { g: st.g + 1, ids: new Set() };
  save();
  lsDel('onb_hint_done'); lsDel('onb_hint_pending');
  pushCloud(true);
}

if (isMock() && /[?&]coach=reset\b/.test(location.search)) reset();

// Облако читаем один раз, UI не блокируем: к показу тура максимум ждём чуть-чуть.
const cloudReady = new Promise((res) => {
  const c = cloud();
  if (!c) { res(); return; }
  const t = setTimeout(res, 1500);
  try {
    c.getItem(KEY, (err, val) => {
      clearTimeout(t);
      if (!err && val) { try { mergeCloud(parseState(val)); } catch (e) { /* ignore */ } }
      res();
    });
  } catch (e) { clearTimeout(t); res(); }
});

// ------------------------------------------------------------------ состояние тура
let visit = 0;          // номер захода на экран: новый заход отменяет ожидание старого
let active = null;      // { route, steps:[{tip, el}], i, el, tip, root, hole, bubble, catchers, ... }

export const isActive = () => !!active;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reduced = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

function isVisible(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== 'hidden' && cs.display !== 'none';
}
function find(tip) {
  try {
    if (tip.when && !tip.when()) return null;
    const el = typeof tip.target === 'function' ? tip.target() : q(tip.target);
    return isVisible(el) ? el : null;
  } catch (e) { return null; }
}
// Рамка цели; очень высокую цель (maxH) подсвечиваем только сверху, чтобы остался запас под пузырь.
function rectOf(el, tip) {
  const r = el.getBoundingClientRect();
  const bottom = tip && tip.maxH && r.height > tip.maxH ? r.top + tip.maxH : r.bottom;
  return { left: r.left, right: r.right, top: r.top, bottom, width: r.width, height: bottom - r.top };
}
const candidates = (route) => TIPS.filter((t) => t.route === route && !isSeen(t.id));

function blocked() {
  const b = document.body;
  return !b || b.dataset.route === 'onb' || b.classList.contains('sheet-open') || b.classList.contains('kb-open')
    || document.hidden || !!document.querySelector('.busy, .scan, .icon-page') || !!active;
}

/** Показать тур для текущего экрана, если на нём есть ещё не виденные подсказки. */
export function maybeShow(info) {
  const path = typeof info === 'string' ? info : info && info.path;
  const route = path ? routeName(path) : null;
  const token = ++visit;
  if (active) close();
  if (!route || !candidates(route).length) return;
  run(route, token).catch((e) => console.warn('coach', e));
}

async function run(route, token) {
  await Promise.race([cloudReady, sleep(1200)]);
  const t0 = Date.now();
  let okSince = null;
  for (;;) {
    if (token !== visit) return;
    const cand = candidates(route);
    if (!cand.length) return;
    const ok = !blocked() && !q('#view .skel') && cand.some((t) => find(t));
    const now = Date.now();
    if (ok) { if (okSince === null) okSince = now; else if (now - okSince >= SETTLE_MS) break; } else okSince = null;
    if (okSince === null && now - t0 > WAIT_MS) return;
    if (now - t0 > WAIT_MS * 2) return;
    await sleep(100);
  }
  const steps = candidates(route).map((tip) => ({ tip, el: find(tip) })).filter((s) => s.el).slice(0, MAX_STEPS);
  if (!steps.length || token !== visit) return;
  start(route, steps);
}

// ------------------------------------------------------------------ отрисовка
function start(route, steps) {
  const catchers = ['t', 'b', 'l', 'r'].map((k) => h('div', { class: 'coach-catch', 'data-k': k }));
  const hole = h('div', { class: 'coach-hole' });
  const bubble = h('div', { class: 'coach-bubble', role: 'dialog', 'aria-live': 'polite', 'aria-label': 'Подсказка' });
  const root = h('div', { class: 'coach' }, hole, ...catchers, bubble);
  document.body.append(root);
  const a = { route, steps, i: -1, el: null, tip: null, root, hole, bubble, catchers, shownAt: 0, stepToken: 0, timer: 0, raf: 0 };
  active = a;

  catchers.forEach((c) => {
    c.addEventListener('click', () => { if (Date.now() - a.shownAt > 250) advance(); });
    c.addEventListener('touchmove', (e) => e.preventDefault(), { passive: false });
    c.addEventListener('wheel', (e) => e.preventDefault(), { passive: false });
  });
  a.onClick = (e) => {
    if (active !== a || (e.target.closest && e.target.closest('.coach'))) return;
    const inHole = (e.clientX || e.clientY) && pointInHole(e.clientX, e.clientY);
    if (inHole || (a.el && a.el.contains(e.target))) tapThrough();
  };
  a.onKey = (e) => { if (e.key === 'Escape') skipAll(); };
  a.onLayout = () => { cancelAnimationFrame(a.raf); a.raf = requestAnimationFrame(() => layout()); };
  document.addEventListener('click', a.onClick, true);
  document.addEventListener('keydown', a.onKey);
  window.addEventListener('resize', a.onLayout);
  window.addEventListener('scroll', a.onLayout, { passive: true });
  a.timer = setInterval(watch, 250);
  window.requestAnimationFrame(() => root.classList.add('on'));
  showStep(0, true);
}

function close() {
  const a = active;
  if (!a) return;
  active = null;
  clearInterval(a.timer);
  cancelAnimationFrame(a.raf);
  document.removeEventListener('click', a.onClick, true);
  document.removeEventListener('keydown', a.onKey);
  window.removeEventListener('resize', a.onLayout);
  window.removeEventListener('scroll', a.onLayout);
  a.root.classList.remove('on');
  a.root.classList.add('out');
  setTimeout(() => a.root.remove(), reduced() ? 0 : 220);
}

function skipAll() {
  const a = active;
  if (!a) return;
  TIPS.filter((t) => t.route === a.route).forEach((t) => markSeen(t.id));
  haptic('selection');
  close();
}

function advance() {
  const a = active;
  if (!a) return;
  if (a.i + 1 < a.steps.length) showStep(a.i + 1); else close();
}

// Тап по подсвеченному элементу = «понял»: пропускаем тап дальше и через мгновение идём к следующему шагу.
function tapThrough() {
  const a = active;
  if (!a) return;
  const hashAt = location.hash;
  const tok = a.stepToken;
  setTimeout(() => {
    if (active !== a || a.stepToken !== tok) return;
    if (location.hash !== hashAt || document.body.classList.contains('sheet-open') || document.body.classList.contains('kb-open') || !isVisible(a.el)) close();
    else advance();
  }, 320);
}

function pointInHole(x, y) {
  const a = active;
  if (!a || !a.rect) return false;
  const r = a.rect;
  return x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;
}

// Периодическая проверка: экран перерисовался, открылась шторка/клавиатура, цель сдвинулась.
function watch() {
  const a = active;
  if (!a) return;
  const b = document.body;
  if (b.classList.contains('sheet-open') || b.classList.contains('kb-open') || document.hidden || b.dataset.route === 'onb') { close(); return; }
  if (!isVisible(a.el)) {
    const el = find(a.tip);
    if (el) { a.el = el; } else { dropStep(); return; }
  }
  layout();
}

// Цель пропала (экран перерисован) — выкидываем шаг из тура и идём дальше.
function dropStep() {
  const a = active;
  if (!a) return;
  a.steps.splice(a.i, 1);
  a.i -= 1;
  advance();
}

function safeBounds(el) {
  const top0 = (parseFloat(getComputedStyle(document.body).paddingTop) || 0) + 6;
  let top = top0;
  let bottom = window.innerHeight - 6;
  const head = q('.page-head');
  if (head && el && !head.contains(el)) {
    const cs = getComputedStyle(head);
    if (cs.position === 'sticky' || cs.position === 'fixed') top = Math.max(top, head.getBoundingClientRect().bottom + 6);
  }
  ['#tabbar', '#mainbar'].forEach((sel) => {
    const bar = q(sel);
    if (bar && !bar.hidden && getComputedStyle(bar).display !== 'none') {
      const r = bar.getBoundingClientRect();
      if (r.height > 0 && r.top < bottom) bottom = r.top - 6;
    }
  });
  return { top, bottom };
}

function isPinned(el) {
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const p = getComputedStyle(n).position;
    if (p === 'fixed' || p === 'sticky') return true;
  }
  return false;
}

// Если цели (или места для пузыря) не видно — плавно прокручиваем страницу.
async function bring(el, tip, bh) {
  if (!el || isPinned(el)) return;
  const pad = tip.pad ?? 8;
  const { top, bottom } = safeBounds(el);
  const r = rectOf(el, tip);
  const need = bh + 28;
  const inView = r.top - pad >= top && r.bottom + pad <= bottom;
  const roomAbove = r.top - pad - top;
  const roomBelow = bottom - (r.bottom + pad);
  if (inView && (roomAbove >= need || roomBelow >= need)) return;
  const total = r.height + pad * 2;
  const dy = total + need <= bottom - top
    ? (r.top - pad) - (top + 8)                            // цель вверх, пузырь под ней
    : (r.top + r.height / 2) - (top + bottom) / 2;         // огромная цель: по центру
  if (Math.abs(dy) < 2) return;
  window.scrollBy({ top: dy, behavior: reduced() ? 'auto' : 'smooth' });
  let last = -1; let still = 0;
  for (let n = 0; n < 40 && still < 3; n += 1) {
    await new Promise((res) => requestAnimationFrame(res));
    const y = window.scrollY;
    still = Math.abs(y - last) < 0.5 ? still + 1 : 0;
    last = y;
  }
}

async function showStep(i, first) {
  const a = active;
  if (!a) return;
  const tok = ++a.stepToken;
  a.i = i;
  const { tip } = a.steps[i];
  let el = find(tip);
  if (!el) { dropStep(); return; }
  a.tip = tip;
  a.el = el;
  a.rect = null;
  markSeen(tip.id);

  const n = a.steps.length;
  const last = i === n - 1;
  const b = a.bubble;
  b.classList.remove('in');
  b.replaceChildren(
    n > 1 ? h('div', { class: 'coach-prog', 'aria-hidden': 'true' },
      h('span', { class: 'coach-dots' }, a.steps.map((_, k) => h('i', { class: k === i ? 'on' : '' }))),
      h('span', { class: 'coach-count' }, `${i + 1} из ${n}`)) : null,
    h('div', { class: 'coach-title' }, tip.title),
    h('div', { class: 'coach-text' }, tip.text),
    h('div', { class: 'coach-foot' },
      n > 1 && !last ? h('button', { class: 'coach-skip', type: 'button', onclick: skipAll }, 'Пропустить') : h('span'),
      h('button', { class: 'coach-next', type: 'button', onclick: advance }, last ? 'Понятно' : 'Дальше')));
  b.setAttribute('aria-label', 'Подсказка: ' + tip.title);

  // высота пузыря известна только после раскладки: меряем, затем при необходимости прокручиваем
  const vw = window.innerWidth;
  b.style.width = Math.min(300, vw - 32) + 'px';
  b.style.visibility = 'hidden';
  b.style.left = '0px'; b.style.top = '0px';
  await bring(el, tip, b.offsetHeight);
  if (active !== a || a.stepToken !== tok) return;
  if (!isVisible(el)) { el = find(tip); if (!el) { dropStep(); return; } a.el = el; }

  b.style.visibility = '';
  a.root.classList.toggle('no-tr', !!first);
  layout();
  void a.root.offsetWidth;
  a.root.classList.remove('no-tr');
  a.shownAt = Date.now();
  // перезапуск входной анимации пузыря
  void b.offsetWidth;
  b.classList.add('in');
  haptic('light');
  const next = b.querySelector('.coach-next');
  try { if (next) next.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
}

function layout() {
  const a = active;
  if (!a || !a.el || !a.tip) return;
  const el = a.el;
  if (!isVisible(el)) return;
  const vw = window.innerWidth; const vh = window.innerHeight;
  const r = rectOf(el, a.tip);
  const pad = a.tip.pad ?? 8;
  const x0 = Math.max(4, r.left - pad); const y0 = Math.max(4, r.top - pad);
  const x1 = Math.min(vw - 4, r.right + pad); const y1 = Math.min(vh - 4, r.bottom + pad);
  const w = Math.max(0, x1 - x0); const hh = Math.max(0, y1 - y0);
  const rad = Math.min(a.tip.round ? 999 : (a.tip.radius ?? 18), w / 2, hh / 2);
  a.rect = { x0, y0, x1, y1 };
  Object.assign(a.hole.style, { left: x0 + 'px', top: y0 + 'px', width: w + 'px', height: hh + 'px', borderRadius: rad + 'px' });

  const set = (c, x, y, ww, h2) => Object.assign(c.style, { left: x + 'px', top: y + 'px', width: Math.max(0, ww) + 'px', height: Math.max(0, h2) + 'px' });
  const [ct, cb, cl, cr] = a.catchers;
  set(ct, 0, 0, vw, y0);
  set(cb, 0, y1, vw, vh - y1);
  set(cl, 0, y0, x0, hh);
  set(cr, x1, y0, vw - x1, hh);

  // пузырь
  const b = a.bubble;
  const bw = Math.min(300, vw - 32);
  b.style.width = bw + 'px';
  const bh = b.offsetHeight;
  const { top, bottom } = safeBounds(el);
  const gap = 14;
  const roomBelow = bottom - y1 - gap;
  const roomAbove = y0 - gap - top;
  const pref = a.tip.placement || 'auto';
  let place;
  if (pref === 'below' && roomBelow >= bh) place = 'below';
  else if (pref === 'above' && roomAbove >= bh) place = 'above';
  else if (roomBelow >= bh) place = 'below';
  else if (roomAbove >= bh) place = 'above';
  else place = 'float';
  const cx = (x0 + x1) / 2;
  const left = Math.min(vw - bw - 16, Math.max(16, cx - bw / 2));
  let ty;
  if (place === 'below') ty = y1 + gap;
  else if (place === 'above') ty = y0 - gap - bh;
  else ty = Math.max(top, bottom - bh);                // не влезает ни сверху, ни снизу: прижимаем к нижней границе, без стрелки
  b.style.left = left + 'px';
  b.style.top = ty + 'px';
  const ax = Math.min(bw - 26, Math.max(26, cx - left));
  b.style.setProperty('--ax', ax + 'px');
  b.style.transformOrigin = `${ax}px ${place === 'below' ? '0%' : '100%'}`;
  b.classList.toggle('below', place === 'below');
  b.classList.toggle('above', place === 'above');
  b.classList.toggle('float', place === 'float');
}

// Уход с экрана / новый заход гасит тур и ожидание
window.addEventListener('hashchange', () => { visit += 1; if (active) close(); });

export const coach = { maybeShow, reset, isActive, isSeen, TIPS };
export default coach;
