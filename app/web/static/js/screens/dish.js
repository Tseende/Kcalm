// Dish add/edit form
import { api } from '../api.js';
import {
  h, icon, toast, haptic, nav, mainButton, pageHead, field, numInput, numVal, toggle, confirmDialog, errorState,
  photoImg, compressImage, DISH_KINDS, dishKind,
} from '../ui.js';

const val = (v) => (v === null || v === undefined ? '' : v);

export default async function render(root, ctx) {
  const isNew = ctx.params.id === 'new';
  const view = h('div', { class: 'screen' });
  root.append(view);
  let dish = { kind: 'plate', is_default: false };
  if (!isNew) {
    try {
      dish = (await api.get('/dishes')).dishes.find((x) => String(x.id) === ctx.params.id);
      if (!dish) throw new Error('Посуда не найдена');
    } catch (e) { const b = h('div'); view.append(pageHead('Посуда', { parent: '/menu/dishes' }), b); errorState(b, e); return; }
  }
  if (!ctx.alive()) return;

  let isDefault = !!dish.is_default;
  let photoFile = null;
  const name = h('input', { type: 'text', value: val(dish.name), maxLength: 60, placeholder: 'Глубокая тарелка', 'aria-label': 'Название' });
  const kind = h('select', { 'aria-label': 'Тип посуды' }, Object.entries(DISH_KINDS).map(([k, v]) => h('option', { value: k, selected: k === dish.kind }, v.label)));
  const diam = numInput({ value: val(dish.diameter_cm), min: 1, max: 80, step: 0.5, placeholder: '20', 'aria-label': 'Диаметр' });
  const depth = numInput({ value: val(dish.depth_cm), min: 0.5, max: 50, step: 0.5, placeholder: '5', 'aria-label': 'Глубина' });
  const vol = numInput({ value: val(dish.volume_ml), min: 10, max: 10000, step: 10, placeholder: '500', inputmode: 'numeric', 'aria-label': 'Объём' });
  const preview = h('div', { class: 'photo-pick' });
  const fileInput = h('input', { type: 'file', accept: 'image/*', hidden: true, onchange: async () => {
    const f = fileInput.files[0];
    fileInput.value = '';
    if (!f) return;
    try { photoFile = await compressImage(f, 1200); } catch (e) { toast(e.message, 'error'); return; }
    paintPhoto(URL.createObjectURL(photoFile));
  } });
  function paintPhoto(url) {
    preview.replaceChildren(...[
      url ? h('img', { src: url, alt: 'Фото посуды' }) : (dish.photo_url ? photoImg(dish.photo_url, '', 'Фото посуды') : h('div', { class: 'ph-big', 'aria-hidden': 'true' }, icon(dishKind(kind.value).icon, 56))),
      isNew ? h('button', { class: 'btn btn-secondary btn-sm', type: 'button', onclick: () => fileInput.click() }, icon('camera', 18), url ? 'Заменить фото' : 'Фото пустой посуды') : null].filter(Boolean));
  }
  kind.addEventListener('change', () => { if (!photoFile && !dish.photo_url) paintPhoto(null); });
  paintPhoto(null);

  let busy = false;
  async function save() {
    if (busy) return;
    const n = name.value.trim();
    if (!n) { toast('Введите название', 'error'); name.focus(); return; }
    const d = numVal(diam), dp = numVal(depth), v = numVal(vol);
    if ([d, dp, v].some((x) => x !== null && x <= 0)) { toast('Размеры должны быть больше нуля', 'error'); return; }
    busy = true; mainButton.setLoading(true);
    try {
      if (isNew) {
        const fd = new FormData();
        fd.append('name', n); fd.append('kind', kind.value);
        if (d !== null) fd.append('diameter_cm', d);
        if (dp !== null) fd.append('depth_cm', dp);
        if (v !== null) fd.append('volume_ml', Math.round(v));
        fd.append('is_default', isDefault ? 'true' : 'false');
        if (photoFile) fd.append('photo', photoFile, photoFile.name || 'dish.jpg');
        await api.post('/dishes', fd);
      } else {
        await api.patch('/dishes/' + dish.id, { name: n, kind: kind.value, diameter_cm: d, depth_cm: dp, volume_ml: v === null ? null : Math.round(v), is_default: isDefault });
      }
      haptic('success'); toast('Посуда сохранена', 'ok'); mainButton.hide(); nav.replace('/menu/dishes');
    } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
  }
  async function remove() {
    if (!(await confirmDialog(`Удалить «${dish.name}»?`))) return;
    try { await api.del('/dishes/' + dish.id); haptic('success'); toast('Удалено'); mainButton.hide(); nav.replace('/menu/dishes'); } catch (e) { toast(e.message, 'error'); }
  }

  view.append(...[pageHead(isNew ? 'Новая посуда' : 'Посуда', { parent: '/menu/dishes' }),
    h('form', { class: 'stack', onsubmit: (e) => { e.preventDefault(); save(); } },
      h('div', { class: 'hint-card small' }, 'Размер посуды помогает точнее оценить порцию. Достаточно диаметра, а для глубоких тарелок и кружек добавьте объём.'),
      preview, field('Название', name), field('Тип', kind),
      h('div', { class: 'grid2' }, field('Диаметр, см', diam), field('Глубина, см', depth)),
      field('Объём, мл', vol),
      h('div', { class: 'card row-between' },
        h('div', null, h('div', { class: 'row-title' }, 'По умолчанию'), h('div', { class: 'muted small' }, 'Подставлять при добавлении фото')),
        toggle(isDefault, (c) => { isDefault = c; }, 'Посуда по умолчанию')),
      fileInput, h('button', { type: 'submit', hidden: true })),
    isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, 'Удалить посуду')].filter(Boolean));
  mainButton.show('Сохранить', save);
}
