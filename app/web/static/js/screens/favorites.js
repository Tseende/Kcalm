// Избранное: приёмы, которые можно записать одним касанием на экране «Добавить».
import { api } from '../api.js';
import { h, icon, toast, haptic, pageHead, emptyState, errorState, skeletonCards, plural } from '../ui.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonCards(3, 64));
  view.append(pageHead('Избранное', { parent: '/menu' }), box);
  root.append(view);
  let items;
  try { items = ((await api.get('/frequent', { limit: 50 })).items || []).filter((x) => x.favorite_id); } catch (e) { if (ctx.alive()) errorState(box, e, () => render(root, ctx)); return; }
  if (!ctx.alive()) return;

  function paint() {
    if (!items.length) { box.replaceChildren(emptyState('star', 'Избранного пока нет', 'Откройте приём пищи и нажмите звезду: он появится здесь и на экране «Добавить».')); return; }
    box.replaceChildren(h('div', { class: 'list tiles' }, items.map((f) => h('div', { class: 'row' },
      h('span', { class: 'grow fav-text' }, h('span', { class: 'row-title' }, f.title), h('span', { class: 'row-sub' }, `${f.kcal} ккал`)),
      h('button', { class: 'icon-btn danger', type: 'button', 'aria-label': `Убрать из избранного: ${f.title}`, onclick: async () => {
        try { await api.del('/favorites/' + f.favorite_id); items = items.filter((x) => x !== f); haptic('light'); toast('Убрано из избранного'); paint(); } catch (e) { toast(e.message, 'error'); }
      } }, icon('trash', 20))))),
    h('div', { class: 'footnote' }, `В избранном: ${plural(items.length, 'приём', 'приёма', 'приёмов')}`));
  }
  paint();
}
