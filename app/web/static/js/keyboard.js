// Keep focused fields visible without rebuilding the page under the iOS keyboard.
import { mainButton } from './ui.js';
import { tg, inTelegram } from './api.js';

const TEXTUAL = new Set(['text', 'number', 'search', 'tel', 'email', 'url', 'password', 'time', 'date', '']);
const isField = (el) => !!el && ((el.tagName === 'TEXTAREA') || (el.tagName === 'INPUT' && TEXTUAL.has((el.getAttribute('type') || '').toLowerCase())) || el.isContentEditable);

// Scroll the containing dialog/list first. Scrolling window cannot reveal a field
// inside a sheet while body is locked, and makes the underlying page jump instead.
function moveField(el, delta) {
  let rest = delta;
  for (let parent = el.parentElement; parent && parent !== document.body && parent !== document.documentElement; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    if (!/(auto|scroll)/.test(style.overflowY) || parent.scrollHeight <= parent.clientHeight) continue;
    const before = parent.scrollTop;
    parent.scrollTop = Math.max(0, Math.min(parent.scrollHeight - parent.clientHeight, before + rest));
    rest -= parent.scrollTop - before;
    if (Math.abs(rest) < 1) return;
  }
  if (Math.abs(rest) >= 1 && !document.body.classList.contains('sheet-open')) window.scrollBy(0, rest);
}

export function initKeyboard() {
  const root = document.documentElement;
  const body = document.body;
  const vv = window.visualViewport;
  let focused = false;
  let open = false;
  let baseH = window.innerHeight;
  let timer = 0;

  function viewport() {
    const top = vv ? Math.max(0, vv.offsetTop) : 0;
    const telegramH = inTelegram && Number(tg?.viewportHeight) > 0 ? Number(tg.viewportHeight) : window.innerHeight;
    const bottom = Math.min(window.innerHeight, vv ? top + vv.height : window.innerHeight, telegramH);
    return { top, bottom, height: Math.max(0, bottom - top), lift: Math.max(0, window.innerHeight - bottom) };
  }

  function reveal(delay = 120) {
    clearTimeout(timer);
    const el = document.activeElement;
    if (!isField(el)) return;
    timer = setTimeout(() => {
      if (document.activeElement !== el || !el.isConnected) return;
      const view = viewport();
      const sheet = el.closest('.sheet');
      let top = view.top + 12;
      let bottom = view.bottom - 12;
      if (sheet) {
        const bounds = sheet.getBoundingClientRect();
        top = Math.max(top, bounds.top + 12);
        bottom = Math.min(bottom, bounds.bottom - 12);
      } else {
        const safe = parseFloat(getComputedStyle(body).paddingTop) || 0;
        top = Math.max(top, safe + 8);
        const head = el.closest('.screen')?.querySelector('.page-head');
        if (head) top = Math.max(top, head.getBoundingClientRect().bottom + 8);
        const bar = document.getElementById('kbbar');
        if (open && bar && !bar.hidden) bottom = Math.min(bottom, bar.getBoundingClientRect().top - 12);
      }
      if (bottom <= top) return; // Ignore intermediate geometry during a keyboard animation.
      const rect = el.getBoundingClientRect();
      // A tall textarea cannot fit in the visible area. Align its top rather than
      // its bottom: bottom-alignment is what used to send the input above the screen.
      if (rect.height > bottom - top) {
        if (rect.top < top || rect.top >= bottom) moveField(el, rect.top - top);
      } else if (rect.bottom > bottom) moveField(el, Math.ceil(rect.bottom - bottom));
      else if (rect.top < top) moveField(el, Math.floor(rect.top - top));
    }, delay);
  }

  function update() {
    focused = isField(document.activeElement) && document.activeElement.isConnected;
    body.classList.toggle('kb-focus', focused);
    const view = viewport();
    // When Android resizes innerHeight, lift can be zero; the baseline still
    // detects its keyboard. On iOS VisualViewport or Telegram provides the inset.
    const zoomed = vv && vv.scale > 1.05;
    const next = focused && !zoomed && (baseH - view.height > 120 || view.lift > 80);
    if (!focused && !open) baseH = Math.max(baseH, window.innerHeight);
    root.style.setProperty('--kb-h', (next ? Math.round(view.lift) : 0) + 'px');
    root.style.setProperty('--kb-visible-h', Math.round(view.height) + 'px');
    if (next !== open) {
      open = next;
      body.classList.toggle('kb-open', open);
      if (open) mainButton.suspend(); else mainButton.resume();
    }
  }

  document.addEventListener('focusin', (e) => {
    if (!isField(e.target)) return;
    focused = true;
    body.classList.add('kb-focus');
    // Do not hide sibling blocks, grow the textarea, or reset scroll here.
    // Safari is already panning to the focused field; changing its layout races it.
    update();
    reveal(320);
  });
  document.addEventListener('focusout', () => {
    setTimeout(() => {
      if (isField(document.activeElement)) return;
      focused = false;
      clearTimeout(timer);
      body.classList.remove('kb-focus');
      update();
      mainButton.resume();
    }, 60);
  });
  const resized = () => { update(); if (focused) reveal(); };
  if (vv) {
    vv.addEventListener('resize', resized);
    vv.addEventListener('scroll', resized);
  }
  window.addEventListener('resize', resized);
  window.addEventListener('orientationchange', () => {
    // Do not use the keyboard-reduced height as the new full-height baseline.
    baseH = Math.max(window.innerHeight, inTelegram ? Number(tg?.viewportStableHeight) || 0 : 0);
    setTimeout(resized, 300);
  });
  if (inTelegram && tg?.onEvent) {
    try { tg.onEvent('viewportChanged', resized); } catch (e) { /* old client */ }
  }
  document.addEventListener('pointerdown', (e) => {
    if (focused && !isField(e.target) && !e.target.closest('button, a, label, .composer, .sheet')) document.activeElement?.blur?.();
  }, { passive: true });
}
