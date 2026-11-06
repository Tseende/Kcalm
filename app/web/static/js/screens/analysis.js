// Screen: "Анализ" — «тренер»: вердикт, что получилось, главное действие, советы
import { api } from '../api.js';
import { weekGoalBlock } from './weekgoal.js';
import { ring, sparkline } from '../charts.js';
import { onbImg } from '../onb/controls.js';
import { h, icon, tile, glyph, nav, pageHead, segmented, shortDate, fmtN, skeletonCards, emptyState, errorState, sectionTitle, todayISO, dateSwitch, countUp, staggerIn, reducedMotion, heroCard, progressBar, statTile } from '../ui.js';

let curDate = todayISO();
const PERIODS = [['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц']];
function loadPeriod() { try { const v = localStorage.getItem('an_period'); return PERIODS.some((x) => x[0] === v) ? v : 'day'; } catch (e) { return 'day'; } }
function savePeriod(v) { try { localStorage.setItem('an_period', v); } catch (e) { /* ignore */ } }

// Сводка статусов под заголовком: сколько показателей в норме, около нормы и требуют внимания
function statusChips(items) {
  const n = { good: 0, ok: 0, bad: 0 };
  items.forEach((m) => { if (n[m.status] !== undefined) n[m.status] += 1; });
  const defs = [['good', 'в норме'], ['ok', 'почти'], ['bad', 'поправить']].filter(([k]) => n[k]);
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
  const hm = t.match(/(\d+)\+?\s*ч(?![а-я])/);
  const mm = t.match(/(\d+)\s*мин/) || t.match(/(\d+)\s*м(?![а-я])/);
  if (hm || mm) return (hm ? +hm[1] * 60 : 0) + (mm ? +mm[1] : 0);
  const m = t.replace(/(\d)\s+(?=\d)/g, '$1').replace(',', '.').match(/\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}
const BAR_STATUS = { good: 'good', ok: 'ok', bad: 'bad' };
function metricBar(value, goal, status, i) {
  const v = numOf(value); const g = numOf(goal);
  if (v == null || !g || !BAR_STATUS[status]) return null;
  const el = progressBar(v, g, { status: BAR_STATUS[status] });
  if (!reducedMotion()) { el.classList.add('bar-scale'); el.style.setProperty('--i', i); }
  return el;
}

const focusCard = (kicker, t) => h('div', { class: 'an-focus' },
  h('div', { class: 'an-focus-k' }, kicker),
  h('div', { class: 'an-focus-row' },
    h('span', { class: 'an-focus-ico' }, glyph(t.icon, 44)),
    h('div', { class: 'an-focus-body' }, h('div', { class: 'an-focus-t' }, t.title), h('div', { class: 'an-focus-x' }, t.text))));
const tipCard = (t) => h('div', { class: 'card an-tip' }, glyph(t.icon, 36), h('div', { class: 'an-tip-body' }, h('div', { class: 'an-tip-title' }, t.title), h('div', null, t.text)));
const pills = (good) => h('div', { class: 'pills' }, good.map((g) => h('span', { class: 'pill-good' }, h('span', { class: 'pill-ck' }, icon('check', 13)), g)));
const signed = (v, d = 1) => (v > 0 ? '+' : v < 0 ? '−' : '') + fmtN(Math.abs(v), d) + ' кг';

const TILE_ICON = {
  kcal: ['tools-kitchen', 'orange'], protein: ['egg-fried', 'blue'], water: ['droplet', 'teal'],
  steps: ['walk', 'green'], sleep: ['bed', 'indigo'], logged: ['calendar', 'purple'],
};

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' });
  root.append(view);

  let period = loadPeriod();
  const sw = dateSwitch(curDate, (d) => { curDate = d; load(); });
  const dateRow = sw.el;
  const seg = segmented(PERIODS, period, (v) => { period = v; savePeriod(v); sync(); load(); }, 'Период анализа');
  view.append(pageHead('Анализ', { back: false }), seg, dateRow, body);
  const sync = () => { dateRow.hidden = period !== 'day'; };
  sync();

  async function load() {
    if (period !== 'day') return loadPeriodView();
    sw.set(curDate);
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
      if (ctx.alive()) errorState(body, e, loadPeriodView);
    }
  }

  function drawPeriod(a) {
    const word = a.period === 'week' ? 'неделю' : 'месяц';
    const nb = (t) => t.replace(/ /g, '\u00A0');   // «6 нояб.» не рвётся на две строки
    const range = `${nb(shortDate(a.from))} — ${nb(shortDate(a.to))}`;
    if (!a.logged_days) {
      body.replaceChildren(emptyState('sparkles', 'Пока мало данных', `За ${word} нет записей о еде. Записывайте приёмы пищи, и здесь появится разбор по всем показателям.`,
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => nav.go('/add/meal') }, 'Добавить приём пищи')));
      return;
    }
    const kids = [heroVerdict(a.metrics, a.headline, [`Разбор за ${word}`, h('span', { class: 'an-hero-range' }, range)])];
    if (a.focus) kids.push(focusCard(a.period === 'week' ? 'Главное на следующую неделю' : 'Главное на ближайшее время', a.focus));
    if (a.metrics.length) {
      kids.push(sectionTitle('Показатели'),
        h('div', { class: 'stat-grid' }, a.metrics.map((m, i) => {
          const [ic, col] = TILE_ICON[m.key] || ['sparkles', 'blue'];
          let bar = null; let st = m.status;
          const goalTxt = m.key === 'logged' ? null : m.goal;
          const cap = [goalTxt, m.note && m.note.replace(/^(цель достигнута|в норме)\s+/, '')].filter(Boolean).join(' · ') || null;
          const t = statTile({ icon: ic, color: col, title: m.label, value: m.value, status: BAR_STATUS[st] || 'none',
            delta: m.delta ? h('span', { class: 'chip-delta ' + m.delta.dir }, m.delta.text.replace(' к прошлому', '')) : null, caption: cap });
          if (m.key === 'logged') { const mm = String(m.value).match(/(\d+)\D+(\d+)/); if (mm) bar = progressBar(+mm[1], +mm[2], { status: BAR_STATUS[st] || 'good' }); } else bar = metricBar(m.value, m.goal, st, i);
          if (bar) t.insertBefore(bar, t.querySelector('.stat-tile-foot'));
          return t;
        })));
    }
    const w = a.weight;
    if (w && w.change != null) {
      const toGoal = w.remaining != null && w.goal != null
        ? (Math.abs(w.remaining) < 0.2 ? 'Цель достигнута' : `До цели ${fmtN(Math.abs(w.remaining), 1)} кг` + (w.eta_weeks ? `, при таком темпе ≈ ${w.eta_weeks} нед.` : ''))
        : null;
      const spark = w.points.length < 3 ? null : sparkline({ values: w.points.map((p) => p.v), width: 120, height: 48, animate: !reducedMotion() });
      kids.push(sectionTitle('Вес'),
        h('section', { class: 'card an-weight' },
          h('div', { class: 'an-w-top' },
            h('div', null, h('div', { class: 'an-w-val' }, signed(w.change)), h('div', { class: 'muted small' }, `тренд за период · ${signed(w.per_week, 2)} в нед.`)),
            spark ? h('div', { class: 'an-w-spark' }, spark) : null),
          toGoal ? h('div', { class: 'an-w-goal' }, toGoal) : null,
          w.expected_change != null ? h('div', { class: 'muted small' }, `По записанной еде ожидалось около ${signed(w.expected_change)}`) : null,
          h('button', { class: 'an-w-link', type: 'button', onclick: () => {
            try { localStorage.setItem('stats_range', a.period === 'week' ? 'week' : 'month'); } catch (e) { /* ignore */ }
            nav.go('/stats');
          } }, 'График', icon('next', 16))));
    } else if (w && w.weighins) {
      kids.push(sectionTitle('Вес'), h('div', { class: 'card an-tip' }, 'Взвешиваний пока мало: для тренда нужно хотя бы два замера за период.'));
    }
    if (a.good.length) kids.push(sectionTitle('Что получилось'), pills(a.good));
    if (a.patterns.length) {
      kids.push(sectionTitle('Закономерности'),
        h('div', { class: 'stack-sm' }, a.patterns.map((t) => h('div', { class: 'card an-tip an-pattern' }, glyph(t.icon, 36),
          h('div', { class: 'an-tip-body' }, h('div', { class: 'an-tip-title' }, t.title), h('div', null, t.text))))));
    }
    if (a.tips.length) kids.push(sectionTitle('Что поправить'), h('div', { class: 'stack-sm' }, a.tips.map(tipCard)));
    if (a.logged_days < Math.ceil(a.days / 2)) {
      kids.push(h('div', { class: 'muted small an-foot' }, 'Записей немного, поэтому выводы приблизительные.'));
    }
    body.replaceChildren(...kids);
    staggerIn(body, kids);
  }

  function draw(a) {
    const kids = [heroVerdict(a.rows, a.headline, a.closed ? 'Итог дня по записям и данным браслета' : 'День ещё идёт: недобор пока не считаем')];

    if (a.rows.length) {
      kids.push(h('div', { class: 'card an-rows2' }, a.rows.map((r, i) => h('div', { class: 'an2-row' },
        h('div', { class: 'an2-top' },
          h('span', { class: 'an-ico ' + r.status }, glyph(r.label, 30)),
          h('span', { class: 'an-label' }, r.label),
          h('span', { class: 'an2-val' }, r.value, r.status === 'none' ? null : h('span', { class: 'muted small' }, ' из ' + r.goal))),
        metricBar(r.value, r.goal, r.status, i)))));
    }
    if (a.good.length) kids.push(sectionTitle('Что получилось'), pills(a.good));
    if (a.tips.length) {
      kids.push(focusCard('Главное на завтра', a.tips[0]));
      if (a.tips.length > 1) kids.push(sectionTitle('Ещё поправить'), h('div', { class: 'stack-sm' }, a.tips.slice(1).map(tipCard)));
    }
    if (!a.good.length && !a.tips.length) {
      kids.push(emptyState('sparkles', 'Пока нечего анализировать', 'Запишите еду, воду и сон, и здесь появится разбор дня.',
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => nav.go('/add/meal') }, 'Добавить приём пищи')));
    }
    if (curDate === todayISO()) kids.push(weekGoalBlock(ctx, { withRecap: true }));
    body.replaceChildren(...kids);
    staggerIn(body, kids);
  }

  load();
}
