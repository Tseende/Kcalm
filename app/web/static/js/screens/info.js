// Menu sub-pages: data (export, delete), appearance, help, about
import { api, downloadCsv, isMock, inTelegram, tg } from '../api.js';
import { h, icon, tile, toast, haptic, nav, pageHead, segmented, sheet, group, frow } from '../ui.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  const page = ctx.params.page;
  if (page === 'goals') { nav.replace('/menu/profile'); return undefined; }
  if (page === 'export') return exportPage(view);
  if (page === 'appearance') return appearance(view);
  if (page === 'help') return help(view);
  return about(view);
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
  view.append(pageHead('Данные', { parent: '/menu' }),
    h('div', { class: 'section-label' }, 'Экспорт CSV'),
    h('div', { class: 'list tiles' }, kinds.map(([k, ic, col, t]) => h('button', { class: 'row', type: 'button', onclick: () => exportKind(k) },
      tile(ic, col), h('span', { class: 'grow row-title' }, t), icon('download', 20)))),
    h('div', { class: 'footnote' }, inTelegram ? 'Откроется окно «Поделиться»: сохраните файл в «Файлы» или отправьте себе.' : 'Файл скачается в браузере.'),
    h('div', { class: 'section-label' }, 'Удаление'),
    h('div', { class: 'list tiles' }, h('button', { class: 'row', type: 'button', onclick: confirmDelete },
      tile('trash', 'red'), h('span', { class: 'grow' }, h('span', { class: 'row-title danger' }, 'Удалить мои данные'), h('span', { class: 'row-sub' }, 'Аккаунт, приёмы, вес, продукты и настройки')))),
    h('div', { class: 'footnote' }, 'Удаление необратимо. Перед ним сохраните экспорт.'));
}

function confirmDelete() {
  const inp = h('input', { type: 'text', placeholder: 'УДАЛИТЬ', autocomplete: 'off', autocapitalize: 'characters', 'aria-label': 'Подтверждение' });
  const go = h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', disabled: true, onclick: async () => {
    go.disabled = true;
    try {
      await api.del('/me', { confirm: 'УДАЛИТЬ' });
      s.close();
      toast('Данные удалены', 'ok');
      setTimeout(() => { if (inTelegram && tg && tg.close) tg.close(); else location.reload(); }, 900);
    } catch (e) { go.disabled = false; toast(e.message, 'error'); }
  } }, 'Удалить навсегда');
  inp.addEventListener('input', () => { go.disabled = inp.value.trim().toUpperCase() !== 'УДАЛИТЬ'; });
  const s = sheet({ title: 'Удалить все данные?', content: h('div', { class: 'stack' },
    h('div', { class: 'muted' }, 'Будут удалены приёмы пищи, вес, вода, сон, активность, свои продукты, посуда и настройки. Чтобы продолжить, введите слово УДАЛИТЬ.'),
    inp, go) });
}

const THEMES = [['auto', 'Как в системе'], ['light', 'Светлая'], ['dark', 'Тёмная']];
function appearance(view) {
  let cur = 'auto';
  try { cur = localStorage.getItem('theme') || 'auto'; } catch (e) { /* storage недоступен */ }
  const seg = segmented(THEMES, cur, (v) => {
    try { if (v === 'auto') localStorage.removeItem('theme'); else localStorage.setItem('theme', v); } catch (e) { /* без сохранения */ }
    window.dispatchEvent(new Event('app:theme'));
    haptic('light');
  }, 'Тема');
  view.append(pageHead('Внешний вид', { parent: '/menu' }),
    h('div', { class: 'section-label' }, 'Тема'), seg,
    h('div', { class: 'footnote' }, 'По умолчанию тема берётся из Telegram или системы. Выбор запоминается на этом устройстве.'));
}

function help(view) {
  const step = (ic, color, title, text) => h('div', { class: 'row static' }, tile(ic, color), h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), h('span', { class: 'row-sub wrap' }, text)));
  view.append(pageHead('Помощь', { parent: '/menu' }),
    h('div', { class: 'list tiles' },
      step('camera', 'orange', 'Записать еду', 'Нажмите «+» и сфотографируйте тарелку, упаковку или этикетку. Можно описать словами.'),
      step('barcode', 'green', 'Уточнить состав', 'В карточке блюда добавьте этикетку, штрихкод или продукт из «Моих продуктов».'),
      step('soup', 'teal', 'Выбрать посуду', 'Размер тарелки помогает точнее оценить порцию. Добавьте свою посуду в меню.'),
      step('target', 'red', 'Цели', 'Норму калорий и БЖУ считает приложение по профилю. Измените данные, и она пересчитается.'),
      step('bell', 'purple', 'Напоминания', 'Бот пришлёт сообщение в чат в выбранное время: вода, вес, итоги дня.')),
    h('div', { class: 'footnote' }, 'Калории и КБЖУ по фото оценочные: смотрите на диапазон, а не на одно число. Приложение не заменяет врача или диетолога. Вопрос или ошибка? Напишите боту в чате.'));
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
    h('div', { class: 'footnote' }, 'Калории и КБЖУ по фото оценочные: смотрите на диапазон, а не на одно число.'));
}
