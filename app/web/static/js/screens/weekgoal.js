// Weekly mini-goal card: pick one habit for the week, progress is counted from the diary.
import { api } from '../api.js';
import { h, icon, toast, haptic, sectionTitle } from '../ui.js';

const days = (n) => (n % 10 === 1 && n % 100 !== 11 ? 'день' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'дня' : 'дней');

// Returns a container that fills itself; `withRecap` adds last week's summary (Analysis screen only).
export function weekGoalBlock(ctx, { withRecap = false } = {}) {
  const box = h('div', { class: 'stack-sm' });
  let busy = false;

  async function act(fn) {
    if (busy) return;
    busy = true;
    try {
      draw(await fn());
      haptic('light');
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      busy = false;
    }
  }

  const choose = (o) => act(() => api.put('/week-goal', { kind: o.kind, target: o.target }));
  const clear = () => act(() => api.del('/week-goal'));

  function dots(g) {
    return h('div', { class: 'wg-dots', role: 'img', 'aria-label': `${g.done} из ${g.target} ${days(g.target)}` },
      g.cells.map((c) => h('span', { class: 'wg-dot ' + c.state })));
  }

  function goalCard(g) {
    return h('section', { class: 'card wg-card' + (g.achieved ? ' achieved' : '') },
      h('div', { class: 'wg-top' },
        h('span', { class: 'wg-ico', 'aria-hidden': 'true' }, g.icon),
        h('div', { class: 'grow' },
          h('div', { class: 'row-title' }, g.title),
          h('div', { class: 'muted small' }, `${g.target} ${days(g.target)} из 7`)),
        h('b', { class: 'wg-count' }, `${g.done}/${g.target}`)),
      dots(g),
      h('div', { class: 'small' }, g.message),
      h('div', { class: 'muted small' }, g.hint),
      h('button', { class: 'btn-link small', type: 'button', onclick: clear }, 'Выбрать другую'));
  }

  function optionsCard(options) {
    return h('section', { class: 'card wg-card' },
      h('div', { class: 'row-title' }, 'Цель на эту неделю'),
      h('div', { class: 'muted small' }, 'Одна простая привычка. Планка выбрана чуть выше того, что получилось на прошлой неделе.'),
      h('div', { class: 'stack-sm' }, options.map((o) => h('button', { class: 'wg-opt', type: 'button', onclick: () => choose(o) },
        h('span', { class: 'wg-ico', 'aria-hidden': 'true' }, o.icon),
        h('span', { class: 'grow' }, h('b', null, o.title), h('span', { class: 'muted small wg-sub' }, `${o.target} ${days(o.target)} из 7`)),
        icon('next', 16)))));
  }

  function recapCard(r) {
    const kids = [sectionTitle('Итог прошлой недели')];
    const rows = [];
    if (r.goal) {
      rows.push(h('div', { class: 'an-item' }, h('span', { class: 'an-mark ' + (r.goal.achieved ? 'good' : '') }, r.goal.achieved ? icon('check', 16) : r.goal.icon),
        h('span', null, `${r.goal.title}: ${r.goal.done} из ${r.goal.target}. ` + (r.goal.achieved ? 'Цель выполнена.' : 'Планка не взялась, это нормально.'))));
    }
    r.good.forEach((g) => rows.push(h('div', { class: 'an-item' }, h('span', { class: 'an-mark good' }, icon('check', 16)), h('span', null, g))));
    kids.push(h('div', { class: 'card an-list' }, rows));
    return kids;
  }

  let recap = null;
  function draw(data) {
    const kids = [];
    if (data.goal) kids.push(goalCard(data.goal));
    else if (data.options.length) kids.push(optionsCard(data.options));
    if (withRecap && recap) kids.push(...recapCard(recap));
    box.replaceChildren(...kids);
  }

  api.get('/week-goal').then((d) => {
    if (!ctx.alive()) return;
    recap = d.recap;
    draw(d);
  }).catch(() => { /* the card is optional: stay silent on errors */ });
  return box;
}
