// Upload a screenshot (sleep / steps / scales) -> parsed metrics -> optional confirm
import { api } from '../api.js';
import { h, icon, toast, haptic, nav, pageHead, compressImage, busyOverlay, fmtN, fmtDur, shortDate, parseISO } from '../ui.js';

// Keys follow app/services/screens.py METRIC_RANGES (the backend's flat metrics).
const n1 = (v) => fmtN(v);
const METRICS = {
  steps: ['Шаги', n1],
  distance_km: ['Дистанция, км', n1],
  active_kcal: ['Активные ккал', n1],
  active_minutes: ['Активные минуты', n1],
  avg_hr: ['Средний пульс', n1],
  resting_hr: ['Пульс покоя', n1],
  sleep_duration_min: ['Сон', fmtDur],
  sleep_deep_min: ['Глубокий сон', fmtDur],
  sleep_light_min: ['Лёгкий сон', fmtDur],
  sleep_rem_min: ['REM', fmtDur],
  sleep_awake_min: ['Бодрствование', fmtDur],
  sleep_score: ['Оценка сна', n1],
  weight_kg: ['Вес, кг', n1],
  bmi: ['ИМТ', n1],
  body_fat_pct: ['Жир, %', n1],
  fat_mass_kg: ['Жировая масса, кг', n1],
  muscle_mass_kg: ['Мышцы, кг', n1],
  muscle_mass_pct: ['Мышцы, %', n1],
  skeletal_muscle_pct: ['Скелетные мышцы, %', n1],
  body_water_pct: ['Вода, %', n1],
  bone_mass_kg: ['Кости, кг', n1],
  protein_pct: ['Белок, %', n1],
  visceral_fat: ['Висцеральный жир', n1],
  bmr_kcal: ['Базовый обмен, ккал', n1],
  metabolic_age: ['Метаболический возраст', n1],
  body_tone: ['Тонус тела', n1],
  body_score: ['Оценка тела', n1],
};

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const out = h('div', { class: 'stack' });
  const input = h('input', { type: 'file', accept: 'image/*', hidden: true, onchange: onPick });
  view.append(pageHead('Скриншот', { parent: '/today' }), h('div', { class: 'stack' },
    h('div', { class: 'muted' }, 'Загрузите скриншот из Mi Fitness, Apple Health, Samsung Health, Garmin или весов: сон, шаги, вес.'),
    h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: () => input.click() }, icon('image', 20), 'Выбрать скриншот'),
    out, input));
  root.append(view);

  async function onPick() {
    const f = input.files[0];
    input.value = '';
    if (!f) return;
    let file;
    try { file = await compressImage(f, 2000, 0.9); } catch (e) { toast(e.message, 'error'); return; }
    const url = URL.createObjectURL(file);
    out.replaceChildren(h('img', { class: 'preview', src: url, alt: 'Выбранный скриншот' }));
    const fd = new FormData();
    fd.append('photo', file, file.name || 'screen.jpg');
    const stop = busyOverlay('Читаю скриншот…', 'Обычно 5–15 секунд');
    try {
      const r = await api.post('/screens', fd, { timeout: 90000 });
      stop();
      result(r, url);
    } catch (e) { stop(); toast(e.message, 'error'); }
  }

  function result(r, url) {
    const rows = Object.entries(r.metrics || {}).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => {
      const m = METRICS[k];
      return h('div', { class: 'row static' }, h('span', { class: 'grow' }, m ? m[0] : k), h('b', null, m ? m[1](v) : String(v)));
    });
    const reasons = Array.isArray(r.reasons) ? r.reasons.filter(Boolean) : [];
    const actions = h('div', { class: 'stack-sm' });
    const status = h('div', { class: 'card status' });
    out.replaceChildren(h('img', { class: 'preview', src: url, alt: 'Выбранный скриншот' }),
      h('div', { class: 'muted small' }, `Распознано за ${shortDate(r.date || new Date().toISOString().slice(0, 10))} · уверенность ${Math.round((r.confidence || 0) * 100)}%`),
      rows.length ? h('div', { class: 'list' }, rows) : h('div', { class: 'card muted' }, 'Не удалось найти данные на скриншоте.'),
      status, actions);

    if (r.applied) {
      haptic('success');
      status.replaceChildren(icon('check', 20), 'Данные сохранены');
      status.classList.add('ok');
      actions.append(h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: () => nav.replace('/today') }, 'Готово'));
    } else if (r.needs_confirm) {
      status.replaceChildren('Проверьте значения. Если всё верно, подтвердите: данные запишутся в дневник.',
        reasons.length ? h('ul', { class: 'notes' }, reasons.map((x) => h('li', null, x))) : null);
      const confirm = async (apply) => {
        try {
          await api.post('/screens/' + r.id + '/confirm', { apply });
          haptic('success');
          toast(apply ? 'Данные сохранены' : 'Отменено', 'ok');
          nav.replace('/today');
        } catch (e) { toast(e.message, 'error'); }
      };
      actions.append(
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: () => confirm(true) }, icon('check', 20), 'Подтвердить'),
        h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: () => confirm(false) }, 'Отмена'));
    } else {
      status.textContent = r.screen === 'unknown' ? 'Это не похоже на экран со сном, шагами или весом.' : 'Данные не применены.';
    }
  }
  void parseISO;
  void ctx;
}
