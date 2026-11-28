// Upload a screenshot (sleep / steps / scales) -> parsed metrics -> optional confirm
import { api } from '../api.js';
import { h, icon, toast, haptic, nav, pageHead, compressImage, busyOverlay, fmtN, fmtDur, shortDate, todayISO } from '../ui.js';
import { t } from '../i18n.js';

// Keys follow app/services/screens.py METRIC_RANGES (the backend's flat metrics).
const n1 = (v) => fmtN(v);
const METRICS = {
  steps: [t('ms.steps'), n1],
  distance_km: [t('ms.distance_km'), n1],
  active_kcal: [t('ms.active_kcal'), n1],
  active_minutes: [t('ms.active_minutes'), n1],
  avg_hr: [t('ms.avg_hr'), n1],
  resting_hr: [t('ms.resting_hr'), n1],
  sleep_duration_min: [t('ms.sleep_duration_min'), fmtDur],
  sleep_deep_min: [t('ms.sleep_deep_min'), fmtDur],
  sleep_light_min: [t('ms.sleep_light_min'), fmtDur],
  sleep_rem_min: [t('ms.rem'), fmtDur],
  sleep_awake_min: [t('ms.sleep_awake_min'), fmtDur],
  sleep_score: [t('ms.sleep_score'), n1],
  weight_kg: [t('ms.weight_kg'), n1],
  bmi: [t('ms.bmi'), n1],
  body_fat_pct: [t('ms.body_fat_pct'), n1],
  fat_mass_kg: [t('ms.fat_mass_kg'), n1],
  muscle_mass_kg: [t('ms.muscle_mass_kg'), n1],
  muscle_mass_pct: [t('ms.muscle_mass_pct'), n1],
  skeletal_muscle_pct: [t('ms.skeletal_muscle_pct'), n1],
  body_water_pct: [t('ms.body_water_pct'), n1],
  bone_mass_kg: [t('ms.bone_mass_kg'), n1],
  protein_pct: [t('ms.protein_pct'), n1],
  visceral_fat: [t('ms.visceral_fat'), n1],
  bmr_kcal: [t('ms.bmr_kcal'), n1],
  metabolic_age: [t('ms.metabolic_age'), n1],
  body_tone: [t('ms.body_tone'), n1],
  body_score: [t('ms.body_score'), n1],
};

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen' });
  const out = h('div', { class: 'stack' });
  let previewUrl;
  let stop;
  const input = h('input', { type: 'file', accept: 'image/*', hidden: true, onchange: onPick });
  const pick = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: () => input.click() }, icon('image', 20), t('shot.pick'));
  view.append(pageHead(t('shot.title'), { parent: '/today' }), h('div', { class: 'stack' },
    h('div', { class: 'muted' }, t('shot.hint')),
    pick,
    out, input));
  root.append(view);

  async function onPick() {
    const f = input.files[0];
    input.value = '';
    if (!f || pick.disabled) return;
    pick.disabled = true;
    try {
      const file = await compressImage(f, 2000, 0.9);
      if (!ctx.alive()) return;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      out.replaceChildren(h('img', { class: 'preview', src: previewUrl, alt: t('shot.alt') }));
      const fd = new FormData();
      fd.append('photo', file, file.name || 'screen.jpg');
      stop = busyOverlay(t('shot.reading'), t('lbl.sub_one'));
      const r = await api.post('/screens', fd, { timeout: 90000 });
      if (r.applied) api._me = null;
      if (ctx.alive()) result(r, previewUrl);
    } catch (e) {
      if (ctx.alive()) toast(e, 'error');
    } finally {
      stop?.();
      stop = null;
      pick.disabled = false;
    }
  }

  function result(r, url) {
    const rows = Object.entries(r.metrics || {}).filter(([k, v]) => METRICS[k] && v !== null && v !== undefined).map(([k, v]) => {   // неизвестные ключи бэкенда не показываем
      const m = METRICS[k];
      return h('div', { class: 'row static' }, h('span', { class: 'grow' }, m[0]), h('b', null, m[1](v)));
    });
    const reasons = Array.isArray(r.reasons) ? r.reasons.filter(Boolean) : [];
    const actions = h('div', { class: 'stack-sm' });
    const status = h('div', { class: 'card status' });
    out.replaceChildren(h('img', { class: 'preview', src: url, alt: t('shot.alt') }),
      h('div', { class: 'muted small' }, t('shot.recognized', { d: shortDate(r.date || todayISO()), p: Math.round((r.confidence || 0) * 100) })),
      rows.length ? h('div', { class: 'list' }, rows) : h('div', { class: 'card muted' }, t('shot.none')),
      status, actions);

    if (r.applied) {
      haptic('success');
      status.replaceChildren(icon('check', 20), t('shot.saved'));
      status.classList.add('ok');
      actions.append(h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: () => nav.replace('/today') }, t('common.done')));
    } else if (r.needs_confirm) {
      status.textContent = t('shot.confirm_text');
      if (reasons.length) status.append(h('ul', { class: 'notes' }, reasons.map((x) => h('li', null, x))));
      let sending = false;
      const confirm = async (apply) => {
        if (sending) return;                          // двойной тап: один запрос
        sending = true;
        btnOk.disabled = btnNo.disabled = true;
        try {
          await api.post('/screens/' + r.id + '/confirm', { apply });
          if (apply) api._me = null;
          if (!ctx.alive()) return;
          haptic('success');
          toast(apply ? t('shot.saved') : t('shot.cancelled'), 'ok');
          nav.replace('/today');
        } catch (e) {
          if (ctx.alive()) toast(e, 'error');
          sending = false;
          btnOk.disabled = btnNo.disabled = false;
        }
      };
      const btnOk = h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: () => confirm(true) }, icon('check', 20), t('common.confirm'));
      const btnNo = h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: () => confirm(false) }, t('common.cancel'));
      actions.append(btnOk, btnNo);
    } else {
      status.textContent = r.screen === 'unknown' ? t('shot.unknown') : t('shot.not_applied');
    }
  }
  return () => {
    stop?.();
    stop = null;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  };
}
