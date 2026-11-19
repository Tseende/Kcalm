import { api } from './api.js';
import { t } from './i18n.js';
import { addDays, frow, group, h, sheet, toast, todayISO } from './ui.js';

export async function pickMealDateTime({ date, time, timeRequired = false, save, onClose, alive }) {
  const me = await api.me().catch(() => null);
  if (!alive()) return;
  const tz = me?.user?.tz;
  const today = tz ? new Intl.DateTimeFormat('sv-SE', { timeZone: tz }).format(new Date()) : todayISO();
  const dateInput = h('input', { type: 'date', value: date || today, max: today, required: true });
  const timeInput = h('input', { type: 'time', value: time || '', required: timeRequired });
  const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, t('common.save'));
  const content = h('form', { class: 'stack', onsubmit: async (e) => {
    e.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    try {
      await save({ date: dateInput.value, ...(timeInput.value ? { time: timeInput.value } : {}) });
      picker.close();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      submit.disabled = false;
    }
  } },
  h('div', { class: 'chips wrap' }, [[today, t('date.today')], [addDays(today, -1), t('date.yesterday')]].map(([day, label]) =>
    h('button', { class: 'chip', type: 'button', onclick: () => { dateInput.value = day; } }, label))),
  group([frow(t('meal.date_label'), dateInput), frow(t('meal.time_label'), timeInput)]), submit);
  const picker = sheet({ title: t('meal.time_title'), content, onClose });
}
