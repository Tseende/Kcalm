// Выбор продукта: поиск по базе, порция в граммах и ручной ввод. Общий для «Добавить» и экрана приёма.
// pickFood() возвращает Promise<entry|null>. entry — позиция для POST /meals/manual и /meals/{id}/items:
//   из базы:  { food_id, name, brand, grams, per100 }
//   вручную:  { name, grams, per100, per, raw, save }   (raw — значения как ввели, per — к чему они относятся)
import { api } from './api.js';
import { t } from './i18n.js';
import {
  h, icon, sheet, toast, haptic, fmtN, numInput, numVal, numCheck, frow, group, segmented, macroLine,
} from './ui.js';

export const G_MAX = 5000;
const r1 = (v) => Math.round(v * 10) / 10;

// Значения позиции на её граммы.
export function entryAbs(e) {
  if (e.per === 'portion' && e.raw) return { kcal: Math.round(e.raw.kcal), protein: r1(e.raw.protein), fat: r1(e.raw.fat), carbs: r1(e.raw.carbs) };
  const k = e.grams / 100;
  return { kcal: Math.round(e.per100.kcal * k), protein: r1(e.per100.protein * k), fat: r1(e.per100.fat * k), carbs: r1(e.per100.carbs * k) };
}

export function entryTotal(list) {
  return list.reduce((s, e) => { const a = entryAbs(e); return { kcal: s.kcal + a.kcal, protein: s.protein + a.protein, fat: s.fat + a.fat, carbs: s.carbs + a.carbs }; },
    { kcal: 0, protein: 0, fat: 0, carbs: 0 });
}

// Новые граммы у позиции: значения «на порцию» после этого перестают быть привязаны к порции.
export function entryWithGrams(e, grams) {
  const n = { ...e, grams };
  if (e.per === 'portion') { n.per = '100g'; delete n.raw; }
  return n;
}

export function toPayload(e) {
  if (e.food_id != null) return { food_id: e.food_id, grams: e.grams };
  const base = e.per === 'portion' && e.raw ? e.raw : e.per100;
  return {
    name: e.name, grams: e.grams, kcal: base.kcal, protein: base.protein, fat: base.fat, carbs: base.carbs,
    per: e.per === 'portion' && e.raw ? 'portion' : '100g', save: !!e.save,
  };
}

const foodLine = (f) => [f.brand, `${fmtN(f.kcal)} ${t('unit.kcal')} ${t('common.per100')}`].filter(Boolean).join(' · ');

// Шторка «граммы»: старт с default_grams / unit_grams / 100, значения пересчитываются на лету.
function gramsSheet(food, { cta, start }) {
  return new Promise((resolve) => {
    let done = false;
    const grams = numInput({ value: start || food.default_grams || food.unit_grams || 100, min: 1, max: G_MAX, decimals: 1, 'aria-label': t('add.grams') });
    const total = h('span', { class: 'row-title' });
    const calc = () => {
      const g = numVal(grams) || 0;
      const a = entryAbs({ grams: g, per100: food });
      total.textContent = `${fmtN(a.kcal)} ${t('unit.kcal')} · ${macroLine(a)}`;
    };
    grams.addEventListener('input', calc);
    calc();
    const go = () => {
      const g = numVal(grams);
      if (!g || g <= 0) { toast(t('add.v_grams'), 'error'); grams.focus(); return; }
      const bad = numCheck(grams, t('add.grams'));
      if (bad) { toast(bad, 'error'); return; }
      done = true;
      sh.close();
      resolve({ food_id: food.id, name: food.name, brand: food.brand || null, grams: g, per100: { kcal: food.kcal, protein: food.protein, fat: food.fat, carbs: food.carbs } });
    };
    grams.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    const sh = sheet({
      title: food.name,
      onClose: () => { if (!done) resolve(null); },
      content: h('div', { class: 'stack' },
        food.brand ? h('div', { class: 'muted center' }, food.brand) : null,
        h('div', { class: 'muted small center' }, `${fmtN(food.kcal)} ${t('unit.kcal')} · ${macroLine(food)} ${t('common.per100')}`),
        group([frow(t('add.ate_g'), grams)]),
        total,
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, cta || t('fp.add'))),
    });
  });
}

// Шторка поиска. Возвращает выбранную позицию (после ввода граммов) или null.
// grams — начальные граммы в шторке (замена позиции сохраняет её вес); без него порция по умолчанию.
export function pickFood({ cta, grams } = {}) {
  return new Promise((resolve) => {
    let finished = false;
    const finish = (v) => { if (finished) return; finished = true; resolve(v); };
    const list = h('div', { class: 'list fp-list' });
    const q = h('input', { type: 'search', placeholder: t('fp.search_ph'), 'aria-label': t('fp.search_aria'), autocomplete: 'off', enterkeyhint: 'search' });
    let timer = null;
    let seq = 0;
    let sh;
    let handed = false;   // шторка закрыта ради следующего шага, а не отменой
    const choose = async (food) => {
      handed = true;
      sh.close();
      finish(await gramsSheet(food, { cta, start: grams }));
    };
    const manual = async () => {
      handed = true;
      sh.close();
      finish(await manualSheet({ cta, name: q.value.trim() }));
    };
    async function load() {
      const query = q.value.trim();
      const my = ++seq;
      let foods = [];
      try { foods = (await api.get('/foods/search', { q: query, limit: 30 })).foods || []; } catch (e) {
        if (my === seq) list.replaceChildren(h('div', { class: 'hint small pad' }, e.message));
        return;
      }
      if (my !== seq) return;                       // ответ на устаревший запрос
      if (!foods.length) { list.replaceChildren(h('div', { class: 'hint small pad' }, query.length < 2 ? t('fp.type_more') : t('add.none_found'))); return; }
      list.replaceChildren(...foods.map((f) => h('button', { type: 'button', class: 'row row-btn', onclick: () => choose(f) },
        h('span', { class: 'grow' },
          h('span', { class: 'row-title' }, f.name, f.own ? h('span', { class: 'own-tag' }, t('fp.mine')) : null),
          h('span', { class: 'row-sub' }, foodLine(f))))));
    }
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    sh = sheet({
      title: t('fp.title'),
      onClose: () => { clearTimeout(timer); if (!handed) finish(null); },
      content: h('div', { class: 'stack' }, q, list,
        h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: manual }, icon('pencil', 18), t('fp.manual'))),
    });
    load();
  });
}

// «Ввести вручную»: название, граммы, К/Б/Ж/У с переключателем «на 100 г / на порцию».
export function manualSheet({ cta, name = '', saveOption = true } = {}) {
  return new Promise((resolve) => {
    let done = false;
    let per = '100g';
    const nameI = h('input', { type: 'text', maxlength: 120, value: name, placeholder: t('fp.name_ph'), 'aria-label': t('common.name'), autocomplete: 'off' });
    const grams = numInput({ value: 100, min: 1, max: G_MAX, decimals: 1, 'aria-label': t('add.grams') });
    const kcal = numInput({ min: 0, max: 10000, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_kcal') });
    const p = numInput({ min: 0, max: 1000, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_p') });
    const f = numInput({ min: 0, max: 1000, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_f') });
    const c = numInput({ min: 0, max: 1000, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_c') });
    const hint = h('div', { class: 'hint small', hidden: true });
    const saveBox = h('input', { type: 'checkbox', 'aria-label': t('fp.save_mine') });
    const lim = () => (per === 'portion' ? { k: 10000, m: 1000 } : { k: 900, m: 100 });
    const seg = segmented([['100g', t('common.per100')], ['portion', t('fp.per_portion')]], per, (v) => { per = v; limits(); mismatch(); }, t('fp.basis'));
    function limits() { const l = lim(); kcal._num.max = l.k; [p, f, c].forEach((x) => { x._num.max = l.m; }); }
    const vals = () => [kcal, p, f, c].map((x) => numVal(x) || 0);
    function mismatch() {
      const [k, pp, ff, cc] = vals();
      const calc = 4 * pp + 9 * ff + 4 * cc;
      hint.hidden = !(k > 0 && Math.abs(k - calc) > Math.max(20, 0.2 * k) && Math.abs(k - calc) > 0.2 * Math.max(k, calc));
      if (!hint.hidden) hint.textContent = t('fp.mismatch', { a: fmtN(Math.round(calc)) });
    }
    [kcal, p, f, c].forEach((x) => x.addEventListener('input', mismatch));
    limits();
    const go = () => {
      const n = nameI.value.trim();
      if (!n) { toast(t('fp.v_name'), 'error'); nameI.focus(); return; }
      const g = numVal(grams);
      if (!g || g <= 0) { toast(t('add.v_grams'), 'error'); grams.focus(); return; }
      for (const [el, label] of [[grams, t('add.grams')], [kcal, t('prof.f_kcal')], [p, t('prof.f_p')], [f, t('prof.f_f')], [c, t('prof.f_c')]]) {
        const bad = numCheck(el, label);
        if (bad) { toast(bad, 'error'); el.focus(); return; }
      }
      if (numVal(kcal) === null) { toast(t('fp.v_kcal'), 'error'); kcal.focus(); return; }
      const [k, pp, ff, cc] = vals();
      const raw = { kcal: k, protein: pp, fat: ff, carbs: cc };
      const m = per === 'portion' ? 100 / g : 1;
      const per100 = { kcal: r1(k * m), protein: r1(pp * m), fat: r1(ff * m), carbs: r1(cc * m) };
      // Сервер хранит значения на 100 г: при вводе «на порцию» они тоже должны укладываться в 900 ккал и 100 г.
      if (per100.kcal > 900 || [per100.protein, per100.fat, per100.carbs].some((x) => x > 100)) { toast(t('fp.v_limits'), 'error'); return; }
      done = true;
      sh.close();
      resolve({ name: n, brand: null, grams: g, per100, per, raw, save: !!(saveOption && saveBox.checked) });
    };
    const sh = sheet({
      title: t('fp.manual_title'),
      onClose: () => { if (!done) resolve(null); },
      content: h('div', { class: 'stack' },
        group([frow(t('common.name'), nameI), frow(t('add.grams'), grams)]),
        seg,
        group([frow(t('prof.f_kcal'), kcal), frow(t('prof.f_p'), p), frow(t('prof.f_f'), f), frow(t('prof.f_c'), c)]),
        hint,
        saveOption ? h('label', { class: 'check-row' }, saveBox, h('span', null, t('fp.save_mine'))) : null,
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, cta || t('fp.add'))),
    });
    setTimeout(() => (name ? grams : nameI).focus(), 250);
  });
}

// Только граммы у уже выбранной позиции (тап по строке в «В этом приёме»).
export function editEntryGrams(e) {
  return new Promise((resolve) => {
    let done = false;
    const grams = numInput({ value: e.grams, min: 1, max: G_MAX, decimals: 1, 'aria-label': t('add.grams') });
    const total = h('span', { class: 'row-title' });
    const calc = () => {
      const g = numVal(grams) || 0;
      const a = entryAbs(entryWithGrams(e, g));
      total.textContent = `${fmtN(a.kcal)} ${t('unit.kcal')} · ${macroLine(a)}`;
    };
    grams.addEventListener('input', calc);
    calc();
    const go = () => {
      const g = numVal(grams);
      if (!g || g <= 0) { toast(t('add.v_grams'), 'error'); grams.focus(); return; }
      const bad = numCheck(grams, t('add.grams'));
      if (bad) { toast(bad, 'error'); return; }
      done = true;
      sh.close();
      haptic('selection');
      resolve(entryWithGrams(e, g));
    };
    grams.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); go(); } });
    const sh = sheet({
      title: e.name,
      onClose: () => { if (!done) resolve(null); },
      content: h('div', { class: 'stack' }, group([frow(t('add.ate_g'), grams)]), total,
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, t('common.save'))),
    });
    setTimeout(() => grams.focus(), 250);
  });
}
