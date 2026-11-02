// Экранная клавиатура: прячем нижние панели, поднимаем поле ввода и показываем то, куда пишет пользователь.
import { mainButton } from './ui.js';
import { tg, inTelegram } from './api.js';

const TEXTUAL = new Set(['text', 'number', 'search', 'tel', 'email', 'url', 'password', 'time', 'date', '']);
const isField = (el) => !!el && ((el.tagName === 'TEXTAREA') || (el.tagName === 'INPUT' && TEXTUAL.has((el.getAttribute('type') || '').toLowerCase())) || el.isContentEditable);

export function initKeyboard() {
  const root = document.documentElement;
  const body = document.body;
  const vv = window.visualViewport;
  let focused = false;
  let open = false;
  let baseH = window.innerHeight;
  let timer = 0;

  function lift() {
    // Сколько нужно поднять зафиксированные снизу элементы: у Telegram на iOS окно не сжимается, и клавиатура их перекрывает.
    const h = vv ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)) : 0;
    root.style.setProperty('--kb-h', h + 'px');
    return h;
  }

  function reveal() {
    const el = document.activeElement;
    if (!isField(el)) return;
    // Ждём, пока клавиатура доедет: до этого размеры окна ещё старые.
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (document.activeElement !== el) return;
      // Прокручиваем только если поле реально закрыто клавиатурой или шапкой: без лишних скачков.
      const r = el.getBoundingClientRect();
      const visTop = (vv ? vv.offsetTop : 0) + 60;
      const visBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
      if (r.top >= visTop && r.bottom <= visBottom - 12) return;
      try { el.scrollIntoView({ block: 'nearest' }); } catch (e) { el.scrollIntoView(); }
    }, 320);
  }

  function update() {
    const shrunk = vv ? baseH - vv.height > 120 : false;
    const next = focused && (shrunk || lift() > 80);
    if (!focused && !shrunk) baseH = Math.max(baseH, window.innerHeight);
    if (next === open) { lift(); return; }
    open = next;
    body.classList.toggle('kb-open', open);
    if (open) { mainButton.suspend(); reveal(); } else mainButton.resume();
    lift();
  }

  document.addEventListener('focusin', (e) => {
    if (!isField(e.target)) return;
    focused = true;
    body.classList.add('kb-focus');
    update();
    reveal();
  });
  document.addEventListener('focusout', () => {
    // Фокус может сразу перейти в соседнее поле: решаем после того, как всё уляжется.
    setTimeout(() => {
      if (isField(document.activeElement)) return;
      focused = false;
      body.classList.remove('kb-focus');
      update();
      if (!open) mainButton.resume();
    }, 60);
  });
  if (vv) {
    vv.addEventListener('resize', () => { update(); if (open) reveal(); });
    vv.addEventListener('scroll', lift);
  }
  window.addEventListener('resize', update);
  // Смена ориентации и сворачивание: запоминаем высоту заново.
  window.addEventListener('orientationchange', () => { baseH = window.innerHeight; setTimeout(update, 300); });
  if (inTelegram && tg && tg.onEvent) {
    try { tg.onEvent('viewportChanged', () => { if (!focused) baseH = window.innerHeight; update(); }); } catch (e) { /* старый клиент */ }
  }
  // Тап по пустому месту убирает клавиатуру.
  document.addEventListener('pointerdown', (e) => {
    if (focused && !isField(e.target) && !e.target.closest('button, a, label, .composer, .sheet')) document.activeElement?.blur?.();
  }, { passive: true });
}
