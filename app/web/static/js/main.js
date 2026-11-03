// Router + tab bar + Telegram bootstrap.
import { api, isMock, onMockEnabled, tg, inTelegram } from './api.js';
import { initKeyboard } from './keyboard.js';
import { h, $, icon, tile, sheet, nav, mainButton, backButton, emptyState, errorState, haptic } from './ui.js';

const R = (re, load, opts = {}) => ({ re: new RegExp('^' + re + '$'), load, ...opts });
const routes = [
  R('/today', () => import('./screens/today.js'), { tab: 'today' }),
  R('/stats', () => import('./screens/stats.js'), { tab: 'stats' }),
  R('/analysis', () => import('./screens/analysis.js'), { tab: 'analysis' }),
  R('/menu', () => import('./screens/menu.js'), { tab: 'menu' }),
  R('/meal/(\\d+)', () => import('./screens/meal.js'), { keys: ['id'], parent: '/today' }),
  R('/add/meal', () => import('./screens/add_meal.js'), { parent: '/today' }),
  R('/add/(drink|water)', () => import('./screens/drinks.js'), { parent: '/today' }),
  R('/add/(weight|sleep|activity)', () => import('./screens/forms.js'), { keys: ['kind'], parent: '/today' }),
  R('/add/screen', () => import('./screens/screenshot.js'), { parent: '/today' }),
  R('/add/label', () => import('./screens/label.js'), { parent: '/menu/products' }),
  R('/menu/products', () => import('./screens/products.js'), { parent: '/menu' }),
  R('/menu/product/(new|\\d+)', () => import('./screens/product.js'), { keys: ['id'], parent: '/menu/products' }),
  R('/menu/dishes', () => import('./screens/dishes.js'), { parent: '/menu' }),
  R('/menu/dish/(new|\\d+)', () => import('./screens/dish.js'), { keys: ['id'], parent: '/menu/dishes' }),
  R('/menu/(goals|export|about)', () => import('./screens/info.js'), { keys: ['page'], parent: '/menu' }),
];

const TABS = [
  ['today', 'Сегодня', 'today', '/today'],
  ['stats', 'Статистика', 'stats', '/stats'],
  ['add', 'Добавить', 'plus', null],
  ['analysis', 'Анализ', 'analysis', '/analysis'],
  ['menu', 'Меню', 'menu', '/menu'],
];

const view = $('#view');
const tabbar = $('#tabbar');
let rid = 0;
let cleanup = null;

function buildTabbar() {
  tabbar.replaceChildren(...TABS.map(([id, label, ic, path]) => {
    if (id === 'add') {
      return h('button', { class: 'tab tab-plus', type: 'button', 'aria-label': 'Добавить', 'aria-haspopup': 'dialog', onclick: openAddSheet }, h('span', { class: 'plus-circle' }, icon('plus', 20)));
    }
    return h('a', { class: 'tab', href: '#' + path, dataset: { tab: id }, 'aria-label': label }, icon(ic, 24), h('span', null, label));
  }));
}

function setActiveTab(tab) {
  tabbar.hidden = !tab;
  document.body.classList.toggle('has-tabbar', !!tab);
  tabbar.querySelectorAll('.tab[data-tab]').forEach((a) => {
    const on = a.dataset.tab === tab;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}

function openAddSheet() {
  haptic('light');
  const actions = [
    ['tools-kitchen-2', 'orange', 'Приём пищи', 'Фото тарелки или описание текстом', '/add/meal', true],
    ['droplet', 'teal', 'Напитки', 'Вода, чай, кофе, газировка', '/add/drink'],
    ['tag', 'pink', 'По этикетке', 'Фото таблицы КБЖУ', '/add/label'],
    ['scale', 'purple', 'Вес', 'Утреннее взвешивание', '/add/weight'],
    ['bed', 'indigo', 'Сон', 'Часы сна и оценка', '/add/sleep'],
    ['run', 'green', 'Активность', 'Шаги, калории, тренировка', '/add/activity'],
    ['device-mobile', 'blue', 'Скриншот', 'Сон, шаги или весы', '/add/screen'],
  ];
  const s = sheet({ title: 'Добавить', content: h('div', { class: 'add-grid' }, actions.map(([ic, col, t, sub, path, wide]) =>
    h('button', { class: 'add-card' + (wide ? ' wide' : ''), type: 'button', onclick: () => { s.close(); nav.go(path); } },
      tile(ic, col, wide ? 48 : 42),
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t), h('span', { class: 'row-sub muted' }, sub)),
      wide ? icon('next', 18) : null))) });
}

function match(path) {
  for (const r of routes) {
    const m = path.match(r.re);
    if (m) {
      const params = {};
      (r.keys || []).forEach((k, i) => { params[k] = m[i + 1]; });
      return { r, params };
    }
  }
  return null;
}

async function render() {
  const raw = location.hash.replace(/^#/, '') || '/today';
  const [path, qstr] = raw.split('?');
  const query = Object.fromEntries(new URLSearchParams(qstr || ''));
  const my = ++rid;
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  mainButton.hide();
  document.querySelectorAll('#overlay .sheet-wrap').forEach((x) => x.remove());
  const found = match(path);
  if (!found) { nav.replace('/today'); return; }
  const { r, params } = found;
  document.body.dataset.route = r.tab || 'sub';
  setActiveTab(r.tab || null);
  if (r.tab) backButton.hide();
  else backButton.show(() => nav.back(r.parent || '/today'));
  view.replaceChildren();
  window.scrollTo(0, 0);
  document.body.classList.remove('scrolled');
  const ctx = { params, query, alive: () => my === rid, parent: r.parent };
  try {
    const mod = await r.load();
    if (my !== rid) return;
    const res = await mod.default(view, ctx);
    if (my !== rid) { if (typeof res === 'function') res(); return; }
    if (typeof res === 'function') cleanup = res;
  } catch (e) {
    console.error(e);
    if (my === rid) errorState(view, e, render);
  }
}

function showBlocking(err) {
  $('#boot')?.remove();
  tabbar.hidden = true;
  view.replaceChildren(h('div', { class: 'screen' }, emptyState(err.status === 409 ? 'mood-smile' : 'lock',
    err.status === 409 ? 'Нужна настройка' : err.status === 401 ? 'Нет доступа' : 'Нет связи',
    err.message, h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => location.reload() }, 'Обновить'))));
}

function mockBadge() {
  if ($('#mock-badge')) return;
  document.body.append(h('div', { id: 'mock-badge', class: 'mock-badge', title: 'Данные не настоящие' }, 'Демо-данные'));
}

async function boot() {
  try {
    if (tg) {
      tg.ready();
      tg.expand();
      const v = (x) => tg.isVersionAtLeast && tg.isVersionAtLeast(x);
      try { if (v('7.7')) tg.disableVerticalSwipes(); } catch (e) { /* old client */ }
      try { if (v('8.0') && !tg.isFullscreen) tg.requestFullscreen(); } catch (e) { /* старый клиент или десктоп */ }
    }
  } catch (e) { console.warn(e); }
  document.documentElement.classList.toggle('in-tg', inTelegram);
  // Полноэкранный режим: заголовок экрана переезжает в полосу системных кнопок Telegram.
  const syncFs = () => document.documentElement.classList.toggle('fs', !!(tg && tg.isFullscreen));
  syncFs();
  try { if (tg && tg.onEvent) tg.onEvent('fullscreenChanged', syncFs); } catch (e) { /* старый клиент */ }
  const applyScheme = () => {
    if (!(inTelegram && tg && tg.colorScheme)) return;
    const dark = tg.colorScheme === 'dark';
    document.documentElement.classList.toggle('scheme-dark', dark);
    document.documentElement.classList.toggle('scheme-light', !dark);
    // Шапка и фон Telegram под фон приложения (системный серый iOS), а не под тему клиента.
    const bg = dark ? '#000000' : '#f2f2f7';
    try { if (tg.isVersionAtLeast && tg.isVersionAtLeast('6.1')) { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } } catch (e) { /* old client */ }
  };
  applyScheme();
  try { if (tg && tg.onEvent) tg.onEvent('themeChanged', applyScheme); } catch (e) { /* old client */ }
  const onScroll = () => document.body.classList.toggle('scrolled', window.scrollY > 4);
  window.addEventListener('scroll', onScroll, { passive: true });
  onMockEnabled(mockBadge);
  buildTabbar();
  initKeyboard();
  nav._init();
  try {
    await api.me();
  } catch (e) {
    if (e.status === 401 || e.status === 409 || e.code === 'network' || e.code === 'timeout') return showBlocking(e);
    console.error(e);
  }
  if (isMock()) mockBadge();
  $('#boot')?.remove();
  // Кнопка из напоминания открывает нужный экран: ?screen=analysis. Хеш занят данными Telegram.
  const screen = new URLSearchParams(location.search).get('screen');
  if (screen) {
    const rest = new URLSearchParams(location.search);
    rest.delete('screen');                            // one-shot: a later reload keeps the user's tab
    const q = rest.toString();
    const hash = ['analysis', 'stats', 'today'].includes(screen) ? '#/' + screen : location.hash;
    history.replaceState(null, '', location.pathname + (q ? '?' + q : '') + hash);
  }
  window.addEventListener('hashchange', () => { nav._track(); render(); });
  render();
}

boot();
