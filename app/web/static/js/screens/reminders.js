// Напоминания: сообщения бота (включить, выбрать время) и свои напоминания по шаблонам.
import { api } from '../api.js';
import { h, icon, tile, toast, haptic, pageHead, sectionTitle, toggle, errorState, skeletonCards, sheet, frow, group, field, confirmDialog } from '../ui.js';
import { t } from '../i18n.js';

// Иконки и цвета шаблонов; неизвестный ключ получает колокольчик.
const TPL_UI = {
  breakfast: ['coffee', 'orange'], lunch: ['tools-kitchen', 'orange'], dinner: ['tools-kitchen-2', 'indigo'], snack: ['apple', 'red'],
  water: ['droplet', 'teal'], steps: ['shoe', 'green'], sleep: ['bed', 'indigo'], weigh: ['scale', 'purple'], walk: ['walk', 'green'],
  vitamins: ['flask', 'pink'], bedtime: ['moon', 'indigo'], screen_off: ['device-mobile', 'gray'], custom: ['bell', 'blue'],
};
const tplTile = (key, size = 34) => tile(...(TPL_UI[key] || TPL_UI.custom), size);
const SKIP_NOTE = new Set(['breakfast', 'lunch', 'dinner', 'water', 'steps', 'sleep', 'weigh']);

const EVERY = [[30, t('rem.e30'), t('rem.e30s')], [60, t('rem.e60'), t('rem.e60s')], [90, t('rem.e90'), t('rem.e90s')],
  [120, t('rem.e120'), t('rem.e120s')], [180, t('rem.e180'), t('rem.e180s')], [240, t('rem.e240'), t('rem.e240s')]];
const DAY_NAMES = t('rem.dn').split(',');
const DAY_LABELS = t('stats.dow').split(',');
const PRESETS = [[t('rem.p_all'), '0123456'], [t('rem.p_wd'), '01234'], [t('rem.p_we'), '56']];

const allDays = (d) => (d || '0123456');
function daysText(d) {
  const v = [...new Set(allDays(d).split(''))].sort().join('');
  if (v === '0123456') return t('rem.d_all');
  if (v === '01234') return t('rem.d_wd');
  if (v === '56') return t('rem.d_we');
  return v.split('').map((x) => DAY_NAMES[+x]).join(', ');
}
function whenText(r) {
  if (r.every_min) {
    const e = EVERY.find((x) => x[0] === r.every_min);
    return `${e ? e[2] : t('rem.every_n', { n: r.every_min })}, ${r.time}–${r.until_time}`;
  }
  return r.time;
}

// Время как «таблетка»: видна подпись, а настоящий input лежит сверху невидимым. У строки нет собственной
// ширины input[type=time], которая в iOS растягивала список и обрезала его справа.
function timePill(value, onChange, label, { disabled = false, fixed = false } = {}) {
  const txt = h('span', { class: 'time-txt' }, value);
  const input = fixed ? null : h('input', { type: 'time', value, 'aria-label': label, disabled, onchange: () => { if (input.value) { txt.textContent = input.value; onChange(input.value); } } });
  const pill = h('label', { class: 'time-pill' + (fixed ? ' fixed' : '') + (disabled ? ' off' : '') }, txt, input);
  pill.setDisabled = (off) => { pill.classList.toggle('off', off); if (input) input.disabled = off; };
  return pill;
}

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const box = h('div', { class: 'stack' }, skeletonCards(3, 64));
  const plus = h('button', { class: 'icon-btn accent', type: 'button', 'aria-label': t('rem.add'), onclick: () => openTemplates() }, icon('plus'));
  view.append(pageHead(t('rem.title'), { parent: '/menu', right: plus }), box);
  root.append(view);
  let state;
  try { state = await api.get('/reminders'); } catch (e) { if (ctx.alive()) errorState(box, e, () => { root.replaceChildren(); render(root, ctx); }); return; }
  if (!ctx.alive()) return;

  const adopt = (data) => { state = { ...state, ...data }; };

  async function saveSystem(kind, body, undo) {
    try { await api.put('/reminders/' + kind, body); haptic('light'); } catch (e) { toast(e.message, 'error'); if (undo) undo(); }
  }

  function systemRow(r) {
    const pill = timePill(r.time, (v) => saveSystem(r.kind, { time: v }), t('rem.time_of', { n: r.title }), { disabled: !r.enabled, fixed: !!r.fixed_time });
    const sw = toggle(r.enabled, (on) => {
      pill.setDisabled(!on);
      saveSystem(r.kind, { enabled: on }, () => { sw.querySelector('input').checked = !on; pill.setDisabled(on); });
    }, r.title);
    return h('div', { class: 'row static' },
      h('span', { class: 'grow' }, h('span', { class: 'row-title' }, r.title), h('span', { class: 'row-sub' }, r.sub)),
      pill, sw);
  }

  function customRow(r) {
    const sw = toggle(r.enabled, async (on) => {
      try { adopt(await api.patch('/user-reminders/' + r.id, { enabled: on })); haptic('light'); } catch (e) { toast(e.message, 'error'); sw.querySelector('input').checked = !on; }
    }, r.title);
    return h('div', { class: 'row static' },
      h('button', { class: 'rem-main', type: 'button', onclick: () => openEditor(state.custom.find((x) => x.id === r.id) || r) },
        tplTile(r.template), h('span', { class: 'grow' }, h('span', { class: 'row-title' }, r.title),
          h('span', { class: 'row-sub wrap' }, `${whenText(r)} · ${daysText(r.days)}`))),
      sw);
  }

  function draw() {
    const custom = state.custom || [];
    box.replaceChildren(
      sectionTitle(t('rem.bot')),
      h('div', { class: 'list rem-list' }, state.reminders.map(systemRow)),
      h('div', { class: 'footnote' }, t('rem.bot_note')),
      sectionTitle(t('rem.mine')),
      custom.length
        ? h('div', { class: 'list rem-list' }, custom.map(customRow))
        : h('div', { class: 'footnote' }, t('rem.empty')),
      h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: () => openTemplates() }, icon('plus', 20), t('rem.add')));
  }

  function openTemplates() {
    const tpls = (state.templates || []).filter((x) => x.available);
    const pick = (x) => { s.close(); openEditor(null, x); };
    const rows = tpls.map((x) => h('button', { class: 'row', type: 'button', onclick: () => pick(x) },
      tplTile(x.key), h('span', { class: 'grow' }, h('span', { class: 'row-title' }, x.title), h('span', { class: 'row-sub' }, whenText(x))), icon('next', 18)));
    rows.push(h('button', { class: 'row', type: 'button', onclick: () => pick(null) },
      tplTile('custom'), h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t('rem.custom')), h('span', { class: 'row-sub' }, t('rem.custom_sub'))), icon('next', 18)));
    const s = sheet({ title: t('rem.new'), content: h('div', { class: 'list rem-pick' }, rows) });
  }

  // Форма: из шаблона (t), правка существующего (r) или пустая «своё».
  function openEditor(r, tp) {
    const isNew = !r;
    const cur = r || { template: tp ? tp.key : 'custom', title: tp ? tp.title : '', text: tp ? tp.text : '', time: tp ? tp.time : '09:00', days: '', every_min: tp ? tp.every_min : null, until_time: tp ? tp.until_time : null };
    let days = new Set(allDays(cur.days).split(''));
    let repeat = !!cur.every_min;
    let busy = false;

    const title = h('input', { type: 'text', value: cur.title, maxLength: 60, placeholder: t('rem.ph_title'), autocomplete: 'off', 'aria-label': t('rem.name') });
    const text = h('textarea', { rows: 3, maxLength: 300, placeholder: t('rem.ph_text'), 'aria-label': t('rem.text') });
    text.value = cur.text || '';
    const time = h('input', { type: 'time', value: cur.time, 'aria-label': t('rem.time') });
    const every = h('select', { 'aria-label': t('rem.rep_every_aria') }, EVERY.map(([v, label]) => h('option', { value: String(v), selected: v === (cur.every_min || 120) }, label)));
    const until = h('input', { type: 'time', value: cur.until_time || '20:00', 'aria-label': t('rem.rep_until_aria') });
    const repeatBox = h('div', { class: 'fgroup' });
    const drawRepeat = () => repeatBox.replaceChildren(...(repeat ? [group([frow(t('rem.rep_every'), every), frow(t('rem.rep_until'), until)])] : []));
    const sw = toggle(repeat, (on) => { repeat = on; drawRepeat(); }, t('rem.rep_day'));
    drawRepeat();

    const dayBtns = DAY_LABELS.map((lab, i) => h('button', { class: 'chip', type: 'button', 'aria-pressed': 'false', onclick: () => {
      const k = String(i);
      if (days.has(k)) days.delete(k); else days.add(k);
      haptic('selection'); paintDays();
    } }, lab));
    const presetBtns = PRESETS.map(([lab, v]) => h('button', { class: 'chip', type: 'button', onclick: () => { days = new Set(v.split('')); haptic('selection'); paintDays(); } }, lab));
    function paintDays() {
      dayBtns.forEach((b, i) => { const on = days.has(String(i)); b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
      const now = [...days].sort().join('');
      presetBtns.forEach((b, i) => b.classList.toggle('active', now === PRESETS[i][1]));
    }
    paintDays();

    function collect() {
      const t_ = title.value.trim(), x = text.value.trim();
      if (!t_) return [t('rem.v_title'), title];
      if (!x) return [t('rem.v_text'), text];
      if (!time.value) return [t('rem.v_time'), time];
      if (!days.size) return [t('rem.v_days'), null];
      const body = { title: t_, text: x, time: time.value, days: [...days].sort().join(''), every_min: null, until_time: null };
      if (repeat) {
        if (!until.value) return [t('rem.v_until'), until];
        if (until.value <= time.value) return [t('rem.v_order'), until];
        body.every_min = +every.value;
        body.until_time = until.value;
      }
      if (isNew && cur.template !== 'custom') body.template = cur.template;
      return [null, null, body];
    }
    async function save() {
      if (busy) return;
      const [msg, el, body] = collect();
      if (msg) { toast(msg, 'error'); if (el) el.focus(); return; }
      busy = true; saveBtn.classList.add('loading');
      try {
        adopt(isNew ? await api.post('/user-reminders', body) : await api.patch('/user-reminders/' + r.id, body));
        haptic('success'); s.close(); draw(); toast(isNew ? t('rem.added') : t('common.saved'), 'ok');
      } catch (e) { busy = false; saveBtn.classList.remove('loading'); toast(e.message, 'error'); }
    }
    async function remove() {
      if (busy || !(await confirmDialog(t('rem.del_q', { n: r.title })))) return;
      busy = true;
      try { adopt(await api.del('/user-reminders/' + r.id)); haptic('success'); s.close(); draw(); toast(t('rem.deleted')); } catch (e) { busy = false; toast(e.message, 'error'); }
    }

    const saveBtn = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, isNew ? t('common.add') : t('common.save'));
    const form = h('form', { class: 'stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      field(t('rem.name'), title),
      field(t('rem.text'), text, SKIP_NOTE.has(cur.template) ? t('rem.skip_note') : t('rem.chat_note')),
      group([frow(t('rem.time'), time)]),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, t('rem.weekdays')),
        h('div', { class: 'day-chips' }, dayBtns), h('div', { class: 'chips wrap' }, presetBtns)),
      group([h('div', { class: 'frow' }, h('span', { class: 'frow-label' }, t('rem.rep')), sw)]),
      repeatBox,
      saveBtn,
      isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, t('common.delete')));
    const s = sheet({ title: isNew ? (tp ? tp.title : t('rem.custom')) : t('rem.one'), content: form });
  }

  draw();
}
