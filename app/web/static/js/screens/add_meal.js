// Add meal: dishes (each = several photos + one plate) and/or a text description. Time and meal type are set by the server (now).
import { api } from '../api.js';
import {
  h, icon, tile, toast, haptic, nav, mainButton, draft, pageHead, compressImage, busyOverlay, dishKind,
} from '../ui.js';
import { dishInfo } from './dishes.js';

const MAX = 6;

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  let dishes = [];
  // Блюдо = несколько фото (тарелка, упаковка, этикетка) + одна посуда. Блюд в приёме может быть несколько.
  const groups = [{ photos: [], dishId: null }]; // photos: [{file, url}]
  const allPhotos = () => groups.flatMap((g) => g.photos);
  let target = groups[0];
  let busy = false;

  try { dishes = (await api.get('/dishes')).dishes || []; } catch (e) { /* dishes are optional */ }
  if (!ctx.alive()) return;
  const defaultDish = dishes.find((d) => d.is_default);
  groups[0].dishId = defaultDish ? defaultDish.id : null;

  const camera = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => addFiles(camera) });
  const gallery = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => addFiles(gallery) });
  const textTa = h('textarea', { rows: 3, placeholder: 'Например: овсянка на молоке 250 г, банан, кофе без сахара', oninput: sync });
  const textLabel = h('span', { class: 'field-label' }, 'Что съели');
  const clearDraft = draft('meal-text', textTa);
  const dishList = h('div', { class: 'stack' });
  const capture = h('div', { class: 'capture' });

  async function addFiles(input) {
    const files = [...input.files];
    const g = target;
    input.value = '';
    for (const f of files) {
      if (allPhotos().length >= MAX) { toast(`Не больше ${MAX} фото`); break; }
      let file;
      try { file = await compressImage(f); } catch (e) { toast(e.message, 'error'); continue; }
      g.photos.push({ file, url: URL.createObjectURL(file) });
    }
    if (!groups.includes(g)) return;
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
    const opt = (id, ic, color, title, sub) => {
      const on = g.dishId === id;
      return h('button', {
        type: 'button', class: 'row row-btn' + (on ? ' selected' : ''), role: 'radio', 'aria-checked': String(on),
        onclick: () => { g.dishId = id; haptic('light'); paint(); },
      },
      tile(ic, color),
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), h('span', { class: 'row-sub' }, sub)),
      h('span', { class: 'radio' + (on ? ' on' : '') }, on ? icon('check', 14) : null));
    };
    return h('div', { class: 'list dish-pick', role: 'radiogroup', 'aria-label': `Посуда блюда ${n}` },
      dishes.map((d) => { const k = dishKind(d.kind); return opt(d.id, k.icon, k.color, d.name, dishInfo(d)); }),
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
      dishes.length ? [h('div', { class: 'section-label' }, 'На чём еда?'), dishPicker(g, n)] : null);
  }

  function paint() {
    const filled = groups.filter((g) => g.photos.length);
    dishList.replaceChildren(...filled.map((g, gi) => groupCard(g, gi)));
    paintCapture(filled);
    sync();
  }

  // Пустое состояние: одно крупное приглашение. Когда фото есть, остаётся кнопка «Добавить ещё одно блюдо».
  function paintCapture(filled) {
    const has = filled.length > 0;
    capture.className = 'capture' + (has ? ' compact' : '');
    if (!has) {
      target = groups[0];
      capture.replaceChildren(
        h('span', { class: 'capture-ico' }, icon('camera', 30)),
        h('div', { class: 'capture-title' }, 'Сфотографируйте еду'),
        h('div', { class: 'capture-sub' }, `Можно до ${MAX} фото: тарелка, упаковка, этикетка. Другое блюдо добавляется отдельно`),
        h('div', { class: 'capture-actions' },
          h('button', { class: 'btn btn-primary', type: 'button', onclick: () => pick(camera, groups[0]) }, icon('camera', 20), 'Снять'),
          h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(gallery, groups[0]) }, icon('image', 20), 'Галерея')));
    } else {
      capture.replaceChildren(
        h('button', {
          class: 'btn btn-secondary btn-block', type: 'button',
          onclick: () => {
            if (allPhotos().length >= MAX) { toast(`Не больше ${MAX} фото`); return; }
            const g = { photos: [], dishId: filled[filled.length - 1].dishId };
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
      if (textTa.value.trim()) fd.append('comment', textTa.value.trim());
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

  const noDishHint = dishes.length ? null : h('div', { class: 'hint-card small' },
    'Размер посуды помогает точнее оценить порцию. ',
    h('a', { href: '#/menu/dish/new' }, 'Добавить посуду'));

  view.append(
    pageHead('Приём пищи', { parent: '/today' }),
    h('div', { class: 'stack' },
      dishList, capture, noDishHint || document.createDocumentFragment(),
      h('label', { class: 'field' }, textLabel, textTa),
      camera, gallery));

  mainButton.show('Сохранить приём', submit, { disabled: true });
  lastDisabled = true;
  paint();
  return () => allPhotos().forEach((p) => URL.revokeObjectURL(p.url));
}
