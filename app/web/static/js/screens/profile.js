// Профиль и цели: данные, из которых бот считает норму, и сама норма (расчётная или ручная).
import { api } from '../api.js';
import { t, tn } from '../i18n.js';
import {
  h, icon, toast, haptic, mainButton, pageHead, frow, group, numInput, numVal, numCheck, checkList, sheet,
  fmtN, fmtKcal, errorState, skeletonBlocks, dataChanged, macroChips,
} from '../ui.js';

// [значение, название, пояснение, короткая подпись для строки (по умолчанию название)]
const ACTIVITY = [
  [1, t('prof.act1'), t('prof.act1_d')],
  [2, t('prof.act2'), t('prof.act2_d')],
  [3, t('prof.act3'), t('prof.act3_d')],
  [4, t('prof.act4'), t('prof.act4_d')],
];
const RATES = [
  [0, t('prof.r0'), t('prof.r0_d'), t('prof.r0_s')],
  [0.25, t('prof.r1'), t('prof.r1_d'), t('prof.r1_s')],
  [0.5, t('prof.r2'), t('prof.r2_d'), t('prof.r2_s')],
  [0.7, t('prof.r3'), t('prof.r3_d'), t('prof.r3_s')],
];

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonBlocks([112, 150, 230]));
  view.append(pageHead(t('prof.title'), { parent: '/menu' }), box);
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
        h('div', null, h('div', { class: 'muted small' }, t('prof.daily')), h('div', { class: 'target-kcal' }, h('b', null, fmtKcal(targets.kcal)), ' ' + t('unit.kcal'))),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: editTargets }, icon('edit', 18), t('prof.edit'))),
      macroChips({ p: targets.protein, f: targets.fat, c: targets.carbs }, { unit: ' ' + t('unit.g'), cls: 'target-macs',
        extra: h('span', { class: 'mac' }, h('i'), t('prof.water', { v: fmtKcal(targets.water_ml) })) }));
  }

  function editTargets() {
    const f = {
      kcal: numInput({ min: 800, max: 6000, step: 10, value: targets.kcal, inputmode: 'numeric', 'aria-label': t('prof.kcal') }),
      protein: numInput({ min: 0, max: 500, step: 1, value: targets.protein, inputmode: 'numeric', 'aria-label': t('macro.protein') }),
      fat: numInput({ min: 0, max: 500, step: 1, value: targets.fat, inputmode: 'numeric', 'aria-label': t('macro.fat') }),
      carbs: numInput({ min: 0, max: 900, step: 1, value: targets.carbs, inputmode: 'numeric', 'aria-label': t('macro.carbs') }),
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
      const bad = numCheck(f.kcal, t('prof.kcal')) || numCheck(f.protein, t('macro.protein')) || numCheck(f.fat, t('macro.fat')) || numCheck(f.carbs, t('macro.carbs'));
      if (bad) { toast(bad, 'error'); return; }
      if (numVal(f.kcal) === null) { toast(t('prof.need_kcal'), 'error'); f.kcal.focus(); return; }
      try {
        const r = await api.put('/targets', { kcal: numVal(f.kcal), protein: numVal(f.protein), fat: numVal(f.fat), carbs: numVal(f.carbs) });
        done(r, r.result.raised ? t('prof.raised', { v: r.targets.kcal }) : t('prof.saved_t'));
      } catch (e) { toast(e.message, 'error'); }
    });
    const auto = guarded(async () => {
      try {
        const r = await api.patch('/profile', { recalc: true });
        const tg = r.result.target;
        done(r, tg ? t('prof.recalc', { a: tg.old ?? '—', b: tg.new }) : t('prof.nochange'));
      } catch (e) { toast(e.message, 'error'); }
    });
    const saveBtn = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: save }, t('prof.save_t'));
    const autoBtn = h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: auto }, t('prof.auto'));
    btns.push(saveBtn, autoBtn);
    const s = sheet({ title: t('prof.sheet'), content: h('div', { class: 'stack' },
      group([frow(t('prof.f_kcal'), f.kcal), frow(t('prof.f_p'), f.protein), frow(t('prof.f_f'), f.fat), frow(t('prof.f_c'), f.carbs)],
        t('prof.min_note')),
      saveBtn, autoBtn) });
  }

  // --- данные профиля
  const num = (key, props) => {
    const el = numInput({ value: cur[key] ?? '', ...props });
    el.addEventListener('input', () => { cur[key] = numVal(el); if (key === 'birth_year') ageNote(); changed(); });
    return el;
  };
  const year = num('birth_year', { min: 1920, max: new Date().getFullYear() - 10, step: 1, inputmode: 'numeric', placeholder: '1990', 'aria-label': t('prof.year') });
  const height = num('height_cm', { min: 120, max: 230, step: 1, inputmode: 'numeric', placeholder: '175', 'aria-label': t('prof.height') });
  const goal = num('goal_weight_kg', { min: 30, max: 250, step: 0.1, placeholder: '70', 'aria-label': t('prof.goal_w') });
  const cutoff = h('input', { type: 'time', value: cur.eating_cutoff || '21:00', 'aria-label': t('prof.cutoff'), oninput: () => { cur.eating_cutoff = cutoff.value; changed(); } });
  const tz = h('input', { type: 'text', value: cur.tz || '', placeholder: 'Europe/Moscow', autocomplete: 'off', autocapitalize: 'off', 'aria-label': t('prof.tz'), oninput: () => { cur.tz = tz.value.trim(); changed(); } });
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
  const act = pickRow(t('prof.activity'), ACTIVITY, () => cur.activity, (v) => { cur.activity = v; changed(); }, t('prof.activity'));
  const rate = pickRow(t('prof.rate'), RATES, () => cur.rate_kg_week, (v) => { cur.rate_kg_week = v; rateNote(); changed(); }, t('prof.rate_sheet'));
  const rateHint = h('div', { class: 'footnote' });
  function ageNote() {
    const y = cur.birth_year;
    const age = y ? new Date().getFullYear() - y : null;
    ageHint.textContent = age ? t('prof.age_note', { a: tn('prof.age', age) }) : t('prof.age_none');
  }
  function rateNote() { rateHint.textContent = cur.rate_kg_week ? t('prof.rate_note', { v: fmtN(cur.rate_kg_week) }) : t('prof.rate_hold'); }
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
    const bad = numCheck(year, t('prof.year')) || numCheck(height, t('prof.height')) || numCheck(goal, t('prof.goal_short'));
    if (bad) { toast(bad, 'error'); return; }
    const body = {};
    keys.forEach((k) => { if (cur[k] !== null && cur[k] !== '') body[k] = cur[k]; });
    if (!Object.keys(body).length) { toast(t('prof.fill'), 'error'); return; }
    busy = true; mainButton.setLoading(true);
    try {
      const r = await api.patch('/profile', body);
      init = { ...r.profile };
      targets = r.targets;
      paintTargets();
      api._me = null;
      dataChanged();
      haptic('success');
      const tg = r.result && r.result.target;
      toast(tg ? t('prof.norm_chg', { a: tg.old ?? '—', b: tg.new }) + (tg.notes.length ? '. ' + tg.notes[0] : '') : t('common.saved'), 'ok');
    } catch (e) { toast(e.message, 'error'); } finally { busy = false; mainButton.setLoading(false); changed(); }
  }

  box.replaceChildren(
    targetCard,
    h('div', { class: 'muted small target-note' }, t('prof.formula')),
    h('div', { class: 'section-label' }, t('prof.about')),
    group([h('div', { class: 'frow static' }, h('span', { class: 'frow-label' }, t('prof.sex')), h('span', { class: 'frow-val' }, cur.sex === 'female' ? t('prof.female') : t('prof.male'))), frow(t('prof.year'), year), frow(t('prof.f_height'), height), h('div', { class: 'frow static' }, h('span', { class: 'frow-label' }, t('prof.weight_now')), h('span', { class: 'frow-val' }, cur.weight_kg ? `${fmtN(cur.weight_kg)} ${t('unit.kg')}` : '—')), frow(t('prof.f_goal'), goal)]),
    ageHint,
    h('div', { class: 'section-label' }, t('prof.act_rate')),
    group([act, rate]), rateHint,
    h('div', { class: 'section-label' }, t('prof.mode')),
    group([frow(t('prof.cutoff'), cutoff), frow(t('prof.tz'), tz)], t('prof.weight_hint')));
  paintTargets();
  mainButton.show(t('common.save'), save, { disabled: true });
}
