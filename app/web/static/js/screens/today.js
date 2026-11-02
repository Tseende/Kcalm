// Screen 1: "Сегодня"
import { api } from '../api.js';
import { ring } from '../charts.js';
import { weekGoalBlock } from './weekgoal.js';
import {
  h, icon, tile, toast, haptic, nav, skeletonCards, emptyState, errorState, sectionTitle, photoImg,
  todayISO, addDays, dateLabel, longDate, fmtN, fmtKcal, fmtDur, mealType, macroLine,
} from '../ui.js';

let curDate = todayISO();
const waterUndo = []; // ids of water entries added in this session
let oftenCache = null; // last /frequent answer, painted instantly on re-render

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

  async function load({ quiet = false } = {}) {
    const isToday = curDate === todayISO();
    label.replaceChildren(h('span', { class: 'date-main' }, dateLabel(curDate)), isToday ? h('span', { class: 'date-sub' }, longDate(curDate)) : h('span', { class: 'date-sub' }, 'нажмите, чтобы вернуться к сегодня'));
    next.disabled = isToday;
    if (!quiet) body.replaceChildren(skeletonCards(3, 120));
    const date = curDate;
    try {
      const day = await api.get('/day', { date });
      if (!ctx.alive() || date !== curDate) return;
      draw(day);
    } catch (e) {
      if (ctx.alive()) errorState(body, e, load);
    }
  }

  function draw(day) {
    const t = day.targets;
    const eaten = day.totals.kcal;
    const remaining = day.remaining_kcal;
    const low = day.meals.reduce((a, m) => a + (m.kcal_low || m.kcal), 0);
    const high = day.meals.reduce((a, m) => a + (m.kcal_high || m.kcal), 0);
    const over = remaining < 0;

    // calories
    const ringBox = h('div', { class: 'ring-box' }, ring({ value: eaten, max: t.kcal, size: 136, stroke: 13, color: 'var(--c-accent)' }),
      h('div', { class: 'ring-center' }, h('div', { class: 'ring-num' }, fmtKcal(eaten)), h('div', { class: 'muted small' }, 'ккал')));
    const kcalCard = h('section', { class: 'card kcal-card' + (over ? ' is-over' : ''), 'aria-label': 'Калории' }, ringBox,
      h('div', { class: 'kcal-info' },
        h('div', { class: 'kcal-cap' }, over ? 'Выше нормы на' : 'Осталось'),
        h('div', { class: 'kcal-left' + (over ? ' over' : '') }, fmtKcal(Math.abs(remaining)), h('span', { class: 'unit' }, ' ккал')),
        h('div', { class: 'muted small' }, `Норма ${fmtKcal(t.kcal)} ккал`),
        day.meals.length ? h('div', { class: 'range-hint small' }, `Оценка: ${fmtKcal(low)}–${fmtKcal(high)} ккал`) : null));

    // macros
    const macro = (name, val, target, cls) => h('div', { class: 'macro ' + cls },
      h('div', { class: 'macro-name' }, h('i', { class: 'macro-dot' }), name),
      h('div', { class: 'macro-val' }, fmtN(val), h('small', null, ' г')),
      h('div', { class: 'bar ' + cls, role: 'progressbar', 'aria-label': name, 'aria-valuenow': Math.round(val), 'aria-valuemax': Math.round(target) },
        h('i', { style: { width: Math.min(100, target ? (val / target) * 100 : 0) + '%' } })),
      h('div', { class: 'macro-of' }, `из ${fmtN(target)} г`));
    const macroCard = h('section', { class: 'card macros', 'aria-label': 'Белки, жиры, углеводы' },
      macro('Белки', day.totals.protein, t.protein, 'bar-p'),
      macro('Жиры', day.totals.fat, t.fat, 'bar-f'),
      macro('Углеводы', day.totals.carbs, t.carbs, 'bar-c'));

    // water
    let waterMl = day.water_ml;
    const waterNum = h('div', { class: 'water-num' });
    const waterBar = h('div', { class: 'bar bar-w', role: 'progressbar', 'aria-label': 'Вода', 'aria-valuemax': t.water_ml }, h('i'));
    const undoBtn = h('button', { class: 'btn btn-secondary btn-sm', type: 'button', 'aria-label': 'Отменить последнюю добавленную воду', onclick: undoWater }, icon('undo', 18), 'Отменить');
    function paintWater() {
      waterNum.replaceChildren(h('b', null, fmtKcal(waterMl)), h('span', { class: 'muted' }, ` / ${fmtKcal(t.water_ml)} мл`));
      waterBar.firstChild.style.width = Math.min(100, (waterMl / t.water_ml) * 100) + '%';
      waterBar.setAttribute('aria-valuenow', waterMl);
      undoBtn.disabled = !waterUndo.length || curDate !== todayISO();
    }
    async function addWater(ml) {
      if (curDate !== todayISO()) { toast('Воду можно добавлять только за сегодня'); return; }
      try {
        const r = await api.post('/water', { ml });
        waterMl = r.water_ml;
        waterUndo.push(r.id);
        haptic('light');
        paintWater();
      } catch (e) { toast(e.message, 'error'); }
    }
    async function undoWater() {
      const id = waterUndo.pop();
      if (!id) return;
      try {
        const r = await api.del('/water/' + id);
        waterMl = r.water_ml;
        paintWater();
      } catch (e) { waterUndo.push(id); toast(e.message, 'error'); }
    }
    const waterCard = h('section', { class: 'card stack-sm', 'aria-label': 'Вода' },
      h('div', { class: 'row-between' }, h('div', { class: 'card-title with-ico' }, tile('droplet', 'teal', 26), 'Вода'), waterNum),
      waterBar,
      h('div', { class: 'quick' }, [200, 300, 500].map((ml) => h('button', { class: 'btn btn-tonal', type: 'button', onclick: () => addWater(ml) }, '+' + ml)), undoBtn));
    paintWater();

    // mini cards
    const mini = (ic, color, title, value, sub, path) => h('button', { class: 'card mini', type: 'button', onclick: () => nav.go(path), 'aria-label': `${title}: ${value}` },
      tile(ic, color, 28), h('div', { class: 'mini-val' }, value), h('div', { class: 'muted small' }, sub || title));
    const miniRow = h('div', { class: 'grid3' },
      mini('bed', 'indigo', 'Сон', day.sleep ? fmtDur(day.sleep.duration_min) : '—', day.sleep && day.sleep.score ? 'оценка ' + day.sleep.score : 'Сон', '/add/sleep'),
      mini('walk', 'green', 'Шаги', day.activity && day.activity.steps != null ? fmtKcal(day.activity.steps) : '—', day.activity && day.activity.active_kcal ? day.activity.active_kcal + ' ккал' : 'Шаги', '/add/activity'),
      mini('scale', 'purple', 'Вес', day.weight != null ? fmtN(day.weight) + ' кг' : '—', 'Вес', '/add/weight'));

    // meals
    const mealsList = day.meals.length
      ? h('div', { class: 'list thumbs' }, day.meals.map(mealRow))
      : emptyState('tools-kitchen-2', 'Пока ничего не записано', 'Нажмите «Добавить», сфотографируйте тарелку, и бот посчитает калории.',
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => nav.go('/add/meal') }, 'Добавить приём'));

    const isToday = curDate === todayISO();
    body.replaceChildren(kcalCard, macroCard, waterCard, miniRow, ...(isToday ? [weekGoalBlock(ctx, { compact: true })] : []),
      sectionTitle('Приёмы пищи'), ...(isToday ? [oftenRow()] : []), mealsList);
  }

  // One-tap «как обычно»: dishes eaten at least twice in 60 days. Hidden when there are none.
  function oftenRow() {
    const row = h('div', { class: 'often', role: 'group', 'aria-label': 'Частые блюда' });
    const paint = (items) => {
      row.hidden = !items.length;
      row.replaceChildren(...items.map((f) => {
        const b = h('button', { class: 'often-chip', type: 'button', title: f.names.join(', '),
          'aria-label': `Записать: ${f.names.join(', ')}, ${f.kcal} ккал`, onclick: () => repeat(f, b) },
        h('span', { class: 'often-name' }, f.title), h('span', { class: 'often-kcal' }, `${fmtKcal(f.kcal)} ккал`));
        return b;
      }));
    };
    paint(oftenCache || []);                          // last known list first: no jump on reload
    api.get('/frequent').then(({ items }) => {
      oftenCache = items;
      if (ctx.alive() && row.isConnected) paint(items);
    }).catch(() => { /* optional row: keep what is shown */ });
    return row;
  }

  async function repeat(f, btn) {
    btn.disabled = true;
    try {
      const r = await api.post(`/meals/${f.meal_id}/repeat`);
      haptic('success');
      toast(`+${fmtKcal(r.kcal)} ккал · ${f.title}`, 'ok', { undo: () => api.del('/meals/' + r.id) });
      load({ quiet: true });
    } catch (e) {
      btn.disabled = false;
      toast(e.message, 'error');
    }
  }

  const onData = () => { if (ctx.alive()) load({ quiet: true }); else window.removeEventListener('app:data', onData); };
  window.addEventListener('app:data', onData);

  function mealRow(m) {
    const mt = mealType(m.type);
    const thumb = m.photos && m.photos.length
      ? h('div', { class: 'thumb' }, photoImg(m.photos[0].url, '', 'Фото приёма'), m.photos.length > 1 ? h('span', { class: 'thumb-count' }, '×' + m.photos.length) : null)
      : tile(mt.icon, mt.color, 56);
    const names = (m.items || []).map((i) => i.name).slice(0, 3).join(', ');
    return h('button', { class: 'row meal-row', type: 'button', onclick: () => nav.go('/meal/' + m.id), 'aria-label': `${mt.label} ${m.time}, ${m.kcal} ккал` },
      thumb,
      h('span', { class: 'grow' },
        h('span', { class: 'row-title' }, `${mt.label} · ${m.time}`),
        h('span', { class: 'row-sub clamp' }, names || 'Без позиций'),
        h('span', { class: 'row-sub' }, macroLine(m))),
      h('span', { class: 'meal-kcal' }, fmtKcal(m.kcal), h('small', null, ' ккал')));
  }

  await load();
}
