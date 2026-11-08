// Screen 2: "Статистика" — «как я иду»: герой «Вес», календарь дней в норме, показатели, динамика, ссылка на разбор
import { api } from '../api.js';
import { lineChart, barChart, stackedBars, makeTip } from '../charts.js';
import { h, icon, tile, nav, segmented, skeletonCards, holdHeight, errorState, emptyState, heroCard, progressBar, statTile, countUp, fmtN, fmtKcal, fmtDur, shortDate, longDate, parseISO, addDays, todayISO, reducedMotion, staggerIn, haptic, plural } from '../ui.js';

const RANGES = [['week', 'Неделя'], ['month', 'Месяц'], ['90d', '90 дней']];
const METRICS = [['kcal', 'Калории'], ['macro', 'БЖУ'], ['water', 'Вода'], ['sleep', 'Сон'], ['steps', 'Шаги']];

function loadRange() {
  try { const v = localStorage.getItem('stats_range'); return RANGES.some((r) => r[0] === v) ? v : 'week'; } catch (e) { return 'week'; }
}
function loadMetric() {
  try { const v = localStorage.getItem('stats_metric'); return METRICS.some((r) => r[0] === v) ? v : 'kcal'; } catch (e) { return 'kcal'; }
}
const seenRanges = new Set(); // графики анимируются только при первом показе диапазона
function saveRange(v) { try { localStorage.setItem('stats_range', v); } catch (e) { /* ignore */ } }
function saveMetric(v) { try { localStorage.setItem('stats_metric', v); } catch (e) { /* ignore */ } }

const dowFmt = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' });
const DOW = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const dowIdx = (iso) => (parseISO(iso).getDay() + 6) % 7;
const cap1 = (t) => t.charAt(0).toUpperCase() + t.slice(1);

// Календарь «дни в норме»: ячейка = день. ok = 80–120 % нормы, low = ниже, over = выше, none = нет записей
function kcalClass(d, target) {
  const t = d.target_kcal || target;
  if (d.kcal == null || !t) return 'none';
  const r = d.kcal / t;
  return r > 1.2 ? 'over' : r < 0.8 ? 'low' : 'ok';
}

function heatmap(days, target, range, animate) {
  const wrap = h('div', { class: 'hm-wrap' });
  const week = range === 'week';
  const cells = [];
  if (!week) for (let i = 0; i < dowIdx(days[0].date); i += 1) cells.push(h('span', { class: 'hm-pad', 'aria-hidden': 'true' }));
  const lead = cells.length;
  const today = todayISO();
  const tip = makeTip(wrap);
  days.forEach((d, i) => {
    const cl = kcalClass(d, target);
    const t = d.target_kcal || target;
    const pct = d.kcal != null && t ? Math.round((d.kcal / t) * 100) : null;
    const text = d.kcal != null ? [cap1(dowFmt.format(parseISO(d.date))), `${fmtKcal(d.kcal)} ккал${pct != null ? ' · ' + pct + '% нормы' : ''}`] : [cap1(dowFmt.format(parseISO(d.date))), 'нет записей'];
    const cell = h('button', { type: 'button', class: `hm-cell hm-${cl}${d.date === today ? ' hm-today' : ''}`, 'aria-label': text.join(', '), onclick: () => {
      haptic('selection');
      const wr = wrap.getBoundingClientRect(); const r = cell.getBoundingClientRect();
      tip.show(r.left - wr.left + r.width / 2, r.top - wr.top, text);
    } }, week ? h('span', { class: 'hm-kcal' }, d.kcal != null ? String(Math.round(d.kcal)) : '') : range === 'month' ? h('span', { class: 'hm-day' }, String(parseISO(d.date).getDate())) : null);
    if (animate) { cell.classList.add('hm-in'); cell.style.setProperty('--d', ((week ? i : Math.floor((i + lead) / 7) + ((i + lead) % 7)) * 8) + 'ms'); }
    cells.push(cell);
  });
  const labels = week ? days.map((d) => DOW[dowIdx(d.date)]) : DOW;
  wrap.prepend(h('div', { class: 'hm-dow' }, labels.map((t) => h('span', null, t))));
  wrap.insertBefore(h('div', { class: `hm-grid hm-${range}` }, cells), tip ? wrap.querySelector('.ch-tip') : null);
  return wrap;
}

// Серия: сколько последних дней подряд в коридоре (если сегодня ещё нет записей, считаем со вчера)
function streakOf(days, target) {
  let i = days.length - 1;
  if (days[i] && days[i].kcal == null) i -= 1;
  let n = 0;
  for (; i >= 0 && kcalClass(days[i], target) === 'ok'; i -= 1) n += 1;
  return n;
}

export default async function render(root, ctx) {
  let range = loadRange();
  let metric = loadMetric();
  const view = h('div', { class: 'screen' });
  const body = h('div', { class: 'stack' });
  view.append(segmented(RANGES, range, (v) => { range = v; saveRange(v); load(); }, 'Период'), body);
  root.append(view);

  async function load() {
    const req = range;
    // скелетон только при первом показе; при смене периода старые графики остаются, пока едут новые
    let release = null;
    if (body.hasChildNodes()) release = holdHeight(body); else body.replaceChildren(skeletonCards(3, 110));
    try {
      const [data, me] = await Promise.all([api.get('/stats', { range: req }), api.me().catch(() => null)]);
      if (!ctx.alive() || req !== range) return;      // ответ на уже сменённый период
      const animate = !seenRanges.has(range) && !reducedMotion();
      seenRanges.add(range);
      draw(data, me, animate);
      if (release) release();
    } catch (e) {
      if (!ctx.alive() || req !== range) return;
      errorState(body, e, load);
      if (release) release();
    }
  }

  function draw({ days, summary: s, prev_summary: ps, goals }, me, animate) {
    if (!days.length || (!s.days_logged && s.avg_sleep_min == null && s.avg_steps == null && s.weight_change == null)) {
      body.replaceChildren(emptyState('chart-bar', 'Пока мало данных', 'Записывайте приёмы пищи несколько дней, и здесь появятся графики.'));
      return;
    }
    goals = goals || {};
    ps = ps || {};
    const lbl = (d) => shortDate(d.date);
    const n = days.length;
    const target = [...days].reverse().find((d) => d.target_kcal)?.target_kcal || null;
    const perWord = { week: 'за неделю', month: 'за месяц', '90d': 'за 90 дней' }[range];
    const kids = [];

    // ---- 1. Герой «Вес»
    const trendVals = days.map((d) => (d.weight_trend != null ? d.weight_trend : d.weight));
    const lastW = [...trendVals].reverse().find((v) => v != null);
    const goalW = (goals.goal_weight_kg) || (me && me.user && me.user.goal_weight_kg) || null;
    const startW = me && me.weight && me.weight.start != null ? me.weight.start : null;
    const wc = s.weight_change;
    if (lastW != null) {
      const num = h('span', { class: 'st-hero-num' });
      const f1 = (v) => v.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      if (animate) countUp(num, lastW, { fmt: f1, ms: 800 }); else num.textContent = f1(lastW);
      const wantDown = goalW != null ? goalW < lastW : null;
      const chipCls = wc != null && wc < 0 && wantDown ? 'good' : 'flat';
      const chart = lineChart({ points: days.map((d) => ({ label: lbl(d), y: d.weight, t: d.weight_trend })), decimals: 1, unit: 'кг', color: 'var(--c-accent)', animate, goal: goalW });
      const goalInChart = chart.dataset && chart.dataset.goal === '1';
      const hero = heroCard({ tone: 'accent', cls: 'st-hero', children: [
        h('div', { class: 'st-hero-head' },
          h('div', null, h('div', { class: 'st-hero-k' }, 'Вес по тренду'),
            h('div', { class: 'st-hero-val' }, num, h('span', { class: 'st-hero-unit' }, ' кг')),
            goalW && !goalInChart ? h('div', { class: 'muted small' }, `цель ${fmtN(goalW)} кг`) : null),
          wc != null && Math.abs(wc) >= 0.05 ? h('span', { class: 'chip-delta ' + chipCls }, `${wc < 0 ? '−' : '+'}${fmtN(Math.abs(wc))} кг ${perWord}`) : null),
        chart] });
      // старт → цель и прогноз
      if (goalW != null) {
        if (startW != null && Math.abs(startW - goalW) > 0.05) {
          const total = Math.abs(startW - goalW);
          const done = Math.max(0, Math.min(total, goalW < startW ? startW - lastW : lastW - startW));
          hero.append(h('div', { class: 'st-goal' },
            progressBar(done, total, { status: 'good', label: 'Прогресс к цели' }),
            h('div', { class: 'st-goal-ends muted small' }, h('span', null, fmtN(startW) + ' кг'), h('span', null, fmtN(goalW) + ' кг'))));
        }
        const left = Math.abs(lastW - goalW);
        let line = left < 0.05 ? 'Цель достигнута' : `до цели ${fmtN(left)} кг`;
        const idx = trendVals.map((v, i) => (v != null ? i : -1)).filter((i) => i >= 0);
        if (left >= 0.05 && idx.length > 1 && idx[idx.length - 1] - idx[0] >= 5) {
          const rate = (trendVals[idx[idx.length - 1]] - trendVals[idx[0]]) / (idx[idx.length - 1] - idx[0]);
          if (goalW < lastW && rate < 0) {
            const need = Math.ceil((lastW - goalW) / -rate);
            if (need <= 730) line += ` · в этом темпе ≈ к ${longDate(addDays(todayISO(), need))}`;
          }
        }
        hero.append(h('div', { class: 'st-hero-foot' }, line));
      }
      kids.push(hero);
    }

    // ---- 2. Дни в норме: календарь
    if (s.days_logged) {
      const streak = streakOf(days, target);
      kids.push(h('section', { class: 'card st-heat' },
        h('div', { class: 'st-heat-head' },
          h('div', { class: 'st-heat-title' }, h('b', null, String(s.days_on_target)), ` из ${n} дней в норме`),
          streak >= 2 ? h('span', { class: 'st-streak' }, icon('flame', 14), `серия ${plural(streak, 'день', 'дня', 'дней')}`) : null),
        heatmap(days, target, range, animate),
        h('div', { class: 'legend st-heat-legend' },
          h('span', null, h('i', { class: 'dot dot-good' }), 'в норме'), h('span', null, h('i', { class: 'dot dot-low' }), 'ниже'),
          h('span', null, h('i', { class: 'dot dot-over' }), 'выше'), h('span', null, h('i', { class: 'dot dot-none' }), 'нет записей'))));
    }

    // ---- 3. Показатели 2×3
    const chip = (cur, prev, unit, better, ref) => {
      if (cur == null || prev == null || !prev) return null;
      const diff = cur - prev;
      if (Math.abs(diff) / Math.abs(prev) < 0.03) return h('span', { class: 'chip-delta flat' }, 'как раньше');
      const good = better === 'near' ? Math.abs(cur - ref) < Math.abs(prev - ref) : (diff > 0) === (better === 'up');
      return h('span', { class: 'chip-delta ' + (good ? 'good' : 'flat') }, (diff > 0 ? '+' : '−') + fmtKcal(Math.abs(diff)) + unit);
    };
    const share = (v, g) => (v != null && g ? v / g : null);
    const stOf = (r) => (r == null ? 'none' : r >= 0.9 ? 'good' : r >= 0.6 ? 'ok' : 'bad');
    const val = (v, f, unit) => {
      if (v == null) return '—';
      const num = h('span', null);
      if (animate && !unit.fmtOnly) countUp(num, v, { fmt: f, ms: 700 }); else num.textContent = f(v);
      return h('span', null, num, unit.u ? h('span', { class: 'stat-unit' }, ' ' + unit.u) : null);
    };
    const kr = share(s.avg_kcal, target);
    const tiles = [
      statTile({ icon: 'tools-kitchen', color: 'orange', title: 'Калории, ср.', value: val(s.avg_kcal, fmtKcal, { u: 'ккал' }), bar: target && s.avg_kcal != null ? [s.avg_kcal, target] : null,
        status: kr == null ? 'none' : kr > 1.2 ? 'bad' : kr >= 0.8 ? 'good' : 'ok', delta: chip(s.avg_kcal, ps.avg_kcal, ' ккал', 'near', target), caption: target ? 'норма ' + fmtKcal(target) : null }),
      statTile({ icon: 'egg-fried', color: 'blue', title: 'Белок, ср.', value: val(s.avg_protein, fmtN, { u: 'г' }), bar: goals.protein_g && s.avg_protein != null ? [s.avg_protein, goals.protein_g] : null,
        status: stOf(share(s.avg_protein, goals.protein_g)), delta: chip(s.avg_protein, ps.avg_protein, ' г', 'up'), caption: goals.protein_g ? 'цель ' + goals.protein_g : null }),
      statTile({ icon: 'droplet', color: 'teal', title: 'Вода, ср.', value: val(s.avg_water, fmtKcal, { u: 'мл' }), bar: goals.water_ml && s.avg_water != null ? [s.avg_water, goals.water_ml] : null,
        status: stOf(share(s.avg_water, goals.water_ml)), delta: chip(s.avg_water, ps.avg_water, ' мл', 'up'), caption: goals.water_ml ? 'цель ' + fmtKcal(goals.water_ml) : null }),
      statTile({ icon: 'bed', color: 'indigo', title: 'Сон, ср.', value: val(s.avg_sleep_min, (v) => fmtDur(v), { u: '', fmtOnly: true }), bar: s.avg_sleep_min != null ? [s.avg_sleep_min, goals.sleep_min || 420] : null,
        status: stOf(share(s.avg_sleep_min, goals.sleep_min || 420)), delta: chip(s.avg_sleep_min, ps.avg_sleep_min, ' мин', 'up'), caption: 'цель 7 ч' }),
      statTile({ icon: 'walk', color: 'green', title: 'Шаги, ср.', value: val(s.avg_steps, fmtKcal, { u: '' }), bar: s.avg_steps != null ? [s.avg_steps, goals.steps || 8000] : null,
        status: stOf(share(s.avg_steps, goals.steps || 8000)), delta: chip(s.avg_steps, ps.avg_steps, '', 'up'), caption: 'цель ' + fmtKcal(goals.steps || 8000) }),
      statTile({ icon: 'calendar', color: 'purple', title: 'Дни с записями', value: h('span', null, String(s.days_logged), h('span', { class: 'stat-unit' }, ` из ${n}`)), bar: [s.days_logged, n],
        status: stOf(s.days_logged / n), delta: null, caption: null }),
    ];
    kids.push(h('div', { class: 'stat-grid' }, tiles));

    // ---- 4. Динамика: один график с переключателем
    const noteEl = h('span', { class: 'card-head-note muted small' });
    const capEl = h('div', { class: 'chart-cap' });
    const legendEl = h('div', { class: 'legend', style: { minHeight: '18px' } });
    const chartBox = h('div', { class: 'dyn-box' });
    const loggedKcal = days.filter((d) => d.kcal != null);
    const over = target ? loggedKcal.filter((d) => d.kcal > target * 1.2).length : 0;
    const under = target ? loggedKcal.filter((d) => d.kcal < target * 0.8).length : 0;
    const wgoal = goals.water_ml || null;
    const sgoal = goals.steps || 8000;
    const cnt = (arr, f) => arr.filter(f).length;
    function dyn(m, anim) {
      let chart; let note = ''; let cap = null; let leg = null;
      if (m === 'kcal') {
        chart = barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.kcal })), target, animate: anim, color: 'var(--c-accent)', unit: 'ккал', status: target ? (v) => (v > target * 1.2 ? 'over' : v < target * 0.8 ? 'low' : 'good') : null });
        note = target ? 'норма ' + fmtKcal(target) : '';
        cap = loggedKcal.length && target ? `В норме ${s.days_on_target} из ${loggedKcal.length} дн.` + (over ? ` · выше нормы ${over}` : '') + (under ? ` · ниже ${under}` : '') : null;
        leg = target ? [['good', 'в норме'], ['low', 'ниже'], ['over', 'выше']] : null;
      } else if (m === 'macro') {
        chart = stackedBars({ bars: days.map((d) => ({ label: lbl(d), parts: d.kcal == null ? null : [d.protein, d.fat, d.carbs] })), animate: anim });
        note = goals.protein_g ? 'белок ' + goals.protein_g + ' г' : '';
        cap = s.avg_protein != null ? `Белка в среднем ${fmtN(s.avg_protein)} г в день` + (goals.protein_g ? `, цель ${goals.protein_g} г` : '') : null;
        leg = [['p', 'Белки'], ['f', 'Жиры'], ['c', 'Углеводы']];
      } else if (m === 'water') {
        chart = barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.water_ml })), target: wgoal, animate: anim, color: 'var(--c-water)', unit: 'мл' });
        note = wgoal ? 'цель ' + fmtKcal(wgoal) : '';
        const wd = days.filter((d) => d.water_ml);
        cap = wd.length && wgoal ? `Цель достигнута ${cnt(wd, (d) => d.water_ml >= wgoal * 0.9)} дн. из ${wd.length}` : null;
      } else if (m === 'sleep') {
        chart = barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.sleep_min == null ? null : d.sleep_min / 60 })), decimals: 1, target: 7, animate: anim, color: 'var(--c-sleep)', unitFmt: (v) => fmtDur(v * 60) });
        note = 'норма 7 ч';
        const sd = days.filter((d) => d.sleep_min != null);
        cap = sd.length ? `Меньше 6 часов: ${cnt(sd, (d) => d.sleep_min < 360)} дн. из ${sd.length}` : null;
      } else {
        chart = barChart({ bars: days.map((d) => ({ label: lbl(d), v: d.steps })), target: sgoal, animate: anim, color: 'var(--c-steps)', unit: 'шагов' });
        note = 'цель ' + fmtKcal(sgoal);
        const st = days.filter((d) => d.steps != null);
        cap = st.length ? `Цель достигнута ${cnt(st, (d) => d.steps >= sgoal)} дн. из ${st.length}` : null;
      }
      noteEl.textContent = note;
      capEl.textContent = cap || '';
      capEl.hidden = !cap;
      legendEl.replaceChildren(...(leg || []).map(([k, t]) => h('span', null, h('i', { class: 'dot dot-' + k }), t)));
      legendEl.style.visibility = leg ? '' : 'hidden';
      chartBox.replaceChildren(chart);
      if (anim || !reducedMotion()) { chartBox.classList.remove('xfade'); void chartBox.offsetWidth; chartBox.classList.add('xfade'); }
    }
    const chipBtns = METRICS.map(([k, t]) => h('button', { type: 'button', role: 'tab', class: 'mchip' + (k === metric ? ' active' : ''), 'aria-selected': String(k === metric), onclick: () => {
      if (k === metric) return;
      metric = k; saveMetric(k); haptic('selection');
      chipBtns.forEach((b, i) => { const on = METRICS[i][0] === k; b.classList.toggle('active', on); b.setAttribute('aria-selected', String(on)); });
      dyn(k, !reducedMotion());
    } }, t));
    kids.push(h('section', { class: 'card chart-card' },
      h('div', { class: 'card-head' }, h('div', { class: 'card-title' }, 'Динамика'), noteEl),
      h('div', { class: 'mchips', role: 'tablist', 'aria-label': 'Показатель' }, chipBtns), chartBox, legendEl, capEl));
    dyn(metric, animate);

    // ---- 5. Ссылка на разбор
    const first = [];
    if (s.days_logged) first.push(s.days_on_target / s.days_logged >= 0.7 ? `Калории держатся в норме: ${s.days_on_target} из ${s.days_logged} дн.` : `В норму по калориям попали ${s.days_on_target} из ${s.days_logged} дн.`);
    if (wc != null) first.push(wc <= -0.2 ? `Тренд веса идёт вниз: ${fmtN(Math.abs(wc))} кг ${perWord}.` : wc >= 0.2 ? `Вес вырос на ${fmtN(wc)} кг ${perWord}.` : 'Вес держится на месте.');
    const sub = first[0] || 'Что получилось и что поправить';
    const aperiod = range === 'week' ? 'week' : 'month';
    kids.push(h('button', { class: 'link-card', type: 'button', onclick: () => {
      try { localStorage.setItem('an_period', aperiod); } catch (e) { /* ignore */ }
      nav.go('/analysis');
    } }, tile('sparkles', 'purple', 40),
    h('span', { class: 'link-card-text' }, h('span', { class: 'link-card-title' }, `Разбор и советы за ${aperiod === 'week' ? 'неделю' : 'месяц'}`), h('span', { class: 'link-card-sub' }, sub)),
    icon('next', 18)));

    body.replaceChildren(...kids);
    if (animate) staggerIn(body);
  }

  await load();
}
