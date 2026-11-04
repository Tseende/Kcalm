// Screen 5: Menu
import { api, tg } from '../api.js';
import { h, icon, tile, nav, pageHead, fmtN } from '../ui.js';

// Фото из Telegram (если пользователь его разрешил), иначе первая буква имени
function avatarEl(name) {
  const letter = name.slice(0, 1).toUpperCase();
  const url = tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.photo_url;
  const box = h('div', { class: 'avatar', 'aria-hidden': 'true' }, letter);
  if (url) {
    const img = h('img', { src: url, alt: '', referrerpolicy: 'no-referrer' });
    img.onload = () => box.replaceChildren(img);
    img.onerror = () => { /* оставляем букву */ };
  }
  return box;
}

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
    name ? h('button', { class: 'card profile row-btn', type: 'button', onclick: () => nav.go('/menu/profile') }, avatarEl(name),
      h('div', { class: 'grow' }, h('div', { class: 'row-title' }, name), h('div', { class: 'muted small' }, w && w.latest ? `Вес ${fmtN(w.latest)} кг` + (me.user.goal_weight_kg ? `, цель ${fmtN(me.user.goal_weight_kg)} кг` : '') : 'Профиль и цели')), icon('next', 18)) : null,
    h('div', { class: 'list tiles' },
      item('target', 'red', 'Профиль и цели', 'Возраст, рост, вес, норма калорий', '/menu/profile'),
      item('bell', 'purple', 'Напоминания', 'Вода, вес, итоги дня', '/menu/reminders')),
    h('div', { class: 'list tiles' },
      item('package', 'orange', 'Мои продукты', 'Свои продукты и напитки', '/menu/products'),
      item('soup', 'teal', 'Моя посуда', 'Помогает оценивать порции', '/menu/dishes'),
      item('star', 'yellow', 'Избранное', 'Приёмы в одно касание', '/menu/favorites')),
    h('div', { class: 'list tiles' },
      item('moon', 'indigo', 'Внешний вид', 'Тема: светлая или тёмная', '/menu/appearance'),
      item('file-export', 'green', 'Данные', 'Экспорт CSV и удаление', '/menu/export')),
    h('div', { class: 'list tiles' },
      item('help-circle', 'blue', 'Помощь', 'Как пользоваться', '/menu/help'),
      item('info-circle', 'gray', 'О приложении', null, '/menu/about'))].filter(Boolean));
}
