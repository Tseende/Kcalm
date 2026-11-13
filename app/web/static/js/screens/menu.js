// Screen 5: Menu
import { api, tg } from '../api.js';
import { h, icon, tile, nav, fmtN, skeletonBlocks, sheet, haptic, errorState, staggerIn, getThemePref, setThemePref } from '../ui.js';
import { t, langPref, setLang } from '../i18n.js';

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

// Оформление: строка как в iOS («Как в системе» / «Светлое» / «Тёмное») и лист выбора. «Как в системе» снимает ключ 'theme'.
const THEMES = [[null, t('menu.theme_sys')], ['light', t('menu.theme_light')], ['dark', t('menu.theme_dark')]];
const LANGS = [['auto', t('lang.auto')], ['ru', 'Русский'], ['en', 'English']];   // названия языков не переводим
function themeRow() {
  const label = () => THEMES.find((x) => x[0] === getThemePref())[1];
  const val = h('span', { class: 'row-value' }, label());
  const open = () => {
    const rows = THEMES.map(([v, text]) => h('button', { class: 'row', type: 'button', role: 'radio', 'aria-checked': String(v === getThemePref()), onclick: () => {
      setThemePref(v); haptic('selection'); val.textContent = label(); s.close();
    } }, h('span', { class: 'grow' }, h('span', { class: 'row-title' }, text)), v === getThemePref() ? h('span', { class: 'row-check' }, icon('check', 20)) : null));
    const langRows = LANGS.map(([v, text]) => h('button', { class: 'row', type: 'button', role: 'radio', 'aria-checked': String(v === langPref()), onclick: () => {
      if (v === langPref()) return;
      haptic('selection'); setLang(v);                  // сохраняет выбор и перезагружает приложение
    } }, h('span', { class: 'grow' }, h('span', { class: 'row-title' }, text)), v === langPref() ? h('span', { class: 'row-check' }, icon('check', 20)) : null));
    const s = sheet({ title: t('menu.appearance'), content: h('div', { class: 'stack' },
      h('div', { class: 'list', role: 'radiogroup', 'aria-label': t('menu.theme') }, rows),
      h('div', { class: 'section-label' }, t('lang.title')),
      h('div', { class: 'list', role: 'radiogroup', 'aria-label': t('lang.title') }, langRows)) });
  };
  return h('button', { class: 'row', type: 'button', onclick: open }, tile('moon', 'indigo'),
    h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('menu.appearance'))), val, icon('next', 18));
}

// Карточка профиля: аватар, имя и прогресс к цели «старт → сейчас → цель».
function profileCard(me, name) {
  const w = me.weight || {};
  const goal = me.user && me.user.goal_weight_kg;
  const now = w.trend != null ? w.trend : w.latest;
  const start = w.start;
  let text = t('menu.profile');
  let bar = null;
  let sub2 = null;
  if (now != null && goal) {
    const left = Math.abs(now - goal);
    const losing = start != null ? goal < start : goal < now;
    if (start != null && Math.abs(start - goal) > 0.05) {
      const total = Math.abs(start - goal);
      const done = Math.max(0, Math.min(total, losing ? start - now : now - start));
      const pct = Math.round((done / total) * 100);
      text = t('menu.prog', { s: losing ? '−' : '+', a: fmtN(done), b: fmtN(total), left: fmtN(left) });
      sub2 = `${fmtN(now)} → ${fmtN(goal)} ${t('unit.kg')}`;
      const fill = h('i', { style: { width: pct + '%' } });
      bar = h('div', { class: 'bar bar-goal', role: 'progressbar', 'aria-label': t('stats.goal_progress'), 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': pct }, fill);
    } else {
      text = t('menu.left_kg', { v: fmtN(left) });
      sub2 = `${fmtN(now)} → ${fmtN(goal)} ${t('unit.kg')}`;
    }
  } else if (now != null) {
    text = t('menu.weight_now', { v: fmtN(now) });
  }
  return h('button', { class: 'card profile row-btn', type: 'button', onclick: () => nav.go('/menu/profile') }, avatarEl(name),
    h('div', { class: 'grow' }, h('div', { class: 'row-title' }, name), bar, h('div', { class: 'muted small profile-sub' }, text), sub2 ? h('div', { class: 'muted small profile-sub' }, sub2) : null), icon('next', 18));
}

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' });
  view.append(body);
  root.append(view);

  async function load() {
    body.replaceChildren(skeletonBlocks([88, 132, 192, 192]));
    let me = null;
    try { me = await api.me(); } catch (e) {
      if (ctx.alive()) errorState(body, e, load);
      return;
    }
    if (!ctx.alive()) return;
    draw(me);
  }

  const item = (ic, color, title, sub, path) => h('button', { class: 'row', type: 'button', onclick: () => nav.go(path) },
    tile(ic, color),
    h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), sub ? h('span', { class: 'row-sub' }, sub) : null),
    icon('next', 18));

  function draw(me) {
    const name = me && me.user && me.user.first_name;
    const kids = [
      name ? profileCard(me, name) : null,
      h('div', { class: 'list tiles' },
        item('target', 'red', t('menu.profile'), t('menu.profile_sub'), '/menu/profile'),
        item('bell', 'purple', t('menu.reminders'), t('menu.reminders_sub'), '/menu/reminders')),
      h('div', { class: 'list tiles' },
        item('star', 'yellow', t('menu.favorites'), t('menu.favorites_sub'), '/menu/favorites'),
        item('package', 'orange', t('menu.products'), t('menu.products_sub'), '/menu/products'),
        item('soup', 'teal', t('menu.dishes'), t('menu.dishes_sub'), '/menu/dishes')),
      h('div', { class: 'list tiles' },
        themeRow(),
        item('file-export', 'green', t('menu.data'), t('menu.data_sub'), '/menu/export'),
        item('help-circle', 'blue', t('menu.help'), t('menu.help_sub'), '/menu/help'))].filter(Boolean);
    body.replaceChildren(...kids);
    staggerIn(body, kids);
  }

  await load();
}
