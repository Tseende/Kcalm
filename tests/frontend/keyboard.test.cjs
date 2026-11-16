const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../app/web/static/js/keyboard.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/\bexport /g, '');

function classes() {
  const values = new Set();
  return { add: (...names) => names.forEach(n => values.add(n)), remove: (...names) => names.forEach(n => values.delete(n)),
    contains: n => values.has(n), toggle: (n, on) => on ? values.add(n) : values.delete(n) };
}
function setup({ telegram = false, visual = true, toolbar = false } = {}) {
  const listeners = new Map();
  const timers = new Map();
  let timerId = 0;
  const events = () => ({ addEventListener: (name, callback) => listeners.set(name + ':' + (++timerId), callback) });
  const dispatch = (name, event = {}) => {
    for (const [key, fn] of [...listeners]) if (key.startsWith(name + ':')) fn(event);
  };
  const scrolls = [];
  const root = { classList: classes(), style: { values: {}, setProperty(k, v) { this.values[k] = v; } } };
  const body = { parentElement: root, children: [], classList: classes(), paddingTop: '0px' };
  const bar = { hidden: !toolbar, getBoundingClientRect: () => ({ top: 332, bottom: 400, height: 68 }) };
  const document = { ...events(), documentElement: root, body, activeElement: body, getElementById: id => id === 'kbbar' ? bar : null };
  const vv = { ...events(), height: 800, offsetTop: 0, scale: 1 };
  const window = { ...events(), innerHeight: 800, scrollY: 650, visualViewport: visual ? vv : null,
    scrollTo: (...args) => scrolls.push(['to', ...args]), scrollBy: (x, y) => scrolls.push(['by', x, y]) };
  const tg = { viewportHeight: 800, viewportStableHeight: 800, onEvent: (n, fn) => listeners.set(n + ':tg', fn) };
  let suspended = 0, resumed = 0;
  const mainButton = { suspend: () => { suspended++; }, resume: () => { resumed++; } };
  const context = { document, window, tg, inTelegram: telegram, mainButton,
    getComputedStyle: el => ({ overflowY: el.overflowY || 'visible', paddingTop: el.paddingTop || '0px' }),
    setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id) };
  vm.createContext(context); vm.runInContext(source, context); context.initKeyboard();
  const flush = () => { const tasks = [...timers.values()]; timers.clear(); tasks.forEach(fn => fn()); };
  function field(tag = 'INPUT', rect = { top: 100, bottom: 140, height: 40 }, parent = body) {
    const el = { tagName: tag, isConnected: true, isContentEditable: false, classList: classes(), parentElement: parent,
      getAttribute: () => 'text', getBoundingClientRect: () => rect,
      matches: () => false, closest: selector => selector === '.screen' ? screen : selector === '.sheet' ? (parent.isSheet ? parent : null) : null };
    parent.children ||= []; parent.children.push(el);
    return el;
  }
  const screen = { parentElement: body, children: [], classList: classes(), querySelector: () => null };
  const focus = el => { document.activeElement = el; dispatch('focusin', { target: el }); };
  const keyboard = height => { vv.height = height; dispatch('resize'); flush(); };
  return { document, body, root, window, vv, tg, dispatch, flush, field, focus, keyboard, scrolls, screen,
    get suspended() { return suspended; }, get resumed() { return resumed; } };
}

test('focusing a textarea does not hide siblings or jump to page top', () => {
  const s = setup();
  const text = s.field('TEXTAREA', { top: 120, bottom: 200, height: 80 }, s.screen);
  const sibling = s.field('INPUT', { top: 240, bottom: 280, height: 40 }, s.screen);
  s.focus(text); s.keyboard(400);
  assert.equal(sibling.classList.contains('ck-hide'), false);
  assert.equal(s.screen.classList.contains('compose'), false);
  assert.deepEqual(s.scrolls, []);
});

test('switching from textarea to input keeps the form visible', () => {
  const s = setup();
  s.focus(s.field('TEXTAREA', { top: 100, bottom: 180, height: 80 }, s.screen));
  s.keyboard(400);
  const next = s.field('INPUT', { top: 210, bottom: 250, height: 40 }, s.screen);
  s.dispatch('focusout'); s.focus(next); s.flush();
  assert.equal(next.classList.contains('ck-hide'), false);
  assert.equal(s.body.classList.contains('kb-focus'), true);
  assert.deepEqual(s.scrolls, []);
});

test('oversized textarea is not bottom-aligned above the screen', () => {
  const s = setup(); s.focus(s.field('TEXTAREA', { top: 80, bottom: 780, height: 700 }));
  s.keyboard(400);
  assert.deepEqual(s.scrolls, []);
});

test('a hidden field moves only enough to clear the keyboard toolbar', () => {
  const s = setup({ toolbar: true }); s.focus(s.field('INPUT', { top: 500, bottom: 540, height: 40 }));
  s.keyboard(400);
  assert.deepEqual(s.scrolls, [['by', 0, 220]]);
  assert.equal(s.root.style.values['--kb-h'], '400px');
});

test('sheet fields scroll the sheet, never the locked page', () => {
  const s = setup(); s.body.classList.add('sheet-open');
  const panel = { isSheet: true, parentElement: s.body, overflowY: 'auto', scrollTop: 0, scrollHeight: 900, clientHeight: 300,
    getBoundingClientRect: () => ({ top: 50, bottom: 380, height: 330 }) };
  s.focus(s.field('INPUT', { top: 600, bottom: 640, height: 40 }, panel));
  s.keyboard(400);
  assert.equal(panel.scrollTop, 272);
  assert.deepEqual(s.scrolls, []);
});

test('Telegram height detects keyboard even if VisualViewport remains unchanged', () => {
  const s = setup({ telegram: true }); s.focus(s.field()); s.tg.viewportHeight = 400;
  s.dispatch('viewportChanged'); s.flush();
  assert.equal(s.body.classList.contains('kb-open'), true);
  assert.equal(s.root.style.values['--kb-h'], '400px');
});

test('layout viewport resize detects keyboard without VisualViewport', () => {
  const s = setup({ visual: false }); s.focus(s.field()); s.window.innerHeight = 400;
  s.dispatch('resize'); s.flush();
  assert.equal(s.body.classList.contains('kb-open'), true);
  assert.equal(s.root.style.values['--kb-h'], '0px');
});

test('closing keyboard does not restore stale scroll or leave keyboard padding', () => {
  const s = setup(); s.focus(s.field()); s.keyboard(400);
  s.document.activeElement = s.body; s.dispatch('focusout'); s.flush();
  assert.equal(s.body.classList.contains('kb-open'), false);
  assert.equal(s.root.style.values['--kb-h'], '0px');
  assert.deepEqual(s.scrolls, []);
  assert.ok(s.resumed > 0);
});

test('delayed reveal ignores detached fields after a screen rerender', () => {
  const s = setup(); const el = s.field('INPUT', { top: 900, bottom: 940, height: 40 });
  s.focus(el); el.isConnected = false; s.flush();
  assert.deepEqual(s.scrolls, []);
});


test('iOS viewport panning is not mistaken for a reason to jump to page top', () => {
  const s = setup(); s.focus(s.field('INPUT', { top: 200, bottom: 240, height: 40 }));
  s.vv.offsetTop = 150; s.keyboard(400);
  assert.equal(s.root.style.values['--kb-h'], '250px');
  assert.deepEqual(s.scrolls, []);
});

test('fullscreen safe area is respected when revealing a field', () => {
  const s = setup(); s.body.paddingTop = '90px';
  s.focus(s.field('INPUT', { top: 60, bottom: 100, height: 40 })); s.keyboard(400);
  assert.deepEqual(s.scrolls, [['by', 0, -38]]);
});

test('removing focused field clears keyboard state on the next viewport event', () => {
  const s = setup(); const el = s.field(); s.focus(el); s.keyboard(400);
  el.isConnected = false; s.document.activeElement = s.body;
  s.dispatch('resize'); s.flush();
  assert.equal(s.body.classList.contains('kb-open'), false);
  assert.equal(s.root.style.values['--kb-h'], '0px');
});
