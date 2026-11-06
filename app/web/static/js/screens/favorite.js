// Избранное: создание и правка блюда (название, ккал и БЖУ порции, комментарий). Из списка избранного.
import { api } from '../api.js';
import { foodPic, mealPic } from '../pics.js';
import { h, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, numCheck, confirmDialog, errorState, skeletonCards, photoImg } from '../ui.js';

export default async function render(root, ctx) {
  const isNew = ctx.params.id === 'new';
  const view = h('div', { class: 'screen' });
  root.append(view);
  if (!isNew) view.append(pageHead('Избранное', { parent: '/menu/favorites' }), skeletonCards(3, 64));
  let fav = { title: '', note: '', kcal: '', protein: '', fat: '', carbs: '' };
  if (!isNew) {
    try { fav = await api.get('/favorites/' + ctx.params.id); } catch (e) {
      const b = h('div'); view.replaceChildren(pageHead('Избранное', { parent: '/menu/favorites' }), b); errorState(b, e); return;
    }
  }
  if (!ctx.alive()) return;
  view.replaceChildren();

  const title = h('input', { type: 'text', value: fav.title, maxLength: 80, placeholder: 'Например: мой омлет', autocomplete: 'off', 'aria-label': 'Название' });
  const num = (v, max, step, label) => numInput({ value: v === null || v === undefined ? '' : v, min: 0, max, step, 'aria-label': label });
  const kcal = num(fav.kcal, 5000, 1, 'Ккал порции');
  const pr = num(fav.protein, 500, 0.1, 'Белки, г');
  const fat = num(fav.fat, 500, 0.1, 'Жиры, г');
  const carb = num(fav.carbs, 1000, 0.1, 'Углеводы, г');
  const note = h('textarea', { rows: 3, maxLength: 500, placeholder: 'Из чего состоит, как готовите: пригодится, когда нужно вспомнить или уточнить блюдо', 'aria-label': 'Комментарий' });
  note.value = fav.note || '';

  const pic = fav.photo_url ? h('div', { class: 'fav-photo' }, photoImg(fav.photo_url, '', fav.title))
    : h('div', { class: 'fav-photo ph' }, foodPic(fav.title, 72) || mealPic('snack', 72));

  let busy = false;
  async function save() {
    if (busy) return;
    const t = title.value.trim();
    if (!t) { toast('Назовите блюдо', 'error'); title.focus(); return; }
    const k = numVal(kcal);
    if (k === null || k === undefined || Number.isNaN(k)) { toast('Укажите калории порции', 'error'); kcal.focus(); return; }
    const bad = numCheck(kcal, 'Калории') || numCheck(pr, 'Белки') || numCheck(fat, 'Жиры') || numCheck(carb, 'Углеводы');
    if (bad) { toast(bad, 'error'); return; }
    const body = { title: t, note: note.value.trim(), kcal: k, protein: numVal(pr) || 0, fat: numVal(fat) || 0, carbs: numVal(carb) || 0 };
    busy = true; mainButton.setLoading(true);
    try {
      if (isNew) await api.post('/favorites/custom', body); else await api.patch('/favorites/' + fav.id, body);
      haptic('success'); toast('Сохранено', 'ok'); mainButton.hide(); nav.replace('/menu/favorites');
    } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
  }
  async function remove() {
    if (!(await confirmDialog(`Убрать «${fav.title}» из избранного?`))) return;
    try { await api.del('/favorites/' + fav.id); haptic('success'); toast('Убрано из избранного'); mainButton.hide(); nav.replace('/menu/favorites'); } catch (e) { toast(e.message, 'error'); }
  }

  view.append(...[
    pageHead(isNew ? 'Новое избранное' : 'Избранное', { parent: '/menu/favorites' }),
    isNew ? null : pic,
    h('form', { class: 'stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      group([frow('Название', title)]),
      h('div', { class: 'section-label' }, 'Одна порция'),
      group([frow('Калории, ккал', kcal), frow('Белки, г', pr), frow('Жиры, г', fat), frow('Углеводы, г', carb)]),
      h('div', { class: 'section-label' }, 'Комментарий'), note,
      h('button', { type: 'submit', hidden: true })),
    isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, 'Убрать из избранного'),
    isNew ? null : h('div', { class: 'footnote' }, 'Правка здесь меняет только избранное: записи в дневнике остаются как были.'),
  ].filter(Boolean));
  mainButton.show('Сохранить', save);
}
