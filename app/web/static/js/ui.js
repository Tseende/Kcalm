// Small DOM helpers, Telegram wrappers, formatters. All server strings go through textContent.
import { tg, inTelegram, api } from './api.js';
import { PATHS } from './icons.js';

export { tg, inTelegram };

// ------------------------------------------------------------------ DOM
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props && (typeof props !== 'object' || props instanceof Node || Array.isArray(props))) {
    children.unshift(props);
    props = null;
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k.startsWith('aria-') || k === 'role' || k === 'for' || k === 'inputmode') el.setAttribute(k, v === true ? '' : v);
    else if (k in el) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  appendKids(el, children);
  return el;
}

function appendKids(el, kids) {
  for (const c of kids) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) appendKids(el, c);
    else if (c instanceof Node) el.append(c);
    else el.append(document.createTextNode(String(c)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);

const ALIAS = {
  today: 'calendar', stats: 'chart-bar', add: 'circle-plus', analysis: 'sparkles', menu: 'menu-2', back: 'chevron-left',
  next: 'chevron-right', image: 'photo', close: 'x', undo: 'arrow-back-up', edit: 'pencil', save: 'bookmark',
};

// Tabler Icons (MIT), inline: js/icons.js
export function icon(name, size = 22) {
  const s = h('span', { class: 'ico', 'aria-hidden': 'true' });
  s.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${PATHS[ALIAS[name] || name] || ''}</svg>`;
  return s;
}

// Rounded-square tile with a white icon, flat iOS system colours (Settings style)
export const TILE = Object.fromEntries(['blue', 'green', 'orange', 'red', 'purple', 'teal', 'pink', 'indigo', 'yellow', 'gray'].map((k) => [k, `var(--t-${k})`]));
export function tile(name, color = 'blue', size = 30) {
  const t = h('span', { class: 'ticon', 'aria-hidden': 'true', style: { background: TILE[color] || color, width: size + 'px', height: size + 'px' } }, icon(name, Math.round(size * 0.63)));
  return t;
}

// Эмодзи из текстов бота -> линейная иконка и цвет (в Мини-аппе эмодзи не показываем)
const GLYPH = {
  '🥩': ['egg-fried', 'red'], '💧': ['droplet', 'teal'], '🚶': ['walk', 'green'], '😴': ['bed', 'indigo'],
  '🍽': ['tools-kitchen', 'orange'], '🌙': ['moon', 'indigo'], '📆': ['calendar', 'blue'], '📝': ['pencil', 'blue'],
  '🍚': ['bowl', 'orange'], '🧭': ['target', 'purple'], '🧮': ['chart-bar', 'blue'], '⏱': ['clock', 'blue'],
  '🕐': ['clock', 'orange'], '⚖️': ['scale', 'purple'], '⚖': ['scale', 'purple'], '📈': ['chart-bar', 'orange'], '✅': ['check', 'green'],
};
const LABEL_GLYPH = { 'Калории': '🍽', 'Белок': '🥩', 'Вода': '💧', 'Шаги': '🚶', 'Сон': '😴', 'Дней с едой': '📆' };
/** Мягкая плитка с иконкой вместо эмодзи. key: эмодзи или название показателя; color перекрывает цвет по умолчанию. */
export function glyph(key, size = 36, color) {
  const [name, col] = GLYPH[LABEL_GLYPH[key] || key] || ['sparkles', 'blue'];
  const t = h('span', { class: 'stile', 'aria-hidden': 'true' }, icon(name, Math.round(size * 0.56)));
  t.style.setProperty('--tc', TILE[color || col]);
  t.style.width = t.style.height = size + 'px';
  return t;
}

// --------------------------------------------------------------- hero / progress / stat tile
/** Карточка-«герой» с мягким градиентом: tone = green | amber | peach | accent. */
export function heroCard({ tone = 'accent', cls = '', children = [] } = {}) {
  return h('section', { class: `hero-card tone-${tone}${cls ? ' ' + cls : ''}` }, children);
}

/** Одна полоска прогресса для Анализа и Статистики: status = good | ok | bad | none. Перебор: полная полоска + оранжевый хвост. */
export function progressBar(value, target, { status = 'good', label = null } = {}) {
  const t = target > 0 ? target : 0;
  const v = Math.max(0, value || 0);
  const max = Math.max(v, t) || 1;
  const kids = [h('i', { class: 'pb-fill ' + status, style: { width: ((t ? Math.min(v, t) : v) / max) * 100 + '%' } })];
  if (t && v > t * 1.0001) kids.push(h('i', { class: 'pb-over', style: { left: (t / max) * 100 + '%', width: ((v - t) / max) * 100 + '%' } }));
  return h('div', { class: 'pb', role: 'progressbar', 'aria-label': label, 'aria-valuemin': 0, 'aria-valuemax': Math.round(max), 'aria-valuenow': Math.round(v) }, kids);
}

/** Плитка показателя: цветная иконка, название, значение, полоска «к цели», чип изменения и подпись. Все плитки в сетке одной высоты. */
export function statTile({ icon: ic, color = 'blue', title, value, bar = null, status = 'good', delta = null, caption = null } = {}) {
  return h('div', { class: 'stat-tile' },
    h('div', { class: 'stat-tile-top' }, tile(ic, color, 28), h('span', { class: 'stat-tile-title' }, title)),
    h('div', { class: 'stat-tile-val' }, value),
    bar && bar[1] > 0 ? progressBar(bar[0], bar[1], { status, label: title }) : null,
    h('div', { class: 'stat-tile-foot' }, delta || null, caption ? h('span', { class: 'muted small' }, caption) : null));
}

// --------------------------------------------------------------- formatters
const nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
export const fmtN = (n) => (n === null || n === undefined || isNaN(n) ? '—' : nf.format(Math.round(n * 10) / 10));
export const fmtKcal = (n) => (n === null || n === undefined || isNaN(n) ? '—' : Math.round(n).toLocaleString('ru-RU'));
export function fmtDur(min) {
  if (min === null || min === undefined || isNaN(min)) return '—';
  const m = Math.round(min);
  return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, '0')} м`;
}
// ------------------------------------------------------------------ motion
export const reducedMotion = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

/** Плавно «досчитывает» число в el до `to` (easeOutCubic). При reduced-motion сразу показывает итог. */
export function countUp(el, to, { ms = 700, fmt = fmtKcal, from = 0 } = {}) {
  const set = (v) => { el.textContent = fmt(v); };
  if (reducedMotion() || !isFinite(to) || from === to) { set(to); return; }
  const t0 = performance.now();
  set(from);
  const step = (now) => {
    if (!el.isConnected && now - t0 > 50) { set(to); return; }
    const k = Math.min(1, (now - t0) / ms);
    set(from + (to - from) * (1 - (1 - k) ** 3));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Карточки верхнего уровня container въезжают по очереди (stagger 40 мс, не больше 8). Класс .stg висит только на время анимации. */
export function staggerIn(container, items) {
  if (reducedMotion()) return;
  const kids = (items || [...container.children]).filter((c) => c && !c.hidden).slice(0, 8);
  kids.forEach((c, i) => {
    c.style.setProperty('--i', i);
    c.classList.add('stg');
    setTimeout(() => { c.classList.remove('stg'); c.style.removeProperty('--i'); }, 500 + i * 40);
  });
}

/** Тема: null = «как в системе», 'light' | 'dark'. «Авто» снимает ключ и просит main.js пересчитать схему. */
export function getThemePref() {
  try { const v = localStorage.getItem('theme'); return v === 'light' || v === 'dark' ? v : null; } catch (e) { return null; }
}
export function setThemePref(v) {
  try { if (v === 'light' || v === 'dark') localStorage.setItem('theme', v); else localStorage.removeItem('theme'); } catch (e) { /* без хранилища */ }
  window.dispatchEvent(new Event('app:theme'));
}

export const isDark = () => (inTelegram && tg && tg.colorScheme ? tg.colorScheme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches);
export function plural(n, one, few, many) {
  const a = Math.abs(n) % 100; const b = a % 10;
  const w = a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  return `${n} ${w}`;
}
export const pad2 = (n) => String(n).padStart(2, '0');
export function isoDate(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}
export const todayISO = () => isoDate(new Date());
export const nowHHMM = () => {
  const d = new Date();
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};
export function parseISO(s) {
  const [y, m, d] = s.split('-').map(Number);
  return new Date(y, m - 1, d);
}
export function addDays(iso, n) {
  const d = parseISO(iso);
  d.setDate(d.getDate() + n);
  return isoDate(d);
}
export function dateLabel(iso) {
  const t = todayISO();
  if (iso === t) return 'Сегодня';
  if (iso === addDays(t, -1)) return 'Вчера';
  if (iso === addDays(t, 1)) return 'Завтра';
  return new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' }).format(parseISO(iso));
}
export const longDate = (iso) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(parseISO(iso));
export const shortDate = (iso) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(parseISO(iso));

export const MEAL_TYPES = {
  breakfast: { label: 'Завтрак', icon: 'egg-fried', color: 'orange' },
  lunch: { label: 'Обед', icon: 'soup', color: 'green' },
  dinner: { label: 'Ужин', icon: 'tools-kitchen-2', color: 'purple' },
  snack: { label: 'Перекус', icon: 'apple', color: 'red' },
};
export const mealType = (t) => MEAL_TYPES[t] || { label: 'Приём', icon: 'tools-kitchen', color: 'blue' };

export const DISH_KINDS = {
  plate: { label: 'Тарелка', icon: 'tools-kitchen-2', color: 'blue' },
  deep_plate: { label: 'Глубокая тарелка', icon: 'soup', color: 'teal' },
  bowl: { label: 'Миска', icon: 'bowl', color: 'green' },
  salad_bowl: { label: 'Салатник', icon: 'bowl', color: 'green' },
  mug: { label: 'Кружка', icon: 'coffee', color: 'orange' },
  cup: { label: 'Чашка', icon: 'cup', color: 'pink' },
  glass: { label: 'Стакан', icon: 'glass-full', color: 'indigo' },
  pan: { label: 'Сковорода', icon: 'cooker', color: 'red' },
  pot: { label: 'Кастрюля', icon: 'cooker', color: 'gray' },
  container: { label: 'Контейнер', icon: 'box', color: 'purple' },
  jar: { label: 'Банка', icon: 'box', color: 'teal' },
  thermos: { label: 'Термос', icon: 'cup', color: 'green' },
  other: { label: 'Другое', icon: 'tools-kitchen', color: 'gray' },
};
export const dishKind = (k) => DISH_KINDS[k] || DISH_KINDS.other;

export function macroLine(o) {
  return `Б ${fmtN(o.protein)} · Ж ${fmtN(o.fat)} · У ${fmtN(o.carbs)}`;
}

/** Чипы БЖУ с цветной точкой. `unit` дописывает « г»; `extra` — дополнительные чипы в конце; `pills` — крупные плашки как на экране приёма. */
export function macroChips({ p, f, c }, { unit = '', extra = null, cls = '', pills = false } = {}) {
  const defs = [['p', 'Б', p], ['f', 'Ж', f], ['c', 'У', c]];
  if (pills) {
    return h('div', { class: 'pills' + (cls ? ' ' + cls : '') },
      defs.map(([k, letter, v]) => h('span', { class: 'pill pill-' + k }, letter + ' ', h('b', null, fmtN(v)), ' г')));
  }
  return h('span', { class: 'macs' + (cls ? ' ' + cls : '') },
    defs.map(([k, letter, v]) => h('span', { class: 'mac ' + k }, h('i'), `${letter} ${fmtN(v)}${unit}`)), extra);
}

/** Переключатель дня «‹ дата ›»: стрелки, тап по дате (к сегодня) и свайп по шапке. onChange(date, dir): dir -1 назад, +1 вперёд. */
export function dateSwitch(initial, onChange) {
  let cur = initial;
  const prev = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Предыдущий день', onclick: () => step(-1) }, icon('back'));
  const next = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Следующий день', onclick: () => step(1) }, icon('next'));
  const label = h('button', { class: 'date-label', type: 'button', onclick: () => {
    if (cur === todayISO()) return;
    cur = todayISO(); paint(); haptic('selection'); onChange(cur, 1);
  } });
  const el = h('div', { class: 'date-switch page-head' }, prev, label, next);
  function paint() {
    const isToday = cur === todayISO();
    label.replaceChildren(h('span', { class: 'date-main' }, dateLabel(cur)),
      h('span', { class: 'date-sub' }, isToday ? longDate(cur) : 'нажмите, чтобы вернуться к сегодня'));
    next.disabled = isToday;
  }
  function step(n) {
    const d = addDays(cur, n);
    if (d > todayISO()) return;
    cur = d; paint(); haptic('selection'); onChange(cur, n);
  }
  // Горизонтальный жест длиннее 60 px; вертикальную прокрутку не трогаем.
  let x0 = 0, y0 = 0, tracking = false;
  el.addEventListener('touchstart', (e) => {
    tracking = e.touches.length === 1;
    if (tracking) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }
  }, { passive: true });
  el.addEventListener('touchend', (e) => {
    if (!tracking) return;
    tracking = false;
    const t = e.changedTouches[0];
    const dx = t.clientX - x0; const dy = t.clientY - y0;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx > 0 ? -1 : 1);
  }, { passive: true });
  paint();
  return { el, get: () => cur, set(d) { cur = d; paint(); } };
}

// Safe "simple markdown": escape everything (text nodes) and turn **bold** into <strong>.
export function rich(text) {
  const frag = document.createDocumentFragment();
  String(text === null || text === undefined ? '' : text)
    .split(/(\*\*[^*]+\*\*)/g)
    .forEach((p) => {
      if (!p) return;
      if (p.length > 4 && p.startsWith('**') && p.endsWith('**')) frag.append(h('strong', null, p.slice(2, -2)));
      else frag.append(document.createTextNode(p));
    });
  return frag;
}

// ------------------------------------------------------------ Telegram bits
export function haptic(type = 'success') {
  try {
    if (!tg || !tg.HapticFeedback) return;
    if (type === 'selection') tg.HapticFeedback.selectionChanged();
    else if (['success', 'error', 'warning'].includes(type)) tg.HapticFeedback.notificationOccurred(type);
    else tg.HapticFeedback.impactOccurred(type);
  } catch (e) { /* ignore */ }
}

let mbCb = null;
let mbEl = null;
let mbSuspended = false;
let kbBar = null;
let kbBtn = null;
const mbState = { text: '', disabled: false, loading: false };
// Пока открыта клавиатура, главная кнопка (Telegram или нижняя панель) пряталась и сохранить было нечем.
// Теперь вместо неё прямо над клавиатурой показывается её копия: тот же текст, состояние и действие.
function paintKbBtn() {
  if (!kbBtn) return;
  kbBtn.textContent = mbState.text;
  kbBtn.disabled = mbState.disabled;
  kbBtn.classList.toggle('loading', mbState.loading);
}
function showKbBar() {
  if (!kbBar) {
    kbBar = h('div', { id: 'kbbar', class: 'kbbar' });
    document.body.append(kbBar);
  }
  // pointerdown не даёт полю потерять фокус и клавиатуре закрыться раньше, чем сработает нажатие.
  kbBtn = h('button', {
    class: 'btn btn-primary btn-block', type: 'button',
    onpointerdown: (e) => e.preventDefault(),
    onclick: () => { if (mbCb && !mbState.disabled) { document.activeElement?.blur?.(); mbCb(); } },
  });
  // Слева «Свернуть»: убрать клавиатуру, введённый текст остаётся.
  const kbHide = h('button', {
    class: 'btn btn-secondary kbbar-hide', type: 'button',
    onpointerdown: (e) => e.preventDefault(),
    onclick: () => document.activeElement?.blur?.(),
  }, 'Свернуть');
  kbBar.replaceChildren(kbHide, kbBtn);
  kbBar.hidden = false;
  paintKbBtn();
}
function hideKbBar() {
  if (kbBar) { kbBar.hidden = true; kbBar.replaceChildren(); }
  kbBtn = null;
}
export const mainButton = {
  suspend() {
    if (mbSuspended || !mbCb) return;
    mbSuspended = true;
    if (inTelegram && tg.MainButton && tg.MainButton.isVisible) tg.MainButton.hide();
    showKbBar();
  },
  resume() {
    if (!mbSuspended) return;
    mbSuspended = false;
    hideKbBar();
    if (mbCb && inTelegram && tg.MainButton) tg.MainButton.show();
  },
  show(text, cb, { disabled = false } = {}) {
    this.hide();
    mbCb = cb;
    Object.assign(mbState, { text, disabled, loading: false });
    if (inTelegram && tg.MainButton) {
      tg.MainButton.setText(text);
      tg.MainButton.onClick(cb);
      tg.MainButton.show();
      disabled ? tg.MainButton.disable() : tg.MainButton.enable();
    } else {
      mbEl = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: cb, disabled }, text);
      const bar = $('#mainbar');
      bar.replaceChildren(mbEl);
      bar.hidden = false;
      document.body.classList.add('has-main');
    }
  },
  setText(text) {
    mbState.text = text;
    paintKbBtn();
    if (inTelegram && tg.MainButton) tg.MainButton.setText(text);
    else if (mbEl) mbEl.textContent = text;
  },
  setDisabled(d) {
    mbState.disabled = d;
    paintKbBtn();
    if (inTelegram && tg.MainButton) (d ? tg.MainButton.disable() : tg.MainButton.enable());
    else if (mbEl) mbEl.disabled = d;
  },
  setLoading(l) {
    mbState.loading = l;
    paintKbBtn();
    if (inTelegram && tg.MainButton) (l ? tg.MainButton.showProgress(false) : tg.MainButton.hideProgress());
    else if (mbEl) mbEl.classList.toggle('loading', l);
  },
  hide() {
    if (inTelegram && tg.MainButton) {
      if (mbCb) tg.MainButton.offClick(mbCb);
      tg.MainButton.hideProgress();
      tg.MainButton.hide();
    }
    mbCb = null;
    mbEl = null;
    mbSuspended = false;
    hideKbBar();
    const bar = $('#mainbar');
    if (bar) {
      bar.hidden = true;
      bar.replaceChildren();
    }
    document.body.classList.remove('has-main');
  },
};

let bbCb = null;
export const backButton = {
  show(cb) {
    this.hide();
    bbCb = cb;
    if (inTelegram && tg.BackButton) {
      tg.BackButton.onClick(cb);
      tg.BackButton.show();
    }
  },
  hide() {
    if (inTelegram && tg.BackButton) {
      if (bbCb) tg.BackButton.offClick(bbCb);
      tg.BackButton.hide();
    }
    bbCb = null;
  },
};

export function confirmDialog(message) {
  return new Promise((resolve) => {
    try {
      if (inTelegram && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) {
        tg.showConfirm(message, (ok) => resolve(!!ok));
        return;
      }
    } catch (e) { /* fall through */ }
    resolve(window.confirm(message));
  });
}

// --------------------------------------------------------------- navigation
let depth = 0;
let replacing = false;
export const nav = {
  dir: 'fwd',   // как пришли на экран: 'fwd' (новый переход / replace), 'back' (назад по истории), 'same' (перерисовка)
  go: (p) => { location.hash = '#' + p; },
  replace: (p) => { replacing = true; location.replace('#' + p); },
  back: (fallback = '/today') => (depth > 0 ? history.back() : nav.replace(fallback)),
  _track() {
    if (replacing) {
      replacing = false;
      nav.dir = 'fwd';
      history.replaceState({ d: depth }, '');
    } else if (history.state && typeof history.state.d === 'number') {
      nav.dir = history.state.d < depth ? 'back' : history.state.d > depth ? 'fwd' : 'same';
      depth = history.state.d;
    } else {
      nav.dir = 'fwd';
      depth += 1;
      history.replaceState({ d: depth }, '');
    }
  },
  _init() { history.replaceState({ d: 0 }, ''); },
};

// -------------------------------------------------------------- components
const GENERIC_ERR = 'Что-то пошло не так. Попробуйте ещё раз.';
/** Текст ошибки для пользователя: русские сообщения сервера показываем как есть, технический текст (TypeError и т.п.) прячем в консоль. */
export function friendly(err, fallback = GENERIC_ERR) {
  const m = typeof err === 'string' ? err : err && err.message;
  if (typeof m === 'string' && /[а-яё]/i.test(m)) return m;
  if (err) console.warn('[app]', err);
  return fallback;
}

/** Перерисовать текущий экран (для «Повторить»): main.js слушает hashchange. */
export function reloadRoute() { window.dispatchEvent(new HashChangeEvent('hashchange')); }

// `undo`: an async function; the toast then shows «Отменить» and stays longer.
export function toast(msg, kind = '', { undo } = {}) {
  if (kind === 'error') msg = friendly(msg);
  const ms = undo ? 5000 : 3000;
  const t = h('div', { class: 'toast ' + kind + (undo ? ' has-action' : ''), role: 'status' }, h('span', null, msg));
  if (undo) {
    let used = false;
    t.append(h('button', { class: 'toast-action', type: 'button', onclick: async () => {
      if (used) return;
      used = true;
      t.remove();
      try {
        await undo();
        haptic('light');
        dataChanged();
      } catch (e) { toast(e, 'error'); }
    } }, 'Отменить'));
  }
  $('#toasts').append(t);
  if (kind === 'error') haptic('error');
  setTimeout(() => t.classList.add('out'), ms);
  setTimeout(() => t.remove(), ms + 400);
}

// Screens that show diary data listen for this to reload after changes made elsewhere (undo).
export function dataChanged() { window.dispatchEvent(new Event('app:data')); }

const openSheets = new Set();
export function closeAllSheets() { [...openSheets].forEach((s) => s.close()); }

export function sheet({ title, content, onClose } = {}) {
  const wrap = h('div', { class: 'sheet-wrap' });
  const panel = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title || 'Окно', tabindex: '-1' },
    h('div', { class: 'grabber' }),
    title ? h('div', { class: 'sheet-title' }, title) : null,
    content);
  const prevFocus = document.activeElement;
  const prevBack = bbCb;
  let closed = false;
  const api_ = { close, el: panel };
  function close() {
    if (closed) return;
    closed = true;
    openSheets.delete(api_);
    document.removeEventListener('keydown', onKey);
    if (!openSheets.size) document.body.classList.remove('sheet-open');
    if (inTelegram && tg.BackButton) { if (prevBack) backButton.show(prevBack); else backButton.hide(); }
    wrap.style.pointerEvents = 'none';
    wrap.classList.remove('open');
    panel.style.transform = '';
    setTimeout(() => wrap.remove(), 220);
    if (prevFocus && prevFocus.isConnected && prevFocus.focus) { try { prevFocus.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }
    if (onClose) onClose();
  }
  function onKey(e) { if (e.key === 'Escape') close(); }
  wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });

  // Свайп вниз закрывает шторку: тянем с любого места, если содержимое прокручено к началу.
  let y0 = 0, dy = 0, t0 = 0, drag = false;
  panel.addEventListener('touchstart', (e) => {
    drag = false; dy = 0;
    if (e.touches.length !== 1) return;
    y0 = e.touches[0].clientY; t0 = Date.now();
  }, { passive: true });
  panel.addEventListener('touchmove', (e) => {
    if (e.touches.length !== 1) return;
    const d = e.touches[0].clientY - y0;
    if (!drag && d > 8 && panel.scrollTop <= 0) { drag = true; panel.style.transition = 'none'; }
    if (!drag) return;
    dy = Math.max(0, d);
    panel.style.transform = `translateY(${dy}px)`;
    if (e.cancelable) e.preventDefault();
  }, { passive: false });
  const release = () => {
    if (!drag) return;
    drag = false;
    panel.style.transition = '';
    if (dy > 110 || (dy > 40 && Date.now() - t0 < 250)) close();
    else panel.style.transform = '';
  };
  panel.addEventListener('touchend', release);
  panel.addEventListener('touchcancel', release);

  document.addEventListener('keydown', onKey);
  openSheets.add(api_);
  document.body.classList.add('sheet-open');
  if (inTelegram && tg.BackButton) backButton.show(close);   // «Назад» Telegram сначала закрывает шторку
  wrap.append(panel);
  $('#overlay').append(wrap);
  requestAnimationFrame(() => wrap.classList.add('open'));
  setTimeout(() => wrap.classList.add('open'), 60);
  try { panel.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
  return api_;
}

export function skel(height = 16, width = '100%', extra = '') {
  return h('div', { class: 'skel ' + extra, style: { height: height + 'px', width } });
}
export function skeletonCards(n = 3, height = 90) {
  return h('div', { class: 'stack' }, Array.from({ length: n }, () => skel(height, '100%', 'skel-card')));
}
/** Скелетон по реальной раскладке экрана: список высот карточек (число) или готовых элементов. */
export function skeletonBlocks(heights) {
  return h('div', { class: 'stack' }, heights.map((x) => (typeof x === 'number' ? skel(x, '100%', 'skel-card') : x)));
}

/** Повторная загрузка блока: старое содержимое остаётся (страница не схлопывается, прокрутка не прыгает), высота фиксируется.
 *  Возвращает release(): вызвать после отрисовки нового содержимого или ошибки. */
export function holdHeight(box) {
  box.style.minHeight = box.offsetHeight + 'px';
  box.classList.add('is-loading');
  return () => { box.classList.remove('is-loading'); box.style.minHeight = ''; };
}

export function emptyState(iconName, title, text, action) {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty-ico', 'aria-hidden': 'true' }, icon(iconName, 34)),
    h('div', { class: 'empty-title' }, title),
    text ? h('div', { class: 'muted' }, text) : null,
    action || null);
}

/** Кнопка для истёкшей сессии: «Обновить» перезагрузила бы страницу с теми же устаревшими данными, помогает только повторное открытие из чата. */
export function sessionExpiredButton() {
  return h('button', { class: 'btn btn-primary', type: 'button', onclick: () => { try { tg.close(); } catch (e) { location.reload(); } } }, 'Закрыть приложение');
}

export function errorState(root, err, retry) {
  if (err) console.error(err);
  if (err && err.expired) {
    root.replaceChildren(emptyState('lock', 'Сессия устарела', err.message, sessionExpiredButton()));
    return;
  }
  root.replaceChildren(emptyState('mood-sad', 'Не получилось загрузить', friendly(err, 'Проверьте соединение и попробуйте ещё раз.'),
    retry ? h('button', { class: 'btn btn-secondary', type: 'button', onclick: retry }, 'Повторить') : null));
}

// Telegram-like top bar: [< Назад] centered title [right]
export function pageHead(title, { right, back = true, parent = '/today' } = {}) {
  return h('header', { class: 'page-head' + (back ? '' : ' large') },
    h('div', { class: 'ph-side' }, back ? h('button', { class: 'back-btn', type: 'button', 'aria-label': 'Назад', onclick: () => nav.back(parent) }, icon('back', 24), h('span', null, 'Назад')) : null),
    h('h1', null, title),
    h('div', { class: 'ph-side end' }, right || null));
}

export function sectionTitle(text, right) {
  return h('div', { class: 'section-title' }, h('span', null, text), right || null);
}

export function segmented(options, value, onChange, label) {
  const el = h('div', { class: 'seg', role: 'tablist', 'aria-label': label || null });
  const btns = options.map(([val, text]) => {
    const b = h('button', { type: 'button', role: 'tab', class: val === value ? 'active' : '', 'aria-selected': String(val === value), onclick: () => {
      btns.forEach((x) => { x.classList.remove('active'); x.setAttribute('aria-selected', 'false'); });
      b.classList.add('active');
      b.setAttribute('aria-selected', 'true');
      haptic('selection');
      onChange(val);
    } }, text);
    return b;
  });
  el.append(...btns);
  return el;
}

export function toggle(checked, onChange, label) {
  const input = h('input', { type: 'checkbox', checked: !!checked, 'aria-label': label || null, onchange: () => onChange(input.checked) });
  return h('label', { class: 'switch' }, input, h('span', { class: 'slider' }));
}

export function field(label, control, hint) {
  return h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), control, hint ? h('span', { class: 'hint' }, hint) : null);
}

// Строка формы в стиле iOS: подпись слева, значение справа. Строки собираются в group().
export function frow(label, control) {
  return h('label', { class: 'frow' }, h('span', { class: 'frow-label' }, label), control);
}
export function group(rows, footnote) {
  return h('div', { class: 'fgroup' }, h('div', { class: 'list form' }, rows), footnote ? h('div', { class: 'footnote' }, footnote) : null);
}

// ------------------------------------------------------------------ числовые поля
// Не type=number: в iOS WebKit он теряет запятую (значение становится пустым), плохо выделяется,
// каретка прыгает, а Enter включает встроенную проверку step/min/max. Здесь обычный текст с цифровой
// клавиатурой: лишнее вырезаем на лету, каретку сохраняем, границы проверяет код экрана (numCheck).
const fmtDec = (v) => (v === null || v === undefined || v === '' || (typeof v === 'number' && isNaN(v)) ? '' : String(v).replace('.', ','));
export { fmtDec as numFmt };

// Выделить всё при фокусе, в том числе на iOS, где select() сразу после фокуса не срабатывает.
function selectAllOnFocus(el) {
  let fresh = 0;
  const sel = () => { if (document.activeElement === el) { try { el.setSelectionRange(0, el.value.length); } catch (e) { /* тип не поддерживает */ } } };
  el.addEventListener('focus', () => { fresh = Date.now(); sel(); setTimeout(sel, 0); setTimeout(sel, 80); });
  // Тап, который дал фокус, не должен сразу сбросить выделение кареткой.
  el.addEventListener('mouseup', (e) => { if (Date.now() - fresh < 400) e.preventDefault(); });
  el.addEventListener('click', () => { if (Date.now() - fresh < 400) setTimeout(sel, 0); });
}

// Enter на цифровой клавиатуре = «Готово»: фокус уходит, значение остаётся.
function doneOnEnter(el) {
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); el.blur(); } });
}

function setClean(el, out, caret) {
  if (out === el.value) return;
  el.value = out;
  try { el.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
}

// props: min, max, step задают границы для numCheck и число разрядов (нативными атрибутами не ставятся);
// integer; decimals; select (по умолчанию true: фокус выделяет всё); value (число или строка).
export function numInput(props = {}) {
  const { min, max, step, value, inputmode, integer, decimals, select = true, ...rest } = props;
  const int = integer ?? inputmode === 'numeric';
  const stepDec = step != null && String(step).includes('.') ? String(step).split('.')[1].length : 0;
  const dec = int ? 0 : Math.max(1, decimals ?? stepDec);
  const maxInt = max != null ? String(Math.trunc(Math.abs(max))).length : Infinity;
  const el = h('input', { type: 'text', inputmode: int ? 'numeric' : 'decimal', autocomplete: 'off', ...rest });
  el.setAttribute('enterkeyhint', 'done');
  el.setAttribute('autocorrect', 'off');
  el.setAttribute('autocapitalize', 'off');
  el.setAttribute('spellcheck', 'false');
  el.value = fmtDec(value);
  el._num = { min: min ?? null, max: max ?? null, int, dec };

  el.addEventListener('input', (e) => {
    if (e.isComposing) return;
    const raw = el.value;
    const caret = el.selectionStart ?? raw.length;
    let out = '', kept = 0, sep = false, intLen = 0, fracLen = 0;
    for (let i = 0; i < raw.length; i++) {
      const ch = raw[i];
      let k = null;
      if (ch >= '0' && ch <= '9') {
        if (sep) { if (fracLen < dec) { k = ch; fracLen++; } } else if (intLen < maxInt) { k = ch; intLen++; }
      } else if (!int && (ch === ',' || ch === '.') && !sep) { k = ','; sep = true; }
      if (k !== null) { out += k; if (i < caret) kept++; }
    }
    // «007» -> «7», «,5» -> «0,5»
    while (out.length > 1 && out[0] === '0' && out[1] !== ',') { out = out.slice(1); if (kept > 0) kept--; }
    if (out[0] === ',') { out = '0' + out; kept++; }
    setClean(el, out, kept);
  });
  el.addEventListener('blur', () => { if (el.value.endsWith(',')) el.value = el.value.slice(0, -1); });
  doneOnEnter(el);
  if (select) selectAllOnFocus(el);
  return el;
}

// Поле из одних цифр (штрихкод): нули в начале сохраняются, лишнее вырезается, каретка не прыгает.
export function codeInput(props = {}) {
  const { maxlength = 14, select = true, ...rest } = props;
  const el = h('input', { type: 'text', inputmode: 'numeric', autocomplete: 'off', maxLength: maxlength, ...rest });
  el.setAttribute('enterkeyhint', 'go');
  el.setAttribute('autocorrect', 'off');
  el.setAttribute('spellcheck', 'false');
  el.addEventListener('input', (e) => {
    if (e.isComposing) return;
    const raw = el.value;
    const caret = el.selectionStart ?? raw.length;
    const out = raw.replace(/\D/g, '').slice(0, maxlength);
    const kept = raw.slice(0, caret).replace(/\D/g, '').length;
    setClean(el, out, Math.min(kept, out.length));
  });
  if (select) selectAllOnFocus(el);
  return el;
}

export function numVal(input) {
  const s = String(input.value).replace(/[\s ]/g, '').replace(',', '.');
  if (s === '' || s === '.') return null;
  const v = Number(s);
  return Number.isFinite(v) ? v : null;
}

// Записать число в поле (с запятой, как видит пользователь).
export function numSet(input, v) { input.value = fmtDec(v); }

// Проверка границ из numInput: текст ошибки или null. Пустое поле не ошибка, это решает экран.
export function numCheck(input, label) {
  const v = numVal(input);
  if (v === null) return String(input.value).trim() ? `${label}: введите число` : null;
  const { min, max } = input._num || {};
  if ((min != null && v < min) || (max != null && v > max)) {
    if (min != null && max != null) return `${label}: от ${fmtDec(min)} до ${fmtDec(max)}`;
    return max != null ? `${label}: не больше ${fmtDec(max)}` : `${label}: не меньше ${fmtDec(min)}`;
  }
  return null;
}

// <img> that supports Authorization-protected photo URLs
// Пока фото грузится, у <img> фон-заглушка (.img-wait); если не загрузилось, на его месте плитка с иконкой.
// fallback: () => Node|null — что показать вместо фото, если файла нет (диск на хостинге временный) или оно не загрузилось.
export function photoImg(url, cls = '', alt = '', fallback = null) {
  const img = h('img', { class: (cls ? cls + ' ' : '') + 'img-wait', alt, loading: 'lazy', decoding: 'async' });
  const fail = () => {
    if (fallback && img.parentNode) {
      let node = null;
      try { node = fallback(); } catch (e) { /* без запасной картинки */ }
      if (node) { img.replaceWith(node); return; }
    }
    img.classList.add('broken');
    if (img.nextElementSibling && img.nextElementSibling.classList.contains('img-ph')) return;
    img.after(h('span', { class: 'img-ph', role: 'img', 'aria-label': alt ? alt + ': не загрузилось' : 'Фото не загрузилось' }, icon('photo', 28)));
  };
  img.addEventListener('load', () => img.classList.remove('img-wait'));
  img.addEventListener('error', fail);
  api.imageUrl(url).then((u) => { img.src = u; }).catch(fail);
  return img;
}

// Every upload goes out as JPEG (the backend rejects HEIC with 415): re-encode through a canvas,
// max side `max` px. Already-small JPEGs are sent untouched. Throws an Error with a
// user-facing message when a file the browser cannot decode is not JPEG/PNG/WebP.
export async function compressImage(file, max = 1600, quality = 0.85) {
  const type = (file.type || '').toLowerCase();
  const isJpeg = type === 'image/jpeg' || /\.jpe?g$/i.test(file.name || '');
  if (type === 'image/gif') return file;
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
    if (k === 1 && isJpeg && file.size < 1.5e6) return file;
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * k);
    c.height = Math.round(bmp.height * k);
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#fff'; // PNG/WebP transparency would turn black in JPEG
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(bmp, 0, 0, c.width, c.height);
    if (bmp.close) bmp.close();
    const blob = await new Promise((r) => c.toBlob(r, 'image/jpeg', quality));
    if (!blob) throw new Error('toBlob');
    return new File([blob], (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' });
  } catch (e) {
    if (/^image\/(jpeg|png|webp)$/.test(type)) return file; // server accepts these as is
    throw new Error('Не удалось прочитать фото. Сохраните его как JPEG или PNG и попробуйте ещё раз.');
  }
}

export function debounce(fn, ms = 300) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

export function busyOverlay(text, sub) {
  const t0 = Date.now();
  const timer = h('div', { class: 'muted' }, '0 с');
  const iv = setInterval(() => { timer.textContent = Math.round((Date.now() - t0) / 1000) + ' с'; }, 1000);
  const el = h('div', { class: 'busy', role: 'alert', 'aria-live': 'assertive' },
    h('div', { class: 'busy-box' }, h('div', { class: 'spinner' }), h('div', { class: 'busy-title' }, text), sub ? h('div', { class: 'muted' }, sub) : null, timer));
  $('#overlay').append(el);
  return () => { clearInterval(iv); el.remove(); };
}

// ------------------------------------------------------------------ drafts
// Черновики текстовых полей: если Telegram выгрузит мини-приложение или пользователь свернёт его,
// набранное возвращается. Хранится локально, не дольше суток; ошибки хранилища не мешают работе.
const DRAFT_TTL = 24 * 3600 * 1000;
export function draft(key, el) {
  const k = 'kcalm:draft:' + key;
  try {
    const raw = localStorage.getItem(k);
    if (raw) {
      const d = JSON.parse(raw);
      if (d && Date.now() - d.t < DRAFT_TTL && !el.value) el.value = d.v;
      else localStorage.removeItem(k);
    }
  } catch (e) { /* хранилище недоступно */ }
  el.addEventListener('input', () => {
    try {
      if (el.value) localStorage.setItem(k, JSON.stringify({ v: el.value, t: Date.now() }));
      else localStorage.removeItem(k);
    } catch (e) { /* хранилище недоступно */ }
  });
  return () => { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } };
}
