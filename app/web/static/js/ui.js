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
  today: 'calendar', stats: 'chart-bar', add: 'circle-plus', chat: 'message-circle', menu: 'menu-2', back: 'chevron-left',
  next: 'chevron-right', image: 'photo', close: 'x', undo: 'arrow-back-up', edit: 'pencil', save: 'bookmark',
};

// Tabler Icons (MIT), inline: js/icons.js
export function icon(name, size = 22) {
  const s = h('span', { class: 'ico', 'aria-hidden': 'true' });
  s.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${PATHS[ALIAS[name] || name] || ''}</svg>`;
  return s;
}

// Rounded-square coloured tile with a white icon (Telegram Settings style)
const grad = (a, b) => `linear-gradient(145deg, ${a}, ${b})`;
export const TILE = {
  blue: grad('#5CC4FF', '#2A8EEA'), green: grad('#5EDB7E', '#25A84B'), orange: grad('#FFB547', '#FF8A00'), red: grad('#FF7A6E', '#F0362B'),
  purple: grad('#C98BF2', '#8E44D6'), teal: grad('#7DDDFB', '#27A9D8'), pink: grad('#FF7A98', '#F0245A'), indigo: grad('#7F7DF0', '#4B49C7'),
  yellow: grad('#FFD04D', '#FFA400'), gray: grad('#A9A9B0', '#7C7C84'),
};
export function tile(name, color = 'blue', size = 30) {
  const t = h('span', { class: 'ticon', 'aria-hidden': 'true', style: { background: TILE[color] || color, width: size + 'px', height: size + 'px' } }, icon(name, Math.round(size * 0.63)));
  return t;
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
export const isDark = () => (inTelegram && tg && tg.colorScheme ? tg.colorScheme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches);
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
  mug: { label: 'Кружка', icon: 'coffee', color: 'orange' },
  cup: { label: 'Чашка', icon: 'cup', color: 'pink' },
  glass: { label: 'Стакан', icon: 'glass-full', color: 'indigo' },
  pan: { label: 'Сковорода', icon: 'cooker', color: 'red' },
  container: { label: 'Контейнер', icon: 'box', color: 'purple' },
  other: { label: 'Другое', icon: 'tools-kitchen', color: 'gray' },
};
export const dishKind = (k) => DISH_KINDS[k] || DISH_KINDS.other;

export function macroLine(o) {
  return `Б ${fmtN(o.protein)} · Ж ${fmtN(o.fat)} · У ${fmtN(o.carbs)}`;
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
    if (['success', 'error', 'warning'].includes(type)) tg.HapticFeedback.notificationOccurred(type);
    else tg.HapticFeedback.impactOccurred(type);
  } catch (e) { /* ignore */ }
}

let mbCb = null;
let mbEl = null;
export const mainButton = {
  show(text, cb, { disabled = false } = {}) {
    this.hide();
    mbCb = cb;
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
    if (inTelegram && tg.MainButton) tg.MainButton.setText(text);
    else if (mbEl) mbEl.textContent = text;
  },
  setDisabled(d) {
    if (inTelegram && tg.MainButton) (d ? tg.MainButton.disable() : tg.MainButton.enable());
    else if (mbEl) mbEl.disabled = d;
  },
  setLoading(l) {
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
  go: (p) => { location.hash = '#' + p; },
  replace: (p) => { replacing = true; location.replace('#' + p); },
  back: (fallback = '/today') => (depth > 0 ? history.back() : nav.replace(fallback)),
  _track() {
    if (replacing) {
      replacing = false;
      history.replaceState({ d: depth }, '');
    } else if (history.state && typeof history.state.d === 'number') depth = history.state.d;
    else {
      depth += 1;
      history.replaceState({ d: depth }, '');
    }
  },
  _init() { history.replaceState({ d: 0 }, ''); },
};

// -------------------------------------------------------------- components
export function toast(msg, kind = '') {
  const t = h('div', { class: 'toast ' + kind, role: 'status' }, msg);
  $('#toasts').append(t);
  if (kind === 'error') haptic('error');
  setTimeout(() => t.classList.add('out'), 3000);
  setTimeout(() => t.remove(), 3400);
}

export function sheet({ title, content, onClose } = {}) {
  const wrap = h('div', { class: 'sheet-wrap' });
  const panel = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title || 'Окно' },
    h('div', { class: 'grabber' }),
    title ? h('div', { class: 'sheet-title' }, title) : null,
    content);
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey);
    wrap.classList.remove('open');
    setTimeout(() => wrap.remove(), 220);
    if (onClose) onClose();
  }
  function onKey(e) { if (e.key === 'Escape') close(); }
  wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });
  document.addEventListener('keydown', onKey);
  wrap.append(panel);
  $('#overlay').append(wrap);
  requestAnimationFrame(() => wrap.classList.add('open'));
  setTimeout(() => wrap.classList.add('open'), 60);
  return { close, el: panel };
}

export function skel(height = 16, width = '100%', extra = '') {
  return h('div', { class: 'skel ' + extra, style: { height: height + 'px', width } });
}
export function skeletonCards(n = 3, height = 90) {
  return h('div', { class: 'stack' }, Array.from({ length: n }, () => skel(height, '100%', 'skel-card')));
}

export function emptyState(iconName, title, text, action) {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty-ico', 'aria-hidden': 'true' }, icon(iconName, 34)),
    h('div', { class: 'empty-title' }, title),
    text ? h('div', { class: 'muted' }, text) : null,
    action || null);
}

export function errorState(root, err, retry) {
  root.replaceChildren(emptyState('mood-sad', 'Что-то пошло не так', err && err.message ? err.message : 'Не удалось загрузить данные.',
    retry ? h('button', { class: 'btn btn-secondary', type: 'button', onclick: retry }, 'Повторить') : null));
}

// Telegram-like top bar: [< Назад] centered title [right]
export function pageHead(title, { right, back = true, parent = '/today' } = {}) {
  return h('header', { class: 'page-head' },
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

export function numInput(props = {}) {
  return h('input', { type: 'number', inputmode: 'decimal', step: 'any', ...props });
}

export function numVal(input) {
  const v = parseFloat(String(input.value).replace(',', '.'));
  return isNaN(v) ? null : v;
}

// <img> that supports Authorization-protected photo URLs
export function photoImg(url, cls = '', alt = '') {
  const img = h('img', { class: cls, alt, loading: 'lazy', decoding: 'async' });
  api.imageUrl(url).then((u) => { img.src = u; }).catch(() => { img.classList.add('broken'); });
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
