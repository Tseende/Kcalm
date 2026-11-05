// Напоминания бота: включить, выключить и выбрать время.
import { api } from '../api.js';
import { h, toast, haptic, pageHead, toggle, errorState, skeletonCards } from '../ui.js';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonCards(3, 64));
  view.append(pageHead('Напоминания', { parent: '/menu' }), box);
  root.append(view);
  let items;
  try { items = (await api.get('/reminders')).reminders; } catch (e) { if (ctx.alive()) errorState(box, e, () => { root.replaceChildren(); render(root, ctx); }); return; }
  if (!ctx.alive()) return;

  async function save(kind, body, undo) {
    try { await api.put('/reminders/' + kind, body); haptic('light'); } catch (e) { toast(e.message, 'error'); if (undo) undo(); }
  }
  const rows = items.map((r) => {
    const time = h('input', { type: 'time', value: r.time, class: 'time-in', 'aria-label': `Время: ${r.title}`, disabled: !r.enabled });
    const sw = toggle(r.enabled, (on) => {
      time.disabled = !on;
      save(r.kind, { enabled: on }, () => { sw.querySelector('input').checked = !on; time.disabled = on; });
    }, r.title);
    time.addEventListener('change', () => { if (time.value) save(r.kind, { time: time.value }); });
    return h('div', { class: 'row static' },
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, r.title), h('span', { class: 'row-sub' }, r.sub)),
      time, sw);
  });
  box.replaceChildren(h('div', { class: 'list rem-list' }, rows),
    h('div', { class: 'footnote' }, 'Сообщения приходят в чат с ботом в выбранное время. Напоминание «Кухня закрывается» двигается вместе с настройкой «Не есть после» в профиле.'));
}
