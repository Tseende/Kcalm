// Drinks: water plus a catalog with calories. Water goes to /water, everything else to /drinks (saved as a meal).
import { api } from '../api.js';
import {
  h, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, numSet, fmtN, TILE, errorState, skeletonBlocks, icon, reloadRoute,
} from '../ui.js';
import { drinkPic } from '../pics.js';
import { t } from '../i18n.js';

const WATER = { id: 'water', name: t('water.title'), emoji: '💧', color: 'teal', kcal_100: 0, sizes: [150, 200, 250, 300, 500], sugar: false, hydrates: true };
// Границы объёма за одну запись — как на сервере (domain/water.py и domain/drinks.py).
const WATER_ML = [10, 3000];
const DRINK_ML = [30, 1500];
const SUGAR_KCAL = 20; // one teaspoon, about 5 g

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' }, skeletonBlocks([64, 180, 120]));
  view.append(pageHead(t('add.drinks'), { parent: '/today' }), body);
  root.append(view);

  let catalog;
  try {
    catalog = [WATER, ...((await api.get('/drinks')).drinks || [])];
  } catch (e) {
    if (ctx.alive()) errorState(body, e, reloadRoute);
    return;
  }
  if (!ctx.alive()) return;

  let cur = WATER;
  // Переход из «Мои продукты → Напитки»: сразу выбран нужный напиток.
  const want = sessionStorage.getItem('drink-pick');
  sessionStorage.removeItem('drink-pick');
  const wanted = want && catalog.find((d) => d.id === want);
  if (wanted) cur = wanted;
  let sugar = 0;
  let busy = false;

  // Границы зависят от напитка (вода 10..3000, остальное 30..1500): поле пускает до 4 цифр, точно проверяет go().
  const ml = numInput({ max: WATER_ML[1], value: cur.sizes[Math.min(1, cur.sizes.length - 1)], inputmode: 'numeric', 'aria-label': t('drink.a_ml') });
  const sizes = h('div', { class: 'chips wrap' });
  const sugarVal = h('b', { class: 'step-val' }, '0');
  const sugarBox = h('div', { class: 'drink-sugar card' },
    h('div', { class: 'grow' }, h('div', { class: 'row-title' }, t('drink.sugar')), h('div', { class: 'muted small' }, t('drink.sugar_sub'))),
    h('div', { class: 'stepper' },
      h('button', { type: 'button', class: 'step', 'aria-label': t('drink.less'), onclick: () => setSugar(sugar - 1) }, icon('minus', 18)),
      sugarVal,
      h('button', { type: 'button', class: 'step', 'aria-label': t('drink.more'), onclick: () => setSugar(sugar + 1) }, icon('plus', 18))));
  const sumKcal = h('div', { class: 'drink-kcal' });
  const sumSub = h('div', { class: 'muted' });
  const sumWater = h('div', { class: 'drink-water' });
  const summary = h('section', { class: 'card drink-sum', 'aria-live': 'polite' }, sumKcal, sumSub, sumWater);

  const cards = catalog.map((d) => {
    const b = h('button', { type: 'button', class: 'drink-card', 'aria-pressed': 'false', onclick: () => select(d) },
      drinkPic(d.id) || h('span', { class: 'drink-emo', style: { background: TILE[d.color] || TILE.blue } }, d.emoji),
      h('span', { class: 'drink-check', 'aria-hidden': 'true' }, icon('check', 12)),
      h('span', { class: 'drink-name' }, d.name),
      h('span', { class: 'drink-sub' }, d.id === 'water' ? t('drink.no_kcal') : d.kcal_100 < 1 ? '0 ' + t('unit.kcal') : `${fmtN(Math.round(d.kcal_100))} ${t('unit.kcal')}`));
    b.dataset.id = d.id;
    return b;
  });

  function kcalNow() {
    const v = numVal(ml) || 0;
    return Math.round(cur.kcal_100 * v / 100 + (cur.sugar ? sugar * SUGAR_KCAL : 0));
  }

  function paint() {
    cards.forEach((b) => {
      const on = b.dataset.id === cur.id;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    });
    sizes.replaceChildren(...cur.sizes.map((v) => h('button', { type: 'button', class: 'chip' + (numVal(ml) === v ? ' active' : ''), 'data-ml': v, onclick: () => { numSet(ml, v); haptic('light'); paintSum(); } }, v + ' ' + t('unit.ml'))));
    paintSum();
  }

  // Что зависит от введённого объёма: пересчитывается на каждый символ, но ничего не пересоздаёт (поле не теряет фокус).
  function paintSum() {
    [...sizes.children].forEach((b) => b.classList.toggle('active', numVal(ml) === Number(b.dataset.ml)));
    sugarBox.hidden = !cur.sugar;
    sugarVal.textContent = String(sugar);
    const v = numVal(ml) || 0;
    if (cur.id === 'water') {
      sumKcal.textContent = '0 ' + t('unit.kcal');
      sumSub.textContent = t('drink.sum_water', { v });
    } else {
      const k = kcalNow();
      sumKcal.textContent = '≈ ' + k + ' ' + t('unit.kcal');
      sumSub.textContent = `${cur.name}${cur.sugar && sugar ? t('drink.plus_sugar', { n: sugar }) : ''} · ${v} ${t('unit.ml')}`;
    }
    sumWater.textContent = cur.hydrates ? t('drink.counts', { v }) : t('drink.not_counted');
    sumWater.classList.toggle('off', !cur.hydrates);
  }

  function select(d) {
    if (d.id === cur.id) return;
    cur = d;
    sugar = 0;
    numSet(ml, d.sizes[Math.min(1, d.sizes.length - 1)]);
    haptic('light');
    paint();
  }

  function setSugar(n) {
    sugar = Math.max(0, Math.min(6, n));
    haptic('light');
    paint();
  }

  ml.addEventListener('input', paintSum);

  async function go() {
    if (busy) return;
    const v = numVal(ml);
    const [lo, hi] = cur.id === 'water' ? WATER_ML : DRINK_ML;
    if (!v || v < lo || v > hi) { toast(t(cur.id === 'water' ? 'drink.lim_water' : 'drink.lim_drink', { lo, hi }), 'error'); ml.focus(); return; }
    busy = true;
    mainButton.setLoading(true);
    try {
      const r = cur.id === 'water'
        ? await api.post('/water', { ml: Math.round(v) })
        : await api.post('/drinks', { drink: cur.id, ml: Math.round(v), sugar_tsp: cur.sugar ? sugar : 0 });
      haptic('success');
      toast(cur.id === 'water' ? t('drink.water_day', { a: r.water_ml, b: r.goal_ml }) : t('drink.drink_kcal', { n: cur.name, k: r.kcal }), 'ok');
      mainButton.hide();
      nav.replace('/today');
    } catch (e) {
      busy = false;
      mainButton.setLoading(false);
      toast(e.message, 'error');
    }
  }

  body.replaceChildren(
    h('div', { class: 'drink-grid' }, cards),
    h('div', { class: 'drink-label' }, t('dish.volume')),
    sizes,
    group([frow(t('drink.custom'), ml)]),
    sugarBox,
    summary,
    h('p', { class: 'muted small drink-note' }, t('drink.note')));
  paint();
  mainButton.show(t('common.add'), go);
}
