// "Мои продукты" list
import { api } from '../api.js';
import { foodPic, foodGroupOf, FOOD_GROUPS } from '../pics.js';
import { h, icon, tile, nav, pageHead, sheet, skeletonCards, emptyState, errorState, debounce, fmtN, plural, segmented } from '../ui.js';
import { drinkPic } from '../pics.js';

const PALETTE = ['blue', 'green', 'orange', 'purple', 'teal', 'pink', 'red'];
const ORDER = [...FOOD_GROUPS.map(([t]) => t), 'Другое'];

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const list = h('div', { class: 'stack' });
  const chips = h('div', { class: 'chips scroll', role: 'tablist', 'aria-label': 'Категории' });
  chips.hidden = true;
  const search = h('input', { type: 'search', placeholder: 'Поиск по названию или бренду', 'aria-label': 'Поиск продуктов', autocomplete: 'off', oninput: debounce(() => load(), 250) });
  const addBtn = h('button', { class: 'icon-btn accent', type: 'button', 'aria-label': 'Добавить продукт', 'aria-haspopup': 'dialog', onclick: openAdd }, icon('plus'));
  let mode = 'food'; // food — свои продукты, drinks — каталог напитков
  const modeSeg = segmented([['food', 'Продукты'], ['drinks', 'Напитки']], mode, (v) => { mode = v; addBtn.hidden = v === 'drinks'; search.placeholder = v === 'drinks' ? 'Поиск напитка' : 'Поиск по названию или бренду'; load(); }, 'Раздел');
  view.append(
    pageHead('Мои продукты', { parent: '/menu', right: addBtn }),
    modeSeg,
    h('div', { class: 'search' }, icon('search', 18), search),
    chips,
    list);
  root.append(view);

  let all = [];
  let cat = null;

  function openAdd() {
    const go = (path) => { s.close(); nav.go(path); };
    const s = sheet({ title: 'Новый продукт', content: h('div', { class: 'list tiles' },
      h('button', { class: 'row', type: 'button', onclick: () => go('/menu/product/new') }, tile('edit', 'blue'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, 'Вручную'), h('span', { class: 'row-sub' }, 'Ввести название и КБЖУ на 100 г'))),
      h('button', { class: 'row', type: 'button', onclick: () => go('/add/label') }, tile('camera', 'green'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, 'По этикетке или штрихкоду'), h('span', { class: 'row-sub' }, 'Несколько фото упаковки или поиск в Open Food Facts')))) });
  }

  function macro(cls, letter, v) {
    return h('span', { class: 'mac ' + cls }, h('i'), `${letter} ${fmtN(v)}`);
  }

  function paint() {
    const groups = new Map();
    for (const p of all) {
      const g = foodGroupOf(p.name, p.icon);
      groups.set(g, (groups.get(g) || 0) + 1);
    }
    if (cat && !groups.has(cat)) cat = null;
    const showChips = all.length >= 4 && groups.size >= 2;
    chips.hidden = !showChips;
    if (!showChips) cat = null;
    const mk = (key, text) => h('button', { type: 'button', role: 'tab', class: 'chip' + (cat === key ? ' active' : ''), 'aria-selected': String(cat === key),
      onclick: () => { cat = key; paint(); } }, text);
    chips.replaceChildren(mk(null, `Все ${all.length}`), ...ORDER.filter((g) => groups.has(g)).map((g) => mk(g, `${g} ${groups.get(g)}`)));
    const shown = cat ? all.filter((p) => foodGroupOf(p.name, p.icon) === cat) : all;
    list.replaceChildren(h('div', { class: 'list tiles prod-list' }, shown.map((p) => h('button', { class: 'row prod-row', type: 'button', onclick: () => nav.go('/menu/product/' + p.id) },
      foodPic(p.name, 48, p.icon) || tile('package', PALETTE[Math.abs(Number(p.id) || 0) % PALETTE.length]),
      h('span', { class: 'grow' },
        h('span', { class: 'row-title' }, p.name),
        p.brand ? h('span', { class: 'row-sub' }, p.brand) : null,
        h('span', { class: 'macs' }, macro('p', 'Б', p.protein), macro('f', 'Ж', p.fat), macro('c', 'У', p.carbs))),
      h('span', { class: 'prod-kcal' }, h('b', null, fmtN(p.kcal)), h('span', null, 'ккал'))))),
      h('div', { class: 'footnote' }, `${plural(shown.length, 'продукт', 'продукта', 'продуктов')} · значения на 100 г`));
  }

  // Напитки: готовый каталог с калориями на 100 мл; тап открывает ввод напитка с этим выбором.
  async function loadDrinks() {
    const q = search.value.trim().toLowerCase();
    chips.hidden = true;
    if (!list.children.length) list.replaceChildren(skeletonCards(3, 64));
    try {
      const { drinks } = await api.get('/drinks');
      if (!ctx.alive() || mode !== 'drinks') return;
      const shown = drinks.filter((d) => !q || d.name.toLowerCase().includes(q));
      if (!shown.length) { list.replaceChildren(emptyState('search', 'Ничего не найдено', 'Попробуйте другое название.')); return; }
      list.replaceChildren(h('div', { class: 'list tiles prod-list' }, shown.map((d) => h('button', {
        class: 'row prod-row', type: 'button',
        onclick: () => { sessionStorage.setItem('drink-pick', d.id); nav.go('/add/drink'); },
      },
      drinkPic(d.id, 48) || h('span', { class: 'drink-emo', style: { width: '48px', height: '48px' } }, d.emoji),
      h('span', { class: 'grow' },
        h('span', { class: 'row-title' }, d.name),
        h('span', { class: 'row-sub' }, d.sizes.join(' / ') + ' мл' + (d.sugar ? ' · можно с сахаром' : ''))),
      h('span', { class: 'prod-kcal' }, h('b', null, fmtN(Math.round(d.kcal_100))), h('span', null, 'ккал / 100 мл'))))),
      h('div', { class: 'footnote' }, 'Значения ориентировочные. Точные берите с этикетки'));
    } catch (e) { if (ctx.alive()) errorState(list, e, loadDrinks); }
  }

  async function load() {
    if (mode === 'drinks') return loadDrinks();
    const q = search.value.trim();
    if (!list.children.length) list.replaceChildren(skeletonCards(3, 64));
    try {
      const { products } = await api.get('/products', { q });
      if (!ctx.alive() || q !== search.value.trim()) return;
      all = products;
      if (!products.length) {
        chips.hidden = true;
        list.replaceChildren(q ? emptyState('search', 'Ничего не найдено', 'Попробуйте другое название.')
          : emptyState('package', 'Своих продуктов пока нет', 'Сохраняйте продукты с этикетки или из приёма пищи: они сразу будут учитываться при распознавании.'));
        return;
      }
      paint();
    } catch (e) { if (ctx.alive()) errorState(list, e, load); }
  }
  await load();
}
