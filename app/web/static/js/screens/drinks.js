// Напитки: каталог по группам; тап открывает шторку порции. Вода идёт в /water, напиток из каталога в /drinks,
// свой напиток записывается приёмом из одной позиции (/meals/manual).
import { api } from '../api.js';
import { h, toast, haptic, nav, mainButton, pageHead, errorState, skeletonBlocks, reloadRoute } from '../ui.js';
import { loadDrinks, drinkGrid, portionSheet, customDrinkSheet, drinkEntry, drinkName } from '../drink_picker.js';
import { toPayload } from '../food_picker.js';
import { t } from '../i18n.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' }, skeletonBlocks([64, 180, 120]));
  view.append(pageHead(t('add.drinks'), { parent: '/today' }), body);
  root.append(view);
  mainButton.hide();

  let catalog;
  try { catalog = await loadDrinks(); } catch (e) {
    if (ctx.alive()) errorState(body, e, reloadRoute);
    return;
  }
  if (!ctx.alive()) return;

  let busy = false;
  async function save(c) {
    if (!c || busy || !ctx.alive()) return;
    busy = true;
    try {
      const d = c.drink;
      let msg;
      if (d.id === 'water') {
        const r = await api.post('/water', { ml: c.ml });
        msg = t('drink.water_day', { a: r.water_ml, b: r.goal_ml });
      } else if (d.id === 'custom') {
        await api.post('/meals/manual', { items: [toPayload(drinkEntry(c))], type: 'snack' });
        msg = t('drink.drink_kcal', { n: d.name, k: Math.round(d.kcal_100 * c.ml / 100) });
      } else {
        const r = await api.post('/drinks', { drink: d.id, ml: c.ml, sugar_tsp: c.sugar });
        msg = t('drink.drink_kcal', { n: drinkName(c), k: r.kcal });
      }
      haptic('success');
      toast(msg, 'ok');
      nav.replace('/today');
    } catch (e) {
      busy = false;
      toast(e.message, 'error');
    }
  }
  const pick = async (d) => save(await portionSheet(d));

  body.replaceChildren(
    drinkGrid(catalog, pick, async () => save(await customDrinkSheet())),
    h('p', { class: 'muted small drink-note' }, t('drink.note')));

  // Переход из «Мои продукты → Напитки»: сразу открыта порция нужного напитка.
  const want = sessionStorage.getItem('drink-pick');
  sessionStorage.removeItem('drink-pick');
  const wanted = want && catalog.find((d) => d.id === want);
  if (wanted) pick(wanted);
}
