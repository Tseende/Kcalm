// Новый продукт по упаковке: сканер штрихкода, фото этикетки (до 4) или цифры вручную -> черновик -> сохранить
import { api } from '../api.js';
import { h, icon, tile, toast, haptic, nav, mainButton, pageHead, compressImage, busyOverlay, codeInput } from '../ui.js';
import { scanBarcode, decodeFile, validBarcode } from '../scanner.js';
import { productForm } from './product.js';
import { t } from '../i18n.js';

const MAX = 4;

export default async function render(root) {
  const view = h('div', { class: 'screen' });
  const photos = []; // [{file, url}]
  const camera = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => add(camera) });
  const gallery = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => add(gallery) });
  const codeShot = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: () => fromShot() });
  const out = h('div', { class: 'stack' });
  const start = h('div', { class: 'stack' });
  const manual = h('div', { class: 'card manual', hidden: true });
  const barcodeIn = codeInput({ placeholder: t('lbl.ph_code'), 'aria-label': t('pf.barcode') });
  barcodeIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); lookup(barcodeIn.value); } });
  manual.append(
    h('div', { class: 'barcode-row' }, barcodeIn,
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => lookup(barcodeIn.value) }, t('lbl.find'))));

  const option = (ic, color, title, sub, onclick, primary) => h('button', { class: 'opt' + (primary ? ' primary' : ''), type: 'button', onclick },
    tile(ic, color, 44),
    h('span', { class: 'grow' }, h('span', { class: 'row-title' }, title), h('span', { class: 'row-sub' }, sub)),
    icon('next', 18));

  function paintStart() {
    const thumbs = photos.map((p, i) => h('div', { class: 'photo-thumb' },
      h('img', { src: p.url, alt: t('lbl.photo_alt', { n: i + 1 }) }),
      h('button', { class: 'remove', type: 'button', 'aria-label': t('lbl.photo_rm', { n: i + 1 }),
        onclick: () => { URL.revokeObjectURL(p.url); photos.splice(i, 1); paintStart(); } }, icon('close', 16))));
    const shots = photos.length ? h('div', { class: 'card photo-card' },
      h('div', { class: 'row-title' }, t('lbl.photos', { n: photos.length, m: MAX })),
      h('div', { class: 'thumbs' }, thumbs),
      h('div', { class: 'capture-actions tight' },
        photos.length < MAX ? h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => camera.click() }, icon('camera', 20), t('lbl.more')) : null,
        photos.length < MAX ? h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => gallery.click() }, icon('image', 20), t('lbl.gallery')) : null),
      h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: recognize }, icon('sparkles', 20), t('lbl.recognize'))) : null;
    start.replaceChildren(...[
      h('div', { class: 'hero' },
        h('span', { class: 'hero-ico' }, icon('barcode', 34)),
        h('div', { class: 'hero-title' }, t('lbl.hero')),
        h('div', { class: 'hero-sub' }, t('lbl.hero_sub'))),
      option('barcode', 'blue', t('lbl.scan'), t('lbl.scan_sub'), scan, true),
      option('camera', 'green', t('lbl.shoot'), t('lbl.shoot_sub', { n: MAX }), () => camera.click()),
      shots,
      h('button', { class: 'btn-link center', type: 'button', onclick: () => { manual.hidden = !manual.hidden; if (!manual.hidden) barcodeIn.focus(); } }, t('lbl.manual')),
      manual].filter(Boolean));
  }

  view.append(pageHead(t('prod.new'), { parent: '/menu/products' }), start, out, camera, gallery, codeShot);
  root.append(view);
  paintStart();

  async function scan() {
    haptic('light');
    const r = await scanBarcode();
    if (!r) return;
    if (r.code) { barcodeIn.value = r.code; lookup(r.code); return; }
    if (r.manual) { manual.hidden = false; barcodeIn.focus(); return; }
    // Камера внутри приложения недоступна (нет разрешения): снимок штрихкода тоже подойдёт.
    toast(r.error === 'load' ? t('lbl.scan_load') : t('lbl.no_cam'), 'error');
    if (r.error === 'camera') codeShot.click();
  }

  async function fromShot() {
    const f = codeShot.files[0];
    codeShot.value = '';
    if (!f) return;
    const stop = busyOverlay(t('lbl.reading_code'));
    let code = null;
    try { code = await decodeFile(f); } catch (e) { /* не вышло: скажем ниже */ }
    stop();
    if (code) { barcodeIn.value = code; lookup(code); }
    else { toast(t('lbl.code_not_found'), 'error'); manual.hidden = false; }
  }

  async function add(input) {
    const files = [...input.files];
    input.value = '';
    for (const f of files) {
      if (photos.length >= MAX) { toast(t('lbl.max', { n: MAX })); break; }
      try { const file = await compressImage(f, 2000, 0.9); photos.push({ file, url: URL.createObjectURL(file) }); }
      catch (e) { toast(e.message, 'error'); }
    }
    paintStart();
  }

  async function recognize() {
    if (!photos.length) return;
    mainButton.hide();
    const fd = new FormData();
    photos.forEach((p, i) => fd.append('photo', p.file, p.file.name || `label${i + 1}.jpg`));
    const stop = busyOverlay(t('lbl.reading'), photos.length > 1 ? t('lbl.sub_multi') : t('lbl.sub_one'));
    try {
      const r = await api.post('/products/label', fd, { timeout: 120000 });
      stop();
      draft(r);
    } catch (e) { stop(); toast(e.message, 'error'); }
  }

  async function lookup(raw) {
    const code = String(raw || '').replace(/\D/g, '');
    if (!code) { toast(t('lbl.v_empty'), 'error'); barcodeIn.focus(); return; }
    if (!validBarcode(code)) { toast(t('lbl.v_bad'), 'error'); return; }
    mainButton.hide();
    const stop = busyOverlay(t('lbl.searching'), 'Open Food Facts');
    try {
      const r = await api.get('/products/barcode/' + code);
      stop();
      haptic('success');
      draft({ draft: r.draft, notes: [t('lbl.off_note')], existing_id: r.existing_id });
    } catch (e) {
      stop();
      toast(e.status === 404 ? t('lbl.not_in_db') : e.message, 'error');
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
        haptic('success'); toast(t('pf.saved'), 'ok'); mainButton.hide(); nav.replace('/menu/products');
      } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
    }
    start.hidden = true;
    out.replaceChildren(...[
      h('button', { class: 'btn-link back-link', type: 'button', onclick: () => { out.replaceChildren(); start.hidden = false; mainButton.hide(); } }, icon('back', 16), t('lbl.other')),
      r.confidence != null ? h('div', { class: 'muted small' }, t('lbl.conf', { p: Math.round((r.confidence || 0) * 100) })) : null,
      r.existing_id ? h('div', { class: 'hint-card small' }, t('lbl.exists'), h('a', { href: '#/menu/product/' + r.existing_id }, t('lbl.open'))) : null,
      (r.notes || []).length ? h('ul', { class: 'notes' }, r.notes.map((n) => h('li', null, n))) : null,
      form.el].filter(Boolean));
    mainButton.show(t('lbl.save'), save);
    window.scrollTo({ top: 0 });
  }

  return () => { mainButton.hide(); photos.forEach((p) => URL.revokeObjectURL(p.url)); };
}
