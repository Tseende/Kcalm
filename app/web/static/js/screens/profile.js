// Профиль и цели: данные, из которых бот считает норму, и сама норма (расчётная или ручная).
import { api } from '../api.js';
import {
  h, icon, toast, haptic, mainButton, pageHead, frow, group, numInput, numVal, segmented, sheet,
  fmtN, fmtKcal, errorState, skeletonCards, dataChanged, plural, macroChips,
} from '../ui.js';

const ACTIVITY = [
  [1, 'Сидячая', 'Офис, мало движения'],
  [2, 'Лёгкая', 'Прогулки, 1–3 тренировки в неделю'],
  [3, 'Средняя', '3–5 тренировок или активная работа'],
  [4, 'Высокая', 'Ежедневные тренировки, физический труд'],
];
const RATES = [[0, 'Держать'], [0.25, '0,25'], [0.5, '0,5'], [0.7, '0,7']];

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonCards(3, 80));
  view.append(pageHead('Профиль и цели', { parent: '/menu' }), box);
  root.append(view);

  let d;
  try { d = await api.get('/profile'); } catch (e) { if (ctx.alive()) errorState(box, e, () => { root.replaceChildren(); render(root, ctx); }); return; }
  if (!ctx.alive()) return;

  let init = { ...d.profile };
  const cur = { ...d.profile };
  let targets = d.targets;
  const targetCard = h('div', { class: 'card target-card' });
  const nearest = (list, v) => list.reduce((a, b) => (Math.abs(b[0] - v) < Math.abs(a[0] - v) ? b : a))[0];
  cur.rate_kg_week = nearest(RATES, cur.rate_kg_week || 0);

  function paintTargets() {
    targetCard.replaceChildren(
      h('div', { class: 'row-between' },
        h('div', null, h('div', { class: 'muted small' }, 'Дневная норма'), h('div', { class: 'target-kcal' }, h('b', null, fmtKcal(targets.kcal)), ' ккал')),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: editTargets }, icon('edit', 18), 'Изменить')),
      macroChips({ p: targets.protein, f: targets.fat, c: targets.carbs }, { unit: ' г', cls: 'target-macs',
        extra: h('span', { class: 'mac' }, h('i'), `Вода ${fmtKcal(targets.water_ml)} мл`) }));
  }

  function editTargets() {
    const f = {
      kcal: numInput({ min: 800, max: 6000, step: 10, value: targets.kcal, inputmode: 'numeric', 'aria-label': 'Калории' }),
      protein: numInput({ min: 0, max: 500, step: 1, value: targets.protein, inputmode: 'numeric', 'aria-label': 'Белки' }),
      fat: numInput({ min: 0, max: 500, step: 1, value: targets.fat, inputmode: 'numeric', 'aria-label': 'Жиры' }),
      carbs: numInput({ min: 0, max: 900, step: 1, value: targets.carbs, inputmode: 'numeric', 'aria-label': 'Углеводы' }),
    };
    const done = (r, text) => { api._me = null; targets = r.targets; paintTargets(); dataChanged(); s.close(); haptic('success'); toast(text, 'ok'); };
    const save = async () => {
      try {
        const r = await api.put('/targets', { kcal: numVal(f.kcal), protein: numVal(f.protein), fat: numVal(f.fat), carbs: numVal(f.carbs) });
        done(r, r.result.raised ? `Калории подняты до безопасного минимума: ${r.targets.kcal}` : 'Норма сохранена');
      } catch (e) { toast(e.message, 'error'); }
    };
    const auto = async () => {
      try {
        const r = await api.patch('/profile', { recalc: true });
        const t = r.result.target;
        done(r, t ? `Пересчитал: ${t.old ?? '—'} → ${t.new} ккал` : 'Без изменений');
      } catch (e) { toast(e.message, 'error'); }
    };
    const s = sheet({ title: 'Норма на день', content: h('div', { class: 'stack' },
      group([frow('Калории, ккал', f.kcal), frow('Белки, г', f.protein), frow('Жиры, г', f.fat), frow('Углеводы, г', f.carbs)],
        'Калории ниже безопасного минимума поднимаются автоматически.'),
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: save }, 'Сохранить норму'),
      h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: auto }, 'Пересчитать по профилю')) });
  }

  // --- данные профиля
  const num = (key, props) => {
    const el = numInput({ value: cur[key] ?? '', ...props });
    el.addEventListener('input', () => { cur[key] = numVal(el); if (key === 'birth_year') ageNote(); changed(); });
    return el;
  };
  const year = num('birth_year', { min: 1920, max: new Date().getFullYear() - 10, step: 1, inputmode: 'numeric', placeholder: '1990', 'aria-label': 'Год рождения' });
  const height = num('height_cm', { min: 120, max: 230, step: 1, inputmode: 'numeric', placeholder: '175', 'aria-label': 'Рост' });
  const goal = num('goal_weight_kg', { min: 30, max: 250, step: 0.1, placeholder: '70', 'aria-label': 'Целевой вес' });
  const cutoff = h('input', { type: 'time', value: cur.eating_cutoff || '21:00', 'aria-label': 'Не есть после', oninput: () => { cur.eating_cutoff = cutoff.value; changed(); } });
  const tz = h('input', { type: 'text', value: cur.tz || '', placeholder: 'Europe/Moscow', autocomplete: 'off', autocapitalize: 'off', 'aria-label': 'Часовой пояс', oninput: () => { cur.tz = tz.value.trim(); changed(); } });
  const ageHint = h('div', { class: 'footnote' });
  const actHint = h('div', { class: 'footnote' });
  const act = segmented(ACTIVITY.map(([v, t]) => [v, t]), cur.activity, (v) => { cur.activity = Number(v); actNote(); changed(); }, 'Активность');
  const rate = segmented(RATES, cur.rate_kg_week, (v) => { cur.rate_kg_week = Number(v); rateNote(); changed(); }, 'Темп');
  const rateHint = h('div', { class: 'footnote' });
  function ageNote() {
    const y = cur.birth_year;
    const age = y ? new Date().getFullYear() - y : null;
    ageHint.textContent = age ? `Возраст: ${plural(age, 'год', 'года', 'лет')}. Нужен, чтобы точнее посчитать обмен веществ и норму калорий.` : 'Год рождения не указан: норма считается как для 35 лет. Укажите его для точности.';
  }
  function actNote() { actHint.textContent = (ACTIVITY.find((a) => a[0] === cur.activity) || ACTIVITY[1])[2]; }
  function rateNote() { rateHint.textContent = cur.rate_kg_week ? `Снижение до ${fmtN(cur.rate_kg_week)} кг в неделю. Быстрее 0,7 кг не предлагаем.` : 'Вес удерживается: дефицита калорий нет.'; }
  ageNote(); actNote(); rateNote();

  const KEYS = ['sex', 'birth_year', 'height_cm', 'goal_weight_kg', 'activity', 'rate_kg_week', 'eating_cutoff', 'tz'];
  const diff = () => KEYS.filter((k) => cur[k] !== init[k] && !(cur[k] == null && init[k] == null) && !(typeof cur[k] === 'number' && Number.isNaN(cur[k])));
  let busy = false;
  function changed() { mainButton.setDisabled(busy || diff().length === 0); }

  async function save() {
    const keys = diff();
    if (busy || !keys.length) return;
    const body = {};
    keys.forEach((k) => { if (cur[k] !== null && cur[k] !== '') body[k] = cur[k]; });
    busy = true; mainButton.setLoading(true);
    try {
      const r = await api.patch('/profile', body);
      init = { ...r.profile };
      targets = r.targets;
      paintTargets();
      api._me = null;
      dataChanged();
      haptic('success');
      const t = r.result && r.result.target;
      toast(t ? `Норма: ${t.old ?? '—'} → ${t.new} ккал` + (t.notes.length ? '. ' + t.notes[0] : '') : 'Сохранено', 'ok');
    } catch (e) { toast(e.message, 'error'); } finally { busy = false; mainButton.setLoading(false); changed(); }
  }

  box.replaceChildren(
    targetCard,
    h('div', { class: 'muted small target-note' }, 'Расход считается по формуле Института медицины США (IOM, 2005) с учётом пола, возраста, роста, веса и активности. Из него вычитается дефицит по выбранному темпу, у цели он плавно убывает. Ниже безопасного минимума норма не опускается.'),
    h('div', { class: 'section-label' }, 'О вас'),
    group([h('div', { class: 'frow static' }, h('span', { class: 'frow-label' }, 'Пол'), h('b', null, cur.sex === 'female' ? 'Женский' : 'Мужской')), frow('Год рождения', year), frow('Рост, см', height), h('div', { class: 'frow static' }, h('span', { class: 'frow-label' }, 'Вес сейчас'), h('b', null, cur.weight_kg ? `${fmtN(cur.weight_kg)} кг` : '—')), frow('Цель, кг', goal)]),
    ageHint,
    h('div', { class: 'section-label' }, 'Активность'), act, actHint,
    h('div', { class: 'section-label' }, 'Темп снижения, кг в неделю'), rate, rateHint,
    h('div', { class: 'section-label' }, 'Режим'),
    group([frow('Не есть после', cutoff), frow('Часовой пояс', tz)], 'Вес записывайте на экране «Вес»: норма пересчитывается по последнему значению.'));
  paintTargets();
  mainButton.show('Сохранить', save, { disabled: true });
}
