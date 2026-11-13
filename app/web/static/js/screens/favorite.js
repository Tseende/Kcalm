// Избранное: создание и правка блюда (название, ккал и БЖУ порции, комментарий). Из списка избранного.
import { api } from '../api.js';
import { foodPic, mealPic } from '../pics.js';
import { h, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, numCheck, confirmDialog, errorState, skeletonCards, photoImg } from '../ui.js';
import { t } from '../i18n.js';
import { CATEGORIES } from './favorites.js';

export default async function render(root, ctx) {
  const isNew = ctx.params.id === 'new';
  const view = h('div', { class: 'screen' });
  root.append(view);
  if (!isNew) view.append(pageHead(t('fav.title'), { parent: '/menu/favorites' }), skeletonCards(3, 64));
  let fav = { title: '', note: '', kcal: '', protein: '', fat: '', carbs: '' };
  if (!isNew) {
    try { fav = await api.get('/favorites/' + ctx.params.id); } catch (e) {
      const b = h('div'); view.replaceChildren(pageHead(t('fav.title'), { parent: '/menu/favorites' }), b); errorState(b, e); return;
    }
  }
  if (!ctx.alive()) return;
  view.replaceChildren();

  const title = h('input', { type: 'text', value: fav.title, maxLength: 80, placeholder: t('favf.ph_title'), autocomplete: 'off', 'aria-label': t('common.name') });
  const num = (v, max, step, label) => numInput({ value: v === null || v === undefined ? '' : v, min: 0, max, step, 'aria-label': label });
  const kcal = num(fav.kcal, 5000, 1, t('favf.kcal_aria'));
  const pr = num(fav.protein, 500, 0.1, t('prof.f_p'));
  const fat = num(fav.fat, 500, 0.1, t('prof.f_f'));
  const carb = num(fav.carbs, 1000, 0.1, t('prof.f_c'));
  const note = h('textarea', { rows: 3, maxLength: 500, placeholder: t('favf.ph_note'), 'aria-label': t('favf.note') });
  note.value = fav.note || '';

  const stub = () => foodPic(fav.title, 72) || foodPic((fav.names || [])[0] || '', 72) || mealPic('snack', 72);
  const pic = fav.photo_url ? h('div', { class: 'fav-photo' }, photoImg(fav.photo_url, '', fav.title, stub))
    : h('div', { class: 'fav-photo ph' }, stub());

  // Категория: в каком разделе списка лежит блюдо.
  let category = CATEGORIES.some(([k]) => k === fav.category) ? fav.category : null;
  const catBox = h('div', { class: 'chips wrap', role: 'radiogroup', 'aria-label': t('favc.aria') });
  function paintCat() {
    catBox.replaceChildren(...CATEGORIES.map(([k, label]) => h('button', { type: 'button', role: 'radio', class: 'chip' + (category === k ? ' active' : ''), 'aria-checked': String(category === k),
      onclick: () => { category = k; haptic('selection'); paintCat(); } }, label)));
  }
  paintCat();

  let busy = false;
  async function save() {
    if (busy) return;
    const ttl = title.value.trim();
    if (!ttl) { toast(t('favf.v_name'), 'error'); title.focus(); return; }
    const k = numVal(kcal);
    if (k === null || k === undefined || Number.isNaN(k)) { toast(t('favf.v_kcal'), 'error'); kcal.focus(); return; }
    const bad = numCheck(kcal, t('prof.kcal')) || numCheck(pr, t('macro.protein')) || numCheck(fat, t('macro.fat')) || numCheck(carb, t('macro.carbs'));
    if (bad) { toast(bad, 'error'); return; }
    const body = { title: ttl, note: note.value.trim(), kcal: k, protein: numVal(pr) || 0, fat: numVal(fat) || 0, carbs: numVal(carb) || 0 };
    if (category) body.category = category;
    busy = true; mainButton.setLoading(true);
    try {
      if (isNew) await api.post('/favorites/custom', body); else await api.patch('/favorites/' + fav.id, body);
      haptic('success'); toast(t('common.saved'), 'ok'); mainButton.hide(); nav.replace('/menu/favorites');
    } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
  }
  async function remove() {
    if (!(await confirmDialog(t('favf.rm_q', { n: fav.title })))) return;
    try { await api.del('/favorites/' + fav.id); haptic('success'); toast(t('favf.removed')); mainButton.hide(); nav.replace('/menu/favorites'); } catch (e) { toast(e.message, 'error'); }
  }

  view.append(...[
    pageHead(isNew ? t('favf.new') : t('fav.title'), { parent: '/menu/favorites' }),
    isNew ? null : pic,
    h('form', { class: 'stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      group([frow(t('common.name'), title)]),
      h('div', { class: 'section-label' }, t('favc.title')), catBox,
      h('div', { class: 'section-label' }, t('favf.one')),
      group([frow(t('prof.f_kcal'), kcal), frow(t('prof.f_p'), pr), frow(t('prof.f_f'), fat), frow(t('prof.f_c'), carb)]),
      h('div', { class: 'section-label' }, t('favf.note')), note,
      h('button', { type: 'submit', hidden: true })),
    isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, t('favf.rm')),
    isNew ? null : h('div', { class: 'footnote' }, t('favf.edit_note')),
  ].filter(Boolean));
  mainButton.show(t('common.save'), save);
}
