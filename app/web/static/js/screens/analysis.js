// Screen: "Анализ" — what went well and what to fix, from the day's data
import { api } from '../api.js';
import { weekGoalBlock } from './weekgoal.js';
import { lineChart } from '../charts.js';
import { h, icon, nav, segmented, shortDate, fmtN, skeletonCards, emptyState, errorState, sectionTitle, todayISO, addDays, dateLabel, longDate } from '../ui.js';

let curDate = todayISO();
const PERIODS = [['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц']];
function loadPeriod() { try { const v = localStorage.getItem('an_period'); return PERIODS.some((x) => x[0] === v) ? v : 'day'; } catch (e) { return 'day'; } }
function savePeriod(v) { try { localStorage.setItem('an_period', v); } catch (e) { /* ignore */ } }
const signed = (v, d = 1) => (v > 0 ? '+' : v < 0 ? '−' : '') + fmtN(Math.abs(v), d) + ' кг';

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' });
  root.append(view);

  const prev = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Предыдущий день', onclick: () => go(-1) }, icon('back'));
  const next = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Следующий день', onclick: () => go(1) }, icon('next'));
  const label = h('button', { class: 'date-label', type: 'button', onclick: () => { if (curDate !== todayISO()) { curDate = todayISO(); load(); } } });
  let period = loadPeriod();
  const dateRow = h('div', { class: 'date-switch page-head' }, prev, label, next);
  const seg = segmented(PERIODS, period, (v) => { period = v; savePeriod(v); sync(); load(); }, 'Период анализа');
  view.append(seg, dateRow, body);
  const sync = () => { dateRow.hidden = period !== 'day'; };
  sync();

  function go(n) {
    const d = addDays(curDate, n);
    if (d > todayISO()) return;
    curDate = d;
    load();
  }

  async function load() {
    if (period !== 'day') return loadPeriodView();
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

  async function loadPeriodView() {
    body.replaceChildren(skeletonCards(3, 110));
    const asked = period;
    try {
      const data = await api.get('/analysis', { period: asked });
      if (!ctx.alive() || asked !== period) return;
      drawPeriod(data);
    } catch (e) {
      if (ctx.alive()) errorState(body, e, load);
    }
  }

  function drawPeriod(a) {
    const word = a.period === 'week' ? 'неделю' : 'месяц';
    const range = `${shortDate(a.from)} — ${shortDate(a.to)}`;
    if (!a.logged_days) {
      body.replaceChildren(emptyState('sparkles', 'Пока мало данных', `За ${word} нет записей о еде. Записывайте приёмы пищи, и здесь появится разбор по всем показателям.`,
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => nav.go('/add/meal') }, 'Добавить приём пищи')));
      return;
    }
    const kids = [h('div', { class: 'card an-head' },
      h('div', { class: 'an-title' }, a.headline),
      h('div', { class: 'muted small' }, `Разбор за ${word}: ${range}`))];
    if (a.focus) {
      kids.push(h('div', { class: 'an-focus' },
        h('div', { class: 'an-focus-k' }, a.period === 'week' ? 'Главное на следующую неделю' : 'Главное на ближайшее время'),
        h('div', { class: 'an-focus-t' }, (a.focus.icon || '') + ' ' + a.focus.title),
        h('div', { class: 'an-focus-x' }, a.focus.text)));
    }
    if (a.metrics.length) {
      kids.push(sectionTitle('Показатели'),
        h('div', { class: 'card an-metrics' }, a.metrics.map((m) => h('div', { class: 'an-metric' },
          h('span', { class: 'an-dot ' + m.status, 'aria-hidden': 'true' }),
          h('div', { class: 'an-m-main' },
            h('div', { class: 'an-m-top' }, h('span', { class: 'an-label' }, m.label), h('span', { class: 'an-val' }, m.value)),
            h('div', { class: 'an-m-sub muted small' },
              h('span', null, [m.goal, m.note].filter(Boolean).join(' · ')),
              m.delta ? h('span', { class: 'chip-delta ' + m.delta.dir }, m.delta.text) : null)))))); 
    }
    const w = a.weight;
    if (w && w.change != null) {
      const toGoal = w.remaining != null && w.goal != null
        ? (Math.abs(w.remaining) < 0.2 ? 'Цель достигнута' : `До цели ${fmtN(Math.abs(w.remaining), 1)} кг` + (w.eta_weeks ? `, при таком темпе около ${w.eta_weeks} нед.` : ''))
        : null;
      kids.push(sectionTitle('Вес'),
        h('section', { class: 'card an-weight' },
          h('div', { class: 'an-w-top' },
            h('div', null, h('div', { class: 'an-w-val' }, signed(w.change)), h('div', { class: 'muted small' }, 'тренд за период')),
            h('div', { class: 'an-w-right' }, h('div', { class: 'an-w-now' }, fmtN(w.now, 1) + ' кг'), h('div', { class: 'muted small' }, `${signed(w.per_week, 2)} в неделю`))),
          lineChart({ points: w.points.map((p) => ({ label: shortDate(p.date), y: null, t: p.v })), decimals: 1, unit: 'кг', height: 120 }),
          toGoal ? h('div', { class: 'an-w-goal' }, toGoal) : null,
          w.expected_change != null ? h('div', { class: 'muted small' }, `По записанной еде ожидалось около ${signed(w.expected_change)}`) : null));
    } else if (w && w.weighins) {
      kids.push(sectionTitle('Вес'), h('div', { class: 'card an-tip' }, 'Взвешиваний пока мало: для тренда нужно хотя бы два замера за период.'));
    }
    if (a.good.length) {
      kids.push(sectionTitle('Что получилось'),
        h('div', { class: 'card an-list' }, a.good.map((g) => h('div', { class: 'an-item' }, h('span', { class: 'an-mark good' }, icon('check', 16)), h('span', null, g)))));
    }
    if (a.patterns.length) {
      kids.push(sectionTitle('Закономерности'),
        h('div', { class: 'stack-sm' }, a.patterns.map((t) => h('div', { class: 'card an-tip an-pattern' },
          h('div', { class: 'an-tip-title' }, t.icon + ' ' + t.title), h('div', null, t.text)))));
    }
    if (a.tips.length) {
      kids.push(sectionTitle('Что поправить'),
        h('div', { class: 'stack-sm' }, a.tips.map((t) => h('div', { class: 'card an-tip' },
          h('div', { class: 'an-tip-title' }, t.icon + ' ' + t.title), h('div', null, t.text)))));
    }
    if (a.logged_days < Math.ceil(a.days / 2)) {
      kids.push(h('div', { class: 'muted small an-foot' }, 'Записей немного, поэтому выводы приблизительные.'));
    }
    body.replaceChildren(...kids);
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
