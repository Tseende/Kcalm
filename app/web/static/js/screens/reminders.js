// Напоминания: сообщения бота (включить, выбрать время) и свои напоминания по шаблонам.
import { api } from '../api.js';
import { h, icon, tile, toast, haptic, pageHead, sectionTitle, toggle, errorState, skeletonCards, sheet, frow, group, field, confirmDialog } from '../ui.js';

// Иконки и цвета шаблонов; неизвестный ключ получает колокольчик.
const TPL_UI = {
  breakfast: ['coffee', 'orange'], lunch: ['tools-kitchen', 'orange'], dinner: ['tools-kitchen-2', 'indigo'], snack: ['apple', 'red'],
  water: ['droplet', 'teal'], steps: ['shoe', 'green'], sleep: ['bed', 'indigo'], weigh: ['scale', 'purple'], walk: ['walk', 'green'],
  vitamins: ['flask', 'pink'], bedtime: ['moon', 'indigo'], screen_off: ['device-mobile', 'gray'], custom: ['bell', 'blue'],
};
const tplTile = (key, size = 34) => tile(...(TPL_UI[key] || TPL_UI.custom), size);
const SKIP_NOTE = new Set(['breakfast', 'lunch', 'dinner', 'water', 'steps', 'sleep', 'weigh']);

const EVERY = [[30, '30 минут', 'каждые 30 мин'], [60, '1 час', 'каждый час'], [90, '1,5 часа', 'каждые 1,5 ч'],
  [120, '2 часа', 'каждые 2 ч'], [180, '3 часа', 'каждые 3 ч'], [240, '4 часа', 'каждые 4 ч']];
const DAY_NAMES = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
const DAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const PRESETS = [['Каждый день', '0123456'], ['Будни', '01234'], ['Выходные', '56']];

const allDays = (d) => (d || '0123456');
function daysText(d) {
  const v = [...new Set(allDays(d).split(''))].sort().join('');
  if (v === '0123456') return 'каждый день';
  if (v === '01234') return 'по будням';
  if (v === '56') return 'по выходным';
  return v.split('').map((x) => DAY_NAMES[+x]).join(', ');
}
function whenText(r) {
  if (r.every_min) {
    const e = EVERY.find((x) => x[0] === r.every_min);
    return `${e ? e[2] : 'каждые ' + r.every_min + ' мин'}, ${r.time}–${r.until_time}`;
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
  const plus = h('button', { class: 'icon-btn accent', type: 'button', 'aria-label': 'Добавить напоминание', onclick: () => openTemplates() }, icon('plus'));
  view.append(pageHead('Напоминания', { parent: '/menu', right: plus }), box);
  root.append(view);
  let state;
  try { state = await api.get('/reminders'); } catch (e) { if (ctx.alive()) errorState(box, e, () => { root.replaceChildren(); render(root, ctx); }); return; }
  if (!ctx.alive()) return;

  const adopt = (data) => { state = { ...state, ...data }; };

  async function saveSystem(kind, body, undo) {
    try { await api.put('/reminders/' + kind, body); haptic('light'); } catch (e) { toast(e.message, 'error'); if (undo) undo(); }
  }

  function systemRow(r) {
    const pill = timePill(r.time, (v) => saveSystem(r.kind, { time: v }), `Время: ${r.title}`, { disabled: !r.enabled, fixed: !!r.fixed_time });
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
      sectionTitle('Сообщения бота'),
      h('div', { class: 'list rem-list' }, state.reminders.map(systemRow)),
      h('div', { class: 'footnote' }, 'Сообщения приходят в чат с ботом в выбранное время. «Кухня закрывается» двигается вместе с настройкой «Не есть после» в профиле.'),
      sectionTitle('Мои напоминания'),
      custom.length
        ? h('div', { class: 'list rem-list' }, custom.map(customRow))
        : h('div', { class: 'footnote' }, 'Пока пусто. Выберите шаблон: завтрак, вода, шаги, сон, прогулка, или создайте своё.'),
      h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: () => openTemplates() }, icon('plus', 20), 'Добавить напоминание'));
  }

  function openTemplates() {
    const tpls = (state.templates || []).filter((t) => t.available);
    const pick = (t) => { s.close(); openEditor(null, t); };
    const rows = tpls.map((t) => h('button', { class: 'row', type: 'button', onclick: () => pick(t) },
      tplTile(t.key), h('span', { class: 'grow' }, h('span', { class: 'row-title' }, t.title), h('span', { class: 'row-sub' }, whenText(t))), icon('next', 18)));
    rows.push(h('button', { class: 'row', type: 'button', onclick: () => pick(null) },
      tplTile('custom'), h('span', { class: 'grow' }, h('span', { class: 'row-title' }, 'Своё напоминание'), h('span', { class: 'row-sub' }, 'Название, текст и время на ваш выбор')), icon('next', 18)));
    const s = sheet({ title: 'Новое напоминание', content: h('div', { class: 'list rem-pick' }, rows) });
  }

  // Форма: из шаблона (t), правка существующего (r) или пустая «своё».
  function openEditor(r, t) {
    const isNew = !r;
    const cur = r || { template: t ? t.key : 'custom', title: t ? t.title : '', text: t ? t.text : '', time: t ? t.time : '09:00', days: '', every_min: t ? t.every_min : null, until_time: t ? t.until_time : null };
    let days = new Set(allDays(cur.days).split(''));
    let repeat = !!cur.every_min;
    let busy = false;

    const title = h('input', { type: 'text', value: cur.title, maxLength: 60, placeholder: 'Например: растяжка', autocomplete: 'off', 'aria-label': 'Название' });
    const text = h('textarea', { rows: 3, maxLength: 300, placeholder: 'Что бот напишет в чат', 'aria-label': 'Текст сообщения' });
    text.value = cur.text || '';
    const time = h('input', { type: 'time', value: cur.time, 'aria-label': 'Время' });
    const every = h('select', { 'aria-label': 'Повторять каждые' }, EVERY.map(([v, label]) => h('option', { value: String(v), selected: v === (cur.every_min || 120) }, label)));
    const until = h('input', { type: 'time', value: cur.until_time || '20:00', 'aria-label': 'Повторять до' });
    const repeatBox = h('div', { class: 'fgroup' });
    const drawRepeat = () => repeatBox.replaceChildren(...(repeat ? [group([frow('Каждые', every), frow('До', until)])] : []));
    const sw = toggle(repeat, (on) => { repeat = on; drawRepeat(); }, 'Повторять в течение дня');
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
      if (!t_) return ['Напишите название напоминания.', title];
      if (!x) return ['Напишите текст сообщения.', text];
      if (!time.value) return ['Выберите время.', time];
      if (!days.size) return ['Выберите хотя бы один день.', null];
      const body = { title: t_, text: x, time: time.value, days: [...days].sort().join(''), every_min: null, until_time: null };
      if (repeat) {
        if (!until.value) return ['Укажите, до какого времени повторять.', until];
        if (until.value <= time.value) return ['Повтор «до» должен быть позже времени начала.', until];
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
        haptic('success'); s.close(); draw(); toast(isNew ? 'Напоминание добавлено' : 'Сохранено', 'ok');
      } catch (e) { busy = false; saveBtn.classList.remove('loading'); toast(e.message, 'error'); }
    }
    async function remove() {
      if (busy || !(await confirmDialog(`Удалить напоминание «${r.title}»?`))) return;
      busy = true;
      try { adopt(await api.del('/user-reminders/' + r.id)); haptic('success'); s.close(); draw(); toast('Напоминание удалено'); } catch (e) { busy = false; toast(e.message, 'error'); }
    }

    const saveBtn = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, isNew ? 'Добавить' : 'Сохранить');
    const form = h('form', { class: 'stack', novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      field('Название', title),
      field('Текст сообщения', text, SKIP_NOTE.has(cur.template) ? 'Если сегодня уже всё записано, напоминание не придёт.' : 'Придёт в чат с ботом.'),
      group([frow('Время', time)]),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Дни недели'),
        h('div', { class: 'day-chips' }, dayBtns), h('div', { class: 'chips wrap' }, presetBtns)),
      group([h('div', { class: 'frow' }, h('span', { class: 'frow-label' }, 'Повторять'), sw)]),
      repeatBox,
      saveBtn,
      isNew ? null : h('button', { class: 'btn btn-danger-ghost btn-block', type: 'button', onclick: remove }, 'Удалить'));
    const s = sheet({ title: isNew ? (t ? t.title : 'Своё напоминание') : 'Напоминание', content: form });
  }

  draw();
}
