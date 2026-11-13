// Menu sub-pages: data (export, delete), help + about
import { api, downloadCsv, isMock, inTelegram, tg } from '../api.js';
import { h, icon, tile, toast, nav, pageHead, sheet, haptic } from '../ui.js';
import { reset as resetCoach } from '../coach.js';
import { t } from '../i18n.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  const page = ctx.params.page;
  if (page === 'goals') { nav.replace('/menu/profile'); return undefined; }
  if (page === 'export') return exportPage(view);
  if (page === 'appearance') { nav.replace('/menu'); return undefined; }   // тема теперь переключателем в меню
  if (page === 'help') return help(view);
  return help(view);
}

async function exportKind(kind) {
  toast(t('info.preparing'));
  try {
    const r = await downloadCsv(kind);
    if (r === 'downloaded') toast(t('info.saved'), 'ok');
  } catch (e) { toast(e.message || t('info.export_fail'), 'error'); }
}

function exportPage(view) {
  const kinds = [['meals', 'tools-kitchen-2', 'orange', t('today.meals')], ['body', 'scale', 'purple', t('info.body')], ['water', 'droplet', 'teal', t('water.title')], ['sleep', 'bed', 'indigo', t('add.sleep')], ['activity', 'run', 'green', t('add.activity')]];
  view.append(pageHead(t('menu.data'), { parent: '/menu' }),
    h('div', { class: 'section-label' }, t('info.export_csv')),
    h('div', { class: 'list tiles' }, kinds.map(([k, ic, col, t]) => h('button', { class: 'row', type: 'button', onclick: () => exportKind(k) },
      tile(ic, col), h('span', { class: 'grow row-title' }, t), icon('download', 20)))),
    h('div', { class: 'footnote' }, inTelegram ? t('info.share_tg') : t('info.share_web')),
    h('div', { class: 'section-label' }, t('info.delete')),
    h('div', { class: 'list tiles' }, h('button', { class: 'row', type: 'button', onclick: confirmDelete },
      tile('trash', 'red'), h('span', { class: 'grow' }, h('span', { class: 'row-title danger' }, t('info.delete_row')), h('span', { class: 'row-sub' }, t('info.delete_sub'))))),
    h('div', { class: 'footnote' }, t('info.delete_warn')));
}

function confirmDelete() {
  const WORD = t('info.word');                        // слово для ввода; серверу всегда уходит 'УДАЛИТЬ'
  const inp = h('input', { type: 'text', placeholder: WORD, autocomplete: 'off', autocapitalize: 'characters', 'aria-label': t('info.confirm') });
  const go = h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', disabled: true, onclick: async () => {
    go.disabled = true;
    try {
      await api.del('/me', { confirm: 'УДАЛИТЬ' });
      s.close();
      toast(t('info.deleted'), 'ok');
      setTimeout(() => { if (inTelegram && tg && tg.close) tg.close(); else location.reload(); }, 900);
    } catch (e) { go.disabled = false; toast(e.message, 'error'); }
  } }, t('info.delete_forever'));
  inp.addEventListener('input', () => { go.disabled = inp.value.trim().toUpperCase() !== WORD; });
  const s = sheet({ title: t('info.delete_title'), content: h('div', { class: 'stack' },
    h('div', { class: 'muted' }, t('info.delete_text', { word: WORD })),
    inp, go) });
}

function help(view) {
  const step = (ic, color, title, text) => h('div', { class: 'row static' }, tile(ic, color), h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), h('span', { class: 'row-sub wrap' }, text)));
  view.append(pageHead(t('info.help'), { parent: '/menu' }),
    h('div', { class: 'list tiles' },
      step('camera', 'orange', t('info.s1'), t('info.s1t')),
      step('barcode', 'green', t('info.s2'), t('info.s2t')),
      step('soup', 'teal', t('info.s3'), t('info.s3t')),
      step('star', 'yellow', t('info.s6'), t('info.s6t')),
      step('cup', 'teal', t('info.s7'), t('info.s7t')),
      step('target', 'red', t('info.s4'), t('info.s4t')),
      step('bell', 'purple', t('menu.reminders'), t('info.s5t'))),
    h('div', { class: 'footnote' }, t('info.disclaimer')),
    h('div', { class: 'section-label' }, t('info.tips')),
    h('div', { class: 'list tiles' },
      h('button', { class: 'row', type: 'button', onclick: () => { resetCoach(); haptic('success'); toast(t('info.tips_on'), 'ok'); } },
        tile('sparkles', 'purple'),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('info.tips_reset')), h('span', { class: 'row-sub' }, t('info.tips_reset_sub'))))),
    h('div', { class: 'section-label' }, t('info.about')),
    h('div', { class: 'list tiles' },
      h('div', { class: 'row static' }, tile('info-circle', 'blue'), h('span', { class: 'grow' }, t('info.version')), h('span', { class: 'muted' }, '2.0')),
      h('div', { class: 'row static' }, tile('device-mobile', 'teal'), h('span', { class: 'grow' }, 'Telegram'), h('span', { class: 'muted' }, tg && tg.version ? tg.version : t('info.tg_none'))),
      h('div', { class: 'row static' }, tile('package', 'orange'), h('span', { class: 'grow' }, t('menu.data')), h('span', { class: 'muted' }, isMock() ? t('info.demo') : t('info.yours')))));
}
