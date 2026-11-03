// Product add/edit form (also reused by the label screen)
import { api } from '../api.js';
import { h, toast, haptic, nav, mainButton, pageHead, frow, group, numInput, numVal, confirmDialog, errorState } from '../ui.js';

const val = (v) => (v === null || v === undefined ? '' : v);

/** Builds the form. Returns {el, collect()} where collect() returns payload or null (after toast). */
export function productForm(p = {}) {
  const name = h('input', { type: 'text', value: val(p.name), maxLength: 60, placeholder: 'Творог 5%', autocomplete: 'off', 'aria-label': 'Название' });
  const brand = h('input', { type: 'text', value: val(p.brand), maxLength: 60, placeholder: 'Необязательно', 'aria-label': 'Бренд' });
  const kcal = numInput({ value: val(p.kcal), min: 0, max: 900, step: 1, 'aria-label': 'Ккал на 100 г' });
  const pr = numInput({ value: val(p.protein), min: 0, max: 100, step: 0.1, 'aria-label': 'Белки на 100 г' });
  const fat = numInput({ value: val(p.fat), min: 0, max: 100, step: 0.1, 'aria-label': 'Жиры на 100 г' });
  const carb = numInput({ value: val(p.carbs), min: 0, max: 100, step: 0.1, 'aria-label': 'Углеводы на 100 г' });
  const dg = numInput({ value: val(p.default_grams), min: 1, max: 5000, step: 1, placeholder: 'например, 150', 'aria-label': 'Порция по умолчанию' });
  const un = h('input', { type: 'text', value: val(p.unit_name), maxLength: 30, placeholder: 'пачка, шт, ломтик', 'aria-label': 'Название единицы' });
  const ug = numInput({ value: val(p.unit_grams), min: 1, max: 5000, step: 1, placeholder: 'г в единице', 'aria-label': 'Грамм в единице' });
  const el = h('div', { class: 'stack' },
    group([frow('Название', name), frow('Бренд', brand)]),
    h('div', { class: 'section-title' }, h('span', null, 'На 100 г')),
    group([frow('Калории, ккал', kcal), frow('Белки, г', pr), frow('Жиры, г', fat), frow('Углеводы, г', carb)]),
    h('div', { class: 'section-title' }, h('span', null, 'Порция')),
    group([frow('По умолчанию, г', dg), frow('Единица', un), frow('Грамм в ней', ug)], 'Подставляется, когда вес не указан.'));

  function collect() {
    const n = name.value.trim();
    if (!n) { toast('Введите название', 'error'); name.focus(); return null; }
    const k = numVal(kcal);
    if (k === null || k < 0 || k > 900) { toast('Ккал на 100 г: число от 0 до 900', 'error'); kcal.focus(); return null; }
    const macros = [numVal(pr), numVal(fat), numVal(carb)];
    if (macros.some((m) => m !== null && (m < 0 || m > 100))) { toast('Белки, жиры и углеводы на 100 г: от 0 до 100', 'error'); return null; }
    if (macros.reduce((a, m) => a + (m || 0), 0) > 100.5) { toast('Сумма Б+Ж+У не может быть больше 100 г', 'error'); return null; }
    const grams = (inp) => { const v = numVal(inp); return v && v > 0 ? Math.round(v) : null; };
    return {
      name: n, brand: brand.value.trim() || null, kcal: Math.round(k),
      protein: macros[0] ?? 0, fat: macros[1] ?? 0, carbs: macros[2] ?? 0,
      default_grams: grams(dg), unit_name: un.value.trim() || null, unit_grams: grams(ug),
    };
  }
  return { el, collect };
}

export default async function render(root, ctx) {
  const isNew = ctx.params.id === 'new';
  const view = h('div', { class: 'screen' });
  root.append(view);
  let product = {};
  if (!isNew) {
    try {
      const all = (await api.get('/products')).products;
      product = all.find((x) => String(x.id) === ctx.params.id);
      if (!product) throw new Error('Продукт не найден');
    } catch (e) { const b = h('div'); view.append(pageHead('Продукт', { parent: '/menu/products' }), b); errorState(b, e); return; }
  }
  if (!ctx.alive()) return;
  const form = productForm(product);
  let busy = false;
  async function save() {
    if (busy) return;
    const data = form.collect();
    if (!data) return;
    busy = true; mainButton.setLoading(true);
    try {
      if (isNew) await api.post('/products', data); else await api.patch('/products/' + product.id, data);
      haptic('success'); toast('Продукт сохранён', 'ok'); mainButton.hide(); nav.replace('/menu/products');
    } catch (e) { busy = false; mainButton.setLoading(false); toast(e.message, 'error'); }
  }
  async function remove() {
    if (!(await confirmDialog(`Удалить «${product.name}»?`))) return;
    try { await api.del('/products/' + product.id); haptic('success'); toast('Удалено'); mainButton.hide(); nav.replace('/menu/products'); } catch (e) { toast(e.message, 'error'); }
  }
  view.append(...[pageHead(isNew ? 'Новый продукт' : 'Продукт', { parent: '/menu/products' }),
    h('form', { class: 'stack', onsubmit: (e) => { e.preventDefault(); save(); } }, form.el, h('button', { type: 'submit', hidden: true })),
    isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, 'Удалить продукт'),
    !isNew && product.times_used ? h('div', { class: 'muted small center' }, `Использован в приёмах: ${product.times_used}`) : null].filter(Boolean));
  mainButton.show('Сохранить', save);
}
