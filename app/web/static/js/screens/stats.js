// Screen 2: "Статистика"
import { api } from '../api.js';
import { lineChart, barChart, stackedBars } from '../charts.js';
import { h, pageHead, segmented, skeletonCards, errorState, emptyState, fmtN, fmtKcal, fmtDur, shortDate } from '../ui.js';

const RANGES = [['week', 'Неделя'], ['month', 'Месяц'], ['90d', '90 дней']];

function loadRange() {
  try { const v = localStorage.getItem('stats_range'); return RANGES.some((r) => r[0] === v) ? v : 'week'; } catch (e) { return 'week'; }
}
function saveRange(v) { try { localStorage.setItem('stats_range', v); } catch (e) { /* ignore */ } }

export default async function render(root, ctx) {
  let range = loadRange();
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' });
  view.append(pageHead('Статистика', { back: false }), segmented(RANGES, range, (v) => { range = v; saveRange(v); load(); }, 'Период'), body);
  root.append(view);

  async function load() {
    body.replaceChildren(skeletonCards(3, 110));
    try {
      const data = await api.get('/stats', { range });
      if (!ctx.alive()) return;
      draw(data);
    } catch (e) { if (ctx.alive()) errorState(body, e, load); }
  }

  function draw({ days, summary: s }) {
    if (!days.length || (!s.days_logged && s.avg_sleep_min == null && s.avg_steps == null && s.weight_change == null)) {
      body.replaceChildren(emptyState('chart-bar', 'Пока мало данных', 'Записывайте приёмы пищи несколько дней, и здесь появятся графики.'));
      return;
    }
    const lbl = (d) => shortDate(d.date);
    const tile = (title, value, sub) => h('div', { class: 'tile' }, h('div', { class: 'muted small' }, title), h('div', { class: 'tile-val' }, value), sub ? h('div', { class: 'muted small' }, sub) : null);
    const wc = s.weight_change;
    const tiles = h('div', { class: 'card stat-group' },
      tile('Средние ккал', s.avg_kcal != null ? fmtKcal(s.avg_kcal) : '—', s.avg_protein != null ? `белка ${fmtN(s.avg_protein)} г` : null),
      tile('Дней в норме', s.days_logged ? `${s.days_on_target} из ${s.days_logged}` : '—', 'по дням с записями'),
      tile('Вес (тренд)', wc == null ? '—' : (wc > 0 ? '+' : wc < 0 ? '−' : '') + fmtN(Math.abs(wc)) + ' кг', 'за период'),
      tile('Средний сон', s.avg_sleep_min != null ? fmtDur(s.avg_sleep_min) : '—'),
      tile('Средние шаги', s.avg_steps != null ? fmtKcal(s.avg_steps) : '—'));
    tiles.lastChild.classList.add('span2');

    const card = (title, chart, legend) => h('section', { class: 'card chart-card' }, h('div', { class: 'card-title' }, title), chart, legend || null);
    const target = [...days].reverse().find((d) => d.target_kcal)?.target_kcal || null;
    const legend = h('div', { class: 'legend' },
      [['p', 'Белки'], ['f', 'Жиры'], ['c', 'Углеводы']].map(([k, t]) => h('span', null, h('i', { class: 'dot dot-' + k }), t)));

    body.replaceChildren(
      tiles,
      card('Вес и тренд, кг', lineChart({ points: days.map((d) => ({ label: lbl(d), y: d.weight, t: d.weight_trend })), decimals: 1, unit: 'кг', color: 'var(--c-accent)' }),
        h('div', { class: 'legend' }, h('span', null, h('i', { class: 'dot dot-accent' }), 'Тренд'), h('span', null, h('i', { class: 'dot dot-faint' }), 'Взвешивания'))),
      card('Калории и норма, ккал', barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.kcal })), target, targetLabel: target ? 'норма ' + fmtKcal(target) : '', overTarget: true, color: 'var(--c-accent)' })),
      card('Белки, жиры, углеводы, г', stackedBars({ bars: days.map((d) => ({ label: lbl(d), parts: d.kcal == null ? null : [d.protein, d.fat, d.carbs] })) }), legend),
      card('Сон, часы', barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.sleep_min == null ? null : d.sleep_min / 60 })), decimals: 1, color: 'var(--c-sleep)', unitFmt: (v) => fmtDur(v * 60) })),
      card('Шаги', barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.steps })), color: 'var(--c-steps)' })),
    );
  }

  await load();
}
