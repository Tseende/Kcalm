// "Мои продукты" list
import { api } from '../api.js';
import { assetUrl, drinkPic, foodPic, foodPicKey, foodGroupOf, FOOD_GROUPS, groupLabel } from '../pics.js';
import { h, icon, tile, nav, pageHead, sheet, skeletonCards, emptyState, errorState, debounce, fmtN, mainButton, macroChips } from '../ui.js';
import { COLUMNS, TILES } from '../food_atlas.js';
import { t, tn } from '../i18n.js';

const PALETTE = ['blue', 'green', 'orange', 'purple', 'teal', 'pink', 'red'];
const ORDER = [...new Set([...FOOD_GROUPS.map(([t]) => t), 'Другое'])];
const PAGE_SIZE = 40;
const ATLAS_THRESHOLD = 120;
const ATLAS_INDEX = new Map(TILES.flatMap(({ asset, rows, keys }) => {
  const url = assetUrl(asset);
  return keys.map((key, i) => [key, { url, rows, i }]);
}));
const DRINKS = '\u0000drinks';   // служебный ключ чипа «Напитки» (каталог напитков, не свои продукты)

function cataloguePic(product, useAtlas, observer) {
  const key = useAtlas ? foodPicKey(product.name, product.icon, product.brand) : null;
  const cell = ATLAS_INDEX.get(key);
  if (!cell) return foodPic(product.name, 48, product.icon, product.brand);
  const { i, rows, url } = cell;
  const x = (i % COLUMNS) * 100 / (COLUMNS - 1);
  const y = Math.floor(i / COLUMNS) * 100 / Math.max(1, rows - 1);
  const sprite = h('span', { class: 'pic-sprite', style: {
    backgroundSize: `${COLUMNS * 100}% ${rows * 100}%`,
    backgroundPosition: `${x}% ${y}%`,
  } });
  if (observer) {
    sprite.dataset.spriteUrl = url;
    observer.observe(sprite);
  } else {
    sprite.style.backgroundImage = `url("${url}")`;
  }
  return h('span', { class: 'pic', 'aria-hidden': 'true', style: { width: '48px', height: '48px' } }, sprite);
}


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
  let catalog = [];
  let cat = null;
  const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver((entries) => {
    for (const { target, isIntersecting } of entries) {
      if (!isIntersecting || !target.isConnected) continue;
      target.style.backgroundImage = `url("${target.dataset.spriteUrl}")`;
      observer.unobserve(target);
    }
  }, { rootMargin: '160px' }) : null;

  function openAdd() {
    const go = (path) => { s.close(); nav.go(path); };
    const s = sheet({ title: t('prod.new'), content: h('div', { class: 'list tiles' },
      h('button', { class: 'row', type: 'button', onclick: () => go('/menu/product/new') }, tile('edit', 'blue'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('prod.manual')), h('span', { class: 'row-sub' }, t('prod.manual_sub')))),
      h('button', { class: 'row', type: 'button', onclick: () => go('/add/label') }, tile('camera', 'green'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('prod.by_label')), h('span', { class: 'row-sub' }, t('prod.by_label_sub'))))) });
  }

  function paint() {
    observer?.disconnect();
    const groups = new Map();
    for (const p of all) {
      const g = p.group;
      groups.set(g, (groups.get(g) || 0) + 1);
    }
    if (cat && cat !== DRINKS && !groups.has(cat)) cat = null;
    const mk = (key, text) => h('button', { type: 'button', role: 'tab', class: 'chip' + (cat === key ? ' active' : ''), 'aria-selected': String(cat === key),
      onclick: () => { cat = key; search.placeholder = key === DRINKS ? t('prod.search_drink') : t('prod.search_ph'); paint(); load(); } }, text);
    const keepX = chips.scrollLeft;
    chips.replaceChildren(mk(null, t('prod.all', { n: all.length })), ...ORDER.filter((g) => groups.has(g)).map((g) => mk(g, `${groupLabel(g)} ${groups.get(g)}`)), mk(DRINKS, t('add.drinks')));
    // Лента пересобирается при выборе: возвращаем прокрутку и держим выбранную вкладку на виду.
    chips.scrollLeft = keepX;
    const selected = chips.querySelector('.chip.active');
    if (selected) {
      const chip = selected.getBoundingClientRect();
      const strip = chips.getBoundingClientRect();
      if (chip.left < strip.left) chips.scrollLeft += chip.left - strip.left;
      else if (chip.right > strip.right) chips.scrollLeft += chip.right - strip.right;
    }
    if (cat === DRINKS) return;
    const shown = cat ? all.filter((p) => p.group === cat) : all;
    const shared = cat ? catalog.filter((p) => p.group === cat) : catalog;
    const rows = h('div', { class: 'list tiles prod-list' });
    const row = (p, fromCatalog = false) => h('button', { class: 'row prod-row', type: 'button',
      onclick: () => fromCatalog
        ? sheet({ title: p.name, content: h('div', { class: 'stack' },
          p.brand ? h('div', null, p.brand) : null,
          h('div', { class: 'footnote' }, t('pf.per100')),
          h('div', null, h('b', null, fmtN(p.kcal)), ' ', t('unit.kcal')),
          macroChips({ p: p.protein, f: p.fat, c: p.carbs })) })
        : nav.go('/menu/product/' + p.id) },
      cataloguePic(p, shown.length >= ATLAS_THRESHOLD, observer) || tile('package', PALETTE[Math.abs(Number(p.id) || 0) % PALETTE.length]),
      h('span', { class: 'grow' },
        h('span', { class: 'row-title' }, p.name),
        p.brand ? h('span', { class: 'row-sub' }, p.brand) : null,
        macroChips({ p: p.protein, f: p.fat, c: p.carbs })),
      h('span', { class: 'prod-kcal' }, h('b', null, fmtN(p.kcal)), h('span', null, t('unit.kcal'))));
    let displayed = 0;
    const more = h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => appendPage(true) }, t('prod.more'));
    function appendPage(focusFirst = false) {
      const batch = shown.slice(displayed, displayed + PAGE_SIZE).map((p) => row(p));
      rows.append(...batch);
      displayed += batch.length;
      if (focusFirst) batch[0]?.focus();
      if (displayed >= shown.length) more.remove();
    }
    appendPage();
    list.replaceChildren();
    if (shown.length) {
      list.append(rows, h('div', { class: 'footnote' }, t('prod.count', { n: tn('prod.n', shown.length) })));
      if (displayed < shown.length) list.append(more);
    }
    if (shared.length) {
      list.append(h('h2', { class: 'section-title' }, t('prod.shared_catalog')),
        h('div', { class: 'list tiles prod-list' }, shared.map((p) => row(p, true))));
    }
  }

  // Напитки: готовый каталог с калориями на 100 мл; тап открывает ввод напитка с этим выбором.
  async function loadDrinks() {
    const q = search.value.trim().toLowerCase();
    if (!list.children.length) list.replaceChildren(skeletonCards(3, 64));
    try {
      const { drinks } = await api.get('/drinks');
      if (!ctx.alive() || cat !== DRINKS || q !== search.value.trim().toLowerCase()) return;
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
      const { products, catalog: matches = [] } = await api.get('/products', { q, catalog: q.length >= 2 ? 1 : undefined });
      if (!ctx.alive() || cat === DRINKS || q !== search.value.trim()) return;
      const grouped = (p) => ({ ...p, group: foodGroupOf(p.name, p.icon, p.brand) });
      all = products.map(grouped);
      catalog = matches.map(grouped);
      if (!products.length && !matches.length) {
        paint();
        list.replaceChildren(q ? emptyState('search', t('common.not_found'), t('common.try_other'))
          : emptyState('package', t('prod.empty_title'), t('prod.empty_text')));
        return;
      }
      paint();
    } catch (e) { if (ctx.alive() && cat !== DRINKS && q === search.value.trim()) errorState(list, e, load); }
  }
  await load();
  return () => observer?.disconnect();
}
