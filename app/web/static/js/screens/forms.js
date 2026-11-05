// Simple add forms: weight, sleep, activity (вода и напитки живут в drinks.js)
import { api } from '../api.js';
import {
  h, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, segmented, fmtN, todayISO,
} from '../ui.js';

export default async function render(root, ctx) {
  const kind = ctx.params.kind;
  const builders = { weight, sleep, activity };
  const view = h('div', { class: 'screen' });
  root.append(view);
  const { title, body, submit } = builders[kind]();
  let busy = false;
  view.append(pageHead(title, { parent: '/today' }), h('form', { class: 'stack', onsubmit: (e) => { e.preventDefault(); go(); } }, body, h('button', { type: 'submit', hidden: true })));

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

function weight() {
  const w = numInput({ min: 25, max: 400, step: 0.1, placeholder: '82,4', 'aria-label': 'Вес в килограммах' });
  api.me().then((m) => { if (m.weight && m.weight.latest && !w.value) w.placeholder = fmtN(m.weight.latest); }).catch(() => {});
  return {
    title: 'Вес',
    body: [group([frow('Вес, кг', w)], 'Лучше взвешиваться утром, натощак.')],
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
  const hh = numInput({ min: 0, max: 24, step: 1, placeholder: '7', inputmode: 'numeric', 'aria-label': 'Часы' });
  const mm = numInput({ min: 0, max: 59, step: 5, placeholder: '30', inputmode: 'numeric', 'aria-label': 'Минуты' });
  const start = h('input', { type: 'time', value: '23:30', 'aria-label': 'Лёг' });
  const end = h('input', { type: 'time', value: '07:10', 'aria-label': 'Встал' });
  const score = numInput({ min: 0, max: 100, step: 1, placeholder: 'необязательно', inputmode: 'numeric', 'aria-label': 'Оценка сна' });
  const durBox = group([frow('Часы', hh), frow('Минуты', mm)]);
  const rangeBox = group([frow('Лёг', start), frow('Встал', end)]);
  rangeBox.hidden = true;
  return {
    title: 'Сон',
    body: [
      segmented([['dur', 'Длительность'], ['range', 'Лёг и встал']], mode, (v) => { mode = v; durBox.hidden = v !== 'dur'; rangeBox.hidden = v !== 'range'; }, 'Способ ввода'),
      durBox, rangeBox, group([frow('Оценка, 0–100', score)], 'Из часов или приложения, если есть.'),
    ],
    async submit() {
      const payload = { date: todayISO() };
      if (mode === 'dur') {
        const total = (numVal(hh) || 0) * 60 + (numVal(mm) || 0);
        if (total < 10 || total > 1440) return invalid('Укажите длительность сна');
        payload.duration_min = Math.round(total);
      } else {
        if (!start.value || !end.value) return invalid('Укажите время');
        payload.start = start.value;
        payload.end = end.value;
      }
      const sc = numVal(score);
      if (sc !== null) { if (sc < 0 || sc > 100) return invalid('Оценка от 0 до 100'); payload.score = Math.round(sc); }
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
      group([frow('Шаги', steps), frow('Активные ккал', kcal), frow('Активные минуты', mins)]),
      group([frow('Тренировка', workout)]),
    ],
    async submit() {
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
