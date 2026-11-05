// Контролы онбординга без клавиатуры: картинка с эмодзи-заглушкой, карточки, барабан, линейки.
import { h, haptic } from '../ui.js';

export const REDUCED = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

// Эмодзи-заглушки на случай, если img/onb/<key>.webp ещё не загружен.
const FALLBACK = {
  camera_plate: '📸', night_tea: '🌙', chart_down: '📉', scale: '⚖️', heart_check: '💚', salad: '🥗',
  male: '♂️', female: '♀️', sofa: '🛋️', sneaker: '👟', bike: '🚴', dumbbell: '🏋️', turtle: '🐢',
  target: '🎯', rabbit: '🐇', fridge_lock: '🔒', alarm: '⏰', party: '🎉',
};

/** Картинка img/onb/<key>.webp; при ошибке загрузки показывается эмодзи. */
export function onbImg(key, size = 64) {
  const box = h('span', { class: 'onb-img', 'aria-hidden': 'true', style: { width: size + 'px', height: size + 'px', fontSize: Math.round(size * 0.7) + 'px' } });
  const img = h('img', { src: `img/onb/${key}.webp`, alt: '', width: size, height: size, decoding: 'async' });
  img.addEventListener('error', () => { img.remove(); box.textContent = FALLBACK[key] || '✨'; box.classList.add('fallback'); });
  box.append(img);
  return box;
}

/** Карточки выбора: items [{value, title, sub, img, badge, extra}]. onPick(value). */
export function cards(items, value, onPick, { cols = 1 } = {}) {
  const root = h('div', { class: 'onb-cards cols-' + cols, role: 'radiogroup' });
  const els = items.map((it) => {
    const el = h('button', { type: 'button', role: 'radio', class: 'onb-card' + (it.value === value ? ' sel' : ''), 'aria-checked': String(it.value === value), onclick: () => {
      haptic('light');
      els.forEach((x, i) => { const on = items[i] === it; x.classList.toggle('sel', on); x.setAttribute('aria-checked', String(on)); });
      onPick(it.value);
    } },
    it.img ? onbImg(it.img, cols > 1 ? 72 : 52) : null,
    h('span', { class: 'onb-card-text' },
      h('span', { class: 'onb-card-title' }, it.title, it.badge ? h('span', { class: 'onb-badge' }, it.badge) : null),
      it.sub ? h('span', { class: 'onb-card-sub' }, it.sub) : null,
      it.extra ? h('span', { class: 'onb-card-extra' }, it.extra) : null));
    return el;
  });
  root.append(...els);
  return root;
}

/**
 * Барабан на scroll-snap. items [{v, label}], value — значение v. Возвращает {el, set(v), get()}.
 * Хаптика selectionChanged на каждом новом значении.
 */
export function drum(items, value, onChange, { itemH = 46, rows = 5, cls = '' } = {}) {
  const pad = ((rows - 1) / 2) * itemH;
  const list = h('div', { class: 'onb-drum-list', role: 'listbox', tabindex: 0, style: { paddingTop: pad + 'px', paddingBottom: pad + 'px' } });
  const els = items.map((it) => h('div', { class: 'onb-drum-i', role: 'option', style: { height: itemH + 'px', lineHeight: itemH + 'px' } }, it.label));
  list.append(...els);
  const el = h('div', { class: 'onb-drum ' + cls, style: { height: itemH * rows + 'px' } }, list, h('div', { class: 'onb-drum-band', style: { height: itemH + 'px' } }));
  let idx = Math.max(0, items.findIndex((i) => i.v === value));
  let raf = 0;
  function paint(pos) {
    els.forEach((e, i) => {
      const d = Math.abs(i - pos);
      if (d > 3.2) { if (e.dataset.far !== '1') { e.style.opacity = '0'; e.dataset.far = '1'; } return; }
      e.dataset.far = '0';
      e.style.opacity = String(Math.max(0.12, 1 - d * 0.3));
      e.style.transform = `scale(${Math.max(0.78, 1 - d * 0.07)})`;
    });
  }
  list.addEventListener('scroll', () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const pos = list.scrollTop / itemH;
      paint(pos);
      const i = Math.min(items.length - 1, Math.max(0, Math.round(pos)));
      if (i !== idx) { idx = i; haptic('selection'); onChange(items[i].v); }
    });
  }, { passive: true });
  function set(v, instant = true) {
    idx = Math.max(0, items.findIndex((i) => i.v === v));
    list.scrollTo({ top: idx * itemH, behavior: instant || REDUCED ? 'auto' : 'smooth' });
    paint(idx);
  }
  // scrollTop можно выставить только после вставки в документ
  const mount = () => { if (el.isConnected) set(items[idx].v); else requestAnimationFrame(mount); };
  requestAnimationFrame(mount);
  paint(idx);
  els.forEach((e, i) => e.addEventListener('click', () => { list.scrollTo({ top: i * itemH, behavior: REDUCED ? 'auto' : 'smooth' }); }));
  return { el, set, get: () => items[idx].v };
}

/**
 * Линейка на нативной прокрутке с доводкой до деления. orientation 'h' | 'v'.
 * Значение растёт при прокрутке вперёд (вправо / вниз). Возвращает {el, set(v), get()}.
 * major — каждые сколько единиц крупное деление с подписью.
 */
export function ruler({ min, max, step = 1, value, orientation = 'h', tick = 10, major = 5, labelEvery = major, decimals = 0, onChange }) {
  const vertical = orientation === 'v';
  const n = Math.round((max - min) / step);
  const perMajor = Math.round(major / step);
  const track = h('div', { class: 'onb-rl-track', style: { [vertical ? 'height' : 'width']: n * tick + 'px' } });
  track.style.setProperty('--tick', tick + 'px');
  track.style.setProperty('--major', tick * perMajor + 'px');
  track.style.setProperty('--half', (tick * perMajor) / 2 + 'px');
  // первое деление каждого слоя (мелкое, среднее, крупное) может не совпасть с началом шкалы: сдвигаем фон
  [step, major / 2, major].forEach((p, k) => {
    const first = Math.ceil(min / p - 1e-9) * p;
    track.style.setProperty('--o' + (k + 1), ((first - min) / step) * tick + 'px');
  });
  const fmt = (v) => v.toFixed(decimals);
  for (let v = Math.ceil(min / labelEvery) * labelEvery; v <= max + 1e-9; v += labelEvery) {
    const at = ((v - min) / step) * tick;
    track.append(h('span', { class: 'onb-rl-lbl', style: { [vertical ? 'top' : 'left']: at + 'px' } }, fmt(v)));
  }
  const scroller = h('div', { class: 'onb-rl-scroll ' + (vertical ? 'v' : 'h'), tabindex: 0, role: 'slider', 'aria-valuemin': min, 'aria-valuemax': max, 'aria-valuenow': value }, track);
  const el = h('div', { class: 'onb-ruler ' + (vertical ? 'v' : 'h') }, scroller, h('div', { class: 'onb-rl-mark' }));
  const clamp = (i) => Math.min(n, Math.max(0, i));
  const valueAt = (i) => Math.round((min + i * step) * 1e6) / 1e6;
  let idx = clamp(Math.round((value - min) / step));
  let timer = 0;
  let touching = false;
  const pos = () => (vertical ? scroller.scrollTop : scroller.scrollLeft);
  const goto = (i, smooth) => scroller.scrollTo({ [vertical ? 'top' : 'left']: i * tick, behavior: smooth && !REDUCED ? 'smooth' : 'auto' });
  scroller.addEventListener('scroll', () => {
    const i = clamp(Math.round(pos() / tick));
    if (i !== idx) {
      idx = i;
      haptic('selection');
      scroller.setAttribute('aria-valuenow', valueAt(i));
      onChange(valueAt(i));
    }
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!touching && Math.abs(pos() - idx * tick) > 0.5) goto(idx, true);
    }, 110);
  }, { passive: true });
  scroller.addEventListener('touchstart', () => { touching = true; }, { passive: true });
  const release = () => { touching = false; clearTimeout(timer); timer = setTimeout(() => { if (Math.abs(pos() - idx * tick) > 0.5) goto(idx, true); }, 160); };
  scroller.addEventListener('touchend', release, { passive: true });
  scroller.addEventListener('touchcancel', release, { passive: true });
  scroller.addEventListener('keydown', (e) => {
    const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (d) { e.preventDefault(); goto(clamp(idx + d * (e.shiftKey ? 10 : 1)), true); }
  });
  function set(v) { idx = clamp(Math.round((v - min) / step)); goto(idx, false); }
  const mount = () => { if (el.isConnected) goto(idx, false); else requestAnimationFrame(mount); };
  requestAnimationFrame(mount);
  return { el, set, get: () => valueAt(idx) };
}
