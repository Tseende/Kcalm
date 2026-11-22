import { api } from './api.js';
import { loadDrinks } from './drink_picker.js';
import { foodLine } from './food_picker.js';
import { t } from './i18n.js';
import { drinkPic, foodPic } from './pics.js';
import { h, haptic, icon, sectionTitle, tile } from './ui.js';

const QUICK_SHOWN = 3;
const FOLD = (value) => value.toLowerCase().replaceAll('ё', 'е');

export function mealCatalog({ alive, canRepeat, repeatRow, addFood, addDrink, addManual, addCustomDrink }) {
  let items = [];
  let drinks = [];
  let drinkError = '';
  let repeat = canRepeat();
  let tab = 'freq';
  try {
    const saved = localStorage.getItem('addmeal_quick_tab');
    if (['freq', 'mine', 'drinks', 'products'].includes(saved)) tab = saved;
  } catch (e) { /* без хранилища */ }
  let expanded = false;
  let foods = [];
  let foodError = '';
  let loadingFoods = false;
  let request = 0;
  let timer = null;
  const query = h('input', {
    type: 'search', placeholder: t('add.catalog_search'), 'aria-label': t('add.catalog_search'),
    autocomplete: 'off', enterkeyhint: 'search', oninput: search,
  });
  const tabs = h('div', { class: 'chips scroll', role: 'tablist', 'aria-label': t('add.catalog') });
  const result = h('div', { class: 'stack-sm', 'aria-live': 'polite' });
  const element = h('div', { class: 'stack-sm meal-catalog' },
    h('label', { class: 'meal-catalog-search' }, icon('search', 20), query), tabs, result);

  function entry(name, subtitle, picture, onClick) {
    return h('button', { type: 'button', class: 'row row-btn meal-catalog-row', onclick: () => {
      query.blur();
      onClick();
    } }, picture, h('span', { class: 'grow' }, h('span', { class: 'row-title' }, name),
      subtitle ? h('span', { class: 'row-sub' }, subtitle) : null),
    h('span', { class: 'meal-catalog-plus', 'aria-hidden': 'true' }, icon('plus', 18)));
  }

  const productRow = (food) => entry(food.name, foodLine(food), foodPic(food.name, 36) || tile('package', 'orange', 36), () => addFood(food));
  const drinkRow = (drink) => entry(drink.name,
    drink.last_ml ? t('add.last_volume', { n: drink.last_ml })
      : drink.kcal_100 > 0 ? `${drink.kcal_100} ${t('unit.kcal')} ${t('drink.per100ml')}` : t('drink.no_kcal'),
    drinkPic(drink.id, 36) || tile('cup', drink.color, 36), () => addDrink(drink));
  const list = (rows) => h('div', { class: 'list tiles prod-list quick-list' }, rows);
  const link = (text, onclick) => h('button', { class: 'an-more quick-more', type: 'button', onclick }, text);
  const hint = (text) => h('div', { class: 'hint small pad' }, text);

  function catalogRows(groups) {
    const nodes = [];
    let remaining = expanded ? Infinity : QUICK_SHOWN;
    let hidden = 0;
    for (const [title, entries, makeRow] of groups) {
      const visible = entries.slice(0, remaining);
      hidden += entries.length - visible.length;
      remaining -= visible.length;
      if (!visible.length) continue;
      if (title) nodes.push(sectionTitle(title));
      nodes.push(list(visible.map(makeRow)));
    }
    if (hidden) nodes.push(link(t('add.show_more', { n: hidden }), () => {
      expanded = true;
      paint();
    }));
    return nodes;
  }

  function paint() {
    const options = [
      ...(repeat ? [['freq', t('add.frequent')], ['mine', t('fav.title')]] : []),
      ['drinks', t('add.drinks')], ['products', t('add.catalog_products')],
    ];
    if (!options.some(([key]) => key === tab)) tab = 'products';
    const raw = query.value.trim();
    const needle = FOLD(raw);
    tabs.hidden = !!raw;
    if (!tabs.hidden) tabs.replaceChildren(...options.map(([key, title]) => h('button', {
      type: 'button', role: 'tab', class: 'chip' + (key === tab ? ' active' : ''), 'aria-selected': String(key === tab),
      onclick: () => {
        if (tab === key) return;
        tab = key;
        try { localStorage.setItem('addmeal_quick_tab', key); } catch (e) { /* без хранилища */ }
        haptic('selection');
        search();
      },
    }, title)));

    if (raw || tab === 'products') {
      const matchingDrinks = raw ? drinks.filter((drink) => FOLD(drink.name).includes(needle)) : [];
      const matchingDishes = raw && repeat ? items.filter((item) => !item.is_drink &&
        [item.title, ...(item.names || [])].some((name) => FOLD(name).includes(needle))) : [];
      const status = foodError || (loadingFoods ? t('lbl.searching')
        : raw.length === 1 && !foods.length ? t('fp.type_more')
          : !foods.length && !matchingDrinks.length && !matchingDishes.length ? (raw ? t('add.none_found') : t('fp.type_more')) : '');
      result.replaceChildren(
        ...catalogRows([
          [t('add.catalog_products'), foods, productRow],
          [t('add.drinks'), matchingDrinks, drinkRow],
          [t('fav.title'), matchingDishes, repeatRow],
        ]),
        ...(status ? [hint(status)] : []),
        link(t('fp.manual'), () => { query.blur(); addManual(raw); }));
    } else if (tab === 'drinks') {
      const recent = drinks.filter((drink) => drink.last_ml);
      const pinned = new Set(recent.map((drink) => drink.id));
      const rest = drinks.filter((drink) => !pinned.has(drink.id));
      const catalogNames = new Set(drinks.map((drink) => FOLD(drink.name)));
      const recorded = repeat ? items.filter((item) => {
        if (!item.is_drink) return false;
        const [name, ...others] = item.names || [];
        const base = FOLD(name || item.title).replace(/\s*[, +]\s*\d+\s*ч\.\s*л\.\s*сахара$/, '');
        return item.favorite_id || others.length > 0 || !catalogNames.has(base);
      }) : [];
      result.replaceChildren(
        ...catalogRows([
          [t('add.frequent_drinks'), recent, drinkRow],
          [t('add.all_drinks'), rest, drinkRow],
          [t('add.recorded_drinks'), recorded, repeatRow],
        ]),
        ...(drinkError ? [hint(drinkError)] : []),
        link(t('drink.custom_title'), addCustomDrink));
    } else {
      const dishes = items.filter((item) => !item.is_drink && !!item.favorite_id === (tab === 'mine'));
      result.replaceChildren(...(dishes.length ? catalogRows([[null, dishes, repeatRow]]) : [hint(t('add.no_quick_meals'))]));
    }
  }

  function search() {
    expanded = false;
    clearTimeout(timer);
    const current = ++request;
    const raw = query.value.trim();
    foods = [];
    foodError = '';
    loadingFoods = (raw.length >= 2 || (!raw && tab === 'products'));
    paint();
    if (!loadingFoods) return;
    timer = setTimeout(async () => {
      try {
        const data = await api.get('/foods/search', { q: raw, limit: 30 });
        if (current === request) foods = data.foods || [];
      } catch (e) {
        if (current === request) foodError = e.message;
      }
      if (current !== request || !alive()) return;
      loadingFoods = false;
      paint();
    }, raw ? 250 : 0);
  }

  function update() {
    if (canRepeat() === repeat) return;
    repeat = canRepeat();
    if (!repeat && (tab === 'freq' || tab === 'mine')) {
      tab = 'products';
      search();
    } else paint();
  }

  api.get('/frequent', { limit: 60 }).then((data) => {
    if (alive()) { items = data.items || []; paint(); }
  }).catch(() => { /* каталог доступен независимо от истории еды */ });
  loadDrinks({ ranked: true }).then((data) => {
    if (alive()) { drinks = data; paint(); }
  }).catch((e) => {
    if (alive()) { drinkError = e.message; paint(); }
  });
  if (!repeat && !['drinks', 'products'].includes(tab)) tab = 'products';
  search();
  return { element, update, destroy: () => { clearTimeout(timer); request++; } };
}
