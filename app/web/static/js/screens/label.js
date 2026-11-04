// Фото этикетки (до 4 штук: состав, таблица КБЖУ, штрихкод) или штрихкод вручную -> черновик -> продукт
import { api } from '../api.js';
import { h, icon, toast, haptic, nav, mainButton, pageHead, compressImage, busyOverlay } from '../ui.js';
import { productForm } from './product.js';

const MAX = 4;

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const out = h('div', { class: 'stack' });
  const photos = []; // [{file, url}]
  const camera = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => add(camera) });
  const gallery = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => add(gallery) });
  const thumbs = h('div', { class: 'thumbs' });
  const actions = h('div', { class: 'row-btns' });
  const barcodeIn = h('input', { type: 'text', inputMode: 'numeric', maxLength: 14, placeholder: 'Цифры под штрихкодом', autocomplete: 'off', 'aria-label': 'Штрихкод' });
  barcodeIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); byBarcode(); } });

  view.append(pageHead('Продукт по этикетке', { parent: '/menu/products' }), h('div', { class: 'stack' },
    h('div', { class: 'muted' }, 'Сфотографируйте этикетку: можно до 4 фото одного продукта, например состав и таблицу КБЖУ. Бот прочитает всё вместе, а вы проверите значения перед сохранением.'),
    thumbs, actions,
    h('div', { class: 'section-label' }, 'Или по штрихкоду'),
    h('div', { class: 'barcode-row' }, barcodeIn,
      h('button', { class: 'btn btn-secondary', type: 'button', onclick: byBarcode }, icon('barcode', 20), 'Найти')),
    h('div', { class: 'hint small' }, 'Ищу в открытой базе Open Food Facts. Если продукта там нет, сфотографируйте этикетку.'),
    out, camera, gallery));
  root.append(view);

  function paintPhotos() {
    thumbs.replaceChildren(...photos.map((p, i) => h('div', { class: 'photo-thumb' },
      h('img', { src: p.url, alt: `Фото этикетки ${i + 1}` }),
      h('button', { class: 'remove', type: 'button', 'aria-label': `Убрать фото ${i + 1}`,
        onclick: () => { URL.revokeObjectURL(p.url); photos.splice(i, 1); paintPhotos(); } }, icon('close', 16)))));
    const more = photos.length < MAX;
    actions.replaceChildren(...[
      more ? h('button', { class: 'btn ' + (photos.length ? 'btn-secondary' : 'btn-primary'), type: 'button', onclick: () => camera.click() }, icon('camera', 20), photos.length ? 'Ещё фото' : 'Снять') : null,
      more ? h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => gallery.click() }, icon('image', 20), 'Из галереи') : null,
      photos.length ? h('button', { class: 'btn btn-primary', type: 'button', onclick: recognize }, 'Распознать') : null].filter(Boolean));
  }

  async function add(input) {
    const files = [...input.files];
    input.value = '';
    for (const f of files) {
      if (photos.length >= MAX) { toast(`Не больше ${MAX} фото`); break; }
      try { const file = await compressImage(f, 2000, 0.9); photos.push({ file, url: URL.createObjectURL(file) }); }
      catch (e) { toast(e.message, 'error'); }
    }
    paintPhotos();
  }

  async function recognize() {
    if (!photos.length) return;
    mainButton.hide();
    const fd = new FormData();
    photos.forEach((p, i) => fd.append('photo', p.file, p.file.name || `label${i + 1}.jpg`));
    const stop = busyOverlay('Читаю этикетку…', photos.length > 1 ? 'Несколько фото, до 30 секунд' : 'Обычно 5–15 секунд');
    try {
      const r = await api.post('/products/label', fd, { timeout: 120000 });
      stop();
      draft(r);
    } catch (e) { stop(); toast(e.message, 'error'); }
  }

  async function byBarcode() {
    const code = barcodeIn.value.replace(/\D/g, '');
    if (!code) { toast('Введите цифры штрихкода', 'error'); barcodeIn.focus(); return; }
    mainButton.hide();
    const stop = busyOverlay('Ищу продукт…', 'Open Food Facts');
    try {
      const r = await api.get('/products/barcode/' + code);
      stop();
      draft({ draft: r.draft, notes: ['Данные из Open Food Facts: сверьте с упаковкой.'], existing_id: r.existing_id });
    } catch (e) {
      stop();
      toast(e.status === 404 ? 'В базе такого штрихкода нет. Сфотографируйте этикетку' : e.message, 'error');
    }
  }

  function draft(r) {
    const form = productForm(r.draft || {});
    let busy = false;
    async function save() {
      if (busy) return;
      const data = form.collect();
      if (!data) return;
      busy = true; mainButton.setLoading(true);
      try {
        await api.post('/products', data);
        haptic('success'); toast('Продукт сохранён', 'ok'); mainButton.hide(); nav.replace('/menu/products');
      } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
    }
    out.replaceChildren(...[
      r.confidence != null ? h('div', { class: 'muted small' }, `Уверенность распознавания: ${Math.round((r.confidence || 0) * 100)}%. Это черновик, проверьте значения.`) : null,
      r.existing_id ? h('div', { class: 'hint-card small' }, 'Такой продукт у вас уже есть. ', h('a', { href: '#/menu/product/' + r.existing_id }, 'Открыть')) : null,
      (r.notes || []).length ? h('ul', { class: 'notes' }, r.notes.map((n) => h('li', null, n))) : null,
      form.el].filter(Boolean));
    mainButton.show('Сохранить продукт', save);
    out.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  paintPhotos();
  return () => { mainButton.hide(); photos.forEach((p) => URL.revokeObjectURL(p.url)); };
}
