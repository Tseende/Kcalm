// Meal detail
import { api } from '../api.js';
import { mealIcon, mealPic } from '../pics.js';
import { scanBarcode, decodeFile, validBarcode } from '../scanner.js';
import {
  h, icon, toast, haptic, nav, sheet, confirmDialog, pageHead, sectionTitle, skeletonBlocks, errorState, photoImg,
  fmtN, fmtKcal, longDate, mealType, MEAL_TYPES, dishKind, numInput, numVal, numSet, codeInput, busyOverlay, macroChips, reloadRoute,
} from '../ui.js';

const G_MIN = 1;
const G_MAX = 5000; // как на сервере (PATCH /items/:id)
const r1 = (v) => Math.round(v * 10) / 10;

export default async function render(root, ctx) {
  const id = ctx.params.id;
  const view = h('div', { class: 'screen' });
  root.append(view);
  // скелетон повторяет раскладку: фото 4:3, итоги, список позиций
  view.append(pageHead('Приём пищи', { parent: '/today' }),
    skeletonBlocks([h('div', { class: 'skel skel-card skel-photo' }), 120, 150]));
  let meal;
  let dishNames = {};

  try {
    const [m, d] = await Promise.all([api.get('/meals/' + id), api.get('/dishes').catch(() => ({ dishes: [] }))]);
    meal = m;
    d.dishes.forEach((x) => { dishNames[x.id] = x; });
  } catch (e) {
    if (ctx.alive()) { view.replaceChildren(pageHead('Приём пищи')); const b = h('div'); view.append(b); errorState(b, e, reloadRoute); }
    return;
  }
  if (!ctx.alive()) return;

  let comps = new Map();
  const totalsEl = h('section', { class: 'card totals' });

  // Итоги = ответ сервера + локальные поправки по строкам, которые правят прямо сейчас (оптимистично).
  function paintTotals() {
    const d = { kcal: 0, protein: 0, fat: 0, carbs: 0 };
    comps.forEach((c) => { const x = c.delta(); d.kcal += x.kcal; d.protein += x.protein; d.fat += x.fat; d.carbs += x.carbs; });
    const kcal = meal.kcal + d.kcal;
    const k = meal.kcal > 0 ? kcal / meal.kcal : 1;
    const range = meal.kcal_low != null && meal.kcal_high != null ? `диапазон ${fmtKcal(meal.kcal_low * k)}–${fmtKcal(meal.kcal_high * k)} ккал` : '';
    totalsEl.replaceChildren(
      h('div', { class: 'totals-kcal' }, h('span', { class: 'big' }, fmtKcal(kcal)), ' ккал'),
      range ? h('div', { class: 'range-hint small' }, range) : null,
      macroChips({ p: meal.protein + d.protein, f: meal.fat + d.fat, c: meal.carbs + d.carbs }, { pills: true }),
      meal.confidence != null ? h('div', { class: 'muted small' }, `Уверенность распознавания: ${Math.round(meal.confidence * 100)}%`) : null);
  }

  function onMeal(m, redraw) {
    meal = m;
    if (redraw) { draw(); return; }
    m.items.forEach((it) => comps.get(it.id)?.update(it));
    for (const [iid, c] of comps) if (!m.items.some((i) => i.id === iid)) { c.el.remove(); comps.delete(iid); }
    if (!m.items.length && !view.querySelector('.items-empty')) itemsBox.append(h('div', { class: 'muted items-empty pad' }, 'Позиций не осталось.'));
    paintTotals();
  }

  // Правки граммов уходят на сервер по одной и по порядку: ответы не обгоняют друг друга, а последний
  // ответ отражает все записи. Поле с ожидающей правкой ответ не перезаписывает (см. update у строки).
  const queued = new Map(); // id позиции -> граммы, ещё не отправлено
  let sendingId = null;
  let pumping = false;
  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (queued.size) {
        const [iid, v] = queued.entries().next().value;
        queued.delete(iid);
        sendingId = iid;
        let m = null;
        try { m = await api.patch('/items/' + iid, { grams: v }); } catch (e) {
          toast(e.message, 'error');
          try { m = await api.get('/meals/' + id); } catch (e2) { /* останутся прежние значения */ }
        }
        sendingId = null;
        if (m && ctx.alive()) onMeal(m);
      }
    } finally { pumping = false; sendingId = null; }
  }

  const itemsBox = h('div', { class: 'list items' });

  function itemCard(initial) {
    let it = initial;      // последнее состояние с сервера
    let local = it.grams;  // что показываем; именно это уйдёт на сервер
    let sent = it.grams;   // что уже отправлено или стоит в очереди
    let timer = null;
    const input = numInput({ value: it.grams, max: G_MAX, decimals: 1, class: 'g-input', 'aria-label': 'Граммы: ' + it.name });
    const field = h('span', { class: 'g-field' }, input, h('span', { class: 'g-unit', 'aria-hidden': 'true' }, 'г'));
    const nameEl = h('span', { class: 'item-title' });
    const kcalEl = h('div', { class: 'item-kcal' });
    const macEl = h('div', { class: 'muted small' });
    const flag = h('span', { class: 'edited', title: 'Изменено вручную' }, icon('pencil', 14));
    const ratio = () => (it.grams > 0 ? local / it.grams : 1);
    // Поле нельзя перезаписывать ответом сервера, пока пользователь его правит или правка ещё не доехала.
    const dirty = () => document.activeElement === input || timer !== null || local !== sent || queued.has(it.id) || sendingId === it.id;
    function paint() {
      const k = ratio();
      nameEl.textContent = it.name;
      kcalEl.replaceChildren(fmtKcal(it.kcal * k), h('small', null, ' ккал'));
      macEl.textContent = `Б ${fmtN(it.protein * k)} · Ж ${fmtN(it.fat * k)} · У ${fmtN(it.carbs * k)}`;
      flag.hidden = !(it.edited || local !== it.grams);
    }
    function setLocal(v) { local = v; paint(); paintTotals(); }
    function feedback(msg) {
      toast(msg);
      haptic('warning');
      field.classList.add('clamped');
      setTimeout(() => field.classList.remove('clamped'), 900);
    }
    function flush() {
      clearTimeout(timer);
      timer = null;
      if (document.activeElement === input) { const v = numVal(input); if (v !== null && v >= G_MIN && v <= G_MAX) setLocal(r1(v)); }
      if (local === sent) return;
      sent = local;
      queued.set(it.id, local);
      pump();
    }
    function schedule(ms) { clearTimeout(timer); timer = setTimeout(flush, ms); }
    // Круглим к десяткам в сторону нажатия: 121 → 130 / 120, а 120 → 130 / 110.
    function step(dir) {
      let cur = numVal(input);
      if (cur === null || cur < G_MIN || cur > G_MAX) cur = local;
      let v = cur % 10 !== 0 ? (dir > 0 ? Math.ceil(cur / 10) : Math.floor(cur / 10)) * 10 : cur + dir * 10;
      v = Math.min(G_MAX, Math.max(G_MIN, v));
      if (v === cur) { haptic('warning'); return false; }
      numSet(input, v);
      if (document.activeElement === input) input.select();
      setLocal(v);
      haptic('selection');
      schedule(500);
      return true;
    }
    input.addEventListener('input', () => {
      const v = numVal(input);
      if (v === null || v < G_MIN || v > G_MAX) { clearTimeout(timer); timer = null; return; }
      setLocal(r1(v));
      schedule(600);
    });
    // Правка закончена: пустое поле возвращает прежнее значение, выход за границы поджимается с подсказкой.
    input.addEventListener('blur', () => {
      const v = numVal(input);
      if (v !== null) {
        if (v < G_MIN) { setLocal(G_MIN); feedback(`Минимум ${G_MIN} г`); }
        else if (v > G_MAX) { setLocal(G_MAX); feedback(`Максимум ${G_MAX} г`); }
        else setLocal(r1(v));
      }
      numSet(input, local);
      flush();
      if (!dirty()) update(it);
    });
    function update(n) {
      it = n;
      if (!dirty()) { local = n.grams; sent = n.grams; numSet(input, n.grams); }
      paint();
    }
    function delta() {
      const k = ratio() - 1;
      return { kcal: it.kcal * k, protein: it.protein * k, fat: it.fat * k, carbs: it.carbs * k };
    }
    // Нажатие и удержание: сразу шаг, затем повтор с ускорением. С клавиатуры (click без указателя) один шаг.
    function holdRepeat(btn, dir) {
      let t = null;
      let n = 0;
      const stop = () => { clearTimeout(t); t = null; };
      const tick = () => { if (!step(dir)) { stop(); return; } n++; t = setTimeout(tick, n > 14 ? 55 : n > 6 ? 90 : 140); };
      btn.addEventListener('pointerdown', (e) => {
        if (e.button) return;
        e.preventDefault(); // фокус и клавиатура остаются как были
        n = 0;
        if (!step(dir)) return;
        t = setTimeout(tick, 420);
      });
      ['pointerup', 'pointercancel', 'pointerleave'].forEach((ev) => btn.addEventListener(ev, stop));
      btn.addEventListener('contextmenu', (e) => e.preventDefault());
      btn.addEventListener('click', (e) => { if (e.detail === 0) step(dir); });
      return btn;
    }
    async function saveProduct() {
      try { await api.post('/items/' + it.id + '/save_product', {}); haptic('success'); toast('Сохранено в «Мои продукты»', 'ok'); } catch (e) { toast(e.message, 'error'); }
    }
    // Уточнение по упаковке: штрихкод → Open Food Facts → название и КБЖУ на 100 г, граммы остаются.
    async function applyCode(raw) {
      const code = String(raw || '').replace(/\D/g, '');
      if (!validBarcode(code)) { toast('Штрихкод не подходит: проверьте цифры', 'error'); return; }
      const stop = busyOverlay('Ищу продукт…', 'Open Food Facts');
      try {
        const m = await api.post('/items/' + it.id + '/barcode', { code });
        stop();
        haptic('success');
        onMeal(m);
        toast('Уточнено по упаковке: ' + (m.items.find((i) => i.id === it.id)?.name || ''), 'ok');
      } catch (e) {
        stop();
        toast(e.status === 404 ? 'В базе такого штрихкода нет. Оставил как есть' : e.message, 'error');
      }
    }
    function typeCode() {
      const inp = codeInput({ placeholder: '13 цифр под штрихкодом', 'aria-label': 'Штрихкод' });
      const go = () => { const v = inp.value; s.close(); applyCode(v); };
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
      const s = sheet({ title: 'Штрихкод с упаковки', content: h('div', { class: 'stack' }, inp,
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, 'Найти')) });
      setTimeout(() => inp.focus(), 250);
    }
    const shot = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: async () => {
      const f = shot.files[0];
      shot.value = '';
      if (!f) return;
      const stop = busyOverlay('Читаю штрихкод…');
      let code = null;
      try { code = await decodeFile(f); } catch (e) { /* скажем ниже */ }
      stop();
      if (code) applyCode(code); else { toast('Штрихкод на фото не нашёл. Введите цифры', 'error'); typeCode(); }
    } });
    async function scan() {
      haptic('light');
      const r = await scanBarcode();
      if (!r) return;
      if (r.code) { applyCode(r.code); return; }
      if (r.manual) { typeCode(); return; }
      toast(r.error === 'load' ? 'Сканер не загрузился' : 'Нет доступа к камере. Сфотографируйте штрихкод', 'error');
      if (r.error === 'camera') shot.click();
    }
    async function remove() {
      if (!(await confirmDialog(`Удалить «${it.name}»?`))) return;
      clearTimeout(timer); timer = null; queued.delete(it.id);
      try { onMeal(await api.del('/items/' + it.id)); } catch (e) { toast(e.message, 'error'); }
    }
    const el = h('div', { class: 'item' },
      h('div', { class: 'row-between top' }, h('div', { class: 'item-name' }, nameEl, ' ', flag), kcalEl),
      macEl,
      h('div', { class: 'item-ctl' },
        h('div', { class: 'stepper' },
          holdRepeat(h('button', { type: 'button', class: 'step', 'aria-label': 'Уменьшить на 10 грамм' }, icon('minus', 18)), -1),
          field,
          holdRepeat(h('button', { type: 'button', class: 'step', 'aria-label': 'Увеличить на 10 грамм' }, icon('plus', 18)), 1)),
        h('div', { class: 'item-actions' }, shot,
          h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Уточнить по штрихкоду упаковки', title: 'Сканер штрихкода', onclick: scan }, icon('barcode', 20)),
          h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Сохранить в мои продукты', title: 'В мои продукты', onclick: saveProduct }, icon('save', 20)),
          h('button', { type: 'button', class: 'icon-btn danger', 'aria-label': 'Удалить позицию', onclick: remove }, icon('trash', 20)))));
    paint();
    return {
      el,
      flush,
      update,
      delta,
    };
  }

  function carousel() {
    if (!meal.photos || !meal.photos.length) {
      return h('div', { class: 'photo-ph', 'aria-hidden': 'true' }, (mealIcon(meal, 64) || icon(mealType(meal.type).icon, 44)), h('span', { class: 'muted small' }, meal.source === 'text' ? 'Описание текстом' : 'Без фото'));
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
      h('div', { class: 'meal-actions' },
        h('button', { class: 'act' + (meal.favorite_id ? ' star-on' : ''), type: 'button',
          'aria-pressed': String(!!meal.favorite_id), 'aria-label': meal.favorite_id ? 'Убрать из избранного' : 'В избранное',
          title: meal.favorite_id ? 'В избранном' : 'В избранное', onclick: (ev) => toggleFavorite(ev.currentTarget) }, icon('star', 24)),
        h('button', { class: 'act', type: 'button', 'aria-label': 'Уточнить', title: 'Уточнить', onclick: refine }, icon('pencil', 24)),
        h('button', { class: 'act danger', type: 'button', 'aria-label': 'Удалить приём', title: 'Удалить приём', onclick: removeMeal }, icon('trash', 24)))].filter(Boolean));
  }

  let favBusy = false;
  async function toggleFavorite(btn) {
    if (favBusy) return;                              // двойной тап не должен создать два избранных
    favBusy = true;
    btn.disabled = true;
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
      // без полной перерисовки: карусель не мигает и не возвращается на первый слайд
      const on = !!meal.favorite_id;
      btn.classList.toggle('star-on', on);
      btn.setAttribute('aria-pressed', String(on));
      btn.setAttribute('aria-label', on ? 'Убрать из избранного' : 'В избранное');
      btn.title = on ? 'В избранном' : 'В избранное';
    } catch (e) { toast(e.message, 'error'); } finally { favBusy = false; btn.disabled = false; }
  }

  draw();
  const flushAll = () => comps.forEach((c) => c.flush());
  document.addEventListener('visibilitychange', flushAll);
  window.addEventListener('pagehide', flushAll);
  return () => { document.removeEventListener('visibilitychange', flushAll); window.removeEventListener('pagehide', flushAll); flushAll(); };
}
