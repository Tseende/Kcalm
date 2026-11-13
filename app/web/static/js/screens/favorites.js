// Избранное: свои блюда одним касанием. Здесь их видно с картинкой, составом и БЖУ; «+» записывает в дневник, строка открывает правку.
import { api } from '../api.js';
import { foodPic, mealPic } from '../pics.js';
import { t, tn } from '../i18n.js';
import {
  h, nav, mainButton, pageHead, emptyState, errorState, skeletonCards, fmtN, photoImg, macroChips, icon, sheet, segmented, sectionTitle,
  toast, haptic, MEAL_TYPES, todayISO, addDays, dataChanged,
} from '../ui.js';

const SEARCH_FROM = 6;                    // поиск появляется, когда блюд стало много
const PORTIONS = [['0.5', t('fav.p05')], ['1', '×1'], ['1.5', t('fav.p15')], ['2', '×2']];
const norm = (t) => String(t || '').toLowerCase().replace(/ё/g, 'е');
// Категории списка: четыре приёма и напитки. У блюда из дневника категория по умолчанию — тип того приёма.
export const CATEGORIES = [['breakfast', t('favc.breakfast')], ['lunch', t('favc.lunch')], ['dinner', t('favc.dinner')], ['snack', t('favc.snack')], ['drink', t('favc.drink')]];
const catOf = (f) => (CATEGORIES.some(([k]) => k === f.category) ? f.category : 'snack');
let lastCat = null;                       // выбранная категория переживает уход в карточку блюда и обратно

function defaultMealType() {
  const hr = new Date().getHours();
  return hr < 11 ? 'breakfast' : hr < 16 ? 'lunch' : hr < 21 ? 'dinner' : 'snack';
}

/** Запасная картинка, когда фото нет или файл пропал: по названию блюда, затем по первой позиции, затем общая. */
const favPic = (f, size = 48) => foodPic(f.title, size) || foodPic((f.names || [])[0] || '', size) || mealPic('snack', size);

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonCards(3, 64));
  view.append(pageHead(t('fav.title'), { parent: '/menu' }), box);
  root.append(view);
  mainButton.show(t('fav.add'), () => nav.go('/menu/favorite/new'));
  let items;
  try { items = (await api.get('/favorites')).items || []; } catch (e) { if (ctx.alive()) errorState(box, e, () => { root.replaceChildren(); render(root, ctx); }); return; }
  if (!ctx.alive()) return;

  const thumb = (f) => (f.photo_url ? h('span', { class: 'fav-thumb' }, photoImg(f.photo_url, '', f.title, () => favPic(f))) : favPic(f));

  if (!items.length) {
    box.replaceChildren(emptyState('star', t('fav.empty_title'),
      t('fav.empty_text')));
    return;
  }

  const listBox = h('div', { class: 'stack' });
  const chips = h('div', { class: 'chips scroll', role: 'tablist', 'aria-label': t('favc.aria') });
  let cat = lastCat;
  const counts = new Map();
  items.forEach((f) => counts.set(catOf(f), (counts.get(catOf(f)) || 0) + 1));
  if (cat && !counts.has(cat)) cat = null;
  function paintChips() {
    const mk = (key, text) => h('button', { type: 'button', role: 'tab', class: 'chip' + (cat === key ? ' active' : ''), 'aria-selected': String(cat === key),
      onclick: () => { cat = key; lastCat = key; haptic('selection'); paintChips(); paint(); } }, text);
    chips.replaceChildren(mk(null, t('prod.all', { n: items.length })), ...CATEGORIES.filter(([k]) => counts.has(k)).map(([k, label]) => mk(k, `${label} ${counts.get(k)}`)));
  }
  const count = h('div', { class: 'footnote' }, t('fav.count', { n: tn('fav.dishes', items.length) }));
  const search = h('input', { type: 'search', placeholder: t('fav.search_ph'), 'aria-label': t('common.search'), autocomplete: 'off', oninput: () => paint() });

  function row(f) {
    const names = (f.names || []).filter(Boolean);
    return h('div', { class: 'row prod-row fav-row' },
      h('button', { class: 'fav-open', type: 'button', onclick: () => nav.go('/menu/favorite/' + f.id) },
        thumb(f),
        h('span', { class: 'grow' },
          h('span', { class: 'row-title' }, f.title),
          names.length > 1 ? h('span', { class: 'fav-names' }, names.join(', ')) : null,
          f.note ? h('span', { class: 'row-sub' }, f.note) : null,
          macroChips({ p: f.protein, f: f.fat, c: f.carbs })),
        h('span', { class: 'prod-kcal' }, h('b', null, fmtN(f.kcal)), h('span', null, t('unit.kcal')))),
      h('button', { class: 'fav-add', type: 'button', 'aria-label': t('fav.add_aria', { n: f.title }), onclick: () => addSheet(f) }, h('span', null, icon('plus', 20))));
  }

  function paint() {
    const q = norm(search.value.trim());
    const found = q ? items.filter((f) => norm(f.title).includes(q) || (f.names || []).some((n) => norm(n).includes(q))) : items;
    const shown = cat ? found.filter((f) => catOf(f) === cat) : found;
    if (!shown.length) { listBox.replaceChildren(emptyState('search', t('common.not_found'), t('common.try_other'))); return; }
    const card = (list) => h('div', { class: 'list tiles prod-list' }, list.map(row));
    // «Все» без поиска: разделы по категориям; выбранная категория или поиск: один список
    if (cat || q) { listBox.replaceChildren(card(shown)); return; }
    listBox.replaceChildren(...CATEGORIES.filter(([k]) => counts.has(k)).flatMap(([k, label]) => [sectionTitle(label), card(shown.filter((f) => catOf(f) === k))]));
  }

  /** «Добавить в дневник»: тип приёма, порция, день. Остаёмся на списке. */
  function addSheet(f) {
    // тип приёма по умолчанию — категория блюда; у напитков и без категории — по времени суток
    let type = MEAL_TYPES[f.category] ? f.category : defaultMealType(), factor = '1', day = 'today', busy = false;
    const label = () => t('fav.add_btn', { v: fmtN(Math.round((Number(f.kcal) || 0) * Number(factor))) });
    const go = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: submit }, label());
    async function submit() {
      if (busy) return;
      busy = true; go.disabled = true;
      try {
        const body = { factor: Number(factor), meal_type: type };
        if (day === 'yesterday') body.date = addDays(todayISO(), -1);
        const r = await api.post(`/favorites/${f.id}/repeat`, body);
        haptic('success');
        s.close();
        toast(t('fav.added'), 'ok', { undo: () => api.del('/meals/' + r.id) });
        dataChanged();
      } catch (e) { busy = false; go.disabled = false; toast(e.message, 'error'); }
    }
    const s = sheet({
      title: t('fav.sheet'),
      content: h('div', { class: 'fav-sheet' },
        h('div', { class: 'fav-sheet-name' }, f.title),
        h('div', { class: 'seg-label' }, t('add.meal')),
        segmented(Object.entries(MEAL_TYPES).map(([k, v]) => [k, v.label]), type, (v) => { type = v; }, t('add.meal')),
        h('div', { class: 'seg-label' }, t('common.portion')),
        segmented(PORTIONS, factor, (v) => { factor = v; go.textContent = label(); }, t('common.portion')),
        h('div', { class: 'seg-label' }, t('common.day')),
        segmented([['today', t('date.today')], ['yesterday', t('date.yesterday')]], day, (v) => { day = v; }, t('common.day')),
        go),
    });
  }

  paintChips();
  paint();
  box.replaceChildren(
    items.length >= SEARCH_FROM ? h('div', { class: 'search' }, icon('search', 18), search) : null,
    counts.size > 1 ? chips : null,
    listBox, count);
}
