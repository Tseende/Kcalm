// Add meal: photos (+ dish per photo) or text description
import { api } from '../api.js';
import {
  h, icon, tile, toast, haptic, nav, mainButton, pageHead, segmented, field, nowHHMM, todayISO, compressImage, busyOverlay, dishKind,
} from '../ui.js';
import { dishInfo } from './dishes.js';

const MAX = 6;

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  root.append(view);
  let mode = 'photo';
  let dishes = [];
  const photos = []; // {file, url, dishId}
  let busy = false;

  try { dishes = (await api.get('/dishes')).dishes || []; } catch (e) { /* dishes are optional */ }
  if (!ctx.alive()) return;
  const defaultDish = dishes.find((d) => d.is_default);

  const camera = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => addFiles(camera) });
  const gallery = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => addFiles(gallery) });
  const timeInput = h('input', { type: 'time', value: nowHHMM(), 'aria-label': 'Время приёма' });
  const commentTa = h('textarea', { rows: 3, placeholder: 'Что внутри, масло, соус…', oninput: sync });
  const textTa = h('textarea', { rows: 6, placeholder: 'Например: овсянка на молоке 250 г, банан и чёрный кофе без сахара', oninput: sync });
  const photoList = h('div', { class: 'stack-sm' });
  const photoBox = h('div', { class: 'stack' });
  const textBox = h('div', { class: 'stack', hidden: true });

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
    sync();
  }

  function valid() { return mode === 'photo' ? photos.length > 0 : textTa.value.trim().length > 0; }
  function sync() { mainButton.setDisabled(busy || !valid()); }

  async function submit() {
    if (busy || !valid()) return;
    busy = true;
    sync();
    mainButton.setLoading(true);
    const fd = new FormData();
    if (mode === 'photo') {
      photos.forEach((p) => { fd.append('photo', p.file, p.file.name || 'photo.jpg'); });
      photos.forEach((p) => fd.append('dish_id', p.dishId == null ? '' : String(p.dishId)));
      if (commentTa.value.trim()) fd.append('comment', commentTa.value.trim());
    } else {
      fd.append('text', textTa.value.trim());
    }
    if (timeInput.value) fd.append('time', timeInput.value);
    fd.append('date', todayISO());
    const stop = busyOverlay('Распознаю…', 'Это может занять до минуты');
    try {
      const meal = await api.post('/meals', fd, { timeout: 150000 });
      haptic('success');
      stop();
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

  photoBox.append(
    h('div', { class: 'row-btns' },
      h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => (photos.length >= MAX ? toast(`Не больше ${MAX} фото`) : camera.click()) }, icon('camera', 20), 'Снять'),
      h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => (photos.length >= MAX ? toast(`Не больше ${MAX} фото`) : gallery.click()) }, icon('image', 20), 'Из галереи')),
    h('div', { class: 'muted small' }, `До ${MAX} фото: общий вид, второй ракурс, этикетка. Выберите посуду у каждого фото.`),
    photoList, noDishHint || document.createDocumentFragment(),
    field('Комментарий', commentTa));
  textBox.append(field('Что вы съели', textTa, 'Укажите продукты и вес, если знаете'));

  view.append(
    pageHead('Приём пищи', { parent: '/today' }),
    h('div', { class: 'stack' },
      segmented([['photo', h('span', { class: 'seg-in' }, icon('camera', 18), 'Фото')], ['text', h('span', { class: 'seg-in' }, icon('pencil', 18), 'Описать текстом')]], mode, (v) => {
        mode = v;
        photoBox.hidden = v !== 'photo';
        textBox.hidden = v !== 'text';
        sync();
      }, 'Способ добавления'),
      photoBox, textBox,
      field('Время', timeInput),
      camera, gallery));

  mainButton.show('Сохранить приём', submit, { disabled: true });
  paintPhotos();
  return () => photos.forEach((p) => URL.revokeObjectURL(p.url));
}
