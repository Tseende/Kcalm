// Напитки: сетка каталога по группам, шторка порции и «Свой напиток». Общий для экрана «Напитки» и приёма пищи.
// Выбор — это { drink, ml, sugar } (drink.id: 'water', id каталога или 'custom').
// drinkEntry() превращает выбор в позицию приёма (как у food_picker: name, grams = мл, per100).
import { api } from './api.js';
import { t } from './i18n.js';
import {
  h, icon, tile, sheet, toast, haptic, fmtN, numInput, numVal, numSet, numCheck, frow, group, sectionTitle, TILE, macroLine,
} from './ui.js';
import { drinkPic } from './pics.js';

const WATER = { id: 'water', name: t('water.title'), emoji: '💧', color: 'teal', kcal_100: 0, protein: 0, fat: 0, carbs: 0, sizes: [150, 200, 250, 300, 500], sugar: false, hydrates: true, alcohol: false };
// Границы объёма за одну запись — как на сервере (domain/water.py и domain/drinks.py).
const WATER_ML = [10, 3000];
const DRINK_ML = [30, 1500];
const SUGAR_KCAL = 20;   // чайная ложка, около 5 г
const SUGAR_G = 5;
const MAX_SUGAR = 6;
const r1 = (v) => Math.round(v * 10) / 10;

export async function loadDrinks() {
  return [WATER, ...((await api.get('/drinks')).drinks || [])];
}

const kcalLabel = (d) => (d.id === 'water' ? t('drink.no_kcal') : `${d.kcal_100 < 1 ? 0 : fmtN(Math.round(d.kcal_100))} ${t('unit.kcal')}`);
const pic = (d, size = 48) => drinkPic(d.id, size) || h('span', { class: 'drink-emo', style: { background: TILE[d.color] || TILE.blue, width: size + 'px', height: size + 'px' } }, d.emoji || '🥤');

export const drinkKcal = (c) => Math.round(c.drink.kcal_100 * c.ml / 100 + (c.drink.sugar ? c.sugar * SUGAR_KCAL : 0));
export const drinkName = (c) => (c.sugar ? c.drink.name + t('drink.plus_sugar', { n: c.sugar }) : c.drink.name);

/** Выбор напитка → позиция приёма. Сахар раскладывается в значения на 100 мл. */
export function drinkEntry(c) {
  const d = c.drink;
  const extra = d.sugar && c.sugar ? c.sugar * 100 / c.ml : 0;
  return {
    name: drinkName(c), brand: null, grams: c.ml, unit: 'ml', water: d.id === 'water', save: !!c.save,
    per100: { kcal: r1(d.kcal_100 + extra * SUGAR_KCAL), protein: r1(d.protein || 0), fat: r1(d.fat || 0), carbs: r1((d.carbs || 0) + extra * SUGAR_G) },
  };
}

/** Сетка каталога: без калорий, с калориями, алкоголь; внизу строка «Свой напиток». */
export function drinkGrid(catalog, onPick, onCustom) {
  const card = (d) => h('button', { type: 'button', class: 'drink-card', onclick: () => { haptic('light'); onPick(d); } },
    pic(d), h('span', { class: 'drink-name' }, d.name), h('span', { class: 'drink-sub' }, kcalLabel(d)));
  const sect = (title, list) => (list.length ? [sectionTitle(title), h('div', { class: 'drink-grid' }, list.map(card))] : []);
  return h('div', { class: 'stack drink-pick' },
    ...sect(t('drink.g_zero'), catalog.filter((d) => !d.alcohol && d.kcal_100 < 1)),
    ...sect(t('drink.g_kcal'), catalog.filter((d) => !d.alcohol && d.kcal_100 >= 1)),
    ...sect(t('drink.g_alc'), catalog.filter((d) => d.alcohol)),
    h('div', { class: 'list tiles' },
      h('button', { class: 'row', type: 'button', onclick: () => { haptic('light'); onCustom(); } },
        tile('pencil', 'blue'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('drink.custom_title')), h('span', { class: 'row-sub' }, t('drink.custom_sub'))),
        icon('next', 18))));
}

/** Шторка порции: объём кнопками или числом, сахар у чая и кофе, итог. Promise<{drink, ml, sugar} | null>. */
export function portionSheet(d, { cta } = {}) {
  return new Promise((resolve) => {
    let done = false;
    let sugar = 0;
    const [lo, hi] = d.id === 'water' ? WATER_ML : DRINK_ML;
    const ml = numInput({ max: hi, value: d.sizes[Math.min(1, d.sizes.length - 1)], inputmode: 'numeric', 'aria-label': t('drink.a_ml') });
    const chips = h('div', { class: 'chips wrap' }, d.sizes.map((v) => h('button', { type: 'button', class: 'chip', 'data-ml': v,
      onclick: () => { numSet(ml, v); haptic('light'); paint(); } }, v + ' ' + t('unit.ml'))));
    const sugarVal = h('b', { class: 'step-val' }, '0');
    const setSugar = (n) => { sugar = Math.max(0, Math.min(MAX_SUGAR, n)); haptic('light'); paint(); };
    const sugarBox = d.sugar ? h('div', { class: 'drink-sugar' },
      h('div', { class: 'grow' }, h('div', { class: 'row-title' }, t('drink.sugar')), h('div', { class: 'muted small' }, t('drink.sugar_sub'))),
      h('div', { class: 'stepper' },
        h('button', { type: 'button', class: 'step', 'aria-label': t('drink.less'), onclick: () => setSugar(sugar - 1) }, icon('minus', 18)),
        sugarVal,
        h('button', { type: 'button', class: 'step', 'aria-label': t('drink.more'), onclick: () => setSugar(sugar + 1) }, icon('plus', 18)))) : null;
    const sumKcal = h('div', { class: 'drink-kcal' });
    const sumWater = h('div', { class: 'drink-water' });
    function paint() {
      const v = numVal(ml) || 0;
      [...chips.children].forEach((b) => b.classList.toggle('active', v === Number(b.dataset.ml)));
      sugarVal.textContent = String(sugar);
      sumKcal.textContent = d.id === 'water' ? '0 ' + t('unit.kcal') : '≈ ' + fmtN(drinkKcal({ drink: d, ml: v, sugar })) + ' ' + t('unit.kcal');
      sumWater.textContent = d.hydrates ? t('drink.counts', { v }) : t('drink.not_counted');
      sumWater.classList.toggle('off', !d.hydrates);
    }
    ml.addEventListener('input', paint);
    const go = () => {
      const v = numVal(ml);
      if (!v || v < lo || v > hi) { toast(t(d.id === 'water' ? 'drink.lim_water' : 'drink.lim_drink', { lo, hi }), 'error'); ml.focus(); return; }
      done = true;
      sh.close();
      resolve({ drink: d, ml: Math.round(v), sugar: d.sugar ? sugar : 0 });
    };
    ml.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    const sh = sheet({
      title: d.name,
      onClose: () => { if (!done) resolve(null); },
      content: h('div', { class: 'stack drink-sheet' },
        h('div', { class: 'drink-hero' }, pic(d, 64), h('div', { class: 'muted small' }, d.id === 'water' ? t('drink.no_kcal') : `${kcalLabel(d)} ${t('drink.per100ml')}`)),
        chips,
        group([frow(t('drink.custom'), ml)]),
        sugarBox,
        h('div', { class: 'drink-sum', 'aria-live': 'polite' }, sumKcal, sumWater),
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, cta || t('common.add'))),
    });
    paint();
  });
}

/** «Свой напиток»: название, объём, значения на 100 мл. Promise<{drink, ml, sugar: 0, save} | null>. */
export function customDrinkSheet({ cta } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const nameI = h('input', { type: 'text', maxlength: 120, placeholder: t('drink.name_ph'), 'aria-label': t('common.name'), autocomplete: 'off' });
    const ml = numInput({ value: 250, max: DRINK_ML[1], inputmode: 'numeric', 'aria-label': t('drink.a_ml') });
    const kcal = numInput({ min: 0, max: 900, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_kcal') });
    const p = numInput({ min: 0, max: 100, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_p') });
    const f = numInput({ min: 0, max: 100, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_f') });
    const c = numInput({ min: 0, max: 100, decimals: 1, placeholder: '0', 'aria-label': t('prof.f_c') });
    const saveBox = h('input', { type: 'checkbox', 'aria-label': t('fp.save_mine') });
    const total = h('div', { class: 'row-title center' });
    const paint = () => {
      const k = (numVal(ml) || 0) / 100;
      total.textContent = `${fmtN(Math.round((numVal(kcal) || 0) * k))} ${t('unit.kcal')} · ${macroLine({ protein: r1((numVal(p) || 0) * k), fat: r1((numVal(f) || 0) * k), carbs: r1((numVal(c) || 0) * k) })}`;
    };
    [ml, kcal, p, f, c].forEach((x) => x.addEventListener('input', paint));
    const go = () => {
      const n = nameI.value.trim();
      if (!n) { toast(t('fp.v_name'), 'error'); nameI.focus(); return; }
      const v = numVal(ml);
      if (!v || v < DRINK_ML[0] || v > DRINK_ML[1]) { toast(t('drink.lim_drink', { lo: DRINK_ML[0], hi: DRINK_ML[1] }), 'error'); ml.focus(); return; }
      if (numVal(kcal) === null) { toast(t('fp.v_kcal'), 'error'); kcal.focus(); return; }
      for (const [el, label] of [[kcal, t('prof.f_kcal')], [p, t('prof.f_p')], [f, t('prof.f_f')], [c, t('prof.f_c')]]) {
        const bad = numCheck(el, label);
        if (bad) { toast(bad, 'error'); el.focus(); return; }
      }
      done = true;
      sh.close();
      resolve({
        drink: { id: 'custom', name: n, kcal_100: numVal(kcal) || 0, protein: numVal(p) || 0, fat: numVal(f) || 0, carbs: numVal(c) || 0, sugar: false, hydrates: false },
        ml: Math.round(v), sugar: 0, save: saveBox.checked,
      });
    };
    const sh = sheet({
      title: t('drink.custom_title'),
      onClose: () => { if (!done) resolve(null); },
      content: h('div', { class: 'stack' },
        group([frow(t('common.name'), nameI), frow(t('drink.vol_ml'), ml)]),
        group([frow(t('prof.f_kcal'), kcal), frow(t('prof.f_p'), p), frow(t('prof.f_f'), f), frow(t('prof.f_c'), c)], t('drink.per100_note')),
        total,
        h('label', { class: 'check-row' }, saveBox, h('span', null, t('fp.save_mine'))),
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, cta || t('common.add'))),
    });
    paint();
    setTimeout(() => nameI.focus(), 250);
  });
}

/** Шторка «Напиток» для приёма пищи: каталог → порция. Promise<{drink, ml, sugar} | null>. */
export function pickDrink({ cta } = {}) {
  return new Promise((resolve) => {
    let finished = false;
    let handed = false;   // шторка закрыта ради следующего шага, а не отменой
    const finish = (v) => { if (finished) return; finished = true; resolve(v); };
    const box = h('div', { class: 'drink-wait' });
    const next = (fn) => async (arg) => { handed = true; sh.close(); finish(await fn(arg)); };
    const sh = sheet({ title: t('drink.pick_title'), onClose: () => { if (!handed) finish(null); }, content: box });
    loadDrinks().then((catalog) => {
      box.replaceChildren(drinkGrid(catalog, next((d) => portionSheet(d, { cta })), next(() => customDrinkSheet({ cta }))));
    }).catch((e) => box.replaceChildren(h('div', { class: 'hint small pad' }, e.message)));
  });
}
