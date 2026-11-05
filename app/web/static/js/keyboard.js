// Экранная клавиатура: прячем нижние панели, поднимаем поле ввода и показываем то, куда пишет пользователь.
import { mainButton } from './ui.js';
import { tg, inTelegram } from './api.js';

const TEXTUAL = new Set(['text', 'number', 'search', 'tel', 'email', 'url', 'password', 'time', 'date', '']);
const isField = (el) => !!el && ((el.tagName === 'TEXTAREA') || (el.tagName === 'INPUT' && TEXTUAL.has((el.getAttribute('type') || '').toLowerCase())) || el.isContentEditable);

const KBBAR_H = 68; // высота кнопки над клавиатурой, см. .kbbar в app.css

// Свободный текст (комментарий, описание): остальное на экране прячем, поле выходит наверх на всю высоту.
let composed = [];
function composeOn(ta) {
  const screen = ta.closest('.screen');
  if (!screen || ta.closest('.sheet')) return;
  composeOff();
  for (let a = ta; a && a !== screen; a = a.parentElement) {
    for (const sib of a.parentElement.children) {
      if (sib !== a && !sib.matches('.page-head, header, [hidden], input[type=file]')) { sib.classList.add('ck-hide'); composed.push(sib); }
    }
  }
  screen.classList.add('compose');
  composed.push(screen);
  window.scrollTo(0, 0);
}
function composeOff() {
  for (const el of composed) el.classList.remove('ck-hide', 'compose');
  composed = [];
}

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
      // Прокручиваем только если поле реально закрыто клавиатурой, кнопкой над ней или шапкой: без лишних скачков.
      // scrollIntoView здесь не годится: он двигает ещё и visualViewport, и поле «прыгает» вверх.
      const r = el.getBoundingClientRect();
      const visTop = (vv ? vv.offsetTop : 0) + 60;
      const visBottom = (vv ? vv.offsetTop + vv.height : window.innerHeight) - (open ? KBBAR_H : 0);
      if (r.bottom > visBottom - 12) window.scrollBy(0, Math.ceil(r.bottom - visBottom + 16));
      else if (r.top < visTop) window.scrollBy(0, Math.floor(r.top - visTop - 8));
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
    if (e.target.tagName === 'TEXTAREA') composeOn(e.target);
    update();
    reveal();
  });
  document.addEventListener('focusout', () => {
    // Фокус может сразу перейти в соседнее поле: решаем после того, как всё уляжется.
    setTimeout(() => {
      if (isField(document.activeElement)) return;
      focused = false;
      composeOff();
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
