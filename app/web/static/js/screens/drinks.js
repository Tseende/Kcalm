// Drinks: water plus a catalog with calories. Water goes to /water, everything else to /drinks (saved as a meal).
import { api } from '../api.js';
import {
  h, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, fmtN, TILE, errorState, skeletonCards,
} from '../ui.js';
import { drinkPic } from '../pics.js';

const WATER = { id: 'water', name: 'Вода', emoji: '💧', color: 'teal', kcal_100: 0, sizes: [150, 200, 250, 300, 500], sugar: false, hydrates: true };
const SUGAR_KCAL = 20; // one teaspoon, about 5 g

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' }, skeletonCards(2, 90));
  view.append(pageHead('Напитки', { parent: '/today' }), body);
  root.append(view);

  let catalog;
  try {
    catalog = [WATER, ...((await api.get('/drinks')).drinks || [])];
  } catch (e) {
    if (ctx.alive()) errorState(body, e, () => nav.replace('/add/drink'));
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

  const ml = numInput({ min: 30, max: 1500, step: 10, value: cur.sizes[Math.min(1, cur.sizes.length - 1)], inputmode: 'numeric', 'aria-label': 'Миллилитры' });
  const sizes = h('div', { class: 'chips wrap' });
  const sugarVal = h('b', { class: 'step-val' }, '0');
  const sugarBox = h('div', { class: 'drink-sugar card' },
    h('div', { class: 'grow' }, h('div', { class: 'row-title' }, 'Сахар'), h('div', { class: 'muted small' }, 'чайных ложек, по 20 ккал')),
    h('div', { class: 'stepper' },
      h('button', { type: 'button', class: 'step', 'aria-label': 'Меньше сахара', onclick: () => setSugar(sugar - 1) }, '−'),
      sugarVal,
      h('button', { type: 'button', class: 'step', 'aria-label': 'Больше сахара', onclick: () => setSugar(sugar + 1) }, '+')));
  const sumKcal = h('div', { class: 'drink-kcal' });
  const sumSub = h('div', { class: 'muted' });
  const sumWater = h('div', { class: 'drink-water' });
  const summary = h('section', { class: 'card drink-sum', 'aria-live': 'polite' }, sumKcal, sumSub, sumWater);

  const cards = catalog.map((d) => {
    const b = h('button', { type: 'button', class: 'drink-card', 'aria-pressed': 'false', onclick: () => select(d) },
      drinkPic(d.id) || h('span', { class: 'drink-emo', style: { background: TILE[d.color] || TILE.blue } }, d.emoji),
      h('span', { class: 'drink-name' }, d.name),
      h('span', { class: 'drink-sub' }, d.id === 'water' ? 'без калорий' : d.kcal_100 < 1 ? '0 ккал' : `${fmtN(Math.round(d.kcal_100))} ккал`));
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
    sizes.replaceChildren(...cur.sizes.map((v) => h('button', { type: 'button', class: 'chip' + (numVal(ml) === v ? ' active' : ''), onclick: () => { ml.value = v; haptic('light'); paint(); } }, v + ' мл')));
    sugarBox.hidden = !cur.sugar;
    sugarVal.textContent = String(sugar);
    const v = numVal(ml) || 0;
    if (cur.id === 'water') {
      sumKcal.textContent = '0 ккал';
      sumSub.textContent = `Вода · ${v} мл`;
    } else {
      const k = kcalNow();
      sumKcal.textContent = '≈ ' + k + ' ккал';
      sumSub.textContent = `${cur.name}${cur.sugar && sugar ? ` + ${sugar} ч. л. сахара` : ''} · ${v} мл`;
    }
    sumWater.textContent = cur.hydrates ? `+${v} мл в дневную норму воды` : 'В норму воды не идёт';
    sumWater.classList.toggle('off', !cur.hydrates);
  }

  function select(d) {
    if (d.id === cur.id) return;
    cur = d;
    sugar = 0;
    ml.value = d.sizes[Math.min(1, d.sizes.length - 1)];
    haptic('light');
    paint();
  }

  function setSugar(n) {
    sugar = Math.max(0, Math.min(6, n));
    haptic('light');
    paint();
  }

  ml.addEventListener('input', paint);

  async function go() {
    if (busy) return;
    const v = numVal(ml);
    if (!v || v < 30 || v > 1500) { toast('Укажите объём от 30 до 1500 мл', 'error'); return; }
    busy = true;
    mainButton.setLoading(true);
    try {
      const r = cur.id === 'water'
        ? await api.post('/water', { ml: Math.round(v) })
        : await api.post('/drinks', { drink: cur.id, ml: Math.round(v), sugar_tsp: cur.sugar ? sugar : 0 });
      haptic('success');
      toast(cur.id === 'water' ? `Выпито за день: ${r.water_ml} из ${r.goal_ml} мл` : `${cur.name}: ${r.kcal} ккал`, 'ok');
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
    h('div', { class: 'drink-label' }, 'Объём'),
    sizes,
    group([frow('Свой объём, мл', ml)]),
    sugarBox,
    summary,
    h('p', { class: 'muted small drink-note' }, 'Калории напитков ориентировочные. Для точных значений добавьте продукт по этикетке. Чай и кофе засчитываются в воду.'));
  paint();
  mainButton.show('Добавить', go);
}
