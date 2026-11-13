// Screen 1: "Сегодня"
import { api } from '../api.js';
import { kcalRing } from '../charts.js';
import { weekGoalBlock } from './weekgoal.js';
import { mealIcon, mealPic } from '../pics.js';
import { t, tn } from '../i18n.js';
import {
  h, icon, tile, toast, haptic, nav, skeletonCards, holdHeight, emptyState, errorState, sectionTitle,
  todayISO, fmtN, fmtKcal, fmtDur, mealType, macroLine, countUp, staggerIn, dateSwitch, reducedMotion,
} from '../ui.js';

let curDate = todayISO();
const waterUndo = []; // water entries added in this session: {ml, id, p}
let firstPaint = true; // animate the ring once per visit (and on day switch), not on every refresh
let oftenCache = null; // last /frequent answer, painted instantly on re-render

const GROUPS = ['breakfast', 'lunch', 'dinner', 'snack'];
const openGroups = new Set();   // раскрытые группы приёмов: «дата:тип»
const openInit = new Set();     // для каких дат уже выбрана группа по умолчанию

export default async function render(root, ctx) {
  // Пришли на вкладку «Сегодня» с другой вкладки: показываем сегодня, а не день, на котором остановились (иначе «+» пишет в сегодня, а на экране вчера).
  // Возврат с экрана приёма сохраняет выбранный день.
  // Возврат с форм сна/шагов/веса тоже сохраняет день; после сохранения форма сама передаёт ?date=.
  const qd = ctx.query && ctx.query.date;
  if (qd && /^\d{4}-\d{2}-\d{2}$/.test(qd) && qd <= todayISO()) {
    curDate = qd;
    try { history.replaceState(history.state, '', '#/today'); } catch { /* ignore */ }
  } else if (!/^\/(meal\/|add\/(sleep|activity|weight)$)/.test(ctx.prev || '')) curDate = todayISO();
  const view = h('div', { class: 'screen today' });
  const body = h('div', { class: 'stack' });
  root.append(view);

  const sw = dateSwitch(curDate, (d, dir) => { curDate = d; firstPaint = true; load({ dir }); });
  view.append(sw.el, body);

  async function load({ quiet = false, dir = 0 } = {}) {
    // скелетон только на первом показе; при смене дня старое содержимое остаётся (страница не схлопывается)
    let release = null;
    if (!quiet) { if (body.hasChildNodes()) release = holdHeight(body); else body.replaceChildren(skeletonCards(3, 120)); }
    const date = curDate;
    sw.set(date);
    try {
      const day = await api.get('/day', { date });
      if (!ctx.alive() || date !== curDate) return;
      draw(day, { dir, fresh: !quiet });
      if (release) release();
    } catch (e) {
      if (!ctx.alive() || date !== curDate) return;
      errorState(body, e, load);
      if (release) release();
    }
  }

  function draw(day, { dir = 0, fresh = false } = {}) {
    const tgt = day.targets;
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
      kcalRing({ value: eaten, max: tgt.kcal, size: 208, stroke: 20, animate, label: t('today.eaten', { a: fmtKcal(eaten), b: fmtKcal(tgt.kcal) }) }),
      h('div', { class: 'ring-center' },
        h('div', { class: 'ring-cap' }, over ? t('today.over_cap') : t('today.left_cap')),
        num,
        h('div', { class: 'ring-unit' }, t('unit.kcal'))));
    const kcalCard = h('section', { class: 'card kcal-card' + (over ? ' is-over' : ''), 'aria-label': t('today.kcal_card') });
    kcalCard.append(h('div', { class: 'kcal-top' }, ringBox,
      h('div', { class: 'kcal-sub' },
        h('div', null, t('today.eaten', { a: fmtKcal(eaten), b: fmtKcal(tgt.kcal) })))));

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
        h('div', { class: 'macro-left' }, isOver ? t('today.macro_over', { v: fmtN(-left) }) : t('today.macro_left', { v: fmtN(left) })));
    };
    const macroRow = h('div', { class: 'macros', role: 'group', 'aria-label': t('today.macros_group') },
      macro(t('macro.carbs'), C, tgt.carbs, 'bar-c'),
      macro(t('macro.protein'), P, tgt.protein, 'bar-p'),
      macro(t('macro.fat'), F, tgt.fat, 'bar-f'));

    // soft one-line hint instead of a verdict (today only, once something is logged)
    const proteinLeft = Math.round(tgt.protein - day.totals.protein);
    let tipText = null;
    if (curDate === todayISO() && day.meals.length) {
      if (over && proteinLeft > 10) tipText = t('today.tip_over_protein', { n: proteinLeft });
      else if (over) tipText = t('today.tip_over');
      else if (proteinLeft > 15) tipText = t('today.tip_protein', { n: proteinLeft });
    }
    const tip = tipText ? h('div', { class: 'day-tip' }, icon('sparkles', 15), h('span', null, tipText)) : null;
    kcalCard.append(macroRow, ...(tip ? [tip] : []));

    // water
    let waterMl = day.water_ml;
    const waterNum = h('div', { class: 'water-num' });
    const waterSub = h('div', { class: 'water-sub' });
    const waterBar = h('div', { class: 'bar bar-w', role: 'progressbar', 'aria-label': t('water.title'), 'aria-valuemax': tgt.water_ml }, h('i'));
    const undoBtn = h('button', { class: 'water-undo', type: 'button', 'aria-label': t('today.undo_water'), title: t('common.undo'), onclick: undoWater }, icon('undo', 20));
    function paintWater() {
      waterNum.replaceChildren(h('b', null, fmtKcal(waterMl)), h('span', { class: 'muted' }, ` / ${fmtKcal(tgt.water_ml)} ${t('unit.ml')}`));
      const left = tgt.water_ml - waterMl;
      waterSub.textContent = left > 0 ? t('today.water_left', { v: fmtKcal(left) }) : t('today.water_done');
      waterSub.classList.toggle('done', left <= 0);
      waterBar.firstChild.style.width = Math.min(100, (waterMl / tgt.water_ml) * 100) + '%';
      waterBar.setAttribute('aria-valuenow', waterMl);
      undoBtn.disabled = !waterUndo.length || curDate !== todayISO();
    }
    // Оптимистично: цифра и полоска меняются сразу, сервер догоняет в фоне (облачная БД отвечает не мгновенно).
    let waterPending = 0;
    function addWater(ml) {
      if (curDate !== todayISO()) { toast(t('today.water_only_today')); return; }
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
    const waterCard = h('section', { class: 'card stack-sm water-card', 'aria-label': t('water.title') },
      h('div', { class: 'row-between' }, h('div', { class: 'water-head' }, h('span', { class: 'water-ico', 'aria-hidden': 'true' }, icon('droplet', 20)), h('div', null, h('div', { class: 'water-title' }, t('water.title')), waterSub)), waterNum),
      waterBar,
      h('div', { class: 'quick' }, [200, 300, 500].map((ml) => h('button', { class: 'btn btn-tonal', type: 'button', onclick: () => addWater(ml) }, h('b', null, '+' + ml), h('small', null, t('unit.ml')))), undoBtn));
    paintWater();

    // health strip: one grouped card, line icons, empty states are actions
    const mini = (ic, title, value, sub, p, progress = null) => {
      const path = curDate === todayISO() ? p : `${p}?date=${curDate}`;
      const empty = value === null;
      return h('button', { class: 'mini', type: 'button', onclick: () => nav.go(path), 'aria-label': `${title}: ${empty ? t('today.mini_empty_aria') : value}` },
        h('span', { class: 'mini-ico', 'aria-hidden': 'true' }, icon(ic, 22)),
        h('div', { class: 'mini-val' + (empty ? ' is-empty' : '') }, empty ? t('today.mini_add') : value),
        h('div', { class: 'mini-sub' }, sub),
        progress === null ? null : h('span', { class: 'mini-bar' + (progress >= 1 ? ' done' : ''), 'aria-hidden': 'true' }, h('i', { style: { width: Math.round(Math.min(1, progress) * 100) + '%' } })));
    };
    const wl = day.weight_last || (day.weight != null ? { kg: day.weight, date: day.date } : null);
    const ago = (iso) => {
      const n = Math.round((new Date(day.date) - new Date(iso)) / 86400000);
      return n <= 0 ? t('today.w_today') : n === 1 ? t('today.w_yesterday') : tn('today.days_ago', n);
    };
    const sleepOk = !!day.sleep;
    const stepsOk = day.activity && day.activity.steps != null;
    const stepsGoal = day.steps_goal || 8000;
    const miniRow = h('div', { class: 'card mini-group' },
      mini('bed', t('add.sleep'), sleepOk ? fmtDur(day.sleep.duration_min) : null, sleepOk ? (day.sleep.score ? t('today.score', { n: day.sleep.score }) : t('today.sleep_sub')) : t('today.sleep_sub'), '/add/sleep'),
      mini('walk', t('today.steps'), stepsOk ? fmtKcal(day.activity.steps) : null,
        stepsOk ? (day.activity.steps >= stepsGoal ? t('today.steps_done') : t('today.steps_of', { n: fmtKcal(stepsGoal) })) : t('today.steps_sub'),
        '/add/activity', stepsOk ? day.activity.steps / stepsGoal : null),
      mini('scale', t('add.weight'), wl ? fmtN(wl.kg) + ' ' + t('unit.kg') : null, wl ? ago(wl.date) : t('today.weight_sub'), '/add/weight'));

    // meals
    const mealsList = day.meals.length
      ? h('div', { class: 'stack meal-groups' }, mealGroups(day))
      : emptyState('tools-kitchen-2', t('today.empty_title'), t('today.empty_text'),
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => nav.go('/add/meal') }, t('today.add_meal')));

    const isToday = curDate === todayISO();
    const often = isToday ? oftenRow() : null;
    const kids = [kcalCard, waterCard, miniRow, ...(isToday ? [activityHint(day.activity_hint)].filter(Boolean) : []), ...(isToday ? [weekGoalBlock(ctx, { compact: true })] : []),
      sectionTitle(t('today.meals'), isToday ? h('button', { class: 'btn-link section-add', type: 'button', onclick: () => nav.go('/add/meal') }, icon('plus', 14), t('common.add')) : null), mealsList,
      ...(isToday ? [sectionTitle(t('today.repeat')), often, ...(often._ph ? [often._ph] : [])] : [])];
    body.replaceChildren(...kids);
    const oh = body.querySelector('.often');
    if (oh && oh.hidden && !oh._ph && oh.previousElementSibling) oh.previousElementSibling.hidden = true;
    if (dir && !reducedMotion()) {
      body.style.setProperty('--dx', (dir > 0 ? 12 : -12) + 'px');
      body.classList.remove('day-in'); void body.offsetWidth; body.classList.add('day-in');
    } else if (fresh) staggerIn(body, kids);
  }

  // One-tap «как обычно»: dishes eaten at least twice in 60 days. Hidden (with its title) when there are none.
  function oftenRow() {
    const row = h('div', { class: 'often', role: 'group', 'aria-label': t('today.often') });
    const titleOf = (f) => {
      const m = /^(.*?)\s\+(\d+)$/.exec(f.title || '');
      return m ? t('today.and_more', { a: m[1], n: m[2] }) : (f.title || (f.names && f.names[0]) || '');
    };
    // Пока список не пришёл (первый заход за сессию), вместо него заглушка: заголовок не «выпрыгивает» позже.
    const pending = oftenCache === null;
    if (pending) row._ph = h('div', { class: 'often-ph', 'aria-hidden': 'true' }, h('i', { class: 'skel' }), h('i', { class: 'skel' }), h('i', { class: 'skel' }));
    const paint = (items) => {
      row.hidden = !items.length;
      const head = row.previousElementSibling;
      if (head && head.classList.contains('section-title')) head.hidden = !items.length && !row._ph;
      row.replaceChildren(...items.map((f) => {
        const names = f.names || [];
        const pic = mealIcon({ items: names.map((name) => ({ name, kcal: 0 })), type: 'snack' }, 32);
        const b = h('button', { class: 'often-chip', type: 'button', title: names.join(', '),
          'aria-label': t('today.log_aria', { names: names.join(', '), kcal: f.kcal }), onclick: () => repeat(f, b) },
        h('span', { class: 'often-pic' }, pic),
        h('span', { class: 'often-text' },
          h('span', { class: 'often-name' }, titleOf(f)),
          h('span', { class: 'often-kcal' }, `${fmtKcal(f.kcal)} ${t('unit.kcal')}`)),
        f.favorite_id ? h('span', { class: 'often-star' }, icon('star', 13)) : null);
        return b;
      }));
    };
    paint(oftenCache || []);                          // last known list first: no jump on reload
    const settle = () => { if (row._ph) { row._ph.remove(); row._ph = null; } };
    api.get('/frequent').then(({ items }) => {
      oftenCache = items;
      settle();
      if (ctx.alive() && row.isConnected) paint(items);
    }).catch(() => { settle(); if (row.isConnected) paint(oftenCache || []); }); // необязательная строка: оставляем что было
    return row;
  }

  async function repeat(f, btn) {
    btn.disabled = true;
    try {
      const r = await api.post(f.favorite_id ? `/favorites/${f.favorite_id}/repeat` : `/meals/${f.meal_id}/repeat`);
      haptic('success');
      toast(`+${fmtKcal(r.kcal)} ${t('unit.kcal')} · ${f.title}`, 'ok', { undo: () => api.del('/meals/' + r.id) });
      load({ quiet: true });
    } catch (e) {
      btn.disabled = false;
      toast(e.message, 'error');
    }
  }

  const onData = () => { if (ctx.alive()) load({ quiet: true }); else window.removeEventListener('app:data', onData); };
  window.addEventListener('app:data', onData);

  // Шаги норму калорий не меняют. Если средние шаги расходятся с уровнем активности в профиле,
  // предлагаем поправить профиль. «Не сейчас» прячет подсказку на 30 дней.
  function activityHint(hint) {
    if (!hint) return null;
    const KEY = 'kcalm:act-hint';
    try { if (Date.now() - Number(localStorage.getItem(KEY) || 0) < 30 * 86400000) return null; } catch (e) { /* хранилище недоступно */ }
    const lvl = (n) => t('prof.act' + n).toLowerCase();
    const card = h('div', { class: 'card act-hint' },
      h('div', { class: 'act-hint-head' }, tile('walk', 'green', 30),
        h('div', { class: 'grow' },
          h('div', { class: 'row-title' }, t(hint.suggested > hint.current ? 'today.hint_up' : 'today.hint_down')),
          h('div', { class: 'row-sub' }, t('today.hint_text', { n: fmtKcal(hint.avg_steps), d: hint.days, a: lvl(hint.current), b: lvl(hint.suggested) })))),
      h('div', { class: 'act-hint-actions' },
        h('button', { class: 'btn-link', type: 'button', onclick: () => { try { localStorage.setItem(KEY, String(Date.now())); } catch (e) { /* ignore */ } card.remove(); } }, t('today.hint_later')),
        h('button', { class: 'btn-link strong', type: 'button', onclick: () => nav.go('/menu/profile') }, t('today.hint_open'))));
    return card;
  }

  // Приёмы сгруппированы по типу: завтрак, обед, ужин, перекусы. Группа сворачивается; по умолчанию открыта та,
  // где последняя запись за сегодня. Что открыто, помним до ухода с экрана (обновление данных не схлопывает).
  function mealGroups(day) {
    const isToday = day.date === todayISO() || curDate === todayISO();
    const by = new Map(GROUPS.map((g) => [g, []]));
    day.meals.forEach((m) => by.get(GROUPS.includes(m.type) ? m.type : 'snack').push(m));
    const key = (g) => curDate + ':' + g;
    if (!openInit.has(curDate)) {
      openInit.add(curDate);
      const last = isToday ? [...day.meals].sort((x, y) => String(x.time).localeCompare(String(y.time))).pop() : null;
      if (last) openGroups.add(key(GROUPS.includes(last.type) ? last.type : 'snack'));
    }
    return GROUPS.filter((g) => by.get(g).length).map((g) => {
      const list = by.get(g);
      const mt = mealType(g);
      const sum = list.reduce((a, m) => ({ kcal: a.kcal + (m.kcal || 0), protein: a.protein + (m.protein || 0), fat: a.fat + (m.fat || 0), carbs: a.carbs + (m.carbs || 0) }), { kcal: 0, protein: 0, fat: 0, carbs: 0 });
      ['protein', 'fat', 'carbs'].forEach((k) => { sum[k] = Math.round(sum[k] * 10) / 10; });
      const names = list.flatMap((m) => (m.items || []).map((i) => i.name));
      const times = list.map((m) => m.time).filter(Boolean).sort();
      const when = times.length > 1 && times[0] !== times[times.length - 1] ? `${times[0]}–${times[times.length - 1]}` : (times[0] || '');
      const body = h('div', { class: 'meal-group-body' },
        list.map(mealRow),
        isToday ? h('button', { class: 'row meal-add-row', type: 'button', onclick: () => { sessionStorage.setItem('meal-type', g); nav.go('/add/meal'); } },
          icon('plus', 18), h('span', null, t('today.add_to', { n: mt.label.toLowerCase() }))) : null);
      const title = g === 'snack' ? t('meal.snacks') : mt.label;
      const head = h('button', { class: 'row meal-group-head', type: 'button', 'aria-label': `${title}, ${fmtKcal(sum.kcal)} ${t('unit.kcal')}`,
        onclick: () => { haptic('light'); setOpen(!openGroups.has(key(g))); } },
        h('div', { class: 'thumb' }, mealPic(g, 44) || tile(mt.icon, mt.color, 44)),
        h('span', { class: 'grow' },
          h('span', { class: 'row-title' }, title, when ? h('span', { class: 'meal-when' }, when) : null),
          h('span', { class: 'row-sub clamp' }, names.join(', ') || t('today.no_items')),
          h('span', { class: 'row-sub' }, macroLine(sum))),
        h('span', { class: 'meal-kcal' }, fmtKcal(sum.kcal), h('small', null, ' ' + t('unit.kcal'))),
        h('span', { class: 'meal-chev' }, icon('chevron-right', 18)));
      const card = h('div', { class: 'list meal-group' }, head, body);
      function setOpen(on) {
        if (on) openGroups.add(key(g)); else openGroups.delete(key(g));
        card.classList.toggle('open', on);
        head.setAttribute('aria-expanded', String(on));
        body.hidden = !on;
      }
      setOpen(openGroups.has(key(g)));
      return card;
    });
  }

  function mealRow(m) {
    const mt = mealType(m.type);
    const fallback = () => mealIcon(m, 40) || mealPic(m.type, 40) || tile(mt.icon, mt.color, 40);
    // В списке всегда иконка блюда; само фото открывается на экране приёма (здесь только счётчик).
    const n = (m.photos || []).length;
    const thumb = h('div', { class: 'thumb' }, fallback(),
      n ? h('span', { class: 'thumb-count' }, icon('photo', 10), String(n)) : null);
    const names = (m.items || []).map((i) => i.name).slice(0, 3).join(', ');
    return h('button', { class: 'row meal-row', type: 'button', onclick: () => nav.go('/meal/' + m.id), 'aria-label': `${mt.label} ${m.time}, ${m.kcal} ${t('unit.kcal')}` },
      thumb,
      h('span', { class: 'grow' },
        h('span', { class: 'row-title clamp1' }, names || t('today.no_items')),
        h('span', { class: 'row-sub' }, `${m.time} · ${macroLine(m)}`)),
      h('span', { class: 'meal-kcal' }, fmtKcal(m.kcal), h('small', null, ' ' + t('unit.kcal'))));
  }

  await load();
  return () => window.removeEventListener('app:data', onData);
}
