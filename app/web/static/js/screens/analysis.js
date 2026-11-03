// Screen: "Анализ" — what went well and what to fix, from the day's data
import { api } from '../api.js';
import { weekGoalBlock } from './weekgoal.js';
import { h, icon, nav, skeletonCards, emptyState, errorState, sectionTitle, todayISO, addDays, dateLabel, longDate } from '../ui.js';

let curDate = todayISO();

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' });
  root.append(view);

  const prev = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Предыдущий день', onclick: () => go(-1) }, icon('back'));
  const next = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Следующий день', onclick: () => go(1) }, icon('next'));
  const label = h('button', { class: 'date-label', type: 'button', onclick: () => { if (curDate !== todayISO()) { curDate = todayISO(); load(); } } });
  view.append(h('div', { class: 'date-switch page-head' }, prev, label, next), body);

  function go(n) {
    const d = addDays(curDate, n);
    if (d > todayISO()) return;
    curDate = d;
    load();
  }

  async function load() {
    const isToday = curDate === todayISO();
    label.replaceChildren(h('span', { class: 'date-main' }, dateLabel(curDate)), h('span', { class: 'date-sub' }, isToday ? longDate(curDate) : 'нажмите, чтобы вернуться к сегодня'));
    next.disabled = isToday;
    body.replaceChildren(skeletonCards(3, 110));
    const date = curDate;
    try {
      const data = await api.get('/analysis', { date });
      if (!ctx.alive() || date !== curDate) return;
      draw(data);
    } catch (e) {
      if (ctx.alive()) errorState(body, e, load);
    }
  }

  function draw(a) {
    const kids = [h('div', { class: 'card an-head' },
      h('div', { class: 'an-title' }, a.headline),
      h('div', { class: 'muted small' }, a.closed ? 'Итог дня по записям и данным браслета' : 'День ещё идёт: недобор пока не считаем'))];

    if (a.rows.length) {
      kids.push(h('div', { class: 'card an-rows' }, a.rows.map((r) => h('div', { class: 'an-row' },
        h('span', { class: 'an-dot ' + r.status, 'aria-hidden': 'true' }),
        h('span', { class: 'an-label' }, r.label),
        h('span', { class: 'an-val' }, r.value),
        h('span', { class: 'muted small an-goal' }, r.status === 'none' ? '' : 'из ' + r.goal)))));
    }
    if (a.good.length) {
      kids.push(sectionTitle('Что получилось'),
        h('div', { class: 'card an-list' }, a.good.map((g) => h('div', { class: 'an-item' }, h('span', { class: 'an-mark good' }, icon('check', 16)), h('span', null, g)))));
    }
    if (a.tips.length) {
      kids.push(sectionTitle('Что поправить'),
        h('div', { class: 'stack-sm' }, a.tips.map((t) => h('div', { class: 'card an-tip' },
          h('div', { class: 'an-tip-title' }, t.title),
          h('div', null, t.text)))));
    }
    if (!a.good.length && !a.tips.length) {
      kids.push(emptyState('sparkles', 'Пока нечего анализировать', 'Запишите еду, воду и сон, и здесь появится разбор дня.',
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => nav.go('/add/meal') }, 'Добавить приём пищи')));
    }
    if (curDate === todayISO()) kids.push(weekGoalBlock(ctx, { withRecap: true }));
    body.replaceChildren(...kids);
  }

  load();
}
