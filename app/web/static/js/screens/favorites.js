// Избранное: свои блюда одним касанием. Здесь их видно с картинкой и БЖУ, можно править и создавать новые.
import { api } from '../api.js';
import { foodPic, mealPic } from '../pics.js';
import { h, nav, mainButton, pageHead, emptyState, errorState, skeletonCards, plural, fmtN, photoImg } from '../ui.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonCards(3, 64));
  view.append(pageHead('Избранное', { parent: '/menu' }), box);
  root.append(view);
  mainButton.show('Добавить в избранное', () => nav.go('/menu/favorite/new'));
  let items;
  try { items = (await api.get('/favorites')).items || []; } catch (e) { if (ctx.alive()) errorState(box, e, () => render(root, ctx)); return; }
  if (!ctx.alive()) return;

  const macro = (cls, letter, v) => h('span', { class: 'mac ' + cls }, h('i'), `${letter} ${fmtN(v)}`);
  const thumb = (f) => (f.photo_url ? h('span', { class: 'fav-thumb' }, photoImg(f.photo_url, '', f.title))
    : (foodPic(f.title, 48) || foodPic((f.names || [])[0] || '', 48) || mealPic('snack', 48)));

  if (!items.length) {
    box.replaceChildren(emptyState('star', 'Избранного пока нет', 'Добавьте своё блюдо кнопкой ниже или нажмите звезду в записанном приёме: он появится здесь и на экране «Добавить».'));
    return;
  }
  box.replaceChildren(
    h('div', { class: 'list tiles prod-list' }, items.map((f) => h('button', { class: 'row prod-row', type: 'button', onclick: () => nav.go('/menu/favorite/' + f.id) },
      thumb(f),
      h('span', { class: 'grow' },
        h('span', { class: 'row-title' }, f.title),
        f.note ? h('span', { class: 'row-sub' }, f.note) : null,
        h('span', { class: 'macs' }, macro('p', 'Б', f.protein), macro('f', 'Ж', f.fat), macro('c', 'У', f.carbs))),
      h('span', { class: 'prod-kcal' }, h('b', null, fmtN(f.kcal)), h('span', null, 'ккал'))))),
    h('div', { class: 'footnote' }, `В избранном: ${plural(items.length, 'блюдо', 'блюда', 'блюд')} · значения на порцию`));
}
