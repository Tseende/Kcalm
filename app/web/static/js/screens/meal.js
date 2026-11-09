// Meal detail
import { api } from '../api.js';
import { mealIcon, mealPic } from '../pics.js';
import { scanBarcode, decodeFile, validBarcode } from '../scanner.js';
import { t } from '../i18n.js';
import {
  h, icon, toast, haptic, nav, sheet, confirmDialog, pageHead, sectionTitle, skeletonBlocks, errorState, photoImg,
  fmtN, fmtKcal, macroLine, longDate, mealType, MEAL_TYPES, dishKind, numInput, numVal, numSet, numCheck, codeInput, busyOverlay, macroChips, reloadRoute,
  frow, group, segmented,
} from '../ui.js';
import { pickFood, toPayload } from '../food_picker.js';

const G_MIN = 1;
const G_MAX = 5000; // как на сервере (PATCH /items/:id)
const r1 = (v) => Math.round(v * 10) / 10;

export default async function render(root, ctx) {
  const id = ctx.params.id;
  const view = h('div', { class: 'screen' });
  root.append(view);
  // скелетон повторяет раскладку: фото 4:3, итоги, список позиций
  view.append(pageHead(t('add.meal'), { parent: '/today' }),
    skeletonBlocks([h('div', { class: 'skel skel-card skel-photo' }), 120, 150]));
  let meal;
  let dishNames = {};

  try {
    const [m, d] = await Promise.all([api.get('/meals/' + id), api.get('/dishes').catch(() => ({ dishes: [] }))]);
    meal = m;
    d.dishes.forEach((x) => { dishNames[x.id] = x; });
  } catch (e) {
    if (ctx.alive()) { view.replaceChildren(pageHead(t('add.meal'))); const b = h('div'); view.append(b); errorState(b, e, reloadRoute); }
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
    const range = meal.kcal_low != null && meal.kcal_high != null ? t('meal.range', { a: fmtKcal(meal.kcal_low * k), b: fmtKcal(meal.kcal_high * k) }) : '';
    totalsEl.replaceChildren(
      h('div', { class: 'totals-kcal' }, h('span', { class: 'big' }, fmtKcal(kcal)), ' ' + t('unit.kcal')),
      range ? h('div', { class: 'range-hint small' }, range) : null,
      macroChips({ p: meal.protein + d.protein, f: meal.fat + d.fat, c: meal.carbs + d.carbs }, { pills: true }),
      meal.confidence != null ? h('div', { class: 'muted small' }, t('meal.conf', { p: Math.round(meal.confidence * 100) })) : null);
  }

  function onMeal(m, redraw) {
    meal = m;
    if (redraw) { draw(); return; }
    m.items.forEach((it) => comps.get(it.id)?.update(it));
    for (const [iid, c] of comps) if (!m.items.some((i) => i.id === iid)) { c.el.remove(); comps.delete(iid); }
    if (!m.items.length && !view.querySelector('.items-empty')) itemsBox.append(h('div', { class: 'muted items-empty pad' }, t('meal.no_left')));
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

  // Дождаться, пока все правки граммов дойдут до сервера (перед шторкой правки позиции).
  async function settle() {
    comps.forEach((c) => c.flush());
    for (let i = 0; i < 200 && (pumping || queued.size); i++) await new Promise((r) => setTimeout(r, 30));
  }

  // Шторка «Изменить позицию»: название, граммы, К/Б/Ж/У. Числа хранятся на 100 г или на порцию (на текущие граммы).
  function itemSheet(it) {
    let per = '100g';
    const base = it.per100 || { kcal: it.kcal * 100 / it.grams, protein: it.protein * 100 / it.grams, fat: it.fat * 100 / it.grams, carbs: it.carbs * 100 / it.grams };
    const nameI = h('input', { type: 'text', maxlength: 120, value: it.name, 'aria-label': t('common.name'), autocomplete: 'off' });
    const grams = numInput({ value: it.grams, min: G_MIN, max: G_MAX, decimals: 1, 'aria-label': t('add.grams') });
    const mk = (v, label) => numInput({ value: Math.round(v * 10) / 10, min: 0, max: 900, decimals: 1, 'aria-label': label });
    const kcal = mk(base.kcal, t('prof.f_kcal'));
    const p = mk(base.protein, t('prof.f_p'));
    const f = mk(base.fat, t('prof.f_f'));
    const c = mk(base.carbs, t('prof.f_c'));
    const nums = [kcal, p, f, c];
    const hint = h('div', { class: 'hint small', hidden: true });
    const g = () => numVal(grams) || it.grams;
    const limits = () => { kcal._num.max = per === 'portion' ? 10000 : 900; [p, f, c].forEach((x) => { x._num.max = per === 'portion' ? 1000 : 100; }); };
    limits();
    let touched = false;   // числа правили руками; простое переключение «на 100 г / на порцию» ничего не меняет
    function mismatch() {
      const [k, pp, ff, cc] = nums.map((x) => numVal(x) || 0);
      const calc = 4 * pp + 9 * ff + 4 * cc;
      const d = Math.abs(k - calc);
      hint.hidden = !(k > 0 && d > 20 && d > 0.2 * Math.max(k, calc));
      if (!hint.hidden) hint.textContent = t('fp.mismatch', { a: fmtN(Math.round(calc)) });
    }
    nums.forEach((x) => x.addEventListener('input', () => { touched = true; mismatch(); }));
    const seg = segmented([['100g', t('common.per100')], ['portion', t('fp.per_portion')]], per, (v) => {
      if (v === per) return;
      const k = v === 'portion' ? g() / 100 : 100 / g();
      nums.forEach((x) => { const n = numVal(x); if (n !== null) numSet(x, Math.round(n * k * 10) / 10); });
      per = v;
      limits();
      mismatch();
    }, t('fp.basis'));
    let busy = false;
    const save = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: async () => {
      if (busy) return;
      const n = nameI.value.trim();
      if (!n) { toast(t('fp.v_name'), 'error'); nameI.focus(); return; }
      for (const [el, label] of [[grams, t('add.grams')], [kcal, t('prof.f_kcal')], [p, t('prof.f_p')], [f, t('prof.f_f')], [c, t('prof.f_c')]]) {
        const bad = numCheck(el, label);
        if (bad) { toast(bad, 'error'); el.focus(); return; }
        if (numVal(el) === null) { toast(t('num.enter', { label }), 'error'); el.focus(); return; }
      }
      const gv = numVal(grams);
      if (gv < G_MIN || gv > G_MAX) { toast(t('meal.max_g', { v: G_MAX }), 'error'); return; }
      const body = {};
      if (n !== it.name) body.name = n;
      if (gv !== it.grams) body.grams = gv;
      const [k, pp, ff, cc] = nums.map((x) => numVal(x));
      const changed = touched;
      if (changed) Object.assign(body, { kcal: k, protein: pp, fat: ff, carbs: cc, per });
      if (!Object.keys(body).length) { sh.close(); return; }
      busy = true;
      save.disabled = true;
      try {
        const mm = await api.patch('/items/' + it.id, body);
        haptic('success');
        sh.close();
        onMeal(mm);
      } catch (e) { busy = false; save.disabled = false; toast(e.message, 'error'); }
    } }, t('common.save'));
    const replace = h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: async () => {
      sh.close();
      const e = await pickFood({ cta: t('fp.replace_cta'), grams: it.grams });
      if (!e) return;
      if (!ctx.alive()) return;
      try {
        // из базы: позиция берёт значения продукта; свободный ввод уходит как обычная ручная правка
        const { save: _s, ...rest } = toPayload(e);
        const body = rest;
        const mm = await api.patch('/items/' + it.id, body);
        haptic('success');
        onMeal(mm);
      } catch (er) { toast(er.message, 'error'); }
    } }, icon('package', 18), t('fp.replace'));
    const sh = sheet({
      title: t('fp.edit_title'),
      content: h('div', { class: 'stack' },
        group([frow(t('common.name'), nameI), frow(t('add.grams'), grams)]),
        seg,
        group([frow(t('prof.f_kcal'), kcal), frow(t('prof.f_p'), p), frow(t('prof.f_f'), f), frow(t('prof.f_c'), c)]),
        hint, save, replace),
    });
    mismatch();
  }

  async function addProduct() {
    await settle();
    const e = await pickFood({ cta: t('fp.add') });
    if (!e || !ctx.alive()) return;
    try {
      const mm = await api.post('/meals/' + meal.id + '/items', toPayload(e));
      haptic('success');
      onMeal(mm, true);
      toast(t('fp.added'), 'ok');
    } catch (er) { toast(er.message, 'error'); }
  }

  const itemsBox = h('div', { class: 'list items' });

  function itemCard(initial) {
    let it = initial;      // последнее состояние с сервера
    let local = it.grams;  // что показываем; именно это уйдёт на сервер
    let sent = it.grams;   // что уже отправлено или стоит в очереди
    let timer = null;
    const input = numInput({ value: it.grams, max: G_MAX, decimals: 1, class: 'g-input', 'aria-label': t('meal.a_grams', { n: it.name }) });
    const field = h('span', { class: 'g-field' }, input, h('span', { class: 'g-unit', 'aria-hidden': 'true' }, t('unit.g')));
    const nameEl = h('button', { type: 'button', class: 'item-title item-title-btn', 'aria-label': t('fp.edit_title'), onclick: () => editItem() });
    const kcalEl = h('div', { class: 'item-kcal' });
    const macEl = h('div', { class: 'muted small' });
    // карандаш внутри кнопки названия: клик открывает ту же шторку, а перенос строки его не отрывает
    const flag = h('span', { class: 'edit-btn', 'aria-hidden': 'true' }, icon('pencil', 14));
    const ratio = () => (it.grams > 0 ? local / it.grams : 1);
    // Поле нельзя перезаписывать ответом сервера, пока пользователь его правит или правка ещё не доехала.
    const dirty = () => document.activeElement === input || timer !== null || local !== sent || queued.has(it.id) || sendingId === it.id;
    function paint() {
      const k = ratio();
      // последнее слово + карандаш в одном неразрывном куске
      const m = /^(.*?)(\S+)\s*$/s.exec(it.name);
      nameEl.replaceChildren(m && m[1] ? m[1] : '', h('span', { class: 'nw' }, m ? m[2] : it.name, flag));
      kcalEl.replaceChildren(fmtKcal(it.kcal * k), h('small', null, ' ' + t('unit.kcal')));
      macEl.textContent = macroLine({ protein: it.protein * k, fat: it.fat * k, carbs: it.carbs * k });
      const ed = !!(it.edited || local !== it.grams);
      flag.classList.toggle('on', ed);
      flag.title = ed ? t('meal.edited') : t('fp.edit_title');
    }
    async function editItem() {
      await settle();                    // граммы, что ещё не уехали, должны быть на сервере до открытия шторки
      if (!ctx.alive()) return;
      itemSheet(it);
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
        if (v < G_MIN) { setLocal(G_MIN); feedback(t('meal.min_g', { v: G_MIN })); }
        else if (v > G_MAX) { setLocal(G_MAX); feedback(t('meal.max_g', { v: G_MAX })); }
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
      try { await api.post('/items/' + it.id + '/save_product', {}); haptic('success'); toast(t('meal.saved_prod'), 'ok'); } catch (e) { toast(e.message, 'error'); }
    }
    // Уточнение по упаковке: штрихкод → Open Food Facts → название и КБЖУ на 100 г, граммы остаются.
    async function applyCode(raw) {
      const code = String(raw || '').replace(/\D/g, '');
      if (!validBarcode(code)) { toast(t('lbl.v_bad'), 'error'); return; }
      const stop = busyOverlay(t('lbl.searching'), 'Open Food Facts');
      try {
        const m = await api.post('/items/' + it.id + '/barcode', { code });
        stop();
        haptic('success');
        onMeal(m);
        toast(t('meal.refined') + ': ' + (m.items.find((i) => i.id === it.id)?.name || ''), 'ok');
      } catch (e) {
        stop();
        toast(e.status === 404 ? t('meal.not_in_db') : e.message, 'error');
      }
    }
    function typeCode() {
      const inp = codeInput({ placeholder: t('lbl.ph_code'), 'aria-label': t('pf.barcode') });
      const go = () => { const v = inp.value; s.close(); applyCode(v); };
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
      const s = sheet({ title: t('add.code_title'), content: h('div', { class: 'stack' }, inp,
        h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, t('lbl.find'))) });
      setTimeout(() => inp.focus(), 250);
    }
    const shot = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: async () => {
      const f = shot.files[0];
      shot.value = '';
      if (!f) return;
      const stop = busyOverlay(t('lbl.reading_code'));
      let code = null;
      try { code = await decodeFile(f); } catch (e) { /* скажем ниже */ }
      stop();
      if (code) applyCode(code); else { toast(t('add.code_not_found'), 'error'); typeCode(); }
    } });
    async function scan() {
      haptic('light');
      const r = await scanBarcode();
      if (!r) return;
      if (r.code) { applyCode(r.code); return; }
      if (r.manual) { typeCode(); return; }
      toast(r.error === 'load' ? t('lbl.scan_load') : t('lbl.no_cam'), 'error');
      if (r.error === 'camera') shot.click();
    }
    async function remove() {
      if (!(await confirmDialog(t('pf.del_q', { n: it.name })))) return;
      clearTimeout(timer); timer = null; queued.delete(it.id);
      try { onMeal(await api.del('/items/' + it.id)); } catch (e) { toast(e.message, 'error'); }
    }
    const el = h('div', { class: 'item' },
      h('div', { class: 'row-between top' }, h('div', { class: 'item-name' }, nameEl), kcalEl),
      macEl,
      h('div', { class: 'item-ctl' },
        h('div', { class: 'stepper' },
          holdRepeat(h('button', { type: 'button', class: 'step', 'aria-label': t('meal.dec10') }, icon('minus', 18)), -1),
          field,
          holdRepeat(h('button', { type: 'button', class: 'step', 'aria-label': t('meal.inc10') }, icon('plus', 18)), 1)),
        h('div', { class: 'item-actions' }, shot,
          h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('meal.a_scan'), title: t('meal.t_scan'), onclick: scan }, icon('barcode', 20)),
          h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('meal.a_save'), title: t('meal.t_save'), onclick: saveProduct }, icon('save', 20)),
          h('button', { type: 'button', class: 'icon-btn danger', 'aria-label': t('meal.a_del_item'), onclick: remove }, icon('trash', 20)))));
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
      return h('div', { class: 'photo-ph', 'aria-hidden': 'true' }, (mealIcon(meal, 64) || icon(mealType(meal.type).icon, 44)), h('span', { class: 'muted small' }, meal.source === 'text' ? t('meal.text_src') : t('meal.no_photo')));
    }
    const track = h('div', { class: 'carousel', tabindex: 0, 'aria-label': t('meal.photo_alt') }, meal.photos.map((p, i) => {
      const dish = p.dish_id && dishNames[p.dish_id];
      return h('figure', { class: 'slide', 'aria-label': t('meal.photo_n', { i: i + 1, n: meal.photos.length }) }, photoImg(p.url, '', t('meal.photo_alt')),
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
    const input = h('input', { type: 'time', value: meal.time, class: 'time-big', 'aria-label': t('meal.time_title') });
    const s = sheet({ title: t('meal.time_title'), content: h('div', { class: 'stack' }, input,
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: async () => {
        if (!input.value) return;
        try { const m = await api.patch('/meals/' + meal.id, { time: input.value }); s.close(); haptic('success'); onMeal(m, true); } catch (e) { toast(e.message, 'error'); }
      } }, t('common.save'))) });
  }

  function pickType() {
    const s = sheet({ title: t('meal.type_title'), content: h('div', { class: 'chips wrap' }, Object.entries(MEAL_TYPES).map(([k, v]) =>
      h('button', { class: 'chip' + (meal.type === k ? ' active' : ''), type: 'button', onclick: async () => {
        try { const m = await api.patch('/meals/' + meal.id, { type: k }); s.close(); onMeal(m, true); } catch (e) { toast(e.message, 'error'); }
      } }, (mealPic(k, 24) || icon(v.icon, 18)), v.label))) });
  }

  function refine() {
    const ta = h('textarea', { rows: 3, placeholder: t('meal.ph_refine'), 'aria-label': t('meal.a_refine') });
    const s = sheet({ title: t('meal.refine_title'), content: h('div', { class: 'stack' },
      h('div', { class: 'muted small' }, t('meal.refine_hint')), ta,
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: async () => {
        const text = ta.value.trim();
        if (!text) return;
        s.close();
        const stop = busyOverlay(t('meal.recalc'), t('add.recognizing_sub'));
        try { const m = await api.post('/meals/' + meal.id + '/refine', { comment: text }, { timeout: 120000 }); haptic('success'); onMeal(m, true); } catch (e) { toast(e.message, 'error'); } finally { stop(); }
      } }, t('meal.recalc_btn'))) });
    setTimeout(() => ta.focus(), 250);
  }

  async function removeMeal() {
    if (!(await confirmDialog(t('meal.del_q')))) return;
    try { await api.del('/meals/' + meal.id); haptic('success'); toast(t('meal.deleted')); nav.replace('/today'); } catch (e) { toast(e.message, 'error'); }
  }

  function draw() {
    comps = new Map();
    itemsBox.replaceChildren();
    meal.items.forEach((it) => { const c = itemCard(it); comps.set(it.id, c); itemsBox.append(c.el); });
    if (!meal.items.length) itemsBox.append(h('div', { class: 'muted items-empty pad' }, t('meal.no_items')));
    const mt = mealType(meal.type);
    paintTotals();
    view.replaceChildren(...[
      pageHead(t('add.meal'), { parent: '/today' }),
      carousel(),
      h('div', { class: 'meta-row' },
        h('button', { class: 'chip', type: 'button', onclick: pickType, 'aria-label': t('meal.a_type', { n: mt.label }) }, (mealPic(meal.type, 24) || icon(mt.icon, 18)), mt.label),
        h('button', { class: 'chip', type: 'button', onclick: pickTime, 'aria-label': t('meal.a_time', { n: meal.time }) }, icon('clock', 16), meal.time),
        h('span', { class: 'muted small' }, longDate(meal.date))),
      totalsEl,
      meal.comment ? h('div', { class: 'card comment' }, h('div', { class: 'muted small' }, t('add.comment')), h('div', null, meal.comment)) : null,
      sectionTitle(t('meal.items')),
      itemsBox,
      h('button', { class: 'btn btn-secondary btn-block add-item-btn', type: 'button', onclick: addProduct }, icon('plus', 18), t('fp.add_item')),
      h('div', { class: 'meal-actions' },
        h('button', { class: 'act' + (meal.favorite_id ? ' star-on' : ''), type: 'button',
          'aria-pressed': String(!!meal.favorite_id), 'aria-label': meal.favorite_id ? t('favf.rm') : t('add.fav_aria'),
          title: meal.favorite_id ? t('meal.in_fav') : t('add.fav_aria'), onclick: (ev) => toggleFavorite(ev.currentTarget) }, icon('star', 24)),
        h('button', { class: 'act', type: 'button', 'aria-label': t('meal.refine'), title: t('meal.refine'), onclick: refine }, icon('pencil', 24)),
        h('button', { class: 'act danger', type: 'button', 'aria-label': t('meal.del_meal'), title: t('meal.del_meal'), onclick: removeMeal }, icon('trash', 24)))].filter(Boolean));
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
        toast(t('favf.removed'));
      } else {
        meal.favorite_id = (await api.post('/favorites', { meal_id: meal.id })).id;
        toast(t('meal.fav_added'), 'ok');
      }
      haptic('light');
      // без полной перерисовки: карусель не мигает и не возвращается на первый слайд
      const on = !!meal.favorite_id;
      btn.classList.toggle('star-on', on);
      btn.setAttribute('aria-pressed', String(on));
      btn.setAttribute('aria-label', on ? t('favf.rm') : t('add.fav_aria'));
      btn.title = on ? t('meal.in_fav') : t('add.fav_aria');
    } catch (e) { toast(e.message, 'error'); } finally { favBusy = false; btn.disabled = false; }
  }

  draw();
  const flushAll = () => comps.forEach((c) => c.flush());
  document.addEventListener('visibilitychange', flushAll);
  window.addEventListener('pagehide', flushAll);
  return () => { document.removeEventListener('visibilitychange', flushAll); window.removeEventListener('pagehide', flushAll); flushAll(); };
}
