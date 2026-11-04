// Screen 2: "Статистика"
import { api } from '../api.js';
import { lineChart, barChart, stackedBars } from '../charts.js';
import { h, nav, pageHead, segmented, skeletonCards, errorState, emptyState, fmtN, fmtKcal, fmtDur, shortDate } from '../ui.js';

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

  function draw({ days, summary: s, prev_summary: ps, goals }) {
    if (!days.length || (!s.days_logged && s.avg_sleep_min == null && s.avg_steps == null && s.weight_change == null)) {
      body.replaceChildren(emptyState('chart-bar', 'Пока мало данных', 'Записывайте приёмы пищи несколько дней, и здесь появятся графики.'));
      return;
    }
    goals = goals || {};
    const lbl = (d) => shortDate(d.date);
    const n = days.length;
    const target = [...days].reverse().find((d) => d.target_kcal)?.target_kcal || null;
    const per = RANGES.find((r) => r[0] === range)[1].toLowerCase();

    // chip: change vs the previous period; better = 'up' | 'down' | 'near'(closer to target)
    const chip = (cur, prev, unit, better, ref) => {
      if (cur == null || prev == null || !prev) return null;
      const diff = cur - prev;
      if (Math.abs(diff) / Math.abs(prev) < 0.03) return h('span', { class: 'chip-delta flat' }, 'как раньше');
      const good = better === 'near' ? Math.abs(cur - ref) < Math.abs(prev - ref) : (diff > 0) === (better === 'up');
      return h('span', { class: 'chip-delta ' + (good ? 'good' : 'bad') }, (diff > 0 ? '+' : '−') + fmtKcal(Math.abs(diff)) + unit);
    };
    const tile = (title, value, sub, delta) => h('div', { class: 'tile' }, h('div', { class: 'muted small' }, title), h('div', { class: 'tile-val' }, value),
      sub ? h('div', { class: 'muted small' }, sub) : null, delta || null);
    const wc = s.weight_change;
    const tiles = h('div', { class: 'card stat-group' },
      tile('Средние ккал', s.avg_kcal != null ? fmtKcal(s.avg_kcal) : '—', target ? `норма ${fmtKcal(target)}` : null, chip(s.avg_kcal, ps && ps.avg_kcal, '', 'near', target)),
      tile('Дней в норме', s.days_logged ? `${s.days_on_target} из ${s.days_logged}` : '—', 'коридор 80–120 % нормы', chip(s.days_on_target, ps && ps.days_on_target, '', 'up')),
      tile('Белок', s.avg_protein != null ? fmtN(s.avg_protein) + ' г' : '—', goals.protein_g ? `цель ${goals.protein_g} г` : null, chip(s.avg_protein, ps && ps.avg_protein, ' г', 'up')),
      tile('Вес (тренд)', wc == null ? '—' : (wc > 0 ? '+' : wc < 0 ? '−' : '') + fmtN(Math.abs(wc)) + ' кг', 'за период', null),
      tile('Средний сон', s.avg_sleep_min != null ? fmtDur(s.avg_sleep_min) : '—', 'цель 7+ ч', chip(s.avg_sleep_min, ps && ps.avg_sleep_min, ' мин', 'up')),
      tile('Средние шаги', s.avg_steps != null ? fmtKcal(s.avg_steps) : '—', `цель ${fmtKcal(goals.steps || 8000)}`, chip(s.avg_steps, ps && ps.avg_steps, '', 'up')));

    // plain-language summary: what it means, not just numbers
    const lines = [];
    if (s.days_logged) {
      const rate = s.days_on_target / s.days_logged;
      lines.push(s.days_logged < Math.ceil(n / 2)
        ? `Еда записана за ${s.days_logged} из ${n} дней: картина пока неполная.`
        : rate >= 0.7 ? `Калории держатся в норме: ${s.days_on_target} из ${s.days_logged} дней.` : `В норму по калориям попали ${s.days_on_target} из ${s.days_logged} дней.`);
    }
    if (wc != null) lines.push(wc <= -0.2 ? `Тренд веса идёт вниз: ${fmtN(Math.abs(wc))} кг за ${per}.` : wc >= 0.2 ? `Вес вырос на ${fmtN(wc)} кг за ${per}.` : 'Вес держится на месте.');
    if (s.avg_protein != null && goals.protein_g) lines.push(s.avg_protein >= goals.protein_g * 0.9 ? 'Белка хватает.' : `Белка маловато: в среднем ${fmtN(s.avg_protein)} из ${goals.protein_g} г.`);
    if (s.avg_sleep_min != null && s.avg_sleep_min < 390) lines.push(`Сон короткий: в среднем ${fmtDur(s.avg_sleep_min)}.`);
    const summary = h('section', { class: 'card st-summary' },
      h('div', { class: 'card-title' }, 'Что видно'),
      h('ul', { class: 'st-lines' }, lines.map((l) => h('li', null, l))),
      h('button', { class: 'btn btn-secondary st-more', type: 'button', onclick: () => nav.go('/analysis') }, 'Подробный разбор и советы'));

    // short caption under each chart: what the chart says
    const cap = (text) => (text ? h('div', { class: 'chart-cap' }, text) : null);
    const loggedKcal = days.filter((d) => d.kcal != null);
    const over = target ? loggedKcal.filter((d) => d.kcal > target * 1.2).length : 0;
    const under = target ? loggedKcal.filter((d) => d.kcal < target * 0.8).length : 0;
    const kcalCap = loggedKcal.length && target ? `В норме ${s.days_on_target} из ${loggedKcal.length} дн.` + (over ? ` · выше нормы ${over}` : '') + (under ? ` · ниже ${under}` : '') : null;
    const weighins = days.filter((d) => d.weight != null).length;
    const weightCap = weighins < 2 ? 'Для тренда нужно хотя бы два взвешивания.' : 'Тренд сглаживает колебания воды: смотрите на него, а не на отдельные цифры.';
    const sleepDays = days.filter((d) => d.sleep_min != null);
    const sleepCap = sleepDays.length ? `Меньше 6 часов: ${sleepDays.filter((d) => d.sleep_min < 360).length} дн. из ${sleepDays.length}` : null;
    const stepDays = days.filter((d) => d.steps != null);
    const stepsCap = stepDays.length ? `Цель ${fmtKcal(goals.steps || 8000)} шагов достигнута ${stepDays.filter((d) => d.steps >= (goals.steps || 8000)).length} дн. из ${stepDays.length}` : null;
    const waterDays = days.filter((d) => d.water_ml);
    const wgoal = goals.water_ml || null;
    const waterCap = waterDays.length && wgoal ? `Цель ${fmtKcal(wgoal)} мл достигнута ${waterDays.filter((d) => d.water_ml >= wgoal * 0.9).length} дн. из ${waterDays.length}` : null;
    const macroCap = s.avg_protein != null ? `Белка в среднем ${fmtN(s.avg_protein)} г в день` + (goals.protein_g ? `, цель ${goals.protein_g} г` : '') : null;

    const card = (title, chart, legend, caption) => h('section', { class: 'card chart-card' }, h('div', { class: 'card-title' }, title), chart, legend || null, cap(caption));
    const legend = h('div', { class: 'legend' },
      [['p', 'Белки'], ['f', 'Жиры'], ['c', 'Углеводы']].map(([k, t]) => h('span', null, h('i', { class: 'dot dot-' + k }), t)));

    body.replaceChildren(
      tiles, summary,
      card('Вес и тренд, кг', lineChart({ points: days.map((d) => ({ label: lbl(d), y: d.weight, t: d.weight_trend })), decimals: 1, unit: 'кг', color: 'var(--c-accent)' }),
        h('div', { class: 'legend' }, h('span', null, h('i', { class: 'dot dot-accent' }), 'Тренд'), h('span', null, h('i', { class: 'dot dot-faint' }), 'Взвешивания')), weightCap),
      card('Калории и норма, ккал', barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.kcal })), target, targetLabel: target ? 'норма ' + fmtKcal(target) : '', overTarget: true, color: 'var(--c-accent)' }), null, kcalCap),
      card('Белки, жиры, углеводы, г', stackedBars({ bars: days.map((d) => ({ label: lbl(d), parts: d.kcal == null ? null : [d.protein, d.fat, d.carbs] })) }), legend, macroCap),
      card('Вода, мл', barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.water_ml })), target: wgoal, targetLabel: wgoal ? 'цель ' + fmtKcal(wgoal) : '', color: 'var(--c-water, #30b0c7)' }), null, waterCap),
      card('Сон, часы', barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.sleep_min == null ? null : d.sleep_min / 60 })), decimals: 1, target: 7, targetLabel: 'цель 7 ч', color: 'var(--c-sleep)', unitFmt: (v) => fmtDur(v * 60) }), null, sleepCap),
      card('Шаги', barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.steps })), target: goals.steps || 8000, targetLabel: 'цель ' + fmtKcal(goals.steps || 8000), color: 'var(--c-steps)' }), null, stepsCap),
    );
  }

  await load();
}
