// Dish add/edit form
import { api } from '../api.js';
import { dishPic } from '../pics.js';
import { t } from '../i18n.js';
import {
  h, icon, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, numCheck, toggle, confirmDialog, errorState, skeletonCards,
  photoImg, compressImage, DISH_KINDS, dishKind,
} from '../ui.js';

const val = (v) => (v === null || v === undefined ? '' : v);

export default async function render(root, ctx) {
  const isNew = ctx.params.id === 'new';
  const view = h('div', { class: 'screen' });
  root.append(view);
  if (!isNew) view.append(pageHead(t('dish.one'), { parent: '/menu/dishes' }), skeletonCards(3, 64));
  let dish = { kind: 'plate', is_default: false };
  if (!isNew) {
    try {
      dish = (await api.get('/dishes')).dishes.find((x) => String(x.id) === ctx.params.id);
      if (!dish) throw Object.assign(new Error(t('dish.not_found')), { user: true });
    } catch (e) { const b = h('div'); view.replaceChildren(pageHead(t('dish.one'), { parent: '/menu/dishes' }), b); errorState(b, e); return; }
  }
  if (!ctx.alive()) return;
  view.replaceChildren();

  let isDefault = !!dish.is_default;
  let photoFile = null;
  const name = h('input', { type: 'text', value: val(dish.name), maxLength: 60, placeholder: t('dish.ph_name'), 'aria-label': t('common.name') });
  let kindVal = DISH_KINDS[dish.kind] ? dish.kind : 'plate';
  const kindGrid = h('div', { class: 'kind-grid', role: 'radiogroup', 'aria-label': t('dish.kind') });
  function paintKinds() {
    kindGrid.replaceChildren(...Object.entries(DISH_KINDS).map(([k, v]) => h('button', {
      type: 'button', class: 'kind-opt' + (kindVal === k ? ' on' : ''), role: 'radio', 'aria-checked': String(kindVal === k),
      onclick: () => { kindVal = k; haptic('light'); paintKinds(); if (!photoFile && !dish.photo_url) paintPhoto(null); },
    }, dishPic(k, 44) || h('span', { class: 'pic', style: { width: '44px', height: '44px' } }, icon(v.icon, 24)), h('span', { class: 'kind-lbl' }, v.label))));
  }
  const diam = numInput({ value: val(dish.diameter_cm), min: 1, max: 80, step: 0.5, placeholder: '20', 'aria-label': t('dish.a_diam') });
  const depth = numInput({ value: val(dish.depth_cm), min: 0.5, max: 50, step: 0.5, placeholder: '5', 'aria-label': t('dish.a_depth') });
  const vol = numInput({ value: val(dish.volume_ml), min: 10, max: 10000, step: 10, placeholder: '500', inputmode: 'numeric', 'aria-label': t('dish.volume') });
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
      url ? h('img', { src: url, alt: t('dish.photo_alt') }) : (dish.photo_url ? photoImg(dish.photo_url, '', t('dish.photo_alt')) : (dishPic(kindVal, 96) || h('div', { class: 'ph-big', 'aria-hidden': 'true' }, icon(dishKind(kindVal).icon, 56)))),
      isNew ? h('button', { class: 'btn btn-secondary btn-sm', type: 'button', onclick: () => fileInput.click() }, icon('camera', 18), url ? t('dish.photo_replace') : t('dish.photo_empty')) : null].filter(Boolean));
  }
  paintKinds();
  paintPhoto(null);

  let busy = false;
  async function save() {
    if (busy) return;
    const n = name.value.trim();
    if (!n) { toast(t('pf.v_name'), 'error'); name.focus(); return; }
    const d = numVal(diam), dp = numVal(depth), v = numVal(vol);
    const bad = numCheck(diam, t('dish.f_diam')) || numCheck(depth, t('dish.f_depth')) || numCheck(vol, t('dish.f_vol'));
    if (bad) { toast(bad, 'error'); return; }
    busy = true; mainButton.setLoading(true);
    try {
      if (isNew) {
        const fd = new FormData();
        fd.append('name', n); fd.append('kind', kindVal);
        if (d !== null) fd.append('diameter_cm', d);
        if (dp !== null) fd.append('depth_cm', dp);
        if (v !== null) fd.append('volume_ml', Math.round(v));
        fd.append('is_default', isDefault ? 'true' : 'false');
        if (photoFile) fd.append('photo', photoFile, photoFile.name || 'dish.jpg');
        await api.post('/dishes', fd);
      } else {
        await api.patch('/dishes/' + dish.id, { name: n, kind: kindVal, diameter_cm: d, depth_cm: dp, volume_ml: v === null ? null : Math.round(v), is_default: isDefault });
      }
      haptic('success'); toast(t('dish.saved'), 'ok'); mainButton.hide(); nav.replace('/menu/dishes');
    } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
  }
  async function remove() {
    if (!(await confirmDialog(t('pf.del_q', { n: dish.name })))) return;
    try { await api.del('/dishes/' + dish.id); haptic('success'); toast(t('pf.deleted')); mainButton.hide(); nav.replace('/menu/dishes'); } catch (e) { toast(e.message, 'error'); }
  }

  view.append(...[pageHead(isNew ? t('dish.new') : t('dish.one'), { parent: '/menu/dishes' }),
    h('form', { class: 'stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      h('div', { class: 'hint-card small' }, t('dish.hint')),
      preview,
      group([frow(t('common.name'), name)]),
      h('div', { class: 'section-title' }, h('span', null, t('dish.kind'))),
      kindGrid,
      group([frow(t('dish.f_diam'), diam), frow(t('dish.f_depth'), depth), frow(t('dish.f_vol'), vol)]),
      group([h('div', { class: 'frow' },
        h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('dish.default')), h('span', { class: 'row-sub' }, t('dish.default_sub'))),
        toggle(isDefault, (c) => { isDefault = c; }, t('dish.default_aria')))]),
      fileInput, h('button', { type: 'submit', hidden: true })),
    isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, t('dish.delete'))].filter(Boolean));
  mainButton.show(t('common.save'), save);
}
