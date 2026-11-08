// Product add/edit form (also reused by the label screen)
import { api } from '../api.js';
import { foodPic, foodGroupOf, FOOD_ICONS, FOOD_GROUPS, groupLabel } from '../pics.js';
import { h, icon, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, numCheck, codeInput, confirmDialog, errorState, skeletonCards } from '../ui.js';
import { t } from '../i18n.js';

const val = (v) => (v === null || v === undefined ? '' : v);

/** Builds the form. Returns {el, collect()} where collect() returns payload or null (after toast). */
export function productForm(p = {}) {
  const name = h('input', { type: 'text', value: val(p.name), maxLength: 60, placeholder: t('pf.ph_name'), autocomplete: 'off', 'aria-label': t('common.name') });
  const brand = h('input', { type: 'text', value: val(p.brand), maxLength: 60, placeholder: t('pf.optional'), 'aria-label': t('pf.brand') });
  const kcal = numInput({ value: val(p.kcal), min: 0, max: 900, step: 1, 'aria-label': t('pf.a_kcal') });
  const pr = numInput({ value: val(p.protein), min: 0, max: 100, step: 0.1, 'aria-label': t('pf.a_p') });
  const fat = numInput({ value: val(p.fat), min: 0, max: 100, step: 0.1, 'aria-label': t('pf.a_f') });
  const carb = numInput({ value: val(p.carbs), min: 0, max: 100, step: 0.1, 'aria-label': t('pf.a_c') });
  const dg = numInput({ value: val(p.default_grams), min: 1, max: 5000, step: 1, placeholder: t('pf.ph_dg'), 'aria-label': t('pf.default_portion') });
  const barcode = codeInput({ value: val(p.barcode), placeholder: t('pf.optional'), 'aria-label': t('pf.barcode') });
  const un = h('input', { type: 'text', value: val(p.unit_name), maxLength: 30, placeholder: t('pf.ph_unit'), 'aria-label': t('pf.unit_name') });
  const ug = numInput({ value: val(p.unit_grams), min: 1, max: 5000, step: 1, placeholder: t('pf.ph_ug'), 'aria-label': t('pf.unit_g_aria') });
  let iconVal = FOOD_ICONS.includes(p.icon) ? p.icon : null;
  const iconPrev = h('button', { type: 'button', class: 'icon-prev', 'aria-haspopup': 'dialog', onclick: openIconPage });
  function paintIcon() {
    iconPrev.replaceChildren(foodPic(name.value, 72, iconVal) || h('span', { class: 'pic', style: { width: '72px', height: '72px' } }, icon('package', 32)),
      h('span', { class: 'icon-prev-lbl' }, t('pf.change_icon')));
  }
  // Выбор иконки открывается отдельной страницей поверх формы: введённые поля не теряются.
  let closeIconPage = null;
  function openIconPage() {
    let openGroup = null;
    const g0 = foodGroupOf(name.value, iconVal);
    if (g0 !== 'Другое') openGroup = g0;
    const body = h('div', { class: 'screen icon-page-body', role: 'radiogroup', 'aria-label': t('pf.icon') });
    const close = () => { page.remove(); document.removeEventListener('keydown', onKey); closeIconPage = null; };
    closeIconPage = close;
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    const pick = (k) => { iconVal = k; haptic('light'); paintIcon(); close(); };
    const opt = (k) => h('button', { type: 'button', class: 'icon-opt' + (iconVal === k ? ' on' : ''), role: 'radio', 'aria-checked': String(iconVal === k),
      onclick: () => pick(k) }, foodPic('', 40, k));
    function paint() {
      body.replaceChildren(
        h('div', { class: 'icon-grid' }, h('button', { type: 'button', class: 'icon-opt auto' + (iconVal ? '' : ' on'), role: 'radio', 'aria-checked': String(!iconVal),
          onclick: () => pick(null) }, t('pf.auto'))),
        h('div', { class: 'icon-groups' }, FOOD_GROUPS.filter(([, keys]) => keys.length).map(([title, keys]) => {
          const on = openGroup === title;
          return h('div', { class: 'icon-group' + (on ? ' open' : '') },
            h('button', { type: 'button', class: 'icon-group-head', 'aria-expanded': String(on),
              onclick: () => { openGroup = on ? null : title; paint(); } },
            h('span', { class: 'grow' }, groupLabel(title)), h('span', { class: 'icon-group-n' }, String(keys.length)), icon('next', 16)),
            on ? h('div', { class: 'icon-grid' }, keys.map(opt)) : null);
        })));
    }
    const page = h('div', { class: 'icon-page', role: 'dialog', 'aria-label': t('pf.icon_pick') },
      h('header', { class: 'page-head' },
        h('div', { class: 'ph-side' }, h('button', { class: 'back-btn', type: 'button', 'aria-label': t('common.back'), onclick: close }, icon('back', 24), h('span', null, t('common.back')))),
        h('h1', null, t('pf.icon')), h('div', { class: 'ph-side end' })),
      body);
    paint();
    document.addEventListener('keydown', onKey);
    document.body.append(page);
  }
  name.addEventListener('input', () => { if (!iconVal) paintIcon(); });
  paintIcon();
  const el = h('div', { class: 'stack' },
    iconPrev,
    group([frow(t('common.name'), name), frow(t('pf.brand'), brand), frow(t('pf.barcode'), barcode)]),
    h('div', { class: 'section-title' }, h('span', null, t('pf.per100'))),
    group([frow(t('prof.f_kcal'), kcal), frow(t('prof.f_p'), pr), frow(t('prof.f_f'), fat), frow(t('prof.f_c'), carb)]),
    h('div', { class: 'section-title' }, h('span', null, t('common.portion'))),
    group([frow(t('pf.default_g'), dg), frow(t('pf.unit'), un), frow(t('pf.unit_g'), ug)], t('pf.default_note')));

  function collect() {
    const n = name.value.trim();
    if (!n) { toast(t('pf.v_name'), 'error'); name.focus(); return null; }
    const k = numVal(kcal);
    if (k === null || k < 0 || k > 900) { toast(t('pf.v_kcal'), 'error'); kcal.focus(); return null; }
    const macros = [numVal(pr), numVal(fat), numVal(carb)];
    if (macros.some((m) => m !== null && (m < 0 || m > 100))) { toast(t('pf.v_macros'), 'error'); return null; }
    if (macros.reduce((a, m) => a + (m || 0), 0) > 100.5) { toast(t('pf.v_sum'), 'error'); return null; }
    const bc = barcode.value.replace(/\D/g, '');
    if (bc && ![8, 12, 13, 14].includes(bc.length)) { toast(t('pf.v_barcode'), 'error'); barcode.focus(); return null; }
    const bad = numCheck(dg, t('pf.default_portion')) || numCheck(ug, t('pf.unit_g_aria'));
    if (bad) { toast(bad, 'error'); return null; }
    const grams = (inp) => { const v = numVal(inp); return v && v > 0 ? Math.round(v) : null; };
    return {
      name: n, brand: brand.value.trim() || null, barcode: bc || null, kcal: Math.round(k),
      protein: macros[0] ?? 0, fat: macros[1] ?? 0, carbs: macros[2] ?? 0,
      default_grams: grams(dg), unit_name: un.value.trim() || null, unit_grams: grams(ug), icon: iconVal,
    };
  }
  // страница выбора иконки живёт в <body>: при уходе с экрана её нужно закрыть, иначе она останется поверх и слушатель Esc тоже
  return { el, collect, destroy: () => { if (closeIconPage) closeIconPage(); } };
}

export default async function render(root, ctx) {
  const isNew = ctx.params.id === 'new';
  const view = h('div', { class: 'screen' });
  root.append(view);
  if (!isNew) view.append(pageHead(t('prod.one'), { parent: '/menu/products' }), skeletonCards(3, 64));
  let product = {};
  if (!isNew) {
    try {
      const all = (await api.get('/products')).products;
      product = all.find((x) => String(x.id) === ctx.params.id);
      if (!product) throw Object.assign(new Error(t('pf.not_found')), { user: true });
    } catch (e) { const b = h('div'); view.replaceChildren(pageHead(t('prod.one'), { parent: '/menu/products' }), b); errorState(b, e); return; }
  }
  if (!ctx.alive()) return;
  view.replaceChildren();
  const form = productForm(product);
  let busy = false;
  async function save() {
    if (busy) return;
    const data = form.collect();
    if (!data) return;
    busy = true; mainButton.setLoading(true);
    try {
      if (isNew) await api.post('/products', data); else await api.patch('/products/' + product.id, data);
      haptic('success'); toast(t('pf.saved'), 'ok'); mainButton.hide(); nav.replace('/menu/products');
    } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
  }
  async function remove() {
    if (!(await confirmDialog(t('pf.del_q', { n: product.name })))) return;
    try { await api.del('/products/' + product.id); haptic('success'); toast(t('pf.deleted')); mainButton.hide(); nav.replace('/menu/products'); } catch (e) { toast(e.message, 'error'); }
  }
  view.append(...[pageHead(isNew ? t('prod.new') : t('prod.one'), { parent: '/menu/products' }),
    h('form', { class: 'stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } }, form.el, h('button', { type: 'submit', hidden: true })),
    isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, t('pf.delete')),
    !isNew && product.times_used ? h('div', { class: 'muted small center' }, t('pf.used', { n: product.times_used })) : null].filter(Boolean));
  mainButton.show(t('common.save'), save);
  return () => form.destroy();
}
