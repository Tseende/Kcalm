// Профиль и цели: данные, из которых бот считает норму, и сама норма (расчётная или ручная).
import { api } from '../api.js';
import {
  h, icon, toast, haptic, mainButton, pageHead, frow, group, numInput, numVal, numCheck, checkList, sheet,
  fmtN, fmtKcal, errorState, skeletonBlocks, dataChanged, plural, macroChips,
} from '../ui.js';

// [значение, название, пояснение, короткая подпись для строки (по умолчанию название)]
const ACTIVITY = [
  [1, 'Сидячая', 'Офис, мало движения'],
  [2, 'Лёгкая', 'Прогулки, 1–3 тренировки в неделю'],
  [3, 'Средняя', '3–5 тренировок или активная работа'],
  [4, 'Высокая', 'Ежедневные тренировки, физический труд'],
];
const RATES = [
  [0, 'Держать вес', 'Без дефицита калорий', 'Держать'],
  [0.25, '0,25 кг в неделю', 'Спокойно', '0,25 кг/нед.'],
  [0.5, '0,5 кг в неделю', 'Оптимально', '0,5 кг/нед.'],
  [0.7, '0,7 кг в неделю', 'Быстрее', '0,7 кг/нед.'],
];

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonBlocks([112, 150, 230]));
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
  init.rate_kg_week = cur.rate_kg_week; // привязка к кнопкам темпа не считается правкой пользователя

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
    // Один запрос за раз: двойной тап не отправляет дважды, кнопки на время запроса заблокированы.
    let inflight = false;
    const guarded = (fn) => async () => {
      if (inflight) return;
      inflight = true;
      btns.forEach((b) => { b.disabled = true; });
      try { await fn(); } finally { inflight = false; btns.forEach((b) => { b.disabled = false; }); }
    };
    const btns = [];
    const save = guarded(async () => {
      const bad = numCheck(f.kcal, 'Калории') || numCheck(f.protein, 'Белки') || numCheck(f.fat, 'Жиры') || numCheck(f.carbs, 'Углеводы');
      if (bad) { toast(bad, 'error'); return; }
      if (numVal(f.kcal) === null) { toast('Укажите калории', 'error'); f.kcal.focus(); return; }
      try {
        const r = await api.put('/targets', { kcal: numVal(f.kcal), protein: numVal(f.protein), fat: numVal(f.fat), carbs: numVal(f.carbs) });
        done(r, r.result.raised ? `Калории подняты до безопасного минимума: ${r.targets.kcal}` : 'Норма сохранена');
      } catch (e) { toast(e.message, 'error'); }
    });
    const auto = guarded(async () => {
      try {
        const r = await api.patch('/profile', { recalc: true });
        const t = r.result.target;
        done(r, t ? `Пересчитал: ${t.old ?? '—'} → ${t.new} ккал` : 'Без изменений');
      } catch (e) { toast(e.message, 'error'); }
    });
    const saveBtn = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: save }, 'Сохранить норму');
    const autoBtn = h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: auto }, 'Пересчитать по профилю');
    btns.push(saveBtn, autoBtn);
    const s = sheet({ title: 'Норма на день', content: h('div', { class: 'stack' },
      group([frow('Калории, ккал', f.kcal), frow('Белки, г', f.protein), frow('Жиры, г', f.fat), frow('Углеводы, г', f.carbs)],
        'Калории ниже безопасного минимума поднимаются автоматически.'),
      saveBtn, autoBtn) });
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
  // Строка со значением справа; по нажатию снизу выезжает шторка со списком вариантов (как «Оформление» в меню).
  function pickRow(label, options, get, set, title) {
    const text = () => { const o = options.find((x) => x[0] === get()); return o ? (o[3] || o[1]) : '—'; };
    const val = h('span', { class: 'frow-val' }, text());
    const open = () => {
      const s = sheet({ title, content: checkList(options, get(), (v) => { set(Number(v)); val.textContent = text(); s.close(); }, title) });
    };
    return h('button', { class: 'frow', type: 'button', 'aria-haspopup': 'dialog', onclick: open },
      h('span', { class: 'frow-label' }, label), val, icon('next', 18));
  }
  const act = pickRow('Активность', ACTIVITY, () => cur.activity, (v) => { cur.activity = v; changed(); }, 'Активность');
  const rate = pickRow('Темп снижения', RATES, () => cur.rate_kg_week, (v) => { cur.rate_kg_week = v; rateNote(); changed(); }, 'Темп снижения, кг в неделю');
  const rateHint = h('div', { class: 'footnote' });
  function ageNote() {
    const y = cur.birth_year;
    const age = y ? new Date().getFullYear() - y : null;
    ageHint.textContent = age ? `Возраст: ${plural(age, 'год', 'года', 'лет')}. Нужен, чтобы точнее посчитать обмен веществ и норму калорий.` : 'Год рождения не указан: норма считается как для 35 лет. Укажите его для точности.';
  }
  function rateNote() { rateHint.textContent = cur.rate_kg_week ? `Снижение до ${fmtN(cur.rate_kg_week)} кг в неделю. Быстрее 0,7 кг не предлагаем.` : 'Вес удерживается: дефицита калорий нет.'; }
  ageNote(); rateNote();

  const KEYS = ['sex', 'birth_year', 'height_cm', 'goal_weight_kg', 'activity', 'rate_kg_week', 'eating_cutoff', 'tz'];
  const diff = () => KEYS.filter((k) => cur[k] !== init[k] && !(cur[k] == null && init[k] == null) && !(typeof cur[k] === 'number' && Number.isNaN(cur[k])));
  let busy = false;
  // Кнопку трогаем только когда её состояние меняется: в Telegram каждое enable/disable пересчитывает окно.
  let lastOff = true;
  function changed() {
    const off = busy || diff().length === 0;
    if (off === lastOff) return;
    lastOff = off;
    mainButton.setDisabled(off);
  }

  async function save() {
    const keys = diff();
    if (busy || !keys.length) return;
    const bad = numCheck(year, 'Год рождения') || numCheck(height, 'Рост') || numCheck(goal, 'Цель');
    if (bad) { toast(bad, 'error'); return; }
    const body = {};
    keys.forEach((k) => { if (cur[k] !== null && cur[k] !== '') body[k] = cur[k]; });
    if (!Object.keys(body).length) { toast('Заполните изменённое поле или верните прежнее значение', 'error'); return; }
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
    group([h('div', { class: 'frow static' }, h('span', { class: 'frow-label' }, 'Пол'), h('span', { class: 'frow-val' }, cur.sex === 'female' ? 'Женский' : 'Мужской')), frow('Год рождения', year), frow('Рост, см', height), h('div', { class: 'frow static' }, h('span', { class: 'frow-label' }, 'Вес сейчас'), h('span', { class: 'frow-val' }, cur.weight_kg ? `${fmtN(cur.weight_kg)} кг` : '—')), frow('Цель, кг', goal)]),
    ageHint,
    h('div', { class: 'section-label' }, 'Активность и темп'),
    group([act, rate]), rateHint,
    h('div', { class: 'section-label' }, 'Режим'),
    group([frow('Не есть после', cutoff), frow('Часовой пояс', tz)], 'Вес записывайте на экране «Вес»: норма пересчитывается по последнему значению.'));
  paintTargets();
  mainButton.show('Сохранить', save, { disabled: true });
}
