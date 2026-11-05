// Три слайда «что я умею»: свайп (scroll-snap) и живые анимации, которые играют, пока слайд на экране.
import { h } from '../ui.js';
import { onbImg, REDUCED } from './controls.js';

const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}) => {
  const el = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
};

/** Считает число от 0 до to за ms; при reduced-motion сразу итог. Возвращает отмену. */
export function countTo(el, to, ms, fmt = (n) => String(Math.round(n))) {
  if (REDUCED) { el.textContent = fmt(to); return () => {}; }
  let raf = 0;
  const t0 = performance.now();
  const tick = (t) => {
    const k = Math.min(1, (t - t0) / ms);
    el.textContent = fmt(to * (1 - (1 - k) ** 3));
    if (k < 1) raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(raf);
}

function slidePhoto() {
  const num = h('b', null, '0');
  const el = h('div', { class: 'onb-vis' }, h('div', { class: 'onb-pwrap' },
    h('div', { class: 'onb-phone' },
      h('div', { class: 'onb-plate' }, onbImg('camera_plate', 84)),
      h('div', { class: 'onb-corners' }),
      h('div', { class: 'onb-scan' })),
    ['Гречка 180 г', 'Котлета 2 шт', 'Салат'].map((t, i) => h('span', { class: 'onb-chip', style: { '--i': i } }, t)),
    h('span', { class: 'onb-kcal' }, '≈ ', num, ' ккал')));
  return { el, ms: 4000, run: (later) => { num.textContent = REDUCED ? '640' : '0'; later(1300, () => countTo(num, 640, 1300)); } };
}

function slideChat() {
  const el = h('div', { class: 'onb-vis' }, h('div', { class: 'onb-chat' },
    h('div', { class: 'onb-msg user' }, 'Опять тянет на сладкое 😩'),
    h('div', { class: 'onb-slot' },
      h('div', { class: 'onb-typing', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
      h('div', { class: 'onb-msg bot' }, 'Давай разберёмся: это голод или усталость? Ты сегодня съела 1450 из 1900 ккал — можно спокойно перекусить')),
    h('div', { class: 'onb-opts' }, ['Голод', 'Скучно', 'Устала'].map((t, i) => h('span', { class: 'onb-opt', style: { '--i': i } }, t)))));
  return { el, ms: 7500, run: () => {} };
}

function slideChart() {
  const svgEl = svg('svg', { viewBox: '0 0 300 110', 'aria-hidden': 'true' });
  const d = 'M8 18 C 50 20, 70 48, 110 52 S 170 60, 200 78 S 262 92, 288 90';
  svgEl.append(
    svg('path', { d: 'M8 100 H292', stroke: 'currentColor', 'stroke-opacity': '.12', 'stroke-width': '2', fill: 'none' }),
    svg('path', { d, class: 'onb-line', pathLength: '1' }),
    svg('circle', { cx: '288', cy: '90', r: '7', class: 'onb-dotend' }));
  const ring = svg('svg', { viewBox: '0 0 36 36', class: 'onb-ring', 'aria-hidden': 'true' });
  ring.append(svg('circle', { cx: '18', cy: '18', r: '15.915', class: 'bg' }),
    svg('circle', { cx: '18', cy: '18', r: '15.915', class: 'fg', pathLength: '100' }));
  const el = h('div', { class: 'onb-vis' }, h('div', { class: 'onb-chartcard' },
    h('span', { class: 'onb-badge-kg' }, '−3,2 кг'),
    svgEl,
    h('div', { class: 'onb-ringrow' }, ring, h('div', { class: 'onb-ringtxt' }, h('b', null, '1 450 ккал'), h('span', null, 'из 1 900 на сегодня')))));
  return { el, ms: 5500, run: () => {} };
}

/** Возвращает {el, index(), goTo(i), cleanup()}; onIndex(i) вызывается при смене слайда. */
export function intro({ name, onIndex }) {
  const defs = [
    { img: 'camera_plate', title: 'Сфотографируйте тарелку — остальное сделаю я', sub: 'Калории и БЖУ по фото, тексту или голосовому. Без весов и поиска по базе.', vis: slidePhoto() },
    { img: 'night_tea', title: 'Поддержу, когда вечером тянет к холодильнику', sub: 'Напишите «хочу сладкого» — разберёмся вместе, без запретов и стыда.', vis: slideChat() },
    { img: 'chart_down', title: 'Видно, что всё работает', sub: 'Вес, сон, шаги и вода в одном месте. Вечером — итог дня, в воскресенье — разбор недели.', vis: slideChart() },
  ];
  const slides = defs.map((d, i) => h('section', { class: 'onb-slide', 'aria-roledescription': 'слайд', 'aria-label': `${i + 1} из ${defs.length}` },
    h('div', { class: 'onb-head' },
      i === 0 && name ? h('div', { class: 'onb-hello' }, `Привет, ${name}!`) : null,
      onbImg(d.img, 72),
      h('h1', { class: 'onb-title' }, d.title),
      h('p', { class: 'onb-sub' }, d.sub)),
    d.vis.el));
  const dots = defs.map(() => h('span', { class: 'onb-dot' }));
  const car = h('div', { class: 'onb-car', role: 'group', 'aria-label': 'Что умеет бот' }, slides);
  const el = h('div', { class: 'onb-intro', style: { display: 'flex', flexDirection: 'column', flex: '1', minHeight: '0' } }, car, h('div', { class: 'onb-dots' }, dots));

  let cur = -1;
  let timers = [];
  let stopLoop = null;
  const later = (ms, fn) => { timers.push(setTimeout(fn, ms)); };
  const stop = () => {
    timers.forEach(clearTimeout); timers = [];
    if (stopLoop) stopLoop();
    stopLoop = null;
    slides.forEach((s) => s.classList.remove('on'));
  };
  function play(i) {
    stop();
    const s = slides[i], v = defs[i].vis;
    if (REDUCED) { s.classList.add('on'); v.run(later); return; }
    let dead = false;
    let t = 0;
    const cycle = () => {
      if (dead) return;
      s.classList.remove('on');
      void s.offsetWidth;                                // перезапуск CSS-анимаций
      s.classList.add('on');
      v.run(later);
      t = setTimeout(cycle, v.ms);
    };
    cycle();
    stopLoop = () => { dead = true; clearTimeout(t); };
  }
  function setIndex(i) {
    if (i === cur) return;
    cur = i;
    dots.forEach((d, k) => d.classList.toggle('on', k === i));
    play(i);
    onIndex(i);
  }
  let raf = 0;
  car.addEventListener('scroll', () => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; setIndex(Math.min(defs.length - 1, Math.max(0, Math.round(car.scrollLeft / car.clientWidth)))); });
  }, { passive: true });
  const start = () => { if (el.isConnected) setIndex(0); else requestAnimationFrame(start); };
  requestAnimationFrame(start);
  return {
    el, count: defs.length,
    index: () => cur,
    goTo: (i) => car.scrollTo({ left: i * car.clientWidth, behavior: REDUCED ? 'auto' : 'smooth' }),
    cleanup: () => { stop(); cancelAnimationFrame(raf); },
  };
}
