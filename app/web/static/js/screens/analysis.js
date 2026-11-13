// Screen: "Анализ" — «тренер»: вердикт, что получилось, главное действие, советы
import { api } from '../api.js';
import { weekGoalBlock } from './weekgoal.js';
import { ring, sparkline } from '../charts.js';
import { onbImg } from '../onb/controls.js';
import { h, icon, tile, glyph, nav, segmented, shortDate, fmtN, skeletonCards, holdHeight, emptyState, errorState, sectionTitle, todayISO, dateSwitch, countUp, staggerIn, reducedMotion, heroCard, progressBar, statTile } from '../ui.js';
import { t } from '../i18n.js';

let curDate = todayISO();
const PERIODS = [['day', t('an.p_day')], ['week', t('stats.r_week')], ['month', t('stats.r_month')]];
function loadPeriod() { try { const v = localStorage.getItem('an_period'); return PERIODS.some((x) => x[0] === v) ? v : 'day'; } catch (e) { return 'day'; } }
function savePeriod(v) { try { localStorage.setItem('an_period', v); } catch (e) { /* ignore */ } }

// Сводка статусов под заголовком: сколько показателей в норме, около нормы и требуют внимания
function statusChips(items) {
  const n = { good: 0, ok: 0, bad: 0, progress: 0 };
  items.forEach((m) => { if (n[m.status] !== undefined) n[m.status] += 1; });
  const defs = [['good', t('stats.lg_ok')], ['ok', t('an.chip_ok')], ['bad', t('an.chip_bad')], ['progress', t('an.chip_progress')]].filter(([k]) => n[k]);
  if (!defs.length) return null;
  return h('div', { class: 'an-chips' }, defs.map(([k, t]) => h('span', { class: 'an-chip ' + k }, h('b', null, String(n[k])), t)));
}

// Вердикт по доле показателей «в норме» (+ половина «почти»): тон карточки, картинка и кольцо-оценка
const VERDICTS = {
  good: { tone: 'green', img: 'party', ring: ['var(--c-green)', 'var(--c-teal)'] },
  ok: { tone: 'amber', img: 'heart_check', ring: ['var(--c-yellow)', 'var(--c-warn)'] },
  fix: { tone: 'peach', img: 'salad', ring: ['var(--c-warn)', '#ffb27a'] },
};
function verdictOf(items) {
  const rated = items.filter((m) => ['good', 'ok', 'bad'].includes(m.status));
  if (!rated.length) return { pct: null, ...VERDICTS.ok, tone: 'accent' };
  const score = (rated.filter((m) => m.status === 'good').length + 0.5 * rated.filter((m) => m.status === 'ok').length) / rated.length;
  const pct = Math.round(score * 100);
  return { pct, ...(pct >= 75 ? VERDICTS.good : pct >= 50 ? VERDICTS.ok : VERDICTS.fix) };
}
function heroVerdict(items, title, sub, extra) {
  const v = verdictOf(items);
  const animate = !reducedMotion();
  let ringEl = null;
  if (v.pct != null) {
    const num = h('span', { class: 'an-ring-num' });
    if (animate) countUp(num, v.pct, { fmt: (x) => Math.round(x) + '%' }); else num.textContent = v.pct + '%';
    ringEl = h('div', { class: 'an-ring an-ring-lg', 'aria-hidden': 'true' }, ring({ value: v.pct, max: 100, size: 84, stroke: 9, gradient: v.ring, animate }), num);
  }
  return heroCard({ tone: v.tone, cls: 'an-hero', children: [
    h('div', { class: 'an-hero-pic' }, onbImg(v.img, 44)),
    h('div', { class: 'an-title' }, title),
    h('div', { class: 'an-hero-main' }, ringEl, h('div', { class: 'an-hero-text' }, h('div', { class: 'an-hero-sub' }, sub))),
    statusChips(items), extra || null] });
}

// Число из строки показателя: «1 580 ккал», «норма 1 650», «6 ч 40 мин», «7+ ч»
function numOf(str) {
  if (str == null) return null;
  const t = String(str).replace(/[  ]/g, ' ');
  const hm = t.match(/(\d+)\+?\s*(?:ч|h)(?![а-яa-z])/);
  const mm = t.match(/(\d+)\s*(?:мин|min)/) || t.match(/(\d+)\s*(?:м|m)(?![а-яa-z])/);
  if (hm || mm) return (hm ? +hm[1] * 60 : 0) + (mm ? +mm[1] : 0);
  const m = t.replace(/(\d)\s+(?=\d)/g, '$1').replace(',', '.').match(/\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}
const BAR_STATUS = { good: 'good', ok: 'ok', bad: 'bad', progress: 'progress' };
// num/max — числа с сервера; строки разбираем только как запасной путь (старые ответы, период)
function metricBar(value, goal, status, i, { num = null, max = null, overGood = false } = {}) {
  const v = num != null ? num : numOf(value); const g = max != null ? max : numOf(goal);
  if (v == null || !g || !BAR_STATUS[status]) return null;
  const el = progressBar(v, g, { status: BAR_STATUS[status], overGood });
  if (!reducedMotion()) { el.classList.add('bar-scale'); el.style.setProperty('--i', i); }
  return el;
}

const focusCard = (kicker, t) => h('div', { class: 'an-focus' },
  h('div', { class: 'an-focus-k' }, kicker),
  h('div', { class: 'an-focus-row' },
    h('span', { class: 'an-focus-ico' }, glyph(t.icon, 44)),
    h('div', { class: 'an-focus-body' }, h('div', { class: 'an-focus-t' }, t.title), h('div', { class: 'an-focus-x' }, t.text),
      t.action ? h('div', { class: 'an-focus-act' }, t.action) : null)));
const tipCard = (t) => h('div', { class: 'card an-tip' }, glyph(t.icon, 36), h('div', { class: 'an-tip-body' }, h('div', { class: 'an-tip-title' }, t.title), h('div', null, t.text)));
// Не больше трёх советов сразу: остальные за кнопкой, чтобы «Главное» не тонуло в списке
function moreTips(list, shown = 3) {
  const box = h('div', { class: 'stack-sm' }, list.slice(0, shown).map(tipCard));
  if (list.length <= shown) return box;
  const btn = h('button', { class: 'an-more', type: 'button', onclick: () => { box.append(...list.slice(shown).map(tipCard)); btn.remove(); } }, t('an.more', { n: list.length - shown }));
  box.append(btn);
  return box;
}
const pills = (good) => h('div', { class: 'pills' }, good.map((g) => h('span', { class: 'pill-good' }, h('span', { class: 'pill-ck' }, icon('check', 13)), g)));
const signed = (v, d = 1) => (v > 0 ? '+' : v < 0 ? '−' : '') + fmtN(Math.abs(v), d) + ' ' + t('unit.kg');

const TILE_ICON = {
  kcal: ['tools-kitchen', 'orange'], protein: ['egg-fried', 'blue'], water: ['droplet', 'teal'],
  steps: ['walk', 'green'], sleep: ['bed', 'indigo'], logged: ['calendar', 'purple'], rhythm: ['clock', 'orange'],
};

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' });
  root.append(view);

  let period = loadPeriod();
  const sw = dateSwitch(curDate, (d) => { curDate = d; load(); });
  const dateRow = sw.el;
  const seg = segmented(PERIODS, period, (v) => { period = v; savePeriod(v); sync(); load(); }, t('an.period'));
  view.append(seg, dateRow, body);
  const sync = () => { dateRow.hidden = period !== 'day'; };
  sync();

  async function load() {
    if (period !== 'day') return loadPeriodView();
    sw.set(curDate);
    const release = pending();
    const date = curDate;
    try {
      const data = await api.get('/analysis', { date });
      if (!ctx.alive() || date !== curDate || period !== 'day') return;
      draw(data);
      release();
    } catch (e) {
      if (!ctx.alive() || date !== curDate || period !== 'day') return;
      errorState(body, e, load);
      release();
    }
  }

  // Первый показ: скелетон. Повторная загрузка (день/период): старое содержимое остаётся, высота зафиксирована.
  function pending() {
    if (body.hasChildNodes()) return holdHeight(body);
    body.replaceChildren(skeletonCards(3, 110));
    return () => {};
  }

  async function loadPeriodView() {
    const release = pending();
    const asked = period;
    try {
      const data = await api.get('/analysis', { period: asked });
      if (!ctx.alive() || asked !== period) return;
      drawPeriod(data);
      release();
    } catch (e) {
      if (!ctx.alive() || asked !== period) return;
      errorState(body, e, loadPeriodView);
      release();
    }
  }

  function drawPeriod(a) {
    const word = a.period === 'week' ? t('an.word_week') : t('an.word_month');
    const nb = (t) => t.replace(/ /g, '\u00A0');   // «6 нояб.» не рвётся на две строки
    const range = `${nb(shortDate(a.from))} — ${nb(shortDate(a.to))}`;
    if (!a.logged_days) {
      body.replaceChildren(emptyState('sparkles', t('stats.empty_title'), t('an.no_food', { w: word }),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => nav.go('/add/meal') }, t('an.add_meal'))));
      return;
    }
    const kids = [heroVerdict(a.metrics, a.headline, [t('an.review', { w: word }), h('span', { class: 'an-hero-range' }, range)])];
    if (a.focus) kids.push(focusCard(a.period === 'week' ? t('an.focus_week') : t('an.focus_month'), a.focus));
    if (a.metrics.length) {
      kids.push(sectionTitle(t('an.metrics')),
        h('div', { class: 'stat-grid' }, a.metrics.map((m, i) => {
          const [ic, col] = TILE_ICON[m.key] || ['sparkles', 'blue'];
          let bar = null; let st = m.status;
          const goalTxt = m.key === 'logged' ? null : m.goal;
          const cap = [goalTxt, m.note && m.note.replace(/^(цель достигнута|в норме|goal reached|on target)\s+/, '')].filter(Boolean).join(' · ') || null;
          const t = statTile({ icon: ic, color: col, title: m.label, value: m.value, status: BAR_STATUS[st] || 'none',
            delta: m.delta ? h('span', { class: 'chip-delta ' + m.delta.dir }, m.delta.text.replace(' к прошлому', '').replace(' vs previous', '')) : null, caption: cap });
          if (m.key === 'logged') { const mm = String(m.value).match(/(\d+)\D+(\d+)/); if (mm) bar = progressBar(+mm[1], +mm[2], { status: BAR_STATUS[st] || 'good' }); } else if (m.key !== 'rhythm') bar = metricBar(m.value, m.goal, st, i, { overGood: m.key !== 'kcal' });
          if (bar) t.insertBefore(bar, t.querySelector('.stat-tile-foot'));
          return t;
        })));
    }
    const w = a.weight;
    if (w && w.change != null) {
      const toGoal = w.remaining != null && w.goal != null
        ? (Math.abs(w.remaining) < 0.2 ? t('stats.goal_reached') : t('an.to_goal', { v: fmtN(Math.abs(w.remaining), 1) }) + (w.eta_weeks ? t('an.eta', { n: w.eta_weeks }) : ''))
        : null;
      const spark = w.points.length < 3 ? null : sparkline({ values: w.points.map((p) => p.v), width: 120, height: 48, animate: !reducedMotion() });
      kids.push(sectionTitle(t('add.weight')),
        h('section', { class: 'card an-weight' },
          h('div', { class: 'an-w-top' },
            h('div', null, h('div', { class: 'an-w-val' }, signed(w.change)), h('div', { class: 'muted small' }, t('an.w_trend', { v: signed(w.per_week, 2) }))),
            spark ? h('div', { class: 'an-w-spark' }, spark) : null),
          toGoal ? h('div', { class: 'an-w-goal' }, toGoal) : null,
          w.expected_change != null ? h('div', { class: 'muted small' }, t('an.w_expected', { v: signed(w.expected_change) })) : null,
          h('button', { class: 'an-w-link', type: 'button', onclick: () => {
            try { localStorage.setItem('stats_range', a.period === 'week' ? 'week' : 'month'); } catch (e) { /* ignore */ }
            nav.go('/stats');
          } }, t('an.chart'), icon('next', 16))));
    } else if (w && w.weighins) {
      kids.push(sectionTitle(t('add.weight')), h('div', { class: 'card an-tip' }, t('an.few_weighins')));
    }
    if (a.good.length) kids.push(sectionTitle(t('an.good')), pills(a.good));
    if (a.patterns.length) {
      kids.push(sectionTitle(t('an.patterns')),
        h('div', { class: 'stack-sm' }, a.patterns.map((t) => h('div', { class: 'card an-tip an-pattern' }, glyph(t.icon, 36),
          h('div', { class: 'an-tip-body' }, h('div', { class: 'an-tip-title' }, t.title), h('div', null, t.text))))));
    }
    if (a.tips.length) kids.push(sectionTitle(t('an.fix')), moreTips(a.tips));
    if (a.logged_days < Math.ceil(a.days / 2)) {
      kids.push(h('div', { class: 'muted small an-foot' }, t('an.few_entries')));
    }
    body.replaceChildren(...kids);
    staggerIn(body, kids);
  }

  function draw(a) {
    const rated = a.rows.filter((r) => ['good', 'ok', 'bad'].includes(r.status));
    const sub = rated.length
      ? [t('an.in_norm', { a: rated.filter((r) => r.status === 'good').length, b: rated.length }), a.closed ? null : h('span', { class: 'an-hero-range' }, t('an.open'))]
      : (a.closed ? t('an.closed') : t('an.open'));
    const kids = [heroVerdict(a.rows, a.headline, sub)];

    if (a.rows.length) {
      kids.push(h('div', { class: 'card an-rows2' }, a.rows.map((r, i) => h('div', { class: 'an2-row' },
        h('div', { class: 'an2-top' },
          h('span', { class: 'an-ico ' + r.status }, glyph(r.label, 30)),
          h('span', { class: 'an-label' }, r.label),
          h('span', { class: 'an2-val' }, r.value, r.status === 'none' ? null : h('span', { class: 'muted small' }, t('an.of') + r.goal))),
        metricBar(r.value, r.goal, r.status, i, { num: r.num, max: r.max, overGood: !!r.more_is_better }),
        r.hint ? h('div', { class: 'an2-hint ' + r.status }, r.hint) : null))));
    }
    if ((a.motivation || []).length) kids.push(h('div', { class: 'card an-mot' }, a.motivation.map((m) => h('div', null, m))));
    if (a.good.length) kids.push(sectionTitle(t('an.good')), pills(a.good));
    if (a.tips.length) {
      kids.push(focusCard(a.closed ? t('an.focus_tomorrow') : t('an.focus_today'), a.tips[0]));
      if (a.tips.length > 1) kids.push(sectionTitle(t('an.more_fix')), moreTips(a.tips.slice(1)));
    }
    if (!a.good.length && !a.tips.length) {
      kids.push(emptyState('sparkles', t('an.nothing'), t('an.nothing_text'),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => nav.go('/add/meal') }, t('an.add_meal'))));
    }
    if (curDate === todayISO()) kids.push(weekGoalBlock(ctx, { withRecap: true }));
    body.replaceChildren(...kids);
    staggerIn(body, kids);
  }

  load();
}
