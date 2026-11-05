// Tiny own SVG charts. Colours come from CSS (--ch custom property + classes).
const NS = 'http://www.w3.org/2000/svg';

function s(tag, attrs, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  kids.forEach((k) => k && el.append(typeof k === 'string' ? document.createTextNode(k) : k));
  return el;
}

const W = 360;
const fmt = (v, d) => (Math.round(v * 10 ** d) / 10 ** d).toLocaleString('ru-RU', { maximumFractionDigits: d });

function empty(text = 'Нет данных за этот период') {
  const d = document.createElement('div');
  d.className = 'chart-empty muted';
  d.textContent = text;
  return d;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  const n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 4 ? 4 : f <= 6 ? 6 : f <= 8 ? 8 : 10;
  return n * p;
}

function xLabels(labels, x, y, n = 4) {
  const out = [];
  const len = labels.length;
  if (!len) return out;
  const idx = len <= n ? labels.map((_, i) => i) : Array.from({ length: n }, (_, i) => Math.round((i * (len - 1)) / (n - 1)));
  [...new Set(idx)].forEach((i, k, arr) => {
    const anchor = k === 0 ? 'start' : k === arr.length - 1 ? 'end' : 'middle';
    out.push(s('text', { x: x(i), y, class: 'ch-axis', 'text-anchor': anchor }, labels[i]));
  });
  return out;
}

function frame(h, ticks, lo, hi, pad, dec, yOf, labelsEls) {
  const g = s('g');
  ticks.forEach((t) => {
    const y = yOf(t);
    g.append(s('line', { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: 'ch-grid' }));
    g.append(s('text', { x: pad.l - 6, y: y + 3.5, class: 'ch-axis', 'text-anchor': 'end' }, fmt(t, dec)));
  });
  labelsEls.forEach((e) => g.append(e));
  return g;
}

let uidSeq = 0;
const uid = (p) => p + (++uidSeq);

/** Плашка-тултип поверх обёртки (position: relative). show(x, y, lines) в пикселях обёртки; скрывается тапом вне её. */
export function makeTip(wrap) {
  const el = document.createElement('div');
  el.className = 'ch-tip';
  el.setAttribute('role', 'status');
  wrap.append(el);
  const hide = () => el.classList.remove('on');
  const onDoc = (e) => {
    if (!wrap.isConnected) { document.removeEventListener('pointerdown', onDoc, true); return; }
    if (!wrap.contains(e.target)) hide();
  };
  document.addEventListener('pointerdown', onDoc, true);
  return {
    hide,
    show(x, y, lines) {
      el.replaceChildren(...lines.filter(Boolean).map((t, i) => {
        const d = document.createElement('div');
        d.className = i ? 'ch-tip-sub' : 'ch-tip-main';
        d.textContent = t;
        return d;
      }));
      el.classList.add('on');
      const w = el.offsetWidth;
      const hh = el.offsetHeight;
      const left = Math.max(0, Math.min(wrap.clientWidth - w, x - w / 2));
      const top = Math.max(0, y - hh - 10);
      el.style.transform = `translate(${left.toFixed(1)}px, ${top.toFixed(1)}px)`;
    },
  };
}

/** Оборачивает SVG: касание/ведение пальцем показывает маркер и тултип ближайшей точки. items: [{x, y, lines}] в координатах viewBox. */
function interactive(svg, items, box) {
  const wrap = document.createElement('div');
  wrap.className = 'chart-wrap';
  wrap.append(svg);
  if (!items.length) return wrap;
  const tip = makeTip(wrap);
  const marker = s('line', { y1: box.t, y2: box.b, class: 'ch-marker' });
  svg.append(marker);
  let down = false;
  const update = (e) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const k = rect.width / W;
    const vx = (e.clientX - rect.left) / k;
    let best = items[0];
    items.forEach((it) => { if (Math.abs(it.x - vx) < Math.abs(best.x - vx)) best = it; });
    marker.setAttribute('x1', best.x);
    marker.setAttribute('x2', best.x);
    marker.classList.add('on');
    tip.show(best.x * k, best.y * k, best.lines);
  };
  svg.addEventListener('pointerdown', (e) => { down = true; update(e); });
  svg.addEventListener('pointermove', (e) => { if (down || e.pointerType === 'mouse') update(e); });
  const up = () => { down = false; };
  svg.addEventListener('pointerup', up);
  svg.addEventListener('pointercancel', up);
  svg.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') { marker.classList.remove('on'); tip.hide(); } });
  document.addEventListener('pointerdown', function off(e) {
    if (!wrap.isConnected) { document.removeEventListener('pointerdown', off, true); return; }
    if (!wrap.contains(e.target)) marker.classList.remove('on');
  }, true);
  return wrap;
}

function topRound(x, y, w, hgt, r) {
  const rr = Math.max(0, Math.min(r, w / 2, hgt));
  return `M${x.toFixed(1)} ${(y + hgt).toFixed(1)}V${(y + rr).toFixed(1)}Q${x.toFixed(1)} ${y.toFixed(1)} ${(x + rr).toFixed(1)} ${y.toFixed(1)}H${(x + w - rr).toFixed(1)}Q${(x + w).toFixed(1)} ${y.toFixed(1)} ${(x + w).toFixed(1)} ${(y + rr).toFixed(1)}V${(y + hgt).toFixed(1)}Z`;
}

const axisCount = () => 4;

/**
 * Линия тренда с градиентной заливкой. points: [{label, y, t}] — y взвешивания (точки), t тренд.
 * goal: пунктир цели, если попадает в диапазон оси (wrap.dataset.goal = '1').
 */
export function lineChart({ points, height = 190, decimals = 1, color = 'var(--c-accent)', unit = '', animate = false, goal = null }) {
  const vals = points.flatMap((p) => [p.y, p.t]).filter((v) => v !== null && v !== undefined);
  if (!vals.length) return empty();
  const pad = { l: 36, r: 12, t: 16, b: 22 };
  const mn = Math.min(...vals);
  const mx = Math.max(...vals);
  const span = Math.max((mx - mn) * 0.25, 0.4);
  const lo = mn - span;
  const hi = mx + span;
  const iw = W - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const n = points.length;
  const x = (i) => pad.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
  const yOf = (v) => pad.t + ih - ((v - lo) / (hi - lo)) * ih;
  const ticks = [0, 1, 2].map((i) => lo + ((hi - lo) * i) / 2);
  const svg = s('svg', { viewBox: `0 0 ${W} ${height}`, class: 'chart', role: 'img', 'aria-label': 'График веса', style: `--ch:${color}` });
  const gid = uid('ag');
  svg.append(s('defs', null, s('linearGradient', { id: gid, x1: '0', y1: '0', x2: '0', y2: '1' },
    s('stop', { offset: '0', style: 'stop-color:var(--ch);stop-opacity:.3' }), s('stop', { offset: '1', style: 'stop-color:var(--ch);stop-opacity:0' }))));
  svg.append(frame(height, ticks, lo, hi, pad, decimals, yOf, xLabels(points.map((p) => p.label), x, height - 5, axisCount(n))));

  const inRange = goal != null && goal >= lo && goal <= hi;
  if (inRange) {
    svg.append(s('line', { x1: pad.l, x2: W - pad.r, y1: yOf(goal), y2: yOf(goal), class: 'ch-target' }));
    svg.append(s('text', { x: W - pad.r, y: yOf(goal) - 5, class: 'ch-axis ch-goal-label', 'text-anchor': 'end' }, `цель ${fmt(goal, decimals)}`));
  }
  const tIdx = points.map((p, i) => i).filter((i) => points[i].t !== null && points[i].t !== undefined);
  if (tIdx.length > 1) {
    const base = pad.t + ih;
    const line = tIdx.map((i, k) => `${k ? 'L' : 'M'}${x(i).toFixed(1)} ${yOf(points[i].t).toFixed(1)}`).join('');
    svg.append(s('path', { d: `${line}L${x(tIdx[tIdx.length - 1]).toFixed(1)} ${base}L${x(tIdx[0]).toFixed(1)} ${base}Z`, fill: `url(#${gid})`, class: 'ch-area' + (animate ? ' ch-fade' : '') }));
    svg.append(s('path', { d: line, class: 'ch-trend' + (animate ? ' ch-draw' : ''), pathLength: animate ? 1 : null }));
  }
  points.forEach((p, i) => {
    if (p.y !== null && p.y !== undefined) svg.append(s('circle', { cx: x(i), cy: yOf(p.y), r: n > 40 ? 1.7 : 2.6, class: 'ch-dot' }));
  });
  const last = tIdx.length ? tIdx[tIdx.length - 1] : undefined;
  if (last !== undefined) {
    svg.append(s('circle', { cx: x(last), cy: yOf(points[last].t), r: 5, class: 'ch-halo' }));
    svg.append(s('circle', { cx: x(last), cy: yOf(points[last].t), r: 4.5, class: 'ch-last' }));
  }
  const items = points.map((p, i) => {
    const v = p.t !== null && p.t !== undefined ? p.t : p.y;
    if (v === null || v === undefined) return null;
    const raw = p.y !== null && p.y !== undefined && p.t !== null && p.t !== undefined && Math.abs(p.y - p.t) >= 0.05 ? `взвешивание ${fmt(p.y, decimals)} ${unit}` : null;
    return { x: x(i), y: yOf(v), lines: [`${p.label} · ${fmt(v, decimals)} ${unit}`.trim(), raw] };
  }).filter(Boolean);
  const wrap = interactive(svg, items, { t: pad.t, b: pad.t + ih });
  if (inRange) wrap.dataset.goal = '1';
  return wrap;
}

/** bars: [{label, v}] target: number|null. Столбики со скруглёнными верхами, тап — тултип. */
export function barChart({ bars, height = 170, decimals = 0, color = 'var(--c-accent)', target = null, status = null, animate = false, unitFmt, unit = '' }) {
  const vals = bars.map((b) => b.v).filter((v) => v !== null && v !== undefined);
  if (!vals.length) return empty();
  const pad = { l: 40, r: 12, t: 12, b: 22 };
  const top = niceMax(Math.max(...vals, target || 0) * 1.05);
  const iw = W - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const n = bars.length;
  const slot = iw / n;
  const bw = Math.max(1.5, Math.min(28, slot * 0.68));
  const x = (i) => pad.l + slot * i + slot / 2;
  const yOf = (v) => pad.t + ih - (v / top) * ih;
  const ticks = [0, 1, 2].map((i) => (top * i) / 2);
  const svg = s('svg', { viewBox: `0 0 ${W} ${height}`, class: 'chart', role: 'img', style: `--ch:${color}` });
  svg.append(frame(height, ticks, 0, top, pad, decimals, yOf, xLabels(bars.map((b) => b.label), x, height - 5, axisCount(n))));
  const items = [];
  bars.forEach((b, i) => {
    if (b.v === null || b.v === undefined) return;
    const y = yOf(b.v);
    const st = status ? status(b.v) : null;   // 'good' | 'low' | 'over'
    const p = s('path', { d: topRound(x(i) - bw / 2, y, bw, Math.max(1, pad.t + ih - y), 3), class: 'ch-bar' + (st ? ' ch-' + st : '') + (animate ? ' ch-grow' : '') });
    if (animate) p.style.setProperty('--i', i);
    svg.append(p);
    items.push({ x: x(i), y, lines: [b.label, `${unitFmt ? unitFmt(b.v) : fmt(b.v, decimals)}${unit ? ' ' + unit : ''}`] });
  });
  if (target) {
    const y = yOf(target);
    svg.append(s('line', { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: 'ch-target' }));
  }
  return interactive(svg, items, { t: pad.t, b: pad.t + ih });
}

/** bars: [{label, parts:[p,f,c]|null}], classes: array of class suffixes */
export function stackedBars({ bars, height = 170, classes = ['p', 'f', 'c'], animate = false }) {
  const tot = bars.map((b) => (b.parts ? b.parts.reduce((a, v) => a + (v || 0), 0) : null));
  const vals = tot.filter((v) => v !== null);
  if (!vals.length) return empty();
  const pad = { l: 40, r: 12, t: 12, b: 22 };
  const top = niceMax(Math.max(...vals) * 1.05);
  const iw = W - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const n = bars.length;
  const slot = iw / n;
  const bw = Math.max(1.5, Math.min(28, slot * 0.68));
  const x = (i) => pad.l + slot * i + slot / 2;
  const yOf = (v) => pad.t + ih - (v / top) * ih;
  const ticks = [0, 1, 2].map((i) => (top * i) / 2);
  const svg = s('svg', { viewBox: `0 0 ${W} ${height}`, class: 'chart', role: 'img' });
  svg.append(frame(height, ticks, 0, top, pad, 0, yOf, xLabels(bars.map((b) => b.label), x, height - 5, axisCount(n))));
  const items = [];
  bars.forEach((b, i) => {
    if (!b.parts) return;
    let acc = 0;
    const lastK = b.parts.reduce((m, v, k) => (v ? k : m), -1);
    b.parts.forEach((v, k) => {
      if (!v) return;
      const y1 = yOf(acc + v);
      const y0 = yOf(acc);
      const cls = 'ch-seg ch-seg-' + classes[k] + (animate ? ' ch-grow' : '');
      const r = k === lastK
        ? s('path', { d: topRound(x(i) - bw / 2, y1, bw, Math.max(0.5, y0 - y1), 3), class: cls })
        : s('rect', { x: x(i) - bw / 2, y: y1, width: bw, height: Math.max(0.5, y0 - y1), class: cls });
      if (animate) r.style.setProperty('--i', i);
      svg.append(r);
      acc += v;
    });
    const [p, f, c] = b.parts;
    items.push({ x: x(i), y: yOf(acc), lines: [b.label, `Б ${Math.round(p || 0)} · Ж ${Math.round(f || 0)} · У ${Math.round(c || 0)} г`] });
  });
  return interactive(svg, items, { t: pad.t, b: pad.t + ih });
}

/** Donut ring. Returns SVG only; put text in an overlay. */
let ringSeq = 0;
export function ring({ value, max, size = 150, stroke = 14, color = 'var(--c-accent)', gradient = null, animate = false }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const over = max > 0 && value > max * 1.0001;
  const svg = s('svg', { viewBox: `0 0 ${size} ${size}`, width: size, height: size, class: 'ring', role: 'img', 'aria-label': `${Math.round(value)} из ${Math.round(max)}`, style: `--ch:${color}` });
  svg.append(s('circle', { cx: size / 2, cy: size / 2, r, class: 'ring-track', 'stroke-width': stroke, fill: 'none' }));
  let strokeStyle = null;
  if (gradient) {
    const id = 'rg' + (++ringSeq);
    svg.append(s('defs', null, s('linearGradient', { id, x1: '0', y1: '0', x2: '1', y2: '1' },
      s('stop', { offset: '0', style: `stop-color:${gradient[0]}` }), s('stop', { offset: '1', style: `stop-color:${gradient[1]}` }))));
    strokeStyle = `stroke:url(#${id})`;
  }
  svg.append(s('circle', {
    style: strokeStyle,
    cx: size / 2, cy: size / 2, r, fill: 'none', 'stroke-width': stroke, 'stroke-linecap': 'round',
    'stroke-dasharray': `${(c * pct).toFixed(1)} ${c.toFixed(1)}`,
    transform: `rotate(-90 ${size / 2} ${size / 2})`,
    class: (over ? 'ring-val ring-over' : 'ring-val') + (animate ? ' ring-anim' : ''),
  }));
  return svg;
}

/**
 * Одно крупное кольцо калорий: градиент зелёный → бирюзовый. Всё, что выше 100 %, второй круг поверх
 * мягким оранжевым (не красным). Возвращает SVG; цифры рисуются поверх HTML-ом.
 */
export function kcalRing({ value, max, size = 208, stroke = 20, animate = false, label = '' }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const cx = size / 2;
  const ratio = max > 0 ? Math.max(0, value / max) : 0;
  const id = 'kr' + (++ringSeq);
  const svg = s('svg', { viewBox: `0 0 ${size} ${size}`, width: size, height: size, class: 'ring kcal-ring', role: 'img', 'aria-label': label });
  svg.append(s('defs', null, s('linearGradient', { id, x1: '0', y1: '0', x2: '1', y2: '1' },
    s('stop', { offset: '0', style: 'stop-color:var(--c-green)' }), s('stop', { offset: '1', style: 'stop-color:var(--c-teal)' }))));
  svg.append(s('circle', { cx, cy: cx, r, fill: 'none', 'stroke-width': stroke, class: 'kr-track' }));
  const arc = (frac, cls, grad) => s('circle', {
    cx, cy: cx, r, fill: 'none', 'stroke-width': stroke, 'stroke-linecap': 'round', transform: `rotate(-90 ${cx} ${cx})`,
    'stroke-dasharray': `${(c * frac).toFixed(1)} ${c.toFixed(1)}`, class: cls + (animate ? ' ring-anim' : ''), style: grad ? `stroke:url(#${id})` : null,
  });
  if (ratio > 0.002) svg.append(arc(Math.min(1, ratio), 'kr-arc', true));
  if (ratio > 1.002) svg.append(arc(Math.min(1, ratio - 1), 'kr-arc kr-over', false));
  return svg;
}

/** Мини-график тренда: values — числа, null пропускаются. */
export function sparkline({ values, width = 120, height = 40, animate = false }) {
  const v = values.filter((x) => x !== null && x !== undefined);
  if (v.length < 2) return null;
  const mn = Math.min(...v); const mx = Math.max(...v);
  const span = Math.max(mx - mn, 0.2);
  const pad = 4;
  const x = (i) => pad + (i * (width - 2 * pad)) / (v.length - 1);
  const y = (val) => pad + (height - 2 * pad) * (1 - (val - mn) / span);
  const d = v.map((val, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(val).toFixed(1)}`).join('');
  const svg = s('svg', { viewBox: `0 0 ${width} ${height}`, class: 'spark', role: 'img', 'aria-label': 'Тренд веса' });
  svg.append(s('path', { d, class: 'ch-trend spark-line' + (animate ? ' ch-draw' : ''), pathLength: animate ? 1 : null }));
  svg.append(s('circle', { cx: x(v.length - 1), cy: y(v[v.length - 1]), r: 3, class: 'ch-last' }));
  return svg;
}
