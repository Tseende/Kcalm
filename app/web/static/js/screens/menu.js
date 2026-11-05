// Screen 5: Menu
import { api, tg, inTelegram } from '../api.js';
import { h, icon, tile, nav, pageHead, fmtN, toggle, haptic } from '../ui.js';

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

// Тёмная тема одним переключателем. Пока человек не трогал его, работает тема Telegram/системы;
// ручной выбор запоминается только если он отличается от системной темы.
function systemDark() {
  if (inTelegram && tg && tg.colorScheme) return tg.colorScheme === 'dark';
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}
function themeRow() {
  let pref = null;
  try { pref = localStorage.getItem('theme'); } catch (e) { /* без хранилища */ }
  const manual = pref === 'dark' || pref === 'light';
  const dark = manual ? pref === 'dark' : systemDark();
  const sub = h('span', { class: 'row-sub' }, manual ? 'Выбрано вручную' : 'Как в системе');
  const sw = toggle(dark, (on) => {
    try {
      if (on === systemDark()) { localStorage.removeItem('theme'); sub.textContent = 'Как в системе'; }
      else { localStorage.setItem('theme', on ? 'dark' : 'light'); sub.textContent = 'Выбрано вручную'; }
    } catch (e) { /* без сохранения */ }
    window.dispatchEvent(new Event('app:theme'));
    haptic('light');
  }, 'Тёмная тема');
  return h('div', { class: 'row static' }, tile('moon', 'indigo'),
    h('span', { class: 'grow' }, h('span', { class: 'row-title' }, 'Тёмная тема'), sub), sw);
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
      themeRow(),
      item('file-export', 'green', 'Данные', 'Экспорт CSV и удаление', '/menu/export'),
      item('help-circle', 'blue', 'Помощь и о приложении', 'Как пользоваться, версия', '/menu/help'))].filter(Boolean));
}
