// Add meal: photos (+ dish per photo) and/or a text description. Time and meal type are set by the server (now).
import { api } from '../api.js';
import {
  h, icon, tile, toast, haptic, nav, mainButton, draft, pageHead, field, compressImage, busyOverlay, dishKind,
} from '../ui.js';
import { dishInfo } from './dishes.js';

const MAX = 6;

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  let dishes = [];
  const photos = []; // {file, url, dishId}
  let busy = false;

  try { dishes = (await api.get('/dishes')).dishes || []; } catch (e) { /* dishes are optional */ }
  if (!ctx.alive()) return;
  const defaultDish = dishes.find((d) => d.is_default);

  const camera = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => addFiles(camera) });
  const gallery = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => addFiles(gallery) });
  const textTa = h('textarea', { rows: 3, placeholder: 'Например: овсянка на молоке 250 г, банан, кофе без сахара', oninput: sync });
  const textLabel = h('span', { class: 'field-label' }, 'Что съели');
  const clearDraft = draft('meal-text', textTa);
  const photoList = h('div', { class: 'stack-sm' });
  const capture = h('div', { class: 'capture' });

  async function addFiles(input) {
    const files = [...input.files];
    input.value = '';
    for (const f of files) {
      if (photos.length >= MAX) { toast(`Не больше ${MAX} фото`); break; }
      let file;
      try { file = await compressImage(f); } catch (e) { toast(e.message, 'error'); continue; }
      photos.push({ file, url: URL.createObjectURL(file), dishId: defaultDish ? defaultDish.id : null });
    }
    paintPhotos();
  }

  // Radio list of the user's dishes with sizes; the size is what makes the estimate better.
  function dishPicker(p, i) {
    const opt = (id, ic, color, title, sub) => {
      const on = p.dishId === id;
      return h('button', {
        type: 'button', class: 'row row-btn' + (on ? ' selected' : ''), role: 'radio', 'aria-checked': String(on),
        onclick: () => { p.dishId = id; haptic('light'); paintPhotos(); },
      },
      tile(ic, color),
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), h('span', { class: 'row-sub' }, sub)),
      h('span', { class: 'radio' + (on ? ' on' : '') }, on ? icon('check', 14) : null));
    };
    return h('div', { class: 'list dish-pick', role: 'radiogroup', 'aria-label': `Посуда на фото ${i + 1}` },
      dishes.map((d) => { const k = dishKind(d.kind); return opt(d.id, k.icon, k.color, d.name, dishInfo(d)); }),
      opt(null, 'x', 'gray', 'Без посуды', 'Размер оценю по фото сам'));
  }

  function chosenLabel(p) {
    const d = dishes.find((x) => x.id === p.dishId);
    return d ? `${d.name} · ${dishInfo(d)}` : 'Посуда не выбрана';
  }

  function paintPhotos() {
    photoList.replaceChildren(...photos.map((p, i) => h('div', { class: 'card photo-card' },
      h('div', { class: 'photo-head' },
        h('div', { class: 'photo-thumb' },
          h('img', { src: p.url, alt: `Фото ${i + 1}` }),
          h('button', { class: 'remove', type: 'button', 'aria-label': `Убрать фото ${i + 1}`, onclick: () => { URL.revokeObjectURL(p.url); photos.splice(i, 1); paintPhotos(); } }, icon('close', 16))),
        h('div', { class: 'grow' },
          h('div', { class: 'row-title' }, photos.length > 1 ? `Фото ${i + 1}` : 'Фото'),
          h('div', { class: 'row-sub' }, chosenLabel(p)))),
      dishes.length ? [h('div', { class: 'section-label' }, 'На чём еда?'), dishPicker(p, i)] : null)));
    paintCapture();
    sync();
  }

  function pick(input) { if (photos.length >= MAX) toast(`Не больше ${MAX} фото`); else input.click(); }

  // Пустое состояние: одно крупное приглашение, без переключателей режимов.
  function paintCapture() {
    const more = photos.length > 0;
    capture.className = 'capture' + (more ? ' compact' : '');
    capture.replaceChildren(
      more ? null : h('span', { class: 'capture-ico' }, icon('camera', 30)),
      more ? null : h('div', { class: 'capture-title' }, 'Сфотографируйте еду'),
      more ? null : h('div', { class: 'capture-sub' }, `Можно до ${MAX} фото: общий вид, второй ракурс, этикетка`),
      h('div', { class: 'capture-actions' },
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => pick(camera) }, icon('camera', 20), more ? 'Ещё фото' : 'Снять'),
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => pick(gallery) }, icon('image', 20), 'Галерея')));
    textLabel.textContent = more ? 'Комментарий' : 'Или опишите словами';
    textTa.placeholder = more ? 'Что внутри, масло, соус…' : 'Например: овсянка на молоке 250 г, банан, кофе без сахара';
  }

  function valid() { return photos.length > 0 || textTa.value.trim().length > 0; }
  function sync() { mainButton.setDisabled(busy || !valid()); }

  async function submit() {
    if (busy || !valid()) return;
    busy = true;
    sync();
    mainButton.setLoading(true);
    const fd = new FormData();
    if (photos.length) {
      photos.forEach((p) => { fd.append('photo', p.file, p.file.name || 'photo.jpg'); });
      photos.forEach((p) => fd.append('dish_id', p.dishId == null ? '' : String(p.dishId)));
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
      capture, photoList, noDishHint || document.createDocumentFragment(),
      h('label', { class: 'field' }, textLabel, textTa),
      camera, gallery));

  mainButton.show('Сохранить приём', submit, { disabled: true });
  paintPhotos();
  return () => photos.forEach((p) => URL.revokeObjectURL(p.url));
}
