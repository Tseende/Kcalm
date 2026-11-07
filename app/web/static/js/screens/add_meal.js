// Add meal: dishes (each = several photos + one plate) and/or a text description. Time and meal type are set by the server (now).
import { api } from '../api.js';
import {
  h, icon, tile, toast, haptic, nav, mainButton, draft, pageHead, compressImage, busyOverlay, dishKind, sheet, fmtN, numInput, numVal, numCheck, codeInput, frow,
} from '../ui.js';
import { scanBarcode, decodeFile, validBarcode } from '../scanner.js';
import { dishInfo } from './dishes.js';
import { dishPic } from '../pics.js';

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
  const textTa = h('textarea', { rows: 3, placeholder: 'Например: овсянка на молоке 250 г, банан, кофе без сахара', oninput: () => { sync(); quickVisibility(); } });
  const textLabel = h('span', { class: 'field-label' }, 'Что съели');
  const clearDraft = draft('meal-text', textTa);
  const dishList = h('div', { class: 'stack' });
  const capture = h('div', { class: 'capture' });
  const quick = h('div', { class: 'stack-sm', hidden: true });

  // Избранное и частые: один тап записывает приём заново, звезда добавляет/убирает из избранного.
  async function loadQuick() {
    let items = [];
    try { items = (await api.get('/frequent', { limit: 20 })).items || []; } catch (e) { return; }
    if (!ctx.alive()) return;
    let starBusy = false;
    quickVisibility = () => { quick.hidden = !items.length || allPhotos().length > 0 || !!textTa.value.trim(); };
    const paintQuick = () => {
      quickVisibility();
      const rows = items.map((f) => {
        const star = h('button', {
          class: 'quick-star' + (f.favorite_id ? ' on' : ''), type: 'button',
          'aria-label': f.favorite_id ? 'Убрать из избранного' : 'В избранное', 'aria-pressed': String(!!f.favorite_id),
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
              star.setAttribute('aria-label', f.favorite_id ? 'Убрать из избранного' : 'В избранное');
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
          }, h('span', { class: 'grow' }, h('span', { class: 'row-title' }, f.title), h('span', { class: 'row-sub' }, `${f.kcal} ккал`))),
          star);
        return row;
      });
      quick.replaceChildren(h('div', { class: 'section-label' }, 'Избранное и частые'), h('div', { class: 'list quick-list' }, rows));
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
    const stop = busyOverlay('Обрабатываю фото…');
    try {
      for (const f of files) {
        if (allPhotos().length >= MAX) { toast(`Не больше ${MAX} фото`); break; }
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
    if (allPhotos().length >= MAX) { toast(`Не больше ${MAX} фото`); return; }
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
    return h('div', { class: 'list dish-pick', role: 'radiogroup', 'aria-label': `Посуда блюда ${n}` },
      dishes.map((d) => { const k = dishKind(d.kind); return opt(d.id, k.icon, k.color, d.name, dishInfo(d), dishPic(d.kind, 40)); }),
      opt(null, 'x', 'gray', 'Без посуды', 'Размер оценю по фото сам'));
  }

  function chosenLabel(g) {
    const d = dishes.find((x) => x.id === g.dishId);
    return d ? `${d.name} · ${dishInfo(d)}` : 'Посуда не выбрана';
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
    if (!validBarcode(code)) { toast('Штрихкод не подходит: проверьте цифры', 'error'); return; }
    const stop = busyOverlay('Ищу продукт…', 'Open Food Facts');
    let d;
    try { d = (await api.get('/products/barcode/' + code)).draft; } catch (e) {
      stop();
      toast(e.status === 404 ? 'В базе такого штрихкода нет. Сфотографируйте этикетку' : e.message, 'error');
      return;
    }
    stop();
    quickPortion(d);
  }
  function quickPortion(d) {
    const per = d.default_grams || d.unit_grams || 100;
    const grams = numInput({ value: per, min: 1, max: 3000, decimals: 1, 'aria-label': 'Граммы' });
    const total = h('span', { class: 'row-title' });
    const calc = () => {
      const g = numVal(grams) || 0;
      total.textContent = `${fmtN(Math.round((d.kcal || 0) * g / 100))} ккал · Б ${fmtN(+((d.protein || 0) * g / 100).toFixed(1))} · Ж ${fmtN(+((d.fat || 0) * g / 100).toFixed(1))} · У ${fmtN(+((d.carbs || 0) * g / 100).toFixed(1))}`;
    };
    grams.addEventListener('input', calc);
    calc();
    const add = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: async () => {
      const g = numVal(grams);
      if (!g || g <= 0) { toast('Укажите граммы', 'error'); grams.focus(); return; }
      const bad = numCheck(grams, 'Граммы');
      if (bad) { toast(bad, 'error'); return; }
      add.disabled = true;
      try {
        const r = await api.post('/meals/product', { name: d.name, brand: d.brand, grams: g, kcal: d.kcal, protein: d.protein, fat: d.fat, carbs: d.carbs });
        haptic('success');
        sh.close();
        mainButton.hide();
        nav.replace('/meal/' + r.id);
      } catch (e) { add.disabled = false; toast(e.message, 'error'); }
    } }, 'Записать приём');
    const sh = sheet({ title: d.name, content: h('div', { class: 'stack' },
      d.brand ? h('div', { class: 'muted center' }, d.brand) : null,
      h('div', { class: 'muted small center' }, `${fmtN(d.kcal)} ккал · Б ${fmtN(d.protein)} · Ж ${fmtN(d.fat)} · У ${fmtN(d.carbs)} на 100 г`),
      h('div', { class: 'fgroup' }, h('div', { class: 'list form' }, frow('Съели, г', grams))),
      total, add) });
  }
  async function ingByCode(g, raw) {
    if (!g) return quickByCode(raw);
    const code = String(raw || '').replace(/\D/g, '');
    if (!validBarcode(code)) { toast('Штрихкод не подходит: проверьте цифры', 'error'); return; }
    const stop = busyOverlay('Ищу продукт…', 'Open Food Facts');
    try {
      const r = await api.get('/products/barcode/' + code);
      stop();
      if (groups.includes(g)) addIng(g, r.draft);
    } catch (e) {
      stop();
      toast(e.status === 404 ? 'В базе такого штрихкода нет. Сфотографируйте этикетку' : e.message, 'error');
    }
  }
  function typeCode(g) {
    const inp = codeInput({ placeholder: '13 цифр под штрихкодом', 'aria-label': 'Штрихкод' });
    const go = () => { const v = inp.value; sh.close(); ingByCode(g, v); };
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    const sh = sheet({ title: 'Штрихкод с упаковки', content: h('div', { class: 'stack' }, inp,
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: go }, 'Найти')) });
    setTimeout(() => inp.focus(), 250);
  }
  let codeFor = null;   // для кого читаем штрихкод: блюдо (состав) или null (быстрая запись продукта)
  const codeShot = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: async () => {
    const f = codeShot.files[0];
    const g = codeFor;
    codeShot.value = '';
    if (!f) return;
    const stop = busyOverlay('Читаю штрихкод…');
    let code = null;
    try { code = await decodeFile(f); } catch (e) { /* скажем ниже */ }
    stop();
    if (code) ingByCode(g, code); else { toast('Штрихкод на фото не нашёл. Введите цифры', 'error'); typeCode(g); }
  } });
  async function scanIng(g) {
    haptic('light');
    codeFor = g;
    if (g) target = g;
    const r = await scanBarcode();
    if (!r) return;
    if (r.code) { ingByCode(g, r.code); return; }
    if (r.manual) { typeCode(g); return; }
    toast(r.error === 'load' ? 'Сканер не загрузился' : 'Нет доступа к камере. Сфотографируйте штрихкод', 'error');
    if (r.error === 'camera') codeShot.click();
  }
  function myProducts(g) {
    const list = h('div', { class: 'list' });
    const q = h('input', { type: 'search', placeholder: 'Поиск по моим продуктам', 'aria-label': 'Поиск продукта', autocomplete: 'off' });
    const sh = sheet({ title: 'Мои продукты', content: h('div', { class: 'stack' }, q, list) });
    let timer = null;
    async function load() {
      const query = q.value.trim();
      let products = [];
      try { products = (await api.get('/products', { q: query })).products || []; } catch (e) { if (query === q.value.trim()) list.replaceChildren(h('div', { class: 'hint small' }, e.message)); return; }
      if (query !== q.value.trim()) return;           // ответ на устаревший запрос
      if (!products.length) { list.replaceChildren(h('div', { class: 'hint small' }, 'Ничего не нашлось. Свои продукты сохраняются из приёма или с этикетки.')); return; }
      list.replaceChildren(...products.slice(0, 30).map((p) => h('button', {
        type: 'button', class: 'row row-btn', onclick: () => { sh.close(); if (groups.includes(g)) addIng(g, p); },
      }, h('span', { class: 'grow' }, h('span', { class: 'row-title' }, p.name),
        h('span', { class: 'row-sub' }, [p.brand, `${fmtN(p.kcal)} ккал на 100 г`].filter(Boolean).join(' · '))))));
    }
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    load();
  }
  function ingBlock(g, n) {
    const note = h('textarea', {
      rows: 2, value: g.note, placeholder: 'Например: жарено на оливковом масле, соус на сметане, без сахара',
      'aria-label': `Состав и готовка блюда ${n}`,
      oninput: () => { g.note = note.value; },
    });
    note.dataset.gid = String(g.id);
    note.value = g.note;
    const ings = g.ings.length ? h('div', { class: 'list ing-list' }, g.ings.map((d, i) => h('div', { class: 'row' },
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, d.name),
        h('span', { class: 'row-sub' }, [d.brand, `${fmtN(d.kcal)} ккал · Б ${fmtN(d.protein)} · Ж ${fmtN(d.fat)} · У ${fmtN(d.carbs)} на 100 г`].filter(Boolean).join(' · '))),
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Убрать ${d.name}`, onclick: () => { g.ings.splice(i, 1); paint(); } }, icon('close', 18))))) : null;
    return [
      h('div', { class: 'section-label' }, 'Состав и готовка'),
      note, ings,
      h('div', { class: 'ing-actions' },
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(camera, g) }, icon('camera', 20), 'Этикетка'),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => scanIng(g) }, icon('barcode', 20), 'Штрихкод'),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => myProducts(g) }, icon('package', 20), 'Мои продукты')),
      h('div', { class: 'hint small' }, 'Уточните ингредиенты словами, фото этикетки, штрихкодом или из своих продуктов'),
    ];
  }

  function groupCard(g, gi) {
    const n = gi + 1;
    const multi = groups.length > 1;
    const thumbs = g.photos.map((p, i) => h('div', { class: 'photo-thumb' },
      h('img', { src: p.url, alt: `Блюдо ${n}, фото ${i + 1}` }),
      h('button', {
        class: 'remove', type: 'button', 'aria-label': `Убрать фото ${i + 1}`,
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
          h('div', { class: 'row-title' }, multi ? `Блюдо ${n}` : 'Блюдо'),
          h('div', { class: 'row-sub' }, dishes.length ? chosenLabel(g) : `${g.photos.length} фото`)),
        multi ? h('button', { class: 'btn-link', type: 'button', onclick: () => removeGroup(g) }, 'Убрать') : null),
      h('div', { class: 'thumbs' }, thumbs),
      h('div', { class: 'capture-actions tight' },
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(camera, g) }, icon('camera', 20), 'Ещё фото'),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(gallery, g) }, icon('image', 20), 'Галерея')),
      h('div', { class: 'hint small' }, 'Тарелка, упаковка, этикетка: всё это одно блюдо'),
      dishes.length ? [h('div', { class: 'section-label' }, 'На чём еда?'), dishPicker(g, n)] : null,
      ingBlock(g, n));
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
        h('div', { class: 'capture-title' }, 'Добавьте приём пищи'),
        h('div', { class: 'capture-sub' }, `До ${MAX} фото: тарелка, упаковка, этикетка`),
        h('div', { class: 'cap-grid' },
          act('camera', 'Снять', 'еду', () => pick(camera, groups[0]), true),
          act('image', 'Галерея', 'из фото', () => pick(gallery, groups[0])),
          act('barcode', 'Штрихкод', 'продукт', () => scanIng(null))));
    } else {
      capture.replaceChildren(
        h('button', {
          class: 'btn btn-secondary btn-block', type: 'button',
          onclick: () => {
            if (allPhotos().length >= MAX) { toast(`Не больше ${MAX} фото`); return; }
            const g = newGroup(filled[filled.length - 1].dishId);
            groups.push(g);
            haptic('light');
            pick(camera, g);
          },
        }, icon('plus', 20), 'Добавить ещё одно блюдо'));
    }
    textLabel.textContent = has ? 'Комментарий' : 'Или опишите словами';
    textTa.placeholder = has ? 'Что внутри, масло, соус…' : 'Например: овсянка на молоке 250 г, банан, кофе без сахара';
  }

  function valid() { return allPhotos().length > 0 || textTa.value.trim().length > 0; }
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
    const fd = new FormData();
    const filled = groups.filter((g) => g.photos.length);
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
    const stop = busyOverlay('Распознаю…', 'Это может занять до минуты');
    try {
      const meal = await api.post('/meals', fd, { timeout: 150000 });
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
    'Размер посуды помогает точнее оценить порцию. ',
    h('a', { href: '#/menu/dish/new' }, 'Добавить посуду'));

  async function loadDishes() {
    try { dishes = (await api.get('/dishes')).dishes || []; } catch (e) { /* dishes are optional */ }
    if (!ctx.alive()) return;
    const defaultDish = dishes.find((d) => d.is_default);
    if (defaultDish && groups[0].dishId == null) groups[0].dishId = defaultDish.id;
    noDishHint.hidden = dishes.length > 0;
    paint();
  }

  view.append(
    pageHead('Приём пищи', { parent: '/today' }),
    h('div', { class: 'stack' },
      capture, dishList, noDishHint,
      h('label', { class: 'field compose-keep' }, textLabel, textTa),
      quick,
      camera, gallery, codeShot));

  mainButton.show('Сохранить приём', submit, { disabled: true });
  lastDisabled = true;
  paint();
  loadDishes();
  loadQuick();
  return () => allPhotos().forEach((p) => URL.revokeObjectURL(p.url));
}
