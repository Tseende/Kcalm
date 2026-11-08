// "Моя посуда" list
import { api } from '../api.js';
import { dishPic } from '../pics.js';
import { h, icon, tile, nav, pageHead, skeletonCards, emptyState, errorState, photoImg, dishKind, fmtN } from '../ui.js';
import { t } from '../i18n.js';

export function dishInfo(d) {
  const parts = [];
  if (d.diameter_cm) parts.push('Ø' + fmtN(d.diameter_cm) + ' ' + t('unit.cm'));
  if (d.depth_cm) parts.push(t('dish.depth') + ' ' + fmtN(d.depth_cm) + ' ' + t('unit.cm'));
  if (d.volume_ml) parts.push(fmtN(d.volume_ml) + ' ' + t('unit.ml'));
  return parts.join(' · ') || dishKind(d.kind).label;
}

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const list = h('div', { class: 'stack' });
  view.append(
    pageHead(t('dishes.title'), { parent: '/menu', right: h('button', { class: 'icon-btn accent', type: 'button', 'aria-label': t('dishes.add'), onclick: () => nav.go('/menu/dish/new') }, icon('plus')) }),
    h('div', { class: 'hint-card small' }, icon('tools-kitchen-2', 18), h('span', null, t('dishes.hint'))),
    list);
  root.append(view);

  async function load() {
    list.replaceChildren(skeletonCards(3, 64));
    try {
      const { dishes } = await api.get('/dishes');
      if (!ctx.alive()) return;
      if (!dishes.length) {
        list.replaceChildren(emptyState('soup', t('dishes.empty_title'), t('dishes.empty_text'),
          h('button', { class: 'btn btn-primary', type: 'button', onclick: () => nav.go('/menu/dish/new') }, t('dishes.add'))));
        return;
      }
      list.replaceChildren(h('div', { class: 'list tiles' }, dishes.map((d) => h('button', { class: 'row', type: 'button', onclick: () => nav.go('/menu/dish/' + d.id) },
        d.photo_url ? h('div', { class: 'thumb sm' }, photoImg(d.photo_url, '', d.name)) : (dishPic(d.kind, 40) || tile(dishKind(d.kind).icon, dishKind(d.kind).color)),
        h('span', { class: 'grow' },
          h('span', { class: 'row-title' }, d.name, d.is_default ? h('span', { class: 'badge' }, t('dishes.default')) : null),
          h('span', { class: 'row-sub' }, dishKind(d.kind).label + ' · ' + dishInfo(d))),
        icon('next', 18)))));
    } catch (e) { if (ctx.alive()) errorState(list, e, load); }
  }
  await load();
}
