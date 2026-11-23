// Screen 5: Menu
import { api, tg } from '../api.js';
import { h, icon, tile, nav, fmtN, skeletonBlocks, sheet, checkList, toggle, sectionTitle, vibrationEnabled, setVibrationEnabled, errorState, staggerIn, getThemePref, setThemePref, THEMES } from '../ui.js';
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

const THEME_OPTIONS = [[null, t('menu.theme_sys')], ...THEMES.map((theme) => [theme, t('menu.theme_' + theme)])];
const LANGS = [['auto', t('lang.auto')], ['ru', 'Русский'], ['en', 'English']];   // названия языков не переводим
function preferenceRow(title, glyph, color, options, getValue, setValue) {
  const currentLabel = () => options.find(([value]) => value === getValue())[1];
  const val = h('span', { class: 'row-value' }, currentLabel());
  const open = () => {
    const choices = options.map(([value, text]) => [value, value === 'cosmic'
      ? h('span', {}, h('span', { class: 'theme-swatch', 'aria-hidden': 'true' }), ' ', text) : text]);
    const content = checkList(choices, getValue(), value => {
      dialog.close();
      if (value !== getValue()) setValue(value);
      val.textContent = currentLabel();
    }, title);
    const dialog = sheet({ title, content });
  };
  return h('button', { class: 'row appearance-row', type: 'button', onclick: open }, tile(glyph, color),
    h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title)), val, icon('next', 18));
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
      const fill = h('i', { style: { transform: `scaleX(${pct / 100})` } });
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
      sectionTitle(t('menu.personal')),
      h('div', { class: 'list tiles' },
        item('target', 'red', t('menu.profile'), t('menu.profile_sub'), '/menu/profile'),
        item('bell', 'purple', t('menu.reminders'), t('menu.reminders_sub'), '/menu/reminders'),
        item('user-plus', 'purple', t('ref.title'), t('ref.subtitle'), '/menu/referral')),
      sectionTitle(t('menu.kitchen')),
      h('div', { class: 'list tiles' },
        item('tools-kitchen-2', 'yellow', t('menu.favorites'), t('menu.favorites_sub'), '/menu/favorites'),
        item('basket', 'orange', t('menu.products'), t('menu.products_sub'), '/menu/products'),
        item('bowl', 'teal', t('menu.dishes'), t('menu.dishes_sub'), '/menu/dishes')),
      sectionTitle(t('menu.interface')),
      h('div', { class: 'list tiles' },
        preferenceRow(t('menu.language'), 'language', 'blue', LANGS, langPref, setLang),
        preferenceRow(t('menu.theme'), 'sun-moon', 'indigo', THEME_OPTIONS, getThemePref, setThemePref),
        h('div', { class: 'row' }, tile('device-mobile-vibration', 'teal'),
          h('span', { class: 'grow' }, t('menu.vibration')),
          toggle(vibrationEnabled(), setVibrationEnabled, t('menu.vibration')))),
      sectionTitle(t('menu.data_help')),
      h('div', { class: 'list tiles' },
        item('file-export', 'green', t('menu.data'), t('menu.data_sub'), '/menu/export'),
        item('help-circle', 'blue', t('menu.help'), t('menu.help_sub'), '/menu/help')),
    ].filter(Boolean);
    body.replaceChildren(...kids);
    staggerIn(body, kids);
  }

  await load();
}
