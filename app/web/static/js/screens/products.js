// "Мои продукты" list
import { api } from '../api.js';
import { foodPic } from '../pics.js';
import { h, icon, tile, nav, pageHead, skeletonCards, emptyState, errorState, debounce, fmtN } from '../ui.js';

const PALETTE = ['blue', 'green', 'orange', 'purple', 'teal', 'pink', 'red'];

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const list = h('div', { class: 'stack' });
  const search = h('input', { type: 'search', placeholder: 'Поиск по названию или бренду', 'aria-label': 'Поиск продуктов', autocomplete: 'off', oninput: debounce(() => load(), 250) });
  view.append(
    pageHead('Мои продукты', { parent: '/menu', right: h('button', { class: 'icon-btn accent', type: 'button', 'aria-label': 'Добавить продукт', onclick: () => nav.go('/menu/product/new') }, icon('plus')) }),
    h('div', { class: 'search' }, icon('search', 18), search),
    h('div', { class: 'row-btns' },
      h('button', { class: 'btn btn-secondary btn-sm', type: 'button', onclick: () => nav.go('/menu/product/new') }, icon('edit', 18), 'Вручную'),
      h('button', { class: 'btn btn-secondary btn-sm', type: 'button', onclick: () => nav.go('/add/label') }, icon('camera', 18), 'По этикетке')),
    list);
  root.append(view);

  async function load() {
    const q = search.value.trim();
    if (!list.children.length) list.replaceChildren(skeletonCards(3, 64));
    try {
      const { products } = await api.get('/products', { q });
      if (!ctx.alive() || q !== search.value.trim()) return;
      if (!products.length) {
        list.replaceChildren(q ? emptyState('search', 'Ничего не найдено', 'Попробуйте другое название.')
          : emptyState('package', 'Своих продуктов пока нет', 'Сохраняйте продукты с этикетки или из приёма пищи: они сразу будут учитываться при распознавании.'));
        return;
      }
      list.replaceChildren(h('div', { class: 'list tiles' }, products.map((p) => h('button', { class: 'row', type: 'button', onclick: () => nav.go('/menu/product/' + p.id) },
        foodPic(p.name) || tile('package', PALETTE[Math.abs(Number(p.id) || 0) % PALETTE.length]),
        h('span', { class: 'grow' },
          h('span', { class: 'row-title' }, p.name, p.brand ? h('span', { class: 'muted' }, ' · ' + p.brand) : null),
          h('span', { class: 'row-sub' }, `${fmtN(p.kcal)} ккал · Б ${fmtN(p.protein)} · Ж ${fmtN(p.fat)} · У ${fmtN(p.carbs)} на 100 г`)),
        icon('next', 18)))));
    } catch (e) { if (ctx.alive()) errorState(list, e, load); }
  }
  await load();
}
