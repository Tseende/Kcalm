// Screen 4: Chat
import { api } from '../api.js';
import { PATHS } from '../icons.js';
import { h, icon, tile, toast, haptic, nav, rich, draft, errorState, emptyState, mealType, fmtKcal, fmtN, isDark, isoDate, dateLabel } from '../ui.js';

// Faint food/health pattern drawn from Tabler icons (inline SVG data URI, no network).
function wallpaper(dark) {
  const names = ['salad', 'apple', 'droplet', 'heart-rate-monitor', 'egg-fried', 'scale', 'flame', 'leaf', 'coffee', 'run'];
  const spots = [[14, 14, -12], [104, 30, 14], [186, 10, -6], [52, 98, 10], [146, 92, -16], [10, 170, 8], [96, 176, -10], [190, 160, 16], [60, 40, 0], [150, 200, 6]];
  const col = dark ? '%23ffffff' : '%23487a3a';
  const op = dark ? '.06' : '.13';
  const g = spots.map(([x, y, r], i) => `<g transform='translate(${x} ${y}) rotate(${r} 12 12) scale(1.25)'>${PATHS[names[i % names.length]]}</g>`).join('');
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='230' height='230' viewBox='0 0 230 230'><g fill='none' stroke='${col}' stroke-opacity='${op}' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'>${g}</g></svg>`;
  return `url("data:image/svg+xml,${svg.replace(/"/g, "'").replace(/#/g, '%23').replace(/%2323/g, '%23').replace(/</g, '%3C').replace(/>/g, '%3E')}")`;
}

const hhmm = (iso) => {
  const d = iso ? new Date(iso) : new Date();
  return isNaN(d) ? '' : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};
const dayKey = (iso) => { const d = iso ? new Date(iso) : new Date(); return isNaN(d) ? '' : isoDate(d); };

export default async function render(root, ctx) {
  const view = h('div', { class: 'screen chat-screen' });
  const wall = h('div', { class: 'chat-wall', 'aria-hidden': 'true' });
  wall.style.setProperty('--wall', wallpaper(isDark()));
  const list = h('div', { class: 'chat-list', role: 'log', 'aria-live': 'polite', 'aria-label': 'Сообщения' });
  const ta = h('textarea', { rows: 1, placeholder: 'Сообщение', 'aria-label': 'Сообщение', maxLength: 2000 });
  const sendBtn = h('button', { class: 'send', type: 'submit', 'aria-label': 'Отправить', hidden: true }, icon('send', 20));
  const clip = h('button', { class: 'clip', type: 'button', 'aria-label': 'Добавить приём пищи', onclick: () => nav.go('/add/meal') }, icon('paperclip', 24));
  const chipBox = h('div', { class: 'ctx-chip-row', hidden: true });
  const composer = h('form', { class: 'composer', onsubmit: (e) => { e.preventDefault(); send(); } }, chipBox, clip, ta, sendBtn);
  const talkBtn = h('button', { class: 'talk-btn', type: 'button', 'aria-label': 'Поговорить', onclick: () => talk() }, icon('mood-smile', 18), h('span', null, 'Поговорить'));
  const head = h('header', { class: 'page-head chat-head' },
    h('div', { class: 'ph-side' }),
    h('div', { class: 'chat-who' },
      h('span', { class: 'chat-ava', 'aria-hidden': 'true' }, icon('salad', 20)),
      h('span', { class: 'chat-name' }, h('b', null, 'Дневник питания'), h('small', null, 'ассистент'))),
    h('div', { class: 'ph-side end' }, talkBtn));
  view.append(head, list);
  root.append(wall, view, composer);
  let sending = false;
  let lastDay = '';

  function scrollDown(smooth) { requestAnimationFrame(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })); }

  function dateChip(key, iso) {
    const chip = h('div', { class: 'date-chip' }, h('span', null, dateLabel(key || isoDate(new Date()))));
    return chip;
  }
  function addDay(iso) {
    const k = dayKey(iso);
    if (k && k !== lastDay) { lastDay = k; list.append(dateChip(k, iso)); }
  }

  function bubble(m) {
    const me = m.role === 'user';
    const meta = h('span', { class: 'meta' }, hhmm(m.created_at), me ? icon('checks', 15) : null);
    const b = h('div', { class: 'bubble ' + (me ? 'me' : 'bot') }, rich(m.text), meta);
    return h('div', { class: 'msg ' + (me ? 'me' : 'bot') }, b);
  }

  function mealCard(m) {
    const mt = mealType(m.type);
    return h('div', { class: 'msg bot' }, h('button', { class: 'meal-card', type: 'button', onclick: () => nav.go('/meal/' + m.id) },
      tile(mt.icon, mt.color, 40),
      h('span', { class: 'grow' }, h('b', null, `${mt.label}: ${fmtKcal(m.kcal)} ккал`), h('span', { class: 'row-sub' }, `Б ${fmtN(m.protein)} · Ж ${fmtN(m.fat)} · У ${fmtN(m.carbs)}`), h('span', { class: 'link-hint' }, 'Открыть приём')),
      icon('next', 18)));
  }

  // Быстрые кнопки (как inline-клавиатура в Telegram) под последним ответом ассистента.
  let actionsEl = null;
  function clearActions() { actionsEl?.remove(); actionsEl = null; }
  function showActions(actions) {
    clearActions();
    if (!actions || !actions.length) return;
    actionsEl = h('div', { class: 'msg bot' }, h('div', { class: 'quick-actions', role: 'group', 'aria-label': 'Быстрые ответы' },
      actions.map((a) => h('button', { class: 'quick-btn', type: 'button', onclick: () => act(a) }, a.label))));
    list.append(actionsEl);
  }

  // Приём, который обсуждаем (?meal=<id>): чип над полем ввода, можно убрать.
  let mealId = null;
  function setMealChip(m) {
    mealId = m ? m.id : null;
    chipBox.replaceChildren();
    chipBox.hidden = !m;
    if (!m) return;
    const mt = mealType(m.type);
    chipBox.append(h('span', { class: 'ctx-chip' }, icon(mt.icon, 16),
      h('span', { class: 'ctx-label' }, `${mt.label} ${m.time} · ${fmtKcal(m.kcal)} ккал`),
      h('button', { class: 'ctx-x', type: 'button', 'aria-label': 'Убрать приём из разговора', onclick: () => setMealChip(null) }, icon('x', 14))));
  }

  const typing = h('div', { class: 'msg bot', 'aria-label': 'Бот печатает' }, h('div', { class: 'bubble bot typing' }, h('i'), h('i'), h('i')));

  function paintAll(msgs) {
    list.replaceChildren();
    lastDay = '';
    if (!msgs.length) {
      list.append(emptyState('message-circle', 'Это ваш ассистент', 'Спросите про калории, вес, неделю или напишите, что съели: «на обед борщ и хлеб».',
        h('button', { class: 'btn btn-secondary', type: 'button', onclick: () => talk() }, icon('mood-smile', 18), 'Поговорить')));
      return;
    }
    msgs.forEach((m) => { addDay(m.created_at); list.append(bubble(m)); });
  }

  try {
    const { messages } = await api.get('/chat', { limit: 50 });
    if (!ctx.alive()) return;
    paintAll(messages);
    scrollDown(false);
  } catch (e) { if (ctx.alive()) errorState(list, e, () => nav.replace('/chat')); return; }
  const wantMeal = parseInt(ctx.query?.meal, 10);
  if (wantMeal) {
    try { const m = await api.get('/meals/' + wantMeal); if (ctx.alive()) { setMealChip(m); ta.focus(); } } catch (e) { /* чип необязателен */ }
  }

  const clearDraft = draft('chat', ta);
  function autosize() { ta.style.height = 'auto'; ta.style.height = Math.min(120, ta.scrollHeight) + 'px'; }
  const toggleSend = () => { sendBtn.hidden = !ta.value.trim(); };
  ta.addEventListener('input', () => { autosize(); toggleSend(); });
  autosize();
  toggleSend();
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });

  // Общий разбор ответа /chat и /chat/action: пузыри, карточка приёма, быстрые кнопки.
  function applyReply(r, mine) {
    typing.remove();
    const msgs = r.messages || [];
    if (mine && msgs.some((m) => m.role === 'user')) mine.remove();
    msgs.forEach((m) => list.append(bubble({ created_at: new Date().toISOString(), ...m })));
    if (r.logged_meal) { list.append(mealCard(r.logged_meal)); haptic('success'); }
    showActions(r.actions);
  }

  async function exchange(path, body, mine, onFail) {
    if (sending) return;
    sending = true;
    sendBtn.disabled = true;
    clearActions();
    list.querySelector('.empty')?.remove();
    list.append(typing);
    scrollDown(true);
    try {
      const r = await api.post(path, body, { timeout: 120000 });
      if (!ctx.alive()) return;
      applyReply(r, mine);
    } catch (e) {
      typing.remove();
      if (onFail) onFail();
      toast(e.message, 'error');
    } finally {
      sending = false;
      sendBtn.disabled = false;
      scrollDown(true);
    }
  }

  async function send() {
    const text = ta.value.trim();
    if (!text || sending) return;
    const mine = bubble({ role: 'user', text });
    list.querySelector('.empty')?.remove();
    addDay(null);
    list.append(mine);
    ta.value = '';
    clearDraft();
    autosize();
    toggleSend();
    await exchange('/chat', mealId ? { text, meal_id: mealId } : { text }, mine, () => {
      ta.value = text;
      ta.dispatchEvent(new Event('input'));
      mine.remove();
      autosize();
      toggleSend();
    });
  }

  const talk = () => exchange('/chat', { mode: 'talk' }, null);
  const act = (a) => exchange('/chat/action', { id: a.id }, null);
}
