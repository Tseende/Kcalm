// Photo of a nutrition label -> editable draft -> save as product
import { api } from '../api.js';
import { h, icon, toast, haptic, nav, mainButton, pageHead, compressImage, busyOverlay } from '../ui.js';
import { productForm } from './product.js';

export default async function render(root) {
  const view = h('div', { class: 'screen' });
  const out = h('div', { class: 'stack' });
  const camera = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => pick(camera) });
  const gallery = h('input', { type: 'file', accept: 'image/*', hidden: true, onchange: () => pick(gallery) });
  view.append(pageHead('Продукт по этикетке', { parent: '/menu/products' }), h('div', { class: 'stack' },
    h('div', { class: 'muted' }, 'Сфотографируйте таблицу пищевой ценности. Бот прочитает её, а вы проверите значения перед сохранением.'),
    h('div', { class: 'row-btns' },
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => camera.click() }, icon('camera', 20), 'Снять'),
      h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => gallery.click() }, icon('image', 20), 'Из галереи')),
    out, camera, gallery));
  root.append(view);

  async function pick(input) {
    const f = input.files[0];
    input.value = '';
    if (!f) return;
    mainButton.hide();
    let file;
    try { file = await compressImage(f, 2000, 0.9); } catch (e) { toast(e.message, 'error'); return; }
    const url = URL.createObjectURL(file);
    const fd = new FormData();
    fd.append('photo', file, file.name || 'label.jpg');
    out.replaceChildren(h('img', { class: 'preview', src: url, alt: 'Фото этикетки' }));
    const stop = busyOverlay('Читаю этикетку…', 'Обычно 5–15 секунд');
    try {
      const r = await api.post('/products/label', fd, { timeout: 90000 });
      stop();
      draft(r, url);
    } catch (e) { stop(); toast(e.message, 'error'); }
  }

  function draft(r, url) {
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
      h('img', { class: 'preview', src: url, alt: 'Фото этикетки' }),
      h('div', { class: 'muted small' }, `Уверенность распознавания: ${Math.round((r.confidence || 0) * 100)}%. Это черновик, проверьте значения.`),
      (r.notes || []).length ? h('ul', { class: 'notes' }, r.notes.map((n) => h('li', null, n))) : null,
      form.el].filter(Boolean));
    mainButton.show('Сохранить продукт', save);
  }
  return () => mainButton.hide();
}
