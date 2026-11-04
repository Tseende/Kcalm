// Meal detail
import { api } from '../api.js';
import { mealPic } from '../pics.js';
import {
  h, icon, toast, haptic, nav, sheet, confirmDialog, pageHead, sectionTitle, skeletonCards, errorState, photoImg,
  fmtN, fmtKcal, longDate, mealType, MEAL_TYPES, dishKind, numVal, busyOverlay,
} from '../ui.js';

export default async function render(root, ctx) {
  const id = ctx.params.id;
  const view = h('div', { class: 'screen' });
  root.append(view);
  view.append(pageHead('Приём пищи', { parent: '/today' }), skeletonCards(3, 120));
  let meal;
  let dishNames = {};

  try {
    const [m, d] = await Promise.all([api.get('/meals/' + id), api.get('/dishes').catch(() => ({ dishes: [] }))]);
    meal = m;
    d.dishes.forEach((x) => { dishNames[x.id] = x; });
  } catch (e) {
    if (ctx.alive()) { view.replaceChildren(pageHead('Приём пищи')); const b = h('div'); view.append(b); errorState(b, e, () => nav.replace('/meal/' + id)); }
    return;
  }
  if (!ctx.alive()) return;

  let comps = new Map();
  const totalsEl = h('section', { class: 'card totals' });

  function paintTotals() {
    const range = meal.kcal_low != null && meal.kcal_high != null ? `диапазон ${fmtKcal(meal.kcal_low)}–${fmtKcal(meal.kcal_high)} ккал` : '';
    totalsEl.replaceChildren(
      h('div', { class: 'totals-kcal' }, h('span', { class: 'big' }, fmtKcal(meal.kcal)), ' ккал'),
      range ? h('div', { class: 'range-hint small' }, range) : null,
      h('div', { class: 'pills' },
        h('span', { class: 'pill pill-p' }, 'Б ', h('b', null, fmtN(meal.protein)), ' г'),
        h('span', { class: 'pill pill-f' }, 'Ж ', h('b', null, fmtN(meal.fat)), ' г'),
        h('span', { class: 'pill pill-c' }, 'У ', h('b', null, fmtN(meal.carbs)), ' г')),
      meal.confidence != null ? h('div', { class: 'muted small' }, `Уверенность распознавания: ${Math.round(meal.confidence * 100)}%`) : null);
  }

  function onMeal(m, redraw) {
    meal = m;
    paintTotals();
    if (redraw) { draw(); return; }
    m.items.forEach((it) => comps.get(it.id)?.update(it));
    for (const [iid, c] of comps) if (!m.items.some((i) => i.id === iid)) { c.el.remove(); comps.delete(iid); }
    if (!m.items.length && !view.querySelector('.items-empty')) itemsBox.append(h('div', { class: 'muted items-empty pad' }, 'Позиций не осталось.'));
  }

  const itemsBox = h('div', { class: 'list items' });

  function itemCard(initial) {
    let it = initial;
    let timer;
    const input = h('input', { type: 'number', inputmode: 'decimal', min: 1, step: 10, value: it.grams, class: 'g-input', 'aria-label': 'Граммы: ' + it.name });
    const kcalEl = h('div', { class: 'item-kcal' });
    const macEl = h('div', { class: 'muted small' });
    const flag = h('span', { class: 'edited', title: 'Изменено вручную' }, icon('pencil', 14));
    function paint() {
      kcalEl.replaceChildren(fmtKcal(it.kcal), h('small', null, ' ккал'));
      macEl.textContent = `Б ${fmtN(it.protein)} · Ж ${fmtN(it.fat)} · У ${fmtN(it.carbs)}`;
      flag.hidden = !it.edited;
    }
    async function commit(v) {
      try { onMeal(await api.patch('/items/' + it.id, { grams: v })); haptic('light'); } catch (e) { toast(e.message, 'error'); input.value = it.grams; }
    }
    let pending = null;
    function schedule(v) { clearTimeout(timer); pending = v; timer = setTimeout(flush, 600); }
    // Правка не должна пропадать, если ушли с экрана или свернули приложение раньше, чем сработал таймер.
    function flush() {
      clearTimeout(timer);
      if (pending === null) return;
      const v = pending;
      pending = null;
      commit(v);
    }
    function step(d) {
      const cur = numVal(input) ?? it.grams;
      const v = Math.max(1, Math.round((cur + d) * 10) / 10);
      input.value = v;
      schedule(v);
    }
    input.addEventListener('input', () => { const v = numVal(input); if (v !== null && v >= 1) schedule(v); });
    input.addEventListener('blur', flush);
    async function saveProduct() {
      try { await api.post('/items/' + it.id + '/save_product', {}); haptic('success'); toast('Сохранено в «Мои продукты»', 'ok'); } catch (e) { toast(e.message, 'error'); }
    }
    async function remove() {
      if (!(await confirmDialog(`Удалить «${it.name}»?`))) return;
      try { onMeal(await api.del('/items/' + it.id)); } catch (e) { toast(e.message, 'error'); }
    }
    const el = h('div', { class: 'item' },
      h('div', { class: 'row-between top' }, h('div', { class: 'item-name' }, it.name, ' ', flag), kcalEl),
      macEl,
      h('div', { class: 'item-ctl' },
        h('div', { class: 'stepper' },
          h('button', { type: 'button', class: 'step', 'aria-label': 'Уменьшить на 10 грамм', onclick: () => step(-10) }, icon('minus', 18)),
          input, h('span', { class: 'muted' }, 'г'),
          h('button', { type: 'button', class: 'step', 'aria-label': 'Увеличить на 10 грамм', onclick: () => step(10) }, icon('plus', 18))),
        h('div', { class: 'item-actions' },
          h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Сохранить в мои продукты', title: 'В мои продукты', onclick: saveProduct }, icon('save', 20)),
          h('button', { type: 'button', class: 'icon-btn danger', 'aria-label': 'Удалить позицию', onclick: remove }, icon('trash', 20)))));
    paint();
    return {
      el,
      flush,
      update(n) { it = n; paint(); if (document.activeElement !== input) input.value = n.grams; },
    };
  }

  function carousel() {
    if (!meal.photos || !meal.photos.length) {
      return h('div', { class: 'photo-ph', 'aria-hidden': 'true' }, (mealPic(meal.type, 64) || icon(mealType(meal.type).icon, 44)), h('span', { class: 'muted small' }, meal.source === 'text' ? 'Описание текстом' : 'Без фото'));
    }
    const track = h('div', { class: 'carousel', tabindex: 0, 'aria-label': 'Фото приёма' }, meal.photos.map((p, i) => {
      const dish = p.dish_id && dishNames[p.dish_id];
      return h('figure', { class: 'slide', 'aria-label': `Фото ${i + 1} из ${meal.photos.length}` }, photoImg(p.url, '', 'Фото приёма'),
        dish ? h('figcaption', { class: 'dish-tag' }, icon(dishKind(dish.kind).icon, 14), dish.name) : null);
    }));
    const dots = h('div', { class: 'dots' }, meal.photos.map((_, i) => h('i', { class: i === 0 ? 'on' : '' })));
    track.addEventListener('scroll', () => {
      const i = Math.round(track.scrollLeft / track.clientWidth);
      [...dots.children].forEach((d, k) => d.classList.toggle('on', k === i));
    }, { passive: true });
    return h('div', { class: 'photos' }, track, meal.photos.length > 1 ? dots : null);
  }

  function pickTime() {
    const input = h('input', { type: 'time', value: meal.time, class: 'time-big', 'aria-label': 'Время приёма' });
    const s = sheet({ title: 'Время приёма', content: h('div', { class: 'stack' }, input,
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: async () => {
        if (!input.value) return;
        try { const m = await api.patch('/meals/' + meal.id, { time: input.value }); s.close(); haptic('success'); onMeal(m, true); } catch (e) { toast(e.message, 'error'); }
      } }, 'Сохранить')) });
  }

  function pickType() {
    const s = sheet({ title: 'Тип приёма', content: h('div', { class: 'chips wrap' }, Object.entries(MEAL_TYPES).map(([k, v]) =>
      h('button', { class: 'chip' + (meal.type === k ? ' active' : ''), type: 'button', onclick: async () => {
        try { const m = await api.patch('/meals/' + meal.id, { type: k }); s.close(); onMeal(m, true); } catch (e) { toast(e.message, 'error'); }
      } }, (mealPic(k, 24) || icon(v.icon, 18)), v.label))) });
  }

  function refine() {
    const ta = h('textarea', { rows: 3, placeholder: 'Например: жарил на масле 1 ст. л., соус сметанный', 'aria-label': 'Уточнение' });
    const s = sheet({ title: 'Уточнить приём', content: h('div', { class: 'stack' },
      h('div', { class: 'muted small' }, 'Добавьте детали: бот пересчитает позиции.'), ta,
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: async () => {
        const text = ta.value.trim();
        if (!text) return;
        s.close();
        const stop = busyOverlay('Пересчитываю…', 'Это может занять до минуты');
        try { const m = await api.post('/meals/' + meal.id + '/refine', { comment: text }, { timeout: 120000 }); haptic('success'); onMeal(m, true); } catch (e) { toast(e.message, 'error'); } finally { stop(); }
      } }, 'Пересчитать')) });
    setTimeout(() => ta.focus(), 250);
  }

  async function removeMeal() {
    if (!(await confirmDialog('Удалить этот приём пищи?'))) return;
    try { await api.del('/meals/' + meal.id); haptic('success'); toast('Приём удалён'); nav.replace('/today'); } catch (e) { toast(e.message, 'error'); }
  }

  function draw() {
    comps = new Map();
    itemsBox.replaceChildren();
    meal.items.forEach((it) => { const c = itemCard(it); comps.set(it.id, c); itemsBox.append(c.el); });
    if (!meal.items.length) itemsBox.append(h('div', { class: 'muted items-empty pad' }, 'Позиций нет.'));
    const mt = mealType(meal.type);
    paintTotals();
    view.replaceChildren(...[
      pageHead('Приём пищи', { parent: '/today' }),
      carousel(),
      h('div', { class: 'meta-row' },
        h('button', { class: 'chip', type: 'button', onclick: pickType, 'aria-label': 'Тип приёма: ' + mt.label }, (mealPic(meal.type, 24) || icon(mt.icon, 18)), mt.label),
        h('button', { class: 'chip', type: 'button', onclick: pickTime, 'aria-label': 'Время ' + meal.time + ', изменить' }, icon('clock', 16), meal.time),
        h('span', { class: 'muted small' }, longDate(meal.date))),
      totalsEl,
      meal.comment ? h('div', { class: 'card comment' }, h('div', { class: 'muted small' }, 'Комментарий'), h('div', null, meal.comment)) : null,
      sectionTitle('Позиции'),
      itemsBox,
      h('div', { class: 'stack-sm actions' },
        h('button', { class: 'btn btn-secondary btn-block' + (meal.favorite_id ? ' star-on' : ''), type: 'button',
          'aria-pressed': String(!!meal.favorite_id), onclick: toggleFavorite },
        icon('star', 18), meal.favorite_id ? 'В избранном' : 'В избранное'),
        h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: refine }, icon('pencil', 18), 'Уточнить'),
        h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: removeMeal }, icon('trash', 18), 'Удалить приём'))].filter(Boolean));
  }

  async function toggleFavorite() {
    try {
      if (meal.favorite_id) {
        await api.del('/favorites/' + meal.favorite_id);
        meal.favorite_id = null;
        toast('Убрано из избранного');
      } else {
        meal.favorite_id = (await api.post('/favorites', { meal_id: meal.id })).id;
        toast('Добавлено в избранное. Теперь оно под рукой при добавлении приёма', 'ok');
      }
      haptic('light');
      draw();
    } catch (e) { toast(e.message, 'error'); }
  }

  draw();
  const flushAll = () => comps.forEach((c) => c.flush());
  document.addEventListener('visibilitychange', flushAll);
  window.addEventListener('pagehide', flushAll);
  return () => { document.removeEventListener('visibilitychange', flushAll); window.removeEventListener('pagehide', flushAll); flushAll(); };
}
