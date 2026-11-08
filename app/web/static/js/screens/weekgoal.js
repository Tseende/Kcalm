// Weekly mini-goal card: pick one habit for the week, progress is counted from the diary.
import { api } from '../api.js';
import { h, icon, glyph, toast, haptic, nav, sectionTitle } from '../ui.js';
import { t, tn } from '../i18n.js';

const days = (n) => tn('wg.days', n).replace(/^-?\d+ /, '');

// Celebrate once per week, on whichever screen first sees the goal done. Storage may be unavailable.
function celebrateOnce(g, weekStart, el) {
  const key = 'wg-done-' + weekStart;
  try {
    if (localStorage.getItem(key)) return;
    localStorage.setItem(key, '1');
  } catch (e) { /* private mode: celebrate anyway, at worst twice */ }
  haptic('success');
  el.classList.add('celebrate');
  toast(t('wg.done_toast', { title: g.title.toLowerCase(), a: g.done, b: g.target }), 'ok');
}

// Была ли цель при прошлом показе: решает, рисовать ли заглушку на «Сегодня» (без цели блока нет, и заглушка «прыгала» бы).
const HAD_KEY = 'wg_had_goal';
const hadGoal = () => { try { return localStorage.getItem(HAD_KEY) === '1'; } catch (e) { return false; } };
const rememberGoal = (has) => { try { localStorage.setItem(HAD_KEY, has ? '1' : '0'); } catch (e) { /* без хранилища */ } };

// Returns a container that fills itself. `withRecap` adds last week's summary (Analysis screen);
// `compact` is a one-line strip for «Сегодня» that opens the full card on «Анализ».
export function weekGoalBlock(ctx, { withRecap = false, compact = false } = {}) {
  const box = h('div', { class: 'stack-sm' });
  let busy = false;

  async function act(fn) {
    if (busy) return;
    busy = true;
    try {
      const d = await fn();
      rememberGoal(!!d.goal);
      lastData = { ...d, recap };                     // PUT/DELETE answers carry no recap
      draw(d, true);
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
    return h('div', { class: 'wg-dots', role: 'img', 'aria-label': t('wg.dots_aria', { a: g.done, b: g.target, d: days(g.target) }) },
      g.cells.map((c) => h('span', { class: 'wg-dot ' + c.state })));
  }

  function goalCard(g) {
    return h('section', { class: 'card wg-card' + (g.achieved ? ' achieved' : '') },
      h('div', { class: 'wg-top' },
        glyph(g.icon, 36),
        h('div', { class: 'grow' },
          h('div', { class: 'row-title' }, g.title),
          h('div', { class: 'muted small' }, t('wg.of7', { n: g.target, d: days(g.target) }))),
        h('b', { class: 'wg-count' }, `${g.done}/${g.target}`)),
      dots(g),
      h('div', { class: 'small' }, g.message),
      h('div', { class: 'muted small' }, g.hint),
      h('button', { class: 'btn-link small', type: 'button', onclick: clear }, t('wg.change')));
  }

  function optionsCard(options) {
    return h('section', { class: 'card wg-card' },
      h('div', { class: 'row-title' }, t('wg.this_week')),
      h('div', { class: 'muted small' }, t('wg.pick_hint')),
      h('div', { class: 'stack-sm' }, options.map((o) => h('button', { class: 'wg-opt', type: 'button', onclick: () => choose(o) },
        glyph(o.icon, 36),
        h('span', { class: 'grow' }, h('b', null, o.title), h('span', { class: 'muted small wg-sub' }, t('wg.of7', { n: o.target, d: days(o.target) }))),
        icon('next', 16)))));
  }

  function recapCard(r) {
    const kids = [sectionTitle(t('wg.recap'))];
    const rows = [];
    if (r.goal) {
      rows.push(h('div', { class: 'an-item' }, h('span', { class: 'an-mark ' + (r.goal.achieved ? 'good' : '') }, icon(r.goal.achieved ? 'check' : 'target', 16)),
        h('span', null, t('wg.recap_line', { title: r.goal.title, done: r.goal.done, target: r.goal.target, res: r.goal.achieved ? t('wg.achieved') : t('wg.missed') }))));
    }
    r.good.forEach((g) => rows.push(h('div', { class: 'an-item' }, h('span', { class: 'an-mark good' }, icon('check', 16)), h('span', null, g))));
    kids.push(h('div', { class: 'card an-list' }, rows));
    return kids;
  }

  function strip(data) {
    const g = data.goal;
    const open = () => nav.go('/analysis');
    if (!g) {
      return h('button', { class: 'card wg-strip', type: 'button', onclick: open },
        h('span', { class: 'wg-ico wg-ico-line', 'aria-hidden': 'true' }, icon('target', 20)),
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('wg.strip')), h('span', { class: 'muted small wg-sub' }, t('wg.strip_hint'))),
        icon('next', 16));
    }
    return h('button', { class: 'card wg-strip' + (g.achieved ? ' achieved' : ''), type: 'button', onclick: open, 'aria-label': t('wg.strip_aria', { title: g.title, a: g.done, b: g.target }) },
      h('span', { class: 'wg-ico wg-ico-line' + (g.achieved ? ' done' : ''), 'aria-hidden': 'true' }, icon(g.achieved ? 'check' : 'target', 20)),
      h('span', { class: 'grow wg-strip-main' }, h('span', { class: 'row-title' }, g.title), dots(g)),
      h('b', { class: 'wg-count' }, `${g.done}/${g.target}`));
  }

  let recap = null;
  function draw(data, fresh = false) {
    const kids = [];
    if (compact) {
      if (data.goal) kids.push(strip(data)); // без выбранной цели на «Сегодня» ничего не показываем: выбор живёт в «Анализе»
    } else if (data.goal) kids.push(goalCard(data.goal));
    else if (data.options.length) kids.push(optionsCard(data.options));
    if (fresh && data.goal && data.goal.achieved && kids[0]) celebrateOnce(data.goal, data.week_start, kids[0]);
    if (withRecap && recap) kids.push(...recapCard(recap));
    box.replaceChildren(...kids);
    if (compact) box.hidden = !data.goal;             // нет цели: блока нет совсем (и пустого места в стопке тоже)
  }

  if (lastData) {                                     // last known state first: no jump on reload
    recap = lastData.recap;
    draw(lastData);
  } else if (!compact || hadGoal()) {                 // первый заход: заглушка вместо пустоты (на «Сегодня» только если цель была)
    box.append(h('div', { class: 'skel wg-ph' + (compact ? ' compact' : ''), 'aria-hidden': 'true' }));
  } else {
    box.hidden = true;
  }
  api.get('/week-goal').then((d) => {
    lastData = d;
    rememberGoal(!!d.goal);
    if (!ctx.alive() || !box.isConnected) return;
    recap = d.recap;
    draw(d, true);
  }).catch(() => { if (!lastData) { box.replaceChildren(); if (compact) box.hidden = true; } }); // optional card: drop the placeholder
  return box;
}

let lastData = null; // last /week-goal answer, shared by «Сегодня» and «Анализ»
