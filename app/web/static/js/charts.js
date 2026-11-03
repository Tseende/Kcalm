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

/** points: [{label, y, t}] — y raw (dots), t trend (thick line). nulls allowed. */
export function lineChart({ points, height = 170, decimals = 1, color = 'var(--c-accent)', unit = '' }) {
  const vals = points.flatMap((p) => [p.y, p.t]).filter((v) => v !== null && v !== undefined);
  if (!vals.length) return empty();
  const pad = { l: 40, r: 12, t: 12, b: 22 };
  const mn = Math.min(...vals);
  const mx = Math.max(...vals);
  const span = Math.max((mx - mn) * 0.2, 0.4);
  const lo = mn - span;
  const hi = mx + span;
  const iw = W - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const n = points.length;
  const x = (i) => pad.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
  const yOf = (v) => pad.t + ih - ((v - lo) / (hi - lo)) * ih;
  const ticks = [0, 1, 2, 3].map((i) => lo + ((hi - lo) * i) / 3);
  const svg = s('svg', { viewBox: `0 0 ${W} ${height}`, class: 'chart', role: 'img', style: `--ch:${color}` });
  svg.append(frame(height, ticks, lo, hi, pad, decimals, yOf, xLabels(points.map((p) => p.label), x, height - 5)));

  const path = (key) => {
    let d = '';
    let pen = false;
    points.forEach((p, i) => {
      const v = p[key];
      if (v === null || v === undefined) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${yOf(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const rawLine = points.map((p, i) => [p, i]).filter(([p]) => p.y !== null && p.y !== undefined);
  if (rawLine.length > 1) svg.append(s('path', { d: rawLine.map(([p, i], k) => `${k ? 'L' : 'M'}${x(i).toFixed(1)} ${yOf(p.y).toFixed(1)}`).join(''), class: 'ch-raw-line' }));
  const tp = path('t');
  if (tp) svg.append(s('path', { d: tp, class: 'ch-trend' }));
  points.forEach((p, i) => {
    if (p.y !== null && p.y !== undefined) svg.append(s('circle', { cx: x(i), cy: yOf(p.y), r: n > 40 ? 1.8 : 2.8, class: 'ch-dot' }, s('title', {}, `${p.label}: ${fmt(p.y, decimals)} ${unit}`)));
  });
  const last = [...points.keys()].reverse().find((i) => points[i].t !== null && points[i].t !== undefined);
  if (last !== undefined) {
    svg.append(s('circle', { cx: x(last), cy: yOf(points[last].t), r: 4.5, class: 'ch-last' }));
  }
  return svg;
}

/** bars: [{label, v}] target: number|null */
export function barChart({ bars, height = 170, decimals = 0, color = 'var(--c-accent)', target = null, targetLabel = '', overTarget = false, unitFmt }) {
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
  svg.append(frame(height, ticks, 0, top, pad, decimals, yOf, xLabels(bars.map((b) => b.label), x, height - 5)));
  bars.forEach((b, i) => {
    if (b.v === null || b.v === undefined) return;
    const y = yOf(b.v);
    const over = overTarget && target && b.v > target * 1.05;
    svg.append(s('rect', { x: x(i) - bw / 2, y, width: bw, height: Math.max(1, pad.t + ih - y), rx: Math.min(3, bw / 2), class: over ? 'ch-bar ch-over' : 'ch-bar' },
      s('title', {}, `${b.label}: ${unitFmt ? unitFmt(b.v) : fmt(b.v, decimals)}`)));
  });
  if (target) {
    const y = yOf(target);
    svg.append(s('line', { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: 'ch-target' }));
    if (targetLabel) svg.append(s('text', { x: W - pad.r, y: y - 4, class: 'ch-axis ch-target-label', 'text-anchor': 'end' }, targetLabel));
  }
  return svg;
}

/** bars: [{label, parts:[p,f,c]|null}], classes: array of class suffixes */
export function stackedBars({ bars, height = 170, classes = ['p', 'f', 'c'] }) {
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
  svg.append(frame(height, ticks, 0, top, pad, 0, yOf, xLabels(bars.map((b) => b.label), x, height - 5)));
  bars.forEach((b, i) => {
    if (!b.parts) return;
    let acc = 0;
    b.parts.forEach((v, k) => {
      if (!v) return;
      const y1 = yOf(acc + v);
      const y0 = yOf(acc);
      svg.append(s('rect', { x: x(i) - bw / 2, y: y1, width: bw, height: Math.max(0.5, y0 - y1), class: 'ch-seg ch-seg-' + classes[k] },
        s('title', {}, `${b.label}: ${Math.round(v)} г`)));
      acc += v;
    });
  });
  return svg;
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
      s('stop', { offset: '0', 'stop-color': gradient[0] }), s('stop', { offset: '1', 'stop-color': gradient[1] }))));
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

// Activity-style concentric rings, outermost first. Over 100 % the arc starts a second lap on top of the first.
export function macroRings({ items, size = 208, stroke = 15, gap = 5, animate = false, label = '' }) {
  const svg = s('svg', { viewBox: `0 0 ${size} ${size}`, width: size, height: size, class: 'ring macro-rings', role: 'img', 'aria-label': label });
  items.forEach((it, i) => {
    const r = (size - stroke) / 2 - i * (stroke + gap);
    if (r <= stroke / 2) return;
    const circ = 2 * Math.PI * r;
    const cx = size / 2;
    const ratio = it.max > 0 ? Math.max(0, it.value / it.max) : 0;
    const tf = `rotate(-90 ${cx} ${cx})`;
    svg.append(s('circle', { cx, cy: cx, r, fill: 'none', 'stroke-width': stroke, class: 'mr-track', style: `stroke:color-mix(in srgb, ${it.color} 20%, transparent)` }));
    const arc = (frac, cls) => s('circle', {
      cx, cy: cx, r, fill: 'none', 'stroke-width': stroke, 'stroke-linecap': 'round', transform: tf,
      'stroke-dasharray': `${(circ * frac).toFixed(1)} ${circ.toFixed(1)}`, class: cls + (animate ? ' ring-anim' : ''), style: `stroke:${it.color}`,
    });
    if (ratio > 0.002) svg.append(arc(Math.min(1, ratio), 'mr-arc'));
    if (ratio > 1.002) svg.append(arc(Math.min(1, ratio - 1), 'mr-arc mr-lap2'));
  });
  return svg;
}
