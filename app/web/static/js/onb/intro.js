// Три слайда «что я умею»: свайп (scroll-snap) и живые анимации, которые играют, пока слайд на экране.
import { h } from '../ui.js';
import { onbImg, REDUCED } from './controls.js';
import { t } from '../i18n.js';

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
    [t('onb.chip1'), t('onb.chip2'), t('onb.chip3')].map((x, i) => h('span', { class: 'onb-chip', style: { '--i': i } }, x)),
    h('span', { class: 'onb-kcal' }, '≈ ', num, ' ' + t('unit.kcal'))));
  return { el, ms: 4000, run: (later) => { num.textContent = REDUCED ? '640' : '0'; later(1300, () => countTo(num, 640, 1300)); } };
}

function slideChat() {
  const el = h('div', { class: 'onb-vis' }, h('div', { class: 'onb-chat' },
    h('div', { class: 'onb-msg user' }, t('onb.chat_user')),
    h('div', { class: 'onb-slot' },
      h('div', { class: 'onb-typing', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
      h('div', { class: 'onb-msg bot' }, t('onb.chat_bot'))),
    h('div', { class: 'onb-opts' }, [t('onb.opt1'), t('onb.opt2'), t('onb.opt3')].map((x, i) => h('span', { class: 'onb-opt', style: { '--i': i } }, x)))));
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
    h('span', { class: 'onb-badge-kg' }, t('onb.badge_kg')),
    svgEl,
    h('div', { class: 'onb-ringrow' }, ring, h('div', { class: 'onb-ringtxt' }, h('b', null, t('onb.ring_b')), h('span', null, t('onb.ring_s'))))));
  return { el, ms: 5500, run: () => {} };
}

/** Возвращает {el, index(), goTo(i), cleanup()}; onIndex(i) вызывается при смене слайда. */
export function intro({ name, onIndex }) {
  const defs = [
    { img: 'camera_plate', title: t('onb.s1_title'), sub: t('onb.s1_sub'), vis: slidePhoto() },
    { img: 'night_tea', title: t('onb.s2_title'), sub: t('onb.s2_sub'), vis: slideChat() },
    { img: 'chart_down', title: t('onb.s3_title'), sub: t('onb.s3_sub'), vis: slideChart() },
  ];
  const slides = defs.map((d, i) => h('section', { class: 'onb-slide', 'aria-roledescription': t('onb.slide'), 'aria-label': t('onb.slide_n', { i: i + 1, n: defs.length }) },
    h('div', { class: 'onb-head' },
      i === 0 && name ? h('div', { class: 'onb-hello' }, t('onb.hello', { name })) : null,
      onbImg(d.img, 72),
      h('h1', { class: 'onb-title' }, d.title),
      h('p', { class: 'onb-sub' }, d.sub)),
    d.vis.el));
  const dots = defs.map(() => h('span', { class: 'onb-dot' }));
  const car = h('div', { class: 'onb-car', role: 'group', 'aria-label': t('onb.car_aria') }, slides);
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
