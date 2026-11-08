// "Мои продукты" list
import { api } from '../api.js';
import { foodPic, foodGroupOf, FOOD_GROUPS, groupLabel } from '../pics.js';
import { h, icon, tile, nav, pageHead, sheet, skeletonCards, emptyState, errorState, debounce, fmtN, mainButton, macroChips } from '../ui.js';
import { drinkPic } from '../pics.js';
import { t, tn } from '../i18n.js';

const PALETTE = ['blue', 'green', 'orange', 'purple', 'teal', 'pink', 'red'];
const ORDER = [...new Set([...FOOD_GROUPS.map(([t]) => t), 'Другое'])];
const DRINKS = '\u0000drinks';   // служебный ключ чипа «Напитки» (каталог напитков, не свои продукты)

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const list = h('div', { class: 'stack' });
  const chips = h('div', { class: 'chips scroll', role: 'tablist', 'aria-label': t('prod.cats') });
  const search = h('input', { type: 'search', placeholder: t('prod.search_ph'), 'aria-label': t('common.search'), autocomplete: 'off', oninput: debounce(() => load(), 250) });
  view.append(
    pageHead(t('prod.title'), { parent: '/menu' }),
    h('div', { class: 'search' }, icon('search', 18), search),
    chips,
    list);
  root.append(view);
  mainButton.show(t('prod.add'), () => openAdd());

  let all = [];
  let cat = null;

  function openAdd() {
    const go = (path) => { s.close(); nav.go(path); };
    const s = sheet({ title: t('prod.new'), content: h('div', { class: 'list tiles' },
      h('button', { class: 'row', type: 'button', onclick: () => go('/menu/product/new') }, tile('edit', 'blue'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('prod.manual')), h('span', { class: 'row-sub' }, t('prod.manual_sub')))),
      h('button', { class: 'row', type: 'button', onclick: () => go('/add/label') }, tile('camera', 'green'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('prod.by_label')), h('span', { class: 'row-sub' }, t('prod.by_label_sub'))))) });
  }

  function paint() {
    const groups = new Map();
    for (const p of all) {
      const g = foodGroupOf(p.name, p.icon);
      groups.set(g, (groups.get(g) || 0) + 1);
    }
    if (cat && cat !== DRINKS && !groups.has(cat)) cat = null;
    const mk = (key, text) => h('button', { type: 'button', role: 'tab', class: 'chip' + (cat === key ? ' active' : ''), 'aria-selected': String(cat === key),
      onclick: () => { cat = key; search.placeholder = key === DRINKS ? t('prod.search_drink') : t('prod.search_ph'); paint(); load(); } }, text);
    const keepX = chips.scrollLeft;
    chips.replaceChildren(mk(null, t('prod.all', { n: all.length })), ...ORDER.filter((g) => groups.has(g)).map((g) => mk(g, `${groupLabel(g)} ${groups.get(g)}`)), mk(DRINKS, t('add.drinks')));
    // Лента пересобирается при выборе: возвращаем прокрутку и держим выбранную вкладку на виду.
    chips.scrollLeft = keepX;
    chips.querySelector('.chip.active')?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
    if (cat === DRINKS) return;
    const shown = cat ? all.filter((p) => foodGroupOf(p.name, p.icon) === cat) : all;
    list.replaceChildren(h('div', { class: 'list tiles prod-list' }, shown.map((p) => h('button', { class: 'row prod-row', type: 'button', onclick: () => nav.go('/menu/product/' + p.id) },
      foodPic(p.name, 48, p.icon) || tile('package', PALETTE[Math.abs(Number(p.id) || 0) % PALETTE.length]),
      h('span', { class: 'grow' },
        h('span', { class: 'row-title' }, p.name),
        p.brand ? h('span', { class: 'row-sub' }, p.brand) : null,
        macroChips({ p: p.protein, f: p.fat, c: p.carbs })),
      h('span', { class: 'prod-kcal' }, h('b', null, fmtN(p.kcal)), h('span', null, t('unit.kcal')))))),
      h('div', { class: 'footnote' }, t('prod.count', { n: tn('prod.n', shown.length) })));
  }

  // Напитки: готовый каталог с калориями на 100 мл; тап открывает ввод напитка с этим выбором.
  async function loadDrinks() {
    const q = search.value.trim().toLowerCase();
    if (!list.children.length) list.replaceChildren(skeletonCards(3, 64));
    try {
      const { drinks } = await api.get('/drinks');
      if (!ctx.alive() || cat !== DRINKS) return;
      const shown = drinks.filter((d) => !q || d.name.toLowerCase().includes(q));
      if (!shown.length) { list.replaceChildren(emptyState('search', t('common.not_found'), t('common.try_other'))); return; }
      list.replaceChildren(h('div', { class: 'list tiles prod-list' }, shown.map((d) => h('button', {
        class: 'row prod-row', type: 'button',
        onclick: () => { sessionStorage.setItem('drink-pick', d.id); nav.go('/add/drink'); },
      },
      drinkPic(d.id, 48) || h('span', { class: 'drink-emo', style: { width: '48px', height: '48px' } }, d.emoji),
      h('span', { class: 'grow' },
        h('span', { class: 'row-title' }, d.name),
        h('span', { class: 'row-sub' }, d.sizes.join(' / ') + ' ' + t('unit.ml') + (d.sugar ? t('prod.sugar') : ''))),
      h('span', { class: 'prod-kcal' }, h('b', null, fmtN(Math.round(d.kcal_100))), h('span', null, t('unit.kcal') + ' / 100 ' + t('unit.ml')))))),
      h('div', { class: 'footnote' }, t('prod.approx')));
    } catch (e) { if (ctx.alive()) errorState(list, e, loadDrinks); }
  }

  async function load() {
    if (cat === DRINKS) return loadDrinks();
    const q = search.value.trim();
    if (!list.children.length) list.replaceChildren(skeletonCards(3, 64));
    try {
      const { products } = await api.get('/products', { q });
      if (!ctx.alive() || q !== search.value.trim()) return;
      all = products;
      if (!products.length) {
        paint();
        list.replaceChildren(q ? emptyState('search', t('common.not_found'), t('common.try_other'))
          : emptyState('package', t('prod.empty_title'), t('prod.empty_text')));
        return;
      }
      paint();
    } catch (e) { if (ctx.alive()) errorState(list, e, load); }
  }
  await load();
}
