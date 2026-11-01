// Screen 5: Menu
import { api } from '../api.js';
import { h, icon, tile, nav, pageHead, fmtN } from '../ui.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  let me = null;
  try { me = await api.me(); } catch (e) { /* header only */ }
  if (!ctx.alive()) return;

  const item = (ic, color, title, sub, path) => h('button', { class: 'row', type: 'button', onclick: () => nav.go(path) },
    tile(ic, color),
    h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), sub ? h('span', { class: 'row-sub' }, sub) : null),
    icon('next', 18));

  const name = me && me.user && me.user.first_name;
  const w = me && me.weight;
  view.append(...[
    pageHead('Меню', { back: false }),
    name ? h('div', { class: 'card profile' }, h('div', { class: 'avatar', 'aria-hidden': 'true' }, name.slice(0, 1).toUpperCase()),
      h('div', null, h('div', { class: 'row-title' }, name), h('div', { class: 'muted small' }, w && w.latest ? `Вес ${fmtN(w.latest)} кг` + (me.user.goal_weight_kg ? `, цель ${fmtN(me.user.goal_weight_kg)} кг` : '') : 'Дневник питания'))) : null,
    h('div', { class: 'list tiles' },
      item('package', 'orange', 'Мои продукты', 'Свои продукты с КБЖУ', '/menu/products'),
      item('soup', 'teal', 'Моя посуда', 'Помогает оценивать порции', '/menu/dishes')),
    h('div', { class: 'list tiles' },
      item('target', 'red', 'Цели', 'Норма калорий, БЖУ и воды', '/menu/goals'),
      item('file-export', 'green', 'Экспорт CSV', 'Приёмы, вес, вода, сон, активность', '/menu/export')),
    h('div', { class: 'list tiles' }, item('info-circle', 'blue', 'О приложении', null, '/menu/about'))].filter(Boolean));
}
