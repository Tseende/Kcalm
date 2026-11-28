// Voice dictation into text fields: mic button -> MediaRecorder -> POST /transcribe -> text lands in the field.
import { api } from './api.js';
import { h, icon, toast, haptic } from './ui.js';
import { t } from './i18n.js';

const MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
const MAX_MS = 60000;
const MIN_MS = 500;
let active = null;                                    // only one recording at a time, globally

export const voiceSupported = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

const pickMime = () => {
  try { return MIMES.find((m) => MediaRecorder.isTypeSupported(m)) || ''; } catch (e) { return ''; }
};
const fileFor = (type) => (/mp4|m4a|aac/.test(type) ? 'voice.m4a' : /ogg/.test(type) ? 'voice.ogg' : 'voice.webm');
const clock = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

function appendTo(target, text) {
  const v = target.value;
  const sep = !v || /\s$/.test(v) ? '' : /[.!?,;:]$/.test(v) ? ' ' : ', ';
  target.value = v + sep + text;
  target.dispatchEvent(new Event('input', { bubbles: true }));   // draft / sync handlers run as for typed text
}

// target: textarea or input. onText(text) replaces the default "append to target". Returns null when unsupported.
export function voiceButton(target, { onText, label, inline = false } = {}) {
  if (!voiceSupported()) return null;
  let state = 'idle';                                 // idle | rec | busy
  let rec = null;
  let stream = null;
  let chunks = [];
  let startedAt = 0;
  let tick = null;
  let limit = null;

  const time = h('span', { class: 'mic-time', hidden: true }, '0:00');
  const btn = h('button', { class: 'mic-btn', type: 'button', onclick: toggle });
  const box = h('span', { class: 'mic-box' + (inline ? ' inline' : '') }, time, btn);
  const aria = label || t('voice.dictate');

  function paint() {
    btn.className = 'mic-btn' + (state === 'rec' ? ' rec' : '') + (state === 'busy' ? ' busy' : '');
    btn.disabled = state === 'busy';
    btn.setAttribute('aria-label', state === 'rec' ? t('voice.stop') : state === 'busy' ? t('voice.busy') : aria);
    btn.setAttribute('aria-pressed', String(state === 'rec'));
    btn.replaceChildren(state === 'busy' ? h('span', { class: 'mic-spin' }) : icon(state === 'rec' ? 'player-stop' : 'microphone', 18));
    time.hidden = state !== 'rec';
  }

  function release() {
    clearInterval(tick);
    clearTimeout(limit);
    if (stream) stream.getTracks().forEach((tr) => tr.stop());
    stream = null;
    if (active === box) active = null;
  }

  async function start() {
    if (active || state !== 'idle') return;           // someone else is recording
    active = box;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) { // denied or no device
      release();
      toast(t('voice.no_mic'), 'error');
      return;
    }
    try {
      const mime = pickMime();
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch (e) {
      release();
      toast(t('voice.fail'), 'error');
      return;
    }
    chunks = [];
    rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) chunks.push(ev.data); };
    rec.onstop = finish;
    rec.start();
    startedAt = Date.now();
    state = 'rec';
    time.textContent = '0:00';
    tick = setInterval(() => { time.textContent = clock(Date.now() - startedAt); }, 250);
    limit = setTimeout(stop, MAX_MS);
    haptic('medium');
    paint();
  }

  function stop() {
    if (state !== 'rec' || !rec) return;
    haptic('light');
    try { rec.stop(); } catch (e) { finish(); }
  }

  async function finish() {
    const ms = Date.now() - startedAt;
    const type = (rec && rec.mimeType) || (chunks[0] && chunks[0].type) || 'audio/webm';
    release();
    rec = null;
    const blob = new Blob(chunks, { type });
    chunks = [];
    if (ms < MIN_MS || !blob.size) {
      state = 'idle';
      paint();
      toast(t('voice.short'));
      return;
    }
    state = 'busy';
    paint();
    try {
      const fd = new FormData();
      fd.append('audio', blob, fileFor(type));
      fd.append('mime', type.split(';')[0]);
      const res = await api.post('/transcribe', fd, { timeout: 75000 });
      const text = ((res && res.text) || '').trim();
      if (!text) toast(t('voice.empty'));
      else {
        if (onText) onText(text); else appendTo(target, text);
        haptic('success');
      }
    } catch (e) {
      haptic('error');
      toast(e && (e.status === 429 || e.status === 413) ? e.message : t('voice.fail'), 'error');
    } finally {
      state = 'idle';
      paint();
    }
  }

  function toggle() {
    if (state === 'rec') stop();
    else if (state === 'idle') start();
  }

  paint();
  return box;
}
