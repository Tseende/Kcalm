// Menu sub-pages: goals (read-only), CSV export, about
import { api, downloadCsv, isMock, inTelegram, tg } from '../api.js';
import { h, icon, tile, toast, pageHead, errorState, fmtN, fmtKcal } from '../ui.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  const page = ctx.params.page;
  if (page === 'goals') return goals(view, ctx);
  if (page === 'export') return exportPage(view);
  return about(view);
}

async function goals(view, ctx) {
  view.append(pageHead('Цели', { parent: '/menu' }));
  const box = h('div', { class: 'stack' });
  view.append(box);
  try {
    const me = await api.me(true);
    if (!ctx.alive()) return;
    const t = me.targets;
    const u = me.user || {};
    const row = (title, value, ic, color) => h('div', { class: 'row static' }, tile(ic, color), h('span', { class: 'grow' }, title), h('b', null, value));
    box.append(
      h('div', { class: 'list tiles' },
        row('Калории', `${fmtKcal(t.kcal)} ккал`, 'flame', 'red'), row('Белки', `${fmtN(t.protein)} г`, 'egg-fried', 'blue'), row('Жиры', `${fmtN(t.fat)} г`, 'flask', 'orange'),
        row('Углеводы', `${fmtN(t.carbs)} г`, 'leaf', 'green'), row('Вода', `${fmtKcal(t.water_ml)} мл`, 'droplet', 'teal')),
      h('div', { class: 'list tiles' },
        u.height_cm ? row('Рост', `${fmtN(u.height_cm)} см`, 'ruler', 'indigo') : null,
        me.weight && me.weight.start ? row('Стартовый вес', `${fmtN(me.weight.start)} кг`, 'scale', 'purple') : null,
        me.weight && me.weight.latest ? row('Текущий вес', `${fmtN(me.weight.latest)} кг`, 'scale', 'blue') : null,
        me.weight && me.weight.trend ? row('Тренд веса', `${fmtN(me.weight.trend)} кг`, 'chart-bar', 'teal') : null,
        u.goal_weight_kg ? row('Цель', `${fmtN(u.goal_weight_kg)} кг`, 'target', 'pink') : null,
        u.eating_cutoff ? row('Не есть после', u.eating_cutoff, 'clock', 'gray') : null),
      h('div', { class: 'muted small center' }, 'Цели считает бот по вашим данным. Поменять их можно в чате с ботом.'));
  } catch (e) { if (ctx.alive()) errorState(box, e); }
}

async function exportKind(kind) {
  toast('Готовлю файл…');
  try {
    const r = await downloadCsv(kind);
    if (r === 'downloaded') toast('Файл сохранён', 'ok');
  } catch (e) { toast(e.message || 'Не удалось выгрузить файл', 'error'); }
}

function exportPage(view) {
  const kinds = [['meals', 'tools-kitchen-2', 'orange', 'Приёмы пищи'], ['body', 'scale', 'purple', 'Вес и замеры'], ['water', 'droplet', 'teal', 'Вода'], ['sleep', 'bed', 'indigo', 'Сон'], ['activity', 'run', 'green', 'Активность']];
  view.append(pageHead('Экспорт CSV', { parent: '/menu' }),
    h('div', { class: 'muted' }, inTelegram ? 'Откроется окно «Поделиться»: сохраните файл в «Файлы» или отправьте себе.' : 'Файл скачается в браузере.'),
    h('div', { class: 'list tiles' }, kinds.map(([k, ic, col, t]) => h('button', { class: 'row', type: 'button', onclick: () => exportKind(k) },
      tile(ic, col), h('span', { class: 'grow row-title' }, t), icon('download', 20)))));
}

function about(view) {
  view.append(pageHead('О приложении', { parent: '/menu' }),
    h('div', { class: 'card center stack-sm' },
      h('div', { class: 'about-tile' }, tile('salad', 'green', 64)),
      h('div', { class: 'row-title' }, 'Дневник питания'),
      h('div', { class: 'muted' }, 'Личный дневник похудения: фото еды, калории и БЖУ, вода, сон, шаги и вес в одном месте.')),
    h('div', { class: 'list tiles' },
      h('div', { class: 'row static' }, tile('info-circle', 'blue'), h('span', { class: 'grow' }, 'Версия'), h('span', { class: 'muted' }, '2.0')),
      h('div', { class: 'row static' }, tile('device-mobile', 'teal'), h('span', { class: 'grow' }, 'Telegram'), h('span', { class: 'muted' }, tg && tg.version ? tg.version : 'не обнаружен')),
      h('div', { class: 'row static' }, tile('package', 'orange'), h('span', { class: 'grow' }, 'Данные'), h('span', { class: 'muted' }, isMock() ? 'демо' : 'ваши'))),
    h('div', { class: 'muted small center' }, 'Калории и КБЖУ по фото оценочные: смотрите на диапазон, а не на одно число.'));
}
