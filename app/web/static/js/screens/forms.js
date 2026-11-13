// Simple add forms: weight, sleep, activity (вода и напитки живут в drinks.js)
import { api } from '../api.js';
import { t } from '../i18n.js';
import {
  h, icon, tile, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, numSet, numCheck, segmented, fmtN, todayISO, addDays, longDate,
} from '../ui.js';

// ?date=ГГГГ-ММ-ДД: день, за который пишем (не в будущем, не старше 30 дней), иначе сегодня.
function pickDate(q) {
  const today = todayISO();
  return /^\d{4}-\d{2}-\d{2}$/.test(q || '') && q <= today && q >= addDays(today, -30) ? q : today;
}

export default async function render(root, ctx) {
  const kind = ctx.params.kind;
  const builders = { weight, sleep, activity };
  const date = pickDate(ctx.query && ctx.query.date);
  const isToday = date === todayISO();
  const when = isToday ? '' : date === addDays(todayISO(), -1) ? t('forms.yday') : longDate(date);
  const view = h('div', { class: 'screen' });
  root.append(view);
  const { title: baseTitle, body, submit, fill } = builders[kind](date, when);
  const title = when ? `${baseTitle} · ${when}` : baseTitle;
  if (fill && !isToday) api.get('/day', { date }).then((day) => { if (ctx.alive()) fill(day); }).catch(() => {});
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
      toast(msg || t('common.saved'), 'ok');
      mainButton.hide();
      nav.replace('/today?date=' + date);
    } catch (e) {
      busy = false;
      mainButton.setLoading(false);
      toast(e.message, 'error');
    }
  }
  mainButton.show(t('common.save'), go);
}

function invalid(msg) { toast(msg, 'error'); return false; }

// Крупное поле: значение по центру, подпись единицы рядом.
function bigField(input, unit) {
  return h('label', { class: 'bigfield' }, input, h('span', { class: 'bigfield-unit' }, unit));
}

function weight(date, when) {
  const w = numInput({ min: 25, max: 400, step: 0.1, placeholder: t('forms.ph_weight'), 'aria-label': t('forms.a_weight'), class: 'bigfield-input' });
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
    if (m.weight.trend) parts.push(t('forms.trend', { v: fmtN(m.weight.trend) }));
    if (goal && lw > goal) parts.push(t('stats.to_goal', { v: fmtN(Math.round((lw - goal) * 10) / 10) }));
    sub.textContent = parts.join(' · ');
    last.replaceChildren(icon('scale', 16), t('forms.same', { v: fmtN(lw) }));
    last.hidden = false;
  }).catch(() => {});
  return {
    title: t('add.weight'),
    fill(day) { if (day.weight != null && !w.value) numSet(w, day.weight); },
    body: [
      h('section', { class: 'card wform' },
        h('div', { class: 'wform-cap' }, when ? t('forms.w_when', { w: when }) : t('forms.w_today')),
        h('div', { class: 'wform-row' }, step(-0.1, t('forms.minus'), 'minus'), bigField(w, t('unit.kg')), step(0.1, t('forms.plus'), 'plus')),
        sub, last),
      h('div', { class: 'footnote wform-note' }, t('forms.w_note')),
    ],
    async submit() {
      const v = numVal(w);
      if (!v || v < 25 || v > 400) return invalid(t('forms.v_weight'));
      const r = await api.post('/weight', { weight_kg: Math.round(v * 10) / 10, ...(when ? { date } : {}) });
      api._me = null;                                   // меню показывает вес из /me: сбрасываем кеш
      return t('forms.w_saved', { v: fmtN(r.trend) });
    },
  };
}

function sleep(date) {
  let mode = 'dur';
  const hh = numInput({ min: 0, max: 24, step: 1, placeholder: '7', inputmode: 'numeric', 'aria-label': t('forms.hours'), class: 'bigfield-input' });
  const mm = numInput({ min: 0, max: 59, step: 5, placeholder: '30', inputmode: 'numeric', 'aria-label': t('forms.minutes'), class: 'bigfield-input' });
  const start = h('input', { type: 'time', value: '23:30', 'aria-label': t('forms.bed'), class: 'bigfield-input' });
  const end = h('input', { type: 'time', value: '07:10', 'aria-label': t('forms.wake'), class: 'bigfield-input' });
  const score = numInput({ min: 0, max: 100, step: 1, placeholder: t('forms.optional'), inputmode: 'numeric', 'aria-label': t('an.sleep_score') });
  const rangeSum = h('div', { class: 'wform-sub', 'aria-live': 'polite' });
  const spanMin = () => {
    if (!start.value || !end.value) return null;
    const [a, b] = start.value.split(':').map(Number);
    const [c, d] = end.value.split(':').map(Number);
    return (((c * 60 + d) - (a * 60 + b)) % 1440 + 1440) % 1440;
  };
  const paintRange = () => {
    const m = spanMin();
    rangeSum.textContent = m === null ? '' : t('forms.slept', { h: Math.floor(m / 60), m: m % 60 });
  };
  start.addEventListener('input', paintRange);
  end.addEventListener('input', paintRange);
  paintRange();
  const durBox = h('section', { class: 'card wform' },
    h('div', { class: 'wform-cap' }, t('forms.how_long')),
    h('div', { class: 'wform-pair' }, bigField(hh, t('unit.h')), bigField(mm, t('unit.min'))));
  const rangeBox = h('section', { class: 'card wform' },
    h('div', { class: 'wform-cap' }, t('forms.bed_wake')),
    h('div', { class: 'wform-pair' },
      h('label', { class: 'bigfield time' }, h('span', { class: 'bigfield-cap' }, t('forms.bed')), start),
      h('label', { class: 'bigfield time' }, h('span', { class: 'bigfield-cap' }, t('forms.wake')), end)),
    rangeSum);
  rangeBox.hidden = true;
  return {
    title: t('add.sleep'),
    fill(day) {
      const m = day.sleep && day.sleep.duration_min;
      if (m && !hh.value && !mm.value) { numSet(hh, Math.floor(m / 60)); numSet(mm, m % 60); }
      if (day.sleep && day.sleep.score != null && !score.value) numSet(score, day.sleep.score);
    },
    body: [
      segmented([['dur', t('forms.duration')], ['range', t('forms.bed_wake')]], mode, (v) => { mode = v; durBox.hidden = v !== 'dur'; rangeBox.hidden = v !== 'range'; }, t('forms.input_mode')),
      durBox, rangeBox,
      group([h('label', { class: 'frow' }, tile('moon', 'indigo', 30), h('span', { class: 'frow-label' }, t('forms.score')), score)], t('forms.score_note')),
    ],
    async submit() {
      const payload = { date };
      if (mode === 'dur') {
        const e = numCheck(hh, t('forms.hours')) || numCheck(mm, t('forms.minutes'));
        if (e) return invalid(e);
        const total = (numVal(hh) || 0) * 60 + (numVal(mm) || 0);
        if (total < 10 || total > 1440) return invalid(t('forms.v_sleep'));
        payload.duration_min = Math.round(total);
      } else {
        if (!start.value || !end.value) return invalid(t('forms.v_time'));
        payload.start = start.value;
        payload.end = end.value;
      }
      const sc = numVal(score);
      if (sc !== null) { const e = numCheck(score, t('an.sleep_score')); if (e) return invalid(e); payload.score = Math.round(sc); }
      await api.post('/sleep', payload);
      return t('forms.sleep_saved');
    },
  };
}

function activity(date) {
  // Только шаги и тренировка: активные ккал и минуты ни на что не влияли (API их по-прежнему принимает со скриншотов).
  const steps = numInput({ min: 0, max: 100000, step: 100, inputmode: 'numeric', placeholder: '8000', 'aria-label': t('today.steps') });
  const workout = h('input', { type: 'text', placeholder: t('forms.ph_workout'), maxLength: 200, 'aria-label': t('forms.workout') });
  return {
    title: t('add.activity'),
    fill(day) {
      const a = day.activity;
      if (a && a.steps != null && !steps.value) numSet(steps, a.steps);
    },
    body: [
      group([h('label', { class: 'frow' }, tile('walk', 'green', 30), h('span', { class: 'frow-label' }, t('today.steps')), steps)], t('forms.steps_note')),
      group([h('label', { class: 'frow' }, tile('run', 'red', 30), h('span', { class: 'frow-label' }, t('forms.workout')), workout)], t('forms.workout_note')),
    ],
    async submit() {
      const bad = numCheck(steps, t('today.steps'));
      if (bad) return invalid(bad);
      const payload = { date };
      const s = numVal(steps);
      if (s !== null) payload.steps = Math.round(s);
      if (workout.value.trim()) payload.workout = workout.value.trim();
      if (Object.keys(payload).length === 1) return invalid(t('forms.v_any'));
      await api.post('/activity', payload);
      return t('forms.act_saved');
    },
  };
}
