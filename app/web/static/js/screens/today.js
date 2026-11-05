// Screen 1: "Сегодня"
import { api } from '../api.js';
import { kcalRing } from '../charts.js';
import { weekGoalBlock } from './weekgoal.js';
import { mealIcon, mealPic } from '../pics.js';
import {
  h, icon, tile, toast, haptic, nav, skeletonCards, emptyState, errorState, sectionTitle,
  todayISO, fmtN, fmtKcal, fmtDur, mealType, macroLine, countUp, staggerIn, dateSwitch, reducedMotion, plural,
} from '../ui.js';

let curDate = todayISO();
const waterUndo = []; // water entries added in this session: {ml, id, p}
let firstPaint = true; // animate the ring once per visit (and on day switch), not on every refresh
let oftenCache = null; // last /frequent answer, painted instantly on re-render

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen today' });
  const body = h('div', { class: 'stack' });
  root.append(view);

  const sw = dateSwitch(curDate, (d, dir) => { curDate = d; firstPaint = true; load({ dir }); });
  view.append(sw.el, body);

  async function load({ quiet = false, dir = 0 } = {}) {
    if (!quiet) body.replaceChildren(skeletonCards(3, 120));
    const date = curDate;
    sw.set(date);
    try {
      const day = await api.get('/day', { date });
      if (!ctx.alive() || date !== curDate) return;
      draw(day, { dir, fresh: !quiet });
    } catch (e) {
      if (ctx.alive()) errorState(body, e, load);
    }
  }

  function draw(day, { dir = 0, fresh = false } = {}) {
    const t = day.targets;
    const eaten = day.totals.kcal;
    const remaining = day.remaining_kcal;
    const over = remaining < 0;

    // hero: one big calorie ring (green -> teal, soft orange past 100 %), macros below
    const P = day.totals.protein; const F = day.totals.fat; const C = day.totals.carbs;
    const animate = firstPaint;
    firstPaint = false;
    const num = h('div', { class: 'ring-num' + (over ? ' over' : '') });
    if (animate) countUp(num, Math.abs(remaining)); else num.textContent = fmtKcal(Math.abs(remaining));
    const ringBox = h('div', { class: 'ring-box' },
      kcalRing({ value: eaten, max: t.kcal, size: 208, stroke: 20, animate, label: `Съедено ${fmtKcal(eaten)} из ${fmtKcal(t.kcal)} ккал` }),
      h('div', { class: 'ring-center' },
        h('div', { class: 'ring-cap' }, over ? 'сверх нормы' : 'осталось'),
        num,
        h('div', { class: 'ring-unit' }, 'ккал')));
    const kcalCard = h('section', { class: 'card kcal-card' + (over ? ' is-over' : ''), 'aria-label': 'Калории и БЖУ' });
    kcalCard.append(h('div', { class: 'kcal-top' }, ringBox,
      h('div', { class: 'kcal-sub' },
        h('div', null, `Съедено ${fmtKcal(eaten)} из ${fmtKcal(t.kcal)} ккал`))));

    // macros: numbers under the rings (what is left, or the excess)
    let macroIdx = 0;
    const macro = (name, val, target, cls) => {
      const left = target - val;
      const isOver = target > 0 && left < -0.05;
      const pct = target > 0 ? Math.min(100, (val / target) * 100) : 0;
      const fill = h('i', { style: { width: pct + '%' } });
      const bar = h('div', { class: 'bar bar-m' + (animate ? ' bar-scale' : ''), role: 'progressbar', 'aria-label': name, 'aria-valuemin': 0, 'aria-valuemax': Math.round(target), 'aria-valuenow': Math.round(val) }, fill);
      fill.style.setProperty('--i', macroIdx++);
      return h('div', { class: 'macro ' + cls + (isOver ? ' is-over' : '') },
        h('div', { class: 'macro-name' }, h('i', { class: 'macro-dot' }), name),
        h('div', { class: 'macro-val' }, fmtN(val), h('small', null, ` / ${fmtN(target)}`)),
        bar,
        h('div', { class: 'macro-left' }, isOver ? `+${fmtN(-left)} г сверх` : `ещё ${fmtN(left)} г`));
    };
    const macroRow = h('div', { class: 'macros', role: 'group', 'aria-label': 'Углеводы, белки, жиры' },
      macro('Углеводы', C, t.carbs, 'bar-c'),
      macro('Белки', P, t.protein, 'bar-p'),
      macro('Жиры', F, t.fat, 'bar-f'));

    // soft one-line hint instead of a verdict (today only, once something is logged)
    const proteinLeft = Math.round(t.protein - day.totals.protein);
    let tipText = null;
    if (curDate === todayISO() && day.meals.length) {
      if (over && proteinLeft > 10) tipText = `Белка ещё ${proteinLeft} г — лучше нежирный источник.`;
      else if (over) tipText = 'Калории выбраны. Смотрите на неделю, а не на день.';
      else if (proteinLeft > 15) tipText = `Белка ещё ${proteinLeft} г.`;
    }
    const tip = tipText ? h('div', { class: 'day-tip' }, icon('sparkles', 15), h('span', null, tipText)) : null;
    kcalCard.append(macroRow, ...(tip ? [tip] : []));

    // water
    let waterMl = day.water_ml;
    const waterNum = h('div', { class: 'water-num' });
    const waterSub = h('div', { class: 'water-sub' });
    const waterBar = h('div', { class: 'bar bar-w', role: 'progressbar', 'aria-label': 'Вода', 'aria-valuemax': t.water_ml }, h('i'));
    const undoBtn = h('button', { class: 'water-undo', type: 'button', 'aria-label': 'Отменить последнюю добавленную воду', title: 'Отменить', onclick: undoWater }, icon('undo', 20));
    function paintWater() {
      waterNum.replaceChildren(h('b', null, fmtKcal(waterMl)), h('span', { class: 'muted' }, ` / ${fmtKcal(t.water_ml)} мл`));
      const left = t.water_ml - waterMl;
      waterSub.textContent = left > 0 ? `ещё ${fmtKcal(left)} мл до нормы` : 'норма выполнена';
      waterSub.classList.toggle('done', left <= 0);
      waterBar.firstChild.style.width = Math.min(100, (waterMl / t.water_ml) * 100) + '%';
      waterBar.setAttribute('aria-valuenow', waterMl);
      undoBtn.disabled = !waterUndo.length || curDate !== todayISO();
    }
    // Оптимистично: цифра и полоска меняются сразу, сервер догоняет в фоне (облачная БД отвечает не мгновенно).
    let waterPending = 0;
    function addWater(ml) {
      if (curDate !== todayISO()) { toast('Воду можно добавлять только за сегодня'); return; }
      const entry = { ml, id: null, p: null };
      waterMl += ml;
      waterUndo.push(entry);
      haptic('light');
      paintWater();
      waterPending++;
      entry.p = api.post('/water', { ml }).then((r) => {
        entry.id = r.id;
        if (--waterPending === 0) { waterMl = r.water_ml; paintWater(); }   // сверка с сервером, когда нет других в пути
      }).catch((e) => {
        waterPending--;
        const i = waterUndo.indexOf(entry);
        if (i >= 0) { waterUndo.splice(i, 1); waterMl -= ml; }
        paintWater();
        toast(e.message, 'error');
      });
    }
    async function undoWater() {
      const entry = waterUndo.pop();
      if (!entry) return;
      waterMl -= entry.ml;
      paintWater();
      await entry.p;                                  // id приходит с ответом на добавление
      if (entry.id == null) return;                   // добавление не удалось: уже откатили
      try {
        const r = await api.del('/water/' + entry.id);
        if (!waterPending) { waterMl = r.water_ml; paintWater(); }
      } catch (e) { waterMl += entry.ml; waterUndo.push(entry); paintWater(); toast(e.message, 'error'); }
    }
    const waterCard = h('section', { class: 'card stack-sm water-card', 'aria-label': 'Вода' },
      h('div', { class: 'row-between' }, h('div', { class: 'water-head' }, h('span', { class: 'water-ico', 'aria-hidden': 'true' }, icon('droplet', 20)), h('div', null, h('div', { class: 'water-title' }, 'Вода'), waterSub)), waterNum),
      waterBar,
      h('div', { class: 'quick' }, [200, 300, 500].map((ml) => h('button', { class: 'btn btn-tonal', type: 'button', onclick: () => addWater(ml) }, h('b', null, '+' + ml), h('small', null, 'мл'))), undoBtn));
    paintWater();

    // health strip: one grouped card, line icons, empty states are actions
    const mini = (ic, title, value, sub, path) => {
      const empty = value === null;
      return h('button', { class: 'mini', type: 'button', onclick: () => nav.go(path), 'aria-label': `${title}: ${empty ? 'нет данных, добавить' : value}` },
        h('span', { class: 'mini-ico', 'aria-hidden': 'true' }, icon(ic, 22)),
        h('div', { class: 'mini-val' + (empty ? ' is-empty' : '') }, empty ? '+ добавить' : value),
        h('div', { class: 'mini-sub' }, sub));
    };
    const wl = day.weight_last || (day.weight != null ? { kg: day.weight, date: day.date } : null);
    const ago = (iso) => {
      const n = Math.round((new Date(day.date) - new Date(iso)) / 86400000);
      return n <= 0 ? 'вес сегодня' : n === 1 ? 'вес вчера' : `${plural(n, 'день', 'дня', 'дней')} назад`;
    };
    const sleepOk = !!day.sleep;
    const stepsOk = day.activity && day.activity.steps != null;
    const miniRow = h('div', { class: 'card mini-group' },
      mini('bed', 'Сон', sleepOk ? fmtDur(day.sleep.duration_min) : null, sleepOk ? (day.sleep.score ? 'оценка ' + day.sleep.score : 'сон') : 'сон', '/add/sleep'),
      mini('walk', 'Шаги', stepsOk ? fmtKcal(day.activity.steps) : null, 'шаги', '/add/activity'),
      mini('scale', 'Вес', wl ? fmtN(wl.kg) + ' кг' : null, wl ? ago(wl.date) : 'вес', '/add/weight'));

    // meals
    const mealsList = day.meals.length
      ? h('div', { class: 'list thumbs' }, day.meals.map(mealRow))
      : emptyState('tools-kitchen-2', 'Пока ничего не записано', 'Нажмите «Добавить», сфотографируйте тарелку, и бот посчитает калории.',
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => nav.go('/add/meal') }, 'Добавить приём'));

    const isToday = curDate === todayISO();
    const kids = [kcalCard, waterCard, miniRow, ...(isToday ? [weekGoalBlock(ctx, { compact: true })] : []),
      sectionTitle('Приёмы пищи', isToday ? h('button', { class: 'btn-link section-add', type: 'button', onclick: () => nav.go('/add/meal') }, icon('plus', 14), 'Добавить') : null), mealsList,
      ...(isToday ? [sectionTitle('Повторить'), oftenRow()] : [])];
    body.replaceChildren(...kids);
    const oh = body.querySelector('.often');
    if (oh && oh.hidden && oh.previousElementSibling) oh.previousElementSibling.hidden = true;
    if (dir && !reducedMotion()) {
      body.style.setProperty('--dx', (dir > 0 ? 12 : -12) + 'px');
      body.classList.remove('day-in'); void body.offsetWidth; body.classList.add('day-in');
    } else if (fresh) staggerIn(body, kids);
  }

  // One-tap «как обычно»: dishes eaten at least twice in 60 days. Hidden (with its title) when there are none.
  function oftenRow() {
    const row = h('div', { class: 'often', role: 'group', 'aria-label': 'Частые блюда' });
    const titleOf = (f) => {
      const m = /^(.*?)\s\+(\d+)$/.exec(f.title || '');
      return m ? `${m[1]} и ещё ${m[2]}` : (f.title || (f.names && f.names[0]) || '');
    };
    const paint = (items) => {
      row.hidden = !items.length;
      const head = row.previousElementSibling;
      if (head && head.classList.contains('section-title')) head.hidden = !items.length;
      row.replaceChildren(...items.map((f) => {
        const names = f.names || [];
        const pic = mealIcon({ items: names.map((name) => ({ name, kcal: 0 })), type: 'snack' }, 32);
        const b = h('button', { class: 'often-chip', type: 'button', title: names.join(', '),
          'aria-label': `Записать: ${names.join(', ')}, ${f.kcal} ккал`, onclick: () => repeat(f, b) },
        h('span', { class: 'often-pic' }, pic),
        h('span', { class: 'often-text' },
          h('span', { class: 'often-name' }, titleOf(f)),
          h('span', { class: 'often-kcal' }, `${fmtKcal(f.kcal)} ккал`)),
        f.favorite_id ? h('span', { class: 'often-star' }, icon('star', 13)) : null);
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
      const r = await api.post(f.favorite_id ? `/favorites/${f.favorite_id}/repeat` : `/meals/${f.meal_id}/repeat`);
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
    const fallback = () => mealIcon(m, 56) || mealPic(m.type, 56) || tile(mt.icon, mt.color, 56);
    // В списке всегда иконка типа приёма; само фото открывается на экране приёма (здесь только счётчик).
    const n = (m.photos || []).length;
    const thumb = h('div', { class: 'thumb' }, fallback(),
      n ? h('span', { class: 'thumb-count' }, icon('photo', 10), String(n)) : null);
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
