// Онбординг первого запуска: интро, 11 вопросов (по одному на экран), расчёт и результат.
// Состояние живёт в sessionStorage: закрыли и открыли — продолжаем с того же шага.
import { api } from '../api.js';
import { h, icon, haptic, toast, toggle, mainButton, backButton, todayISO, addDays, longDate, fmtN, plural } from '../ui.js';
import { onbImg, cards, drum, ruler, REDUCED } from '../onb/controls.js';
import { intro } from '../onb/intro.js';
import { calcView, resultView } from '../onb/result.js';

const KEY = 'onb_state';
const MIN_BMI = 18.5;
const UNDERAGE = 'Бот считает дефицит калорий и рассчитан на взрослых (18+). Детям и подросткам диету подбирает только врач: ограничения в питании в этом возрасте могут навредить росту и здоровью.';
const nowYear = new Date().getFullYear();

const bmiOf = (a) => (a.weight_kg && a.height_cm ? a.weight_kg / (a.height_cm / 100) ** 2 : null);
const floorOf = (a) => Math.round(MIN_BMI * (a.height_cm / 100) ** 2 * 10) / 10;
const r1 = (v) => Math.round(v * 10) / 10;
const pad2 = (n) => String(n).padStart(2, '0');

function loadState() {
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY));
    if (s && s.a && s.step) return s;
  } catch (e) { /* нет хранилища или битый JSON */ }
  return null;
}

/** Есть ли в маршруте вопросы про цель и темп. Недобор ИМТ или цель вплотную к весу: держим вес. */
function willLose(a) {
  if (a.goal === 'maintain') return false;
  if (a.weight_kg && a.height_cm) {
    if (bmiOf(a) < MIN_BMI) return false;
    if (a.weight_kg - floorOf(a) < 1) return false;
  }
  return true;
}

export function stepsFor(a) {
  const s = ['intro', 'goal', 'sex', 'year', 'height', 'weight'];
  if (willLose(a)) s.push('target', 'rate');
  s.push('activity', 'cutoff', 'care', 'reminders');
  return s;
}

/**
 * root — контейнер экрана, me — ответ /me (имя), onDone() — вызвать, когда человек нажал «Начать».
 * Возвращает функцию очистки.
 */
export default function onboarding(root, { me, onDone }) {
  const name = (me && me.user && me.user.first_name) || '';
  const S = loadState() || { step: 'intro', a: {} };
  const a = S.a;
  a.reminders = a.reminders || { morning: true, evening: true, water: true, weigh: true, weekly: true };
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { /* ignore */ } };
  if (['calc', 'result'].includes(S.step)) S.step = 'reminders';

  const prog = h('i');
  const back = h('button', { class: 'onb-back', type: 'button', 'aria-label': 'Назад', onclick: () => goBack() }, icon('back', 24));
  const skip = h('button', { class: 'onb-skip', type: 'button', onclick: () => { haptic('light'); show('goal', 'fwd'); } }, 'Пропустить');
  const stage = h('div', { class: 'onb-stage' });
  const wrap = h('div', { class: 'onb' }, h('div', { class: 'onb-top' }, back, h('div', { class: 'onb-prog', role: 'progressbar', 'aria-label': 'Прогресс настройки' }, prog), skip), stage);
  root.replaceChildren(wrap);

  let cur = null;          // {step, el, cleanup}
  let alive = true;
  let advanceTimer = 0;

  // ------------------------------------------------------------ навигация
  function neighbours(step) {
    const order = stepsFor(a);
    const i = order.indexOf(step === 'stop' ? 'year' : step);
    return { order, i, prev: order[i - 1] || null, next: order[i + 1] || null };
  }
  const goBack = () => {
    if (!cur || cur.step === 'intro' || cur.step === 'calc' || cur.step === 'result') return;
    const { prev } = neighbours(cur.step);
    haptic('light');
    if (cur.step === 'stop') show('year', 'back');
    else if (prev) show(prev, 'back');
  };
  const goNext = () => {
    clearTimeout(advanceTimer);
    const { next } = neighbours(cur.step);
    if (next) show(next, 'fwd');
    else submit();
  };
  function auto() { clearTimeout(advanceTimer); advanceTimer = setTimeout(() => { if (alive && cur) goNext(); }, REDUCED ? 0 : 340); }

  function show(step, dir = 'fwd') {
    if (!alive) return;
    clearTimeout(advanceTimer);
    S.step = step; save();
    const view = build(step);
    const el = view.el;
    el.classList.add('onb-step', dir === 'back' ? 'in-back' : 'in-fwd');
    const old = cur;
    if (old) {
      if (old.cleanup) old.cleanup();
      old.el.classList.add('leaving', dir === 'back' ? 'out-back' : 'out-fwd');
      old.el.setAttribute('aria-hidden', 'true');
      setTimeout(() => old.el.remove(), 300);
    }
    stage.append(el);
    cur = { step, el, cleanup: view.cleanup };
    const { order, i } = neighbours(step);
    const frac = step === 'stop' ? 0.2 : step === 'calc' || step === 'result' ? 1 : (i + 1) / (order.length + 1);
    prog.style.transform = `scaleX(${Math.max(0.04, frac)})`;
    const intro0 = step === 'intro';
    back.hidden = intro0 || step === 'calc' || step === 'result';
    skip.hidden = !intro0;
    if (intro0 || step === 'calc' || step === 'result') backButton.hide(); else backButton.show(goBack);
    if (view.main) mainButton.show(view.main.text, view.main.fn, { disabled: !!view.main.disabled }); else mainButton.hide();
    window.scrollTo(0, 0);
  }

  // ------------------------------------------------------------ построение шагов
  const head = (imgKey, title, why, size = 72) => h('div', { class: 'onb-head' }, imgKey ? onbImg(imgKey, size) : null, why ? h('div', { class: 'onb-why' }, why) : null, h('h1', { class: 'onb-title' }, title));
  const NEXT = { text: 'Дальше', fn: () => goNext() };
  // карточки: выбор сам ведёт дальше; кнопка нужна тем, кто вернулся назад к уже выбранному ответу
  const cardMain = (has) => ({ text: 'Дальше', fn: () => goNext(), disabled: !has });
  const picked = () => { mainButton.setDisabled(false); auto(); };

  function build(step) {
    switch (step) {
      case 'intro': return buildIntro();
      case 'goal': return buildGoal();
      case 'sex': return buildSex();
      case 'year': return buildYear();
      case 'stop': return buildStop();
      case 'height': return buildHeight();
      case 'weight': return buildWeight();
      case 'target': return buildTarget();
      case 'rate': return buildRate();
      case 'activity': return buildActivity();
      case 'cutoff': return buildCutoff();
      case 'care': return buildCare();
      case 'reminders': return buildReminders();
      default: return buildGoal();
    }
  }

  function buildIntro() {
    const it = intro({
      name,
      onIndex: (i) => { if (cur && cur.step === 'intro') mainButton.setText(i === it.count - 1 ? 'Поехали' : 'Дальше'); },
    });
    const next = () => { if (it.index() >= it.count - 1) show('goal', 'fwd'); else it.goTo(it.index() + 1); };
    return { el: h('div', { class: 'onb-step-intro', style: { display: 'flex', flexDirection: 'column', flex: '1', minHeight: '0' } }, it.el), main: { text: 'Дальше', fn: next }, cleanup: it.cleanup };
  }

  function buildGoal() {
    const items = [
      { value: 'lose', title: 'Похудеть', sub: 'Спокойный темп, без жёстких диет', img: 'scale' },
      { value: 'maintain', title: 'Держать вес', sub: 'Следить за питанием без дефицита', img: 'heart_check' },
      { value: 'craving', title: 'Не срываться вечером', sub: 'Разберёмся с тягой к холодильнику', img: 'night_tea' },
      { value: 'mindful', title: 'Есть осознанно', sub: 'Понять, что и зачем вы едите', img: 'salad' },
    ];
    return { el: h('div', null, head(null, 'Что для вас сейчас главное?', 'Так я подстрою советы под вас.'), cards(items, a.goal, (v) => { a.goal = v; save(); picked(); })), main: cardMain(!!a.goal) };
  }

  function buildSex() {
    const items = [{ value: 'male', title: 'Мужской', img: 'male' }, { value: 'female', title: 'Женский', img: 'female' }];
    return { el: h('div', null, head(null, 'Ваш пол', 'Обмен веществ у мужчин и женщин разный — так норма точнее.'), cards(items, a.sex, (v) => { a.sex = v; save(); picked(); }, { cols: 2 })), main: cardMain(!!a.sex) };
  }

  function buildYear() {
    if (!a.birth_year) a.birth_year = nowYear - 35;
    const years = [];
    for (let y = nowYear - 90; y <= nowYear - 10; y++) years.push({ v: y, label: String(y) });
    const age = h('div', { class: 'onb-age', 'aria-live': 'polite' });
    const paintAge = () => { const n = nowYear - a.birth_year; age.replaceChildren(h('b', null, plural(n, 'год', 'года', 'лет'))); };
    paintAge();
    const d = drum(years, a.birth_year, (v) => { a.birth_year = v; save(); paintAge(); }, { cls: 'wide' });
    return {
      el: h('div', null, head(null, 'Год рождения', 'Возраст влияет на расчёт нормы.'), h('div', { class: 'onb-pick' }, d.el, age)),
      main: { text: 'Дальше', fn: () => { if (nowYear - a.birth_year < 18) show('stop', 'fwd'); else goNext(); } },
    };
  }

  function buildStop() {
    return { el: h('div', { class: 'onb-stop' }, onbImg('heart_check', 96), h('h1', { class: 'onb-title' }, 'Пока без меня'), h('p', { class: 'onb-sub' }, UNDERAGE)) };
  }

  function buildHeight() {
    if (!a.height_cm) a.height_cm = a.sex === 'male' ? 176 : 165;
    const val = h('b', null, String(a.height_cm));
    const r = ruler({ min: 120, max: 230, step: 1, value: a.height_cm, orientation: 'v', tick: 10, major: 5, labelEvery: 10, onChange: (v) => { a.height_cm = v; val.textContent = String(v); save(); } });
    return { el: h('div', null, head(null, 'Ваш рост', 'Нужен, чтобы посчитать ИМТ и норму калорий.'), h('div', { class: 'onb-vrow' }, h('div', { class: 'onb-big' }, val, h('span', null, 'см')), r.el)), main: NEXT };
  }

  function bmiText() {
    const b = bmiOf(a);
    if (b === null) return '';
    return `ИМТ ${fmtN(b)}`;
  }

  function buildWeight() {
    if (!a.weight_kg) a.weight_kg = a.sex === 'male' ? 82 : 68;
    const val = h('b', null, fmtN(a.weight_kg));
    const pill = h('span', { class: 'onb-pill' }, bmiText());
    const hint = h('p', { class: 'onb-hint', 'aria-live': 'polite' });
    const paint = () => {
      val.textContent = a.weight_kg.toFixed(1).replace('.', ',');
      pill.textContent = bmiText();
      hint.textContent = bmiOf(a) < MIN_BMI ? 'Ниже ИМТ 18,5 дефицит не включаем — будем держать вес.' : '';
    };
    paint();
    const r = ruler({ min: 35, max: 300, step: 0.1, value: a.weight_kg, tick: 9, major: 1, labelEvery: 1, decimals: 0, onChange: (v) => { a.weight_kg = r1(v); save(); paint(); } });
    return { el: h('div', null, head(null, 'Ваш вес сейчас', 'Это точка отсчёта. Потом вес можно просто присылать скриншотом весов.'),
      h('div', { class: 'onb-center' }, h('div', { class: 'onb-big' }, val, h('span', null, 'кг')), pill), r.el, hint), main: NEXT };
  }

  function buildTarget() {
    const floor = floorOf(a);
    if (!a.goal_weight_kg || a.goal_weight_kg < floor || a.goal_weight_kg >= a.weight_kg) a.goal_weight_kg = Math.max(floor, r1(a.weight_kg * 0.9));
    const val = h('b', null, '');
    const diff = h('span', { class: 'onb-pill big' });
    const hint = h('p', { class: 'onb-hint', 'aria-live': 'polite' });
    const paint = () => {
      val.textContent = a.goal_weight_kg.toFixed(1).replace('.', ',');
      const d = r1(a.weight_kg - a.goal_weight_kg);
      diff.textContent = d > 0 ? `−${fmtN(d)} кг` : 'Без изменений';
      hint.textContent = a.goal_weight_kg <= floor + 0.05 ? 'Ниже ИМТ 18,5 цель не ставим.' : '';
    };
    paint();
    const r = ruler({ min: floor, max: r1(a.weight_kg), step: 0.1, value: a.goal_weight_kg, tick: 10, major: 1, labelEvery: 1, onChange: (v) => { a.goal_weight_kg = r1(v); save(); paint(); } });
    return { el: h('div', null, head('target', 'Желаемый вес', 'Цель можно поменять в любой момент в профиле.'),
      h('div', { class: 'onb-center' }, diff, h('div', { class: 'onb-big' }, val, h('span', null, 'кг'))), r.el, hint), main: NEXT };
  }

  function buildRate() {
    if (!a.rate) a.rate = 0.5;
    const diff = Math.max(0, a.weight_kg - a.goal_weight_kg);
    const when = (rate) => (diff > 0 ? `≈ к ${longDate(addDays(todayISO(), Math.ceil((diff / rate) * 7)))}` : null);
    const items = [
      { value: 0.25, title: 'Спокойно', sub: '0,25 кг в неделю', img: 'turtle', extra: when(0.25) },
      { value: 0.5, title: 'Оптимально', sub: '0,5 кг в неделю', img: 'target', badge: 'рекомендуем', extra: when(0.5) },
      { value: 0.7, title: 'Быстрее', sub: '0,7 кг в неделю', img: 'rabbit', extra: when(0.7) },
    ];
    return { el: h('div', null, head(null, 'Как быстро?', 'Медленнее — проще удержать результат.'), cards(items, a.rate, (v) => { a.rate = v; save(); picked(); })), main: cardMain(true) };
  }

  function buildActivity() {
    const items = [
      { value: 1, title: 'Сидячая', sub: 'Офис, мало движения', img: 'sofa' },
      { value: 2, title: 'Лёгкая', sub: 'Прогулки, 1–3 тренировки', img: 'sneaker' },
      { value: 3, title: 'Средняя', sub: '3–5 тренировок', img: 'bike' },
      { value: 4, title: 'Высокая', sub: 'Каждый день, физический труд', img: 'dumbbell' },
    ];
    return { el: h('div', null, head(null, 'Насколько вы активны?', 'Так я не завышу и не занижу норму.'), cards(items, a.activity, (v) => { a.activity = v; save(); picked(); })), main: cardMain(!!a.activity) };
  }

  function buildCutoff() {
    const [h0, m0] = (a.cutoff || '21:00').split(':').map(Number);
    let hh = h0, mm = m0;
    const upd = () => { a.cutoff = `${pad2(hh)}:${pad2(mm)}`; save(); };
    upd();
    const hours = Array.from({ length: 24 }, (_, i) => ({ v: i, label: pad2(i) }));
    const mins = [0, 15, 30, 45].map((m) => ({ v: m, label: pad2(m) }));
    const dh = drum(hours, hh, (v) => { hh = v; upd(); });
    const dm = drum(mins, mm, (v) => { mm = v; upd(); });
    return { el: h('div', null, head('fridge_lock', 'Когда закрываем кухню?', 'За 15 минут напомню, а после — помогу, если потянет перекусить.'),
      h('div', { class: 'onb-drums' }, dh.el, h('span', { class: 'onb-colon' }, ':'), dm.el)), main: NEXT };
  }

  function buildCare() {
    const items = [
      { value: false, title: 'С калориями', sub: 'Калории, БЖУ и вес в цифрах', img: 'salad' },
      { value: true, title: 'Бережно, без цифр', sub: 'Если цифры тревожат или был опыт расстройства пищевого поведения', img: 'heart_check' },
    ];
    return { el: h('div', null, head(null, 'Как показывать цифры?', 'Это можно сменить позже командой /care.'), cards(items, !!a.care, (v) => { a.care = v; if (v) { a.reminders.weigh = false; if (a.rate > 0.25) a.rate = 0.25; } save(); picked(); })), main: cardMain(true) };
  }

  function buildReminders() {
    const care = !!a.care;
    if (care) a.reminders.weigh = false;
    const rows = [
      ['morning', 'Утро', 'План на день', '08:30'],
      ['evening', 'Итог дня', 'Что получилось и что поправить', '21:30'],
      ['water', 'Вода', 'Пара напоминаний в течение дня', '16:00'],
      ['weigh', 'Взвешивание', care ? 'В бережном режиме не напоминаем' : 'Утром, до еды', '07:30'],
      ['weekly', 'Неделя', 'Разбор по воскресеньям', '19:00'],
    ];
    const list = h('div', { class: 'onb-list' }, rows.map(([kind, title, sub, time]) => {
      const sw = toggle(a.reminders[kind], (v) => { haptic('light'); a.reminders[kind] = v; li.classList.toggle('off', !v); save(); }, title);
      if (care && kind === 'weigh') sw.querySelector('input').disabled = true;
      const li = h('div', { class: 'onb-li' + (a.reminders[kind] ? '' : ' off') }, h('div', { class: 'onb-li-text' }, h('span', { class: 'onb-li-title' }, title), h('span', { class: 'onb-li-sub' }, sub)), h('span', { class: 'onb-li-time' }, kind === 'weekly' ? 'вс ' + time : time), sw);
      return li;
    }));
    return { el: h('div', null, head('alarm', 'Напоминания', 'Время можно поменять потом в профиле.'), list), main: { text: 'Готово', fn: () => submit() } };
  }

  // ------------------------------------------------------------ отправка и результат
  function payload() {
    const lose = willLose(a);
    const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined; } catch (e) { return undefined; } })();
    const body = {
      sex: a.sex || 'female', birth_year: a.birth_year || nowYear - 35, height_cm: a.height_cm, weight_kg: a.weight_kg,
      goal_weight_kg: lose ? a.goal_weight_kg : a.weight_kg, rate_kg_week: lose ? (a.rate || 0.5) : 0,
      activity: a.activity || 2, eating_cutoff: a.cutoff || '21:00', care_mode: !!a.care, goal: a.goal || 'lose',
      reminders: { ...a.reminders, weigh: a.care ? false : a.reminders.weigh },
    };
    if (tz) body.tz = tz;
    return body;
  }

  function submit() {
    if (!a.height_cm || !a.weight_kg) { show(!a.height_cm ? 'height' : 'weight', 'back'); return; }
    mainButton.hide();
    backButton.hide();
    const cv = calcView(() => api.post('/onboarding', payload()));
    const old = cur;
    if (old && old.cleanup) old.cleanup();
    if (old) { old.el.classList.add('leaving', 'out-fwd'); setTimeout(() => old.el.remove(), 300); }
    cv.el.classList.add('in-fwd');
    stage.append(cv.el);
    cur = { step: 'calc', el: cv.el };
    S.step = 'calc';
    prog.style.transform = 'scaleX(1)';
    back.hidden = true; skip.hidden = true;
    cv.done.then((data) => {
      if (!alive) return;
      showResult(data);
    }).catch((e) => {
      if (!alive) return;
      if (e && e.code === 'underage') { show('stop', 'fwd'); return; }
      toast((e && e.message) || 'Не получилось сохранить. Попробуйте ещё раз.', 'error');
      show('reminders', 'back');
    });
  }

  function showResult(data) {
    const rv = resultView(a, data, { care: !!a.care });
    const old = cur;
    if (old) { old.el.classList.add('leaving', 'out-fwd'); setTimeout(() => old.el.remove(), 300); }
    rv.el.classList.add('in-fwd');
    stage.append(rv.el);
    cur = { step: 'result', el: rv.el, cleanup: rv.cleanup };
    S.step = 'result';
    window.scrollTo(0, 0);
    setTimeout(() => rv.play(), REDUCED ? 0 : 250);
    mainButton.show(a.care ? 'Начать' : 'Начать', finish);
  }

  function finish() {
    try { sessionStorage.removeItem(KEY); localStorage.setItem('onb_hint_pending', '1'); } catch (e) { /* ignore */ }
    mainButton.hide();
    onDone();
  }

  show(S.step === 'stop' ? 'year' : S.step, 'fwd');
  return () => {
    alive = false;
    clearTimeout(advanceTimer);
    if (cur && cur.cleanup) cur.cleanup();
    mainButton.hide();
    backButton.hide();
  };
}

/** Подсказка над «+» переехала в тур на «Сегодня» (js/coach.js, шаг today.plus, флаги onb_hint_* те же). Заглушка для совместимости. */
export function maybeShowPlusHint() { /* см. coach.js */ }
