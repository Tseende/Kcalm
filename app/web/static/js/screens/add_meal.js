// Add meal: dishes (each = several photos + one plate) and/or a text description. Time and meal type are set by the server (now).
import { api } from '../api.js';
import {
  h, icon, tile, toast, haptic, nav, mainButton, draft, pageHead, compressImage, busyOverlay, dishKind, sheet, fmtN, numInput, numVal, numCheck, codeInput, frow, macroLine, sectionTitle,
} from '../ui.js';
import { scanBarcode, decodeFile, validBarcode } from '../scanner.js';
import { dishInfo } from './dishes.js';
import { pickFood, editEntryGrams, entryAbs, entryTotal, toPayload } from '../food_picker.js';
import { dishPic } from '../pics.js';
import { t, tn } from '../i18n.js';

const MAX = 6;

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  let dishes = [];
  // Блюдо = несколько фото (тарелка, упаковка, этикетка) + одна посуда. Блюд в приёме может быть несколько.
  // note — уточнение словами (состав, готовка), ings — продукты из базы/штрихкода: [{name, brand, kcal, protein, fat, carbs}]
  let gidSeq = 0;
  const newGroup = (dishId = null) => ({ id: ++gidSeq, photos: [], dishId, note: '', ings: [] });
  const groups = [newGroup()]; // photos: [{file, url}]
  const allPhotos = () => groups.flatMap((g) => g.photos);
  let target = groups[0];
  let busy = false;

  // Форма рисуется сразу; список посуды подтягивается следом (loadDishes).

  const camera = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => addFiles(camera) });
  const gallery = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => addFiles(gallery) });
  const textTa = h('textarea', { rows: 3, placeholder: t('add.ph_text'), oninput: () => { sync(); quickVisibility(); } });
  const textLabel = h('span', { class: 'field-label' }, t('add.what'));
  const clearDraft = draft('meal-text', textTa);
  const dishList = h('div', { class: 'stack' });
  const capture = h('div', { class: 'capture' });
  const quick = h('div', { class: 'stack-sm', hidden: true });
  // Продукты из базы или введённые вручную: отдельный список позиций приёма (см. food_picker.js).
  const prods = [];
  const prodBox = h('div', { class: 'stack-sm prod-box', hidden: true });

  // Избранное и частые: один тап записывает приём заново, звезда добавляет/убирает из избранного.
  async function loadQuick() {
    let items = [];
    try { items = (await api.get('/frequent', { limit: 20 })).items || []; } catch (e) { return; }
    if (!ctx.alive()) return;
    let starBusy = false;
    quickVisibility = () => { quick.hidden = !items.length || allPhotos().length > 0 || !!textTa.value.trim() || prods.length > 0; };
    const paintQuick = () => {
      quickVisibility();
      const rows = items.map((f) => {
        const star = h('button', {
          class: 'quick-star' + (f.favorite_id ? ' on' : ''), type: 'button',
          'aria-label': f.favorite_id ? t('favf.rm') : t('add.fav_aria'), 'aria-pressed': String(!!f.favorite_id),
          onclick: async (ev) => {
            ev.stopPropagation();
            if (starBusy) return;                     // двойной тап не должен создать два избранных
            starBusy = true;
            star.disabled = true;
            try {
              if (f.favorite_id) { await api.del('/favorites/' + f.favorite_id); f.favorite_id = null; }
              else f.favorite_id = (await api.post('/favorites', { meal_id: f.meal_id })).id;
              haptic('light');
              // список не пересортировываем: строка не должна уехать из-под пальца
              star.classList.toggle('on', !!f.favorite_id);
              star.setAttribute('aria-pressed', String(!!f.favorite_id));
              star.setAttribute('aria-label', f.favorite_id ? t('favf.rm') : t('add.fav_aria'));
            } catch (e) { toast(e.message, 'error'); } finally { starBusy = false; star.disabled = false; }
          },
        }, icon('star', 20));
        const row = h('div', { class: 'row' },
          h('button', {
            class: 'row-btn grow', type: 'button', title: f.names.join(', '),
            onclick: async () => {
              row.classList.add('is-pending');
              try {
                const r = await api.post(f.favorite_id ? `/favorites/${f.favorite_id}/repeat` : `/meals/${f.meal_id}/repeat`);
                haptic('success');
                mainButton.hide();
                nav.replace('/meal/' + r.id);
              } catch (e) { row.classList.remove('is-pending'); toast(e.message, 'error'); }
            },
          }, h('span', { class: 'grow' }, h('span', { class: 'row-title' }, f.title), h('span', { class: 'row-sub' }, `${f.kcal} ${t('unit.kcal')}`))),
          star);
        return row;
      });
      quick.replaceChildren(h('div', { class: 'section-label' }, t('add.quick')), h('div', { class: 'list quick-list' }, rows));
    };
    quickPaint = paintQuick;
    paintQuick();
  }
  let quickPaint = null;
  let quickVisibility = () => {};

  let compressing = false;
  async function addFiles(input) {
    const files = [...input.files];
    const g = target;
    input.value = '';
    if (compressing || !files.length) return;         // повторный выбор, пока сжимается предыдущий, не дублирует фото
    compressing = true;
    const stop = busyOverlay(t('add.processing'));
    try {
      for (const f of files) {
        if (allPhotos().length >= MAX) { toast(t('lbl.max', { n: MAX })); break; }
        let file;
        try { file = await compressImage(f); } catch (e) { toast(e.message, 'error'); continue; }
        g.photos.push({ file, url: URL.createObjectURL(file) });
      }
    } finally {
      stop();
      compressing = false;
    }
    if (!ctx.alive() || !groups.includes(g)) return;
    // Новое блюдо без фото (отменили съёмку) не оставляем.
    if (!g.photos.length && groups.length > 1) groups.splice(groups.indexOf(g), 1);
    paint();
  }

  function pick(input, g) {
    if (allPhotos().length >= MAX) { toast(t('lbl.max', { n: MAX })); return; }
    target = g;
    input.click();
  }

  // Radio list of the user's dishes with sizes; the size is what makes the estimate better.
  function dishPicker(g, n) {
    const opt = (id, ic, color, title, sub, pic) => {
      const on = g.dishId === id;
      return h('button', {
        type: 'button', class: 'row row-btn' + (on ? ' selected' : ''), role: 'radio', 'aria-checked': String(on),
        onclick: () => { g.dishId = id; haptic('light'); paint(); },
      },
      (pic || tile(ic, color)),
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), h('span', { class: 'row-sub' }, sub)),
      h('span', { class: 'radio' + (on ? ' on' : '') }, on ? icon('check', 14) : null));
    };
    return h('div', { class: 'list dish-pick', role: 'radiogroup', 'aria-label': t('add.dish_aria', { n }) },
      dishes.map((d) => { const k = dishKind(d.kind); return opt(d.id, k.icon, k.color, d.name, dishInfo(d), dishPic(d.kind, 40)); }),
      opt(null, 'x', 'gray', t('add.no_dish'), t('add.no_dish_sub')));
  }

  function chosenLabel(g) {
    const d = dishes.find((x) => x.id === g.dishId);
    return d ? `${d.name} · ${dishInfo(d)}` : t('add.dish_none');
  }

  function removeGroup(g) {
    g.photos.forEach((p) => URL.revokeObjectURL(p.url));
    groups.splice(groups.indexOf(g), 1);
    if (!groups.includes(target)) target = groups[groups.length - 1];
    paint();
  }

  // ---- Состав и готовка: слова, фото этикетки, штрихкод, «Мои продукты». Всё уходит модели как уточнение к блюду.
  const addIng = (g, d) => {
    g.ings.push({ name: d.name, brand: d.brand || null, kcal: d.kcal, protein: d.protein, fat: d.fat, carbs: d.carbs });
    haptic('success');
    paint();
  };
  // Быстрый путь: штрихкод -> продукт -> порция -> приём записан целиком, без редактирования состава.
  async function quickByCode(raw) {
    const code = String(raw || '').replace(/\D/g, '');
    if (!validBarcode(code)) { toast(t('lbl.v_bad'), 'error'); return; }
    const stop = busyOverlay(t('lbl.searching'), 'Open Food Facts');
    let d;
    try { d = (await api.get('/products/barcode/' + code)).draft; } catch (e) {
      stop();
      toast(e.status === 404 ? t('lbl.not_in_db') : e.message, 'error');
      return;
    }
    stop();
    quickPortion(d);
  }
  function quickPortion(d) {
    const per = d.default_grams || d.unit_grams || 100;
    const grams = numInput({ value: per, min: 1, max: 3000, decimals: 1, 'aria-label': t('add.grams') });
    const total = h('span', { class: 'row-title' });
    const calc = () => {
      const g = numVal(grams) || 0;
      total.textContent = `${fmtN(Math.round((d.kcal || 0) * g / 100))} ${t('unit.kcal')} · ${macroLine({ protein: +((d.protein || 0) * g / 100).toFixed(1), fat: +((d.fat || 0) * g / 100).toFixed(1), carbs: +((d.carbs || 0) * g / 100).toFixed(1) })}`;
    };
    grams.addEventListener('input', calc);
    calc();
    const add = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: async () => {
      const g = numVal(grams);
      if (!g || g <= 0) { toast(t('add.v_grams'), 'error'); grams.focus(); return; }
      const bad = numCheck(grams, t('add.grams'));
      if (bad) { toast(bad, 'error'); return; }
      add.disabled = true;
      try {
        const r = await api.post('/meals/product', { name: d.name, brand: d.brand, grams: g, kcal: d.kcal, protein: d.protein, fat: d.fat, carbs: d.carbs });
        haptic('success');
        sh.close();
        mainButton.hide();
        nav.replace('/meal/' + r.id);
      } catch (e) { add.disabled = false; toast(e.message, 'error'); }
    } }, t('add.log_meal'));
    const sh = sheet({ title: d.name, content: h('div', { class: 'stack' },
      d.brand ? h('div', { class: 'muted center' }, d.brand) : null,
      h('div', { class: 'muted small center' }, `${fmtN(d.kcal)} ${t('unit.kcal')} · ${macroLine(d)} ${t('common.per100')}`),
      h('div', { class: 'fgroup' }, h('div', { class: 'list form' }, frow(t('add.ate_g'), grams))),
      total, add) });
  }
  async function ingByCode(g, raw) {
    if (!g) return quickByCode(raw);
    const code = String(raw || '').replace(/\D/g, '');
    if (!validBarcode(code)) { toast(t('lbl.v_bad'), 'error'); return; }
    const stop = busyOverlay(t('lbl.searching'), 'Open Food Facts');
    try {
      const r = await api.get('/products/barcode/' + code);
      stop();
      if (groups.includes(g)) addIng(g, r.draft);
    } catch (e) {
      stop();
      toast(e.status === 404 ? t('lbl.not_in_db') : e.message, 'error');
    }
  }
  function typeCode(g) {
    const inp = codeInput({ placeholder: t('lbl.ph_code'), 'aria-label': t('pf.barcode') });
    const go = () => { const v = inp.value; sh.close(); ingByCode(g, v); };
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    const sh = sheet({ title: t('add.code_title'), content: h('div', { class: 'stack' }, inp,
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, t('lbl.find'))) });
    setTimeout(() => inp.focus(), 250);
  }
  let codeFor = null;   // для кого читаем штрихкод: блюдо (состав) или null (быстрая запись продукта)
  const codeShot = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: async () => {
    const f = codeShot.files[0];
    const g = codeFor;
    codeShot.value = '';
    if (!f) return;
    const stop = busyOverlay(t('lbl.reading_code'));
    let code = null;
    try { code = await decodeFile(f); } catch (e) { /* скажем ниже */ }
    stop();
    if (code) ingByCode(g, code); else { toast(t('add.code_not_found'), 'error'); typeCode(g); }
  } });
  async function scanIng(g) {
    haptic('light');
    codeFor = g;
    if (g) target = g;
    const r = await scanBarcode();
    if (!r) return;
    if (r.code) { ingByCode(g, r.code); return; }
    if (r.manual) { typeCode(g); return; }
    toast(r.error === 'load' ? t('lbl.scan_load') : t('lbl.no_cam'), 'error');
    if (r.error === 'camera') codeShot.click();
  }
  function myProducts(g) {
    const list = h('div', { class: 'list' });
    const q = h('input', { type: 'search', placeholder: t('add.search_mine'), 'aria-label': t('add.search_aria'), autocomplete: 'off' });
    const sh = sheet({ title: t('prod.title'), content: h('div', { class: 'stack' }, q, list) });
    let timer = null;
    async function load() {
      const query = q.value.trim();
      let products = [];
      try { products = (await api.get('/products', { q: query })).products || []; } catch (e) { if (query === q.value.trim()) list.replaceChildren(h('div', { class: 'hint small' }, e.message)); return; }
      if (query !== q.value.trim()) return;           // ответ на устаревший запрос
      if (!products.length) { list.replaceChildren(h('div', { class: 'hint small' }, t('add.none_found'))); return; }
      list.replaceChildren(...products.slice(0, 30).map((p) => h('button', {
        type: 'button', class: 'row row-btn', onclick: () => { sh.close(); if (groups.includes(g)) addIng(g, p); },
      }, h('span', { class: 'grow' }, h('span', { class: 'row-title' }, p.name),
        h('span', { class: 'row-sub' }, [p.brand, `${fmtN(p.kcal)} ${t('unit.kcal')} ${t('common.per100')}`].filter(Boolean).join(' · '))))));
    }
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    load();
  }
  function ingBlock(g, n) {
    const note = h('textarea', {
      rows: 2, value: g.note, placeholder: t('add.ph_note'),
      'aria-label': t('add.note_aria', { n }),
      oninput: () => { g.note = note.value; },
    });
    note.dataset.gid = String(g.id);
    note.value = g.note;
    const ings = g.ings.length ? h('div', { class: 'list ing-list' }, g.ings.map((d, i) => h('div', { class: 'row' },
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, d.name),
        h('span', { class: 'row-sub' }, [d.brand, `${fmtN(d.kcal)} ${t('unit.kcal')} · ${macroLine(d)} ${t('common.per100')}`].filter(Boolean).join(' · '))),
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': t('add.remove_item', { n: d.name }), onclick: () => { g.ings.splice(i, 1); paint(); } }, icon('close', 18))))) : null;
    return [
      h('div', { class: 'section-label' }, t('add.ingr')),
      note, ings,
      h('div', { class: 'ing-actions' },
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(camera, g) }, icon('camera', 20), t('add.label')),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => scanIng(g) }, icon('barcode', 20), t('pf.barcode')),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => myProducts(g) }, icon('package', 20), t('prod.title'))),
      h('div', { class: 'hint small' }, t('add.ingr_hint')),
    ];
  }

  function groupCard(g, gi) {
    const n = gi + 1;
    const multi = groups.length > 1;
    const thumbs = g.photos.map((p, i) => h('div', { class: 'photo-thumb' },
      h('img', { src: p.url, alt: t('add.dish_photo', { n, i: i + 1 }) }),
      h('button', {
        class: 'remove', type: 'button', 'aria-label': t('lbl.photo_rm', { n: i + 1 }),
        onclick: () => {
          URL.revokeObjectURL(p.url);
          g.photos.splice(i, 1);
          if (!g.photos.length && groups.length > 1) groups.splice(groups.indexOf(g), 1);
          paint();
        },
      }, icon('close', 16))));
    return h('div', { class: 'card photo-card' },
      h('div', { class: 'photo-head' },
        h('div', { class: 'grow' },
          h('div', { class: 'row-title' }, multi ? t('add.dish_n', { n }) : t('add.dish')),
          h('div', { class: 'row-sub' }, dishes.length ? chosenLabel(g) : tn('add.n_photos', g.photos.length))),
        multi ? h('button', { class: 'btn-link', type: 'button', onclick: () => removeGroup(g) }, t('add.remove')) : null),
      h('div', { class: 'thumbs' }, thumbs),
      h('div', { class: 'capture-actions tight' },
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(camera, g) }, icon('camera', 20), t('lbl.more')),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(gallery, g) }, icon('image', 20), t('lbl.gallery'))),
      h('div', { class: 'hint small' }, t('add.one_dish_hint')),
      dishes.length ? [h('div', { class: 'section-label' }, t('add.on_what')), dishPicker(g, n)] : null,
      ingBlock(g, n));
  }

  async function addProd() {
    haptic('light');
    const e = await pickFood();
    if (!e || !ctx.alive()) return;
    prods.push(e);
    haptic('success');
    paintProds();
  }
  async function editProd(i) {
    const e = await editEntryGrams(prods[i]);
    if (!e || !ctx.alive()) return;
    prods[i] = e;
    paintProds();
  }
  function paintProds() {
    prodBox.hidden = !prods.length;
    if (prods.length) {
      const sum = entryTotal(prods);
      prodBox.replaceChildren(
        sectionTitle(t('fp.in_meal')),
        h('div', { class: 'list prod-list' }, prods.map((e, i) => {
          const a = entryAbs(e);
          return h('div', { class: 'row prod-row' },
            h('button', { class: 'row-btn grow', type: 'button', 'aria-label': t('fp.edit_grams', { n: e.name }), onclick: () => editProd(i) },
              h('span', { class: 'grow' }, h('span', { class: 'row-title' }, e.name),
                h('span', { class: 'row-sub' }, `${fmtN(e.grams)} ${t('unit.g')} · ${fmtN(a.kcal)} ${t('unit.kcal')}`))),
            h('button', { class: 'icon-btn danger', type: 'button', 'aria-label': t('add.remove_item', { n: e.name }), onclick: () => { prods.splice(i, 1); paintProds(); } }, icon('trash', 18)));
        })),
        h('div', { class: 'prod-sum row-between' }, h('span', { class: 'muted' }, t('fp.total')),
          h('span', { class: 'row-title' }, `${fmtN(sum.kcal)} ${t('unit.kcal')} · ${macroLine(sum)}`)),
        h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: addProd }, icon('plus', 18), t('fp.add_item')));
    } else prodBox.replaceChildren();
    quickVisibility();
    sync();
  }

  function paint() {
    // Перерисовка (фото досжалось, добавили ингредиент) не должна выбивать фокус из поля уточнения.
    const a = document.activeElement;
    const keep = a && a.dataset && a.dataset.gid ? { gid: a.dataset.gid, s: a.selectionStart, e: a.selectionEnd } : null;
    const filled = groups.filter((g) => g.photos.length);
    dishList.replaceChildren(...filled.map((g, gi) => groupCard(g, gi)));
    if (keep) {
      const n = dishList.querySelector(`textarea[data-gid="${keep.gid}"]`);
      if (n) { n.focus({ preventScroll: true }); try { n.setSelectionRange(keep.s, keep.e); } catch (e) { /* ignore */ } }
    }
    paintCapture(filled);
    if (quickPaint) quickPaint();
    sync();
  }

  // Пустое состояние: одно крупное приглашение. Когда фото есть, остаётся кнопка «Добавить ещё одно блюдо».
  function paintCapture(filled) {
    const has = filled.length > 0;
    capture.className = 'capture' + (has ? ' compact' : '');
    if (!has) {
      target = groups[0];
      const act = (ic, label, sub, fn, main) => h('button', { class: 'cap-tile' + (main ? ' main' : ''), type: 'button', onclick: fn },
        h('span', { class: 'cap-ico' }, icon(ic, 26)), h('span', { class: 'cap-label' }, label), h('span', { class: 'cap-sub' }, sub));
      capture.replaceChildren(
        h('div', { class: 'capture-title' }, t('add.cap_title')),
        h('div', { class: 'capture-sub' }, t('add.cap_sub', { n: MAX })),
        h('div', { class: 'cap-grid' },
          act('camera', t('add.shoot'), t('add.shoot_sub'), () => pick(camera, groups[0]), true),
          act('image', t('lbl.gallery'), t('add.from_photos'), () => pick(gallery, groups[0])),
          act('barcode', t('pf.barcode'), t('add.product_word'), () => scanIng(null)),
          act('package', t('fp.tile'), t('fp.tile_sub'), addProd)));
    } else {
      capture.replaceChildren(
        h('button', {
          class: 'btn btn-secondary btn-block', type: 'button',
          onclick: () => {
            if (allPhotos().length >= MAX) { toast(t('lbl.max', { n: MAX })); return; }
            const g = newGroup(filled[filled.length - 1].dishId);
            groups.push(g);
            haptic('light');
            pick(camera, g);
          },
        }, icon('plus', 20), t('add.another')));
    }
    textLabel.textContent = has ? t('add.comment') : t('add.or_describe');
    textTa.placeholder = has ? t('add.ph_comment') : t('add.ph_text');
  }

  function valid() { return allPhotos().length > 0 || textTa.value.trim().length > 0 || prods.length > 0; }
  let lastDisabled = null;
  function sync() {
    // Не дёргаем кнопку на каждую букву: в Telegram каждое enable/disable пересчитывает окно.
    const d = busy || !valid();
    if (d === lastDisabled) return;
    lastDisabled = d;
    mainButton.setDisabled(d);
  }

  // Уточнения по блюдам + общий комментарий одним текстом для модели.
  function composeNote(filled) {
    const parts = [];
    filled.forEach((g, gi) => {
      const bits = [];
      if (g.ings.length) {
        bits.push('состав: ' + g.ings.map((d) => `${d.name}${d.brand ? ` (${d.brand})` : ''}, на 100 г: ${fmtN(d.kcal)} ккал, Б ${fmtN(d.protein)}, Ж ${fmtN(d.fat)}, У ${fmtN(d.carbs)}`).join('; '));
      }
      if (g.note.trim()) bits.push(g.note.trim());
      if (bits.length) parts.push((filled.length > 1 ? `Блюдо ${gi + 1}: ` : '') + bits.join('. '));
    });
    if (textTa.value.trim()) parts.push(textTa.value.trim());
    return parts.join('\n');
  }

  async function submit() {
    if (busy || !valid()) return;
    busy = true;
    sync();
    mainButton.setLoading(true);
    const filled = groups.filter((g) => g.photos.length);
    const withAI = filled.length > 0 || !!textTa.value.trim();
    const stop = withAI ? busyOverlay(t('add.recognizing'), t('add.recognizing_sub')) : busyOverlay(t('add.processing'));
    try {
      let meal;
      if (!withAI) {
        // только продукты: без модели, один запрос
        meal = await api.post('/meals/manual', { items: prods.map(toPayload) });
      } else {
        const fd = new FormData();
        if (filled.length) {
          // Фото идут блюдо за блюдом; groups — номер блюда для каждого фото.
          const owner = [];
          filled.forEach((g, gi) => g.photos.forEach((p) => {
            fd.append('photo', p.file, p.file.name || 'photo.jpg');
            owner.push(gi);
          }));
          filled.forEach((g) => g.photos.forEach(() => fd.append('dish_id', g.dishId == null ? '' : String(g.dishId))));
          fd.append('groups', owner.join(','));
          const note = composeNote(filled);
          if (note) fd.append('comment', note);
        } else {
          fd.append('text', textTa.value.trim());
        }
        meal = await api.post('/meals', fd, { timeout: 150000 });
        // продукты добавляются в созданный приём по одному; сбой одного не теряет сам приём
        let failed = 0;
        for (const e of prods) {
          try { await api.post('/meals/' + meal.id + '/items', toPayload(e)); } catch (er) { failed++; }
        }
        if (failed) toast(t('fp.add_failed', { n: failed }), 'error');
      }
      haptic('success');
      stop();
      clearDraft();
      mainButton.hide();
      nav.replace('/meal/' + meal.id);
    } catch (e) {
      stop();
      busy = false;
      mainButton.setLoading(false);
      sync();
      toast(e.message, 'error');
    }
  }

  const noDishHint = h('div', { class: 'hint-card small', hidden: true },
    t('add.dish_hint'),
    h('a', { href: '#/menu/dish/new' }, t('dishes.add')));

  async function loadDishes() {
    try { dishes = (await api.get('/dishes')).dishes || []; } catch (e) { /* dishes are optional */ }
    if (!ctx.alive()) return;
    const defaultDish = dishes.find((d) => d.is_default);
    if (defaultDish && groups[0].dishId == null) groups[0].dishId = defaultDish.id;
    noDishHint.hidden = dishes.length > 0;
    paint();
  }

  view.append(
    pageHead(t('add.meal'), { parent: '/today' }),
    h('div', { class: 'stack' },
      capture, dishList, prodBox, noDishHint,
      h('label', { class: 'field compose-keep' }, textLabel, textTa),
      quick,
      camera, gallery, codeShot));

  mainButton.show(t('add.save_meal'), submit, { disabled: true });
  lastDisabled = true;
  paint();
  loadDishes();
  loadQuick();
  return () => allPhotos().forEach((p) => URL.revokeObjectURL(p.url));
}
