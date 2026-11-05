// Экран расчёта и экран результата онбординга.
import { h, icon, haptic, fmtN, fmtKcal, longDate } from '../ui.js';
import { onbImg, REDUCED } from './controls.js';
import { countTo } from './intro.js';

const NS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs = {}) => {
  const el = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
};
const MIN_CALC_MS = 1800;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/** Экран «Считаю вашу норму». run() — запрос; экран держится не меньше 1,8 с. Возвращает {el, done: Promise}. */
export function calcView(run) {
  const rows = ['Обмен веществ по формуле IOM', 'Поправка на активность', 'Безопасный темп'];
  const checks = rows.map((t) => h('div', { class: 'onb-check' }, h('span', { class: 'ok' }, icon('check', 14)), t));
  const el = h('div', { class: 'onb-step onb-calc', 'aria-live': 'polite' },
    h('div', { class: 'onb-spinwrap' }, h('div', { class: 'onb-spinring' }), onbImg('scale', 64)),
    h('h1', { class: 'onb-title' }, 'Считаю вашу норму'),
    h('div', { class: 'onb-checks' }, checks));
  checks.forEach((c, i) => setTimeout(() => c.classList.add('on'), REDUCED ? 0 : 250 + i * 520));
  const done = Promise.all([run(), wait(REDUCED ? 0 : MIN_CALC_MS)]).then(([r]) => r);
  return { el, done };
}

function confetti() {
  const colors = ['#ff9500', '#34c759', '#007aff', '#ff2d55', '#ffcc00', '#af52de'];
  const wrap = h('div', { class: 'onb-conf-wrap', 'aria-hidden': 'true' });
  for (let i = 0; i < 24; i++) {
    const a = (Math.PI * 2 * i) / 24 + Math.random() * 0.4;
    const r = 110 + Math.random() * 120;
    const p = h('i', { class: 'onb-conf' });
    p.style.setProperty('--dx', Math.cos(a) * r + 'px');
    p.style.setProperty('--dy', Math.sin(a) * r * 0.8 - 40 + 'px');
    p.style.setProperty('--rot', (Math.random() * 720 - 360) + 'deg');
    p.style.setProperty('--c', colors[i % colors.length]);
    wrap.append(p);
  }
  setTimeout(() => wrap.remove(), 1500);
  return wrap;
}

function goalChart(a, data) {
  const lose = a.goal !== 'maintain' && data.goal_date && a.goal_weight_kg < a.weight_kg;
  const w = 300, hgt = 80;
  const s = svgEl('svg', { viewBox: `0 0 ${w} ${hgt}`, 'aria-hidden': 'true' });
  const y0 = lose ? 14 : 40, y1 = lose ? 62 : 40;
  const path = svgEl('path', { d: `M12 ${y0} C 110 ${y0 + (lose ? 4 : 0)}, 190 ${y1 - (lose ? 4 : 0)}, 288 ${y1}`, class: 'line', pathLength: '1' });
  s.append(path, svgEl('circle', { cx: 12, cy: y0, r: 6, class: 'pt' }), svgEl('circle', { cx: 288, cy: y1, r: 6, class: 'pt' }));
  const left = h('span', null, h('small', null, 'Сейчас'), h('b', null, fmtN(a.weight_kg) + ' кг'));
  const right = lose
    ? h('span', null, h('small', null, longDate(data.goal_date)), h('b', null, fmtN(a.goal_weight_kg) + ' кг'))
    : h('span', null, h('small', null, 'Цель'), h('b', null, 'Держим вес'));
  return { el: h('div', { class: 'onb-goalcard' }, s, h('div', { class: 'onb-goalrow' }, left, right)), path };
}

/**
 * Результат. a — ответы, data — ответ POST /onboarding, care — осторожный режим.
 * Возвращает {el, play()}: play() запускает конфетти, счётчик и заполнение полосок.
 */
export function resultView(a, data, { care }) {
  const t = data.targets || {};
  const num = h('b', null, REDUCED ? fmtKcal(t.kcal) : '0');
  const norm = care
    ? h('div', { class: 'onb-norm' }, h('div', { class: 'care' }, 'Норма рассчитана, цифры спрячу'))
    : h('div', { class: 'onb-norm' }, h('small', null, 'Ваша норма'), num, h('span', { class: 'unit' }, 'ккал в день'));
  const kcal = Math.max(1, t.kcal || 1);
  const macro = (label, g, kcalPerG, color) => {
    const bar = h('i');
    bar.style.setProperty('--col', color);
    bar.style.setProperty('--w', Math.min(1, (g * kcalPerG) / kcal).toFixed(2));
    return { bar, row: h('div', { class: 'onb-mac' }, h('span', null, label), h('div', { class: 'onb-mac-bar' }, bar), h('b', null, `${Math.round(g)} г`)) };
  };
  const m = [macro('Белки', t.protein, 4, 'var(--c-p)'), macro('Жиры', t.fat, 9, 'var(--c-f)'), macro('Углеводы', t.carbs, 4, 'var(--c-c)')];
  const macros = care ? null : h('div', { class: 'onb-macros' }, m.map((x) => x.row),
    t.water_ml ? h('div', { class: 'onb-water' }, icon('droplet', 18), `Вода: около ${t.water_ml.toLocaleString('ru-RU')} мл в день`) : null);
  const chart = care ? null : goalChart(a, data);
  const notes = (data.notes || []).map((n) => h('div', { class: 'onb-note' }, n));
  const el = h('div', { class: 'onb-step onb-res' },
    h('div', { class: 'onb-head' }, onbImg('party', 88), h('h1', { class: 'onb-title' }, 'Готово!')),
    norm, macros, chart ? chart.el : null, notes,
    h('p', { class: 'onb-legal' }, 'Это не медицинский сервис, оценки приблизительные. Если вы беременны, кормите грудью, есть диабет или расстройство пищевого поведения — обсудите похудение с врачом.'));
  const timers = [];
  function play() {
    haptic('success');
    if (REDUCED) { m.forEach((x) => x.bar.classList.add('on')); if (chart) chart.path.classList.add('on'); return; }
    el.append(confetti());
    if (!care) countTo(num, t.kcal, 900, (n) => fmtKcal(n));
    m.forEach((x, i) => timers.push(setTimeout(() => x.bar.classList.add('on'), 500 + i * 180)));
    if (chart) timers.push(setTimeout(() => chart.path.classList.add('on'), 900));
  }
  return { el, play, cleanup: () => timers.forEach(clearTimeout) };
}
