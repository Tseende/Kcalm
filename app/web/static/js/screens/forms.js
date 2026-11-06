// Simple add forms: weight, sleep, activity (вода и напитки живут в drinks.js)
import { api } from '../api.js';
import {
  h, icon, tile, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, numSet, numCheck, segmented, fmtN, todayISO,
} from '../ui.js';

export default async function render(root, ctx) {
  const kind = ctx.params.kind;
  const builders = { weight, sleep, activity };
  const view = h('div', { class: 'screen' });
  root.append(view);
  const { title, body, submit } = builders[kind]();
  let busy = false;
  view.append(pageHead(title, { parent: '/today' }), h('form', { class: 'stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); go(); } }, body, h('button', { type: 'submit', hidden: true })));

  async function go() {
    if (busy) return;
    busy = true;
    mainButton.setLoading(true);
    try {
      const msg = await submit();
      if (msg === false) { busy = false; mainButton.setLoading(false); return; }
      haptic('success');
      toast(msg || 'Сохранено', 'ok');
      mainButton.hide();
      nav.replace('/today');
    } catch (e) {
      busy = false;
      mainButton.setLoading(false);
      toast(e.message, 'error');
    }
  }
  mainButton.show('Сохранить', go);
}

function invalid(msg) { toast(msg, 'error'); return false; }

// Крупное поле: значение по центру, подпись единицы рядом.
function bigField(input, unit) {
  return h('label', { class: 'bigfield' }, input, h('span', { class: 'bigfield-unit' }, unit));
}

function weight() {
  const w = numInput({ min: 25, max: 400, step: 0.1, placeholder: '82,4', 'aria-label': 'Вес в килограммах', class: 'bigfield-input' });
  let latest = null;
  const sub = h('div', { class: 'wform-sub', 'aria-live': 'polite' });
  const last = h('button', { class: 'chip wform-last', type: 'button', hidden: true, onclick: () => { if (latest) { numSet(w, latest); haptic('light'); } } });
  const bump = (d) => {
    const base = numVal(w) ?? latest ?? 70;
    numSet(w, Math.min(400, Math.max(25, Math.round((base + d) * 10) / 10)));
    haptic('light');
  };
  const step = (d, label, ic) => h('button', { class: 'step wform-step', type: 'button', 'aria-label': label, onclick: () => bump(d) }, icon(ic, 22));
  api.me().then((m) => {
    const lw = m.weight && m.weight.latest;
    if (!lw) return;
    latest = lw;
    if (!w.value) w.placeholder = fmtN(lw);
    const goal = m.user && m.user.goal_weight_kg;
    const parts = [];
    if (m.weight.trend) parts.push(`Тренд ${fmtN(m.weight.trend)} кг`);
    if (goal && lw > goal) parts.push(`до цели ${fmtN(Math.round((lw - goal) * 10) / 10)} кг`);
    sub.textContent = parts.join(' · ');
    last.replaceChildren(icon('scale', 16), `Как в прошлый раз: ${fmtN(lw)} кг`);
    last.hidden = false;
  }).catch(() => {});
  return {
    title: 'Вес',
    body: [
      h('section', { class: 'card wform' },
        h('div', { class: 'wform-cap' }, 'Вес сегодня'),
        h('div', { class: 'wform-row' }, step(-0.1, 'Минус 100 граммов', 'minus'), bigField(w, 'кг'), step(0.1, 'Плюс 100 граммов', 'plus')),
        sub, last),
      h('div', { class: 'footnote wform-note' }, 'Лучше взвешиваться утром, натощак: так цифры сравнимы день ко дню.'),
    ],
    async submit() {
      const v = numVal(w);
      if (!v || v < 25 || v > 400) return invalid('Введите вес от 25 до 400 кг');
      const r = await api.post('/weight', { weight_kg: Math.round(v * 10) / 10 });
      api._me = null;                                   // меню показывает вес из /me: сбрасываем кеш
      return `Вес записан. Тренд: ${fmtN(r.trend)} кг`;
    },
  };
}

function sleep() {
  let mode = 'dur';
  const hh = numInput({ min: 0, max: 24, step: 1, placeholder: '7', inputmode: 'numeric', 'aria-label': 'Часы', class: 'bigfield-input' });
  const mm = numInput({ min: 0, max: 59, step: 5, placeholder: '30', inputmode: 'numeric', 'aria-label': 'Минуты', class: 'bigfield-input' });
  const start = h('input', { type: 'time', value: '23:30', 'aria-label': 'Лёг', class: 'bigfield-input' });
  const end = h('input', { type: 'time', value: '07:10', 'aria-label': 'Встал', class: 'bigfield-input' });
  const score = numInput({ min: 0, max: 100, step: 1, placeholder: 'необязательно', inputmode: 'numeric', 'aria-label': 'Оценка сна' });
  const rangeSum = h('div', { class: 'wform-sub', 'aria-live': 'polite' });
  const spanMin = () => {
    if (!start.value || !end.value) return null;
    const [a, b] = start.value.split(':').map(Number);
    const [c, d] = end.value.split(':').map(Number);
    return (((c * 60 + d) - (a * 60 + b)) % 1440 + 1440) % 1440;
  };
  const paintRange = () => {
    const m = spanMin();
    rangeSum.textContent = m === null ? '' : `Спал ${Math.floor(m / 60)} ч ${m % 60} мин`;
  };
  start.addEventListener('input', paintRange);
  end.addEventListener('input', paintRange);
  paintRange();
  const durBox = h('section', { class: 'card wform' },
    h('div', { class: 'wform-cap' }, 'Сколько спали'),
    h('div', { class: 'wform-pair' }, bigField(hh, 'ч'), bigField(mm, 'мин')));
  const rangeBox = h('section', { class: 'card wform' },
    h('div', { class: 'wform-cap' }, 'Лёг и встал'),
    h('div', { class: 'wform-pair' },
      h('label', { class: 'bigfield time' }, h('span', { class: 'bigfield-cap' }, 'Лёг'), start),
      h('label', { class: 'bigfield time' }, h('span', { class: 'bigfield-cap' }, 'Встал'), end)),
    rangeSum);
  rangeBox.hidden = true;
  return {
    title: 'Сон',
    body: [
      segmented([['dur', 'Длительность'], ['range', 'Лёг и встал']], mode, (v) => { mode = v; durBox.hidden = v !== 'dur'; rangeBox.hidden = v !== 'range'; }, 'Способ ввода'),
      durBox, rangeBox,
      group([h('label', { class: 'frow' }, tile('moon', 'indigo', 30), h('span', { class: 'frow-label' }, 'Оценка, 0–100'), score)], 'Из часов или приложения, если есть.'),
    ],
    async submit() {
      const payload = { date: todayISO() };
      if (mode === 'dur') {
        const e = numCheck(hh, 'Часы') || numCheck(mm, 'Минуты');
        if (e) return invalid(e);
        const total = (numVal(hh) || 0) * 60 + (numVal(mm) || 0);
        if (total < 10 || total > 1440) return invalid('Укажите длительность сна: от 10 минут до 24 часов');
        payload.duration_min = Math.round(total);
      } else {
        if (!start.value || !end.value) return invalid('Укажите время');
        payload.start = start.value;
        payload.end = end.value;
      }
      const sc = numVal(score);
      if (sc !== null) { const e = numCheck(score, 'Оценка сна'); if (e) return invalid(e); payload.score = Math.round(sc); }
      await api.post('/sleep', payload);
      return 'Сон записан';
    },
  };
}

function activity() {
  const steps = numInput({ min: 0, max: 100000, step: 100, inputmode: 'numeric', placeholder: '8000', 'aria-label': 'Шаги' });
  const kcal = numInput({ min: 0, max: 5000, step: 10, inputmode: 'numeric', placeholder: '350', 'aria-label': 'Активные ккал' });
  const mins = numInput({ min: 0, max: 1440, step: 5, inputmode: 'numeric', placeholder: '45', 'aria-label': 'Минуты активности' });
  const workout = h('input', { type: 'text', placeholder: 'Бег, силовая…', maxLength: 200, 'aria-label': 'Тренировка' });
  return {
    title: 'Активность',
    body: [
      group([
        h('label', { class: 'frow' }, tile('walk', 'green', 30), h('span', { class: 'frow-label' }, 'Шаги'), steps),
        h('label', { class: 'frow' }, tile('flame', 'orange', 30), h('span', { class: 'frow-label' }, 'Активные ккал'), kcal),
        h('label', { class: 'frow' }, tile('clock', 'blue', 30), h('span', { class: 'frow-label' }, 'Активные минуты'), mins)]),
      group([h('label', { class: 'frow' }, tile('run', 'red', 30), h('span', { class: 'frow-label' }, 'Тренировка'), workout)], 'Название по желанию: бег, силовая, плавание.'),
    ],
    async submit() {
      const bad = numCheck(steps, 'Шаги') || numCheck(kcal, 'Активные ккал') || numCheck(mins, 'Активные минуты');
      if (bad) return invalid(bad);
      const payload = { date: todayISO() };
      const s = numVal(steps), k = numVal(kcal), m = numVal(mins);
      if (s !== null) payload.steps = Math.round(s);
      if (k !== null) payload.active_kcal = Math.round(k);
      if (m !== null) payload.active_minutes = Math.round(m);
      if (workout.value.trim()) payload.workout = workout.value.trim();
      if (Object.keys(payload).length === 1) return invalid('Заполните хотя бы одно поле');
      await api.post('/activity', payload);
      return 'Активность записана';
    },
  };
}
