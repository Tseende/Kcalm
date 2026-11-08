// Локализация: русский (по умолчанию) и английский. Словари: js/i18n/ru.js, js/i18n/en.js (плоские ключи).
// t(ключ, {имя: значение}) подставляет {имя}; tn(ключ, n, vars) выбирает форму числа (ru: one/few/many, en: one/other).
// Язык выбирается один раз при загрузке (после смены в настройках страница перезагружается):
//   ?lang=en|ru в адресе -> localStorage 'lang' -> language_code пользователя Telegram -> русский.
import ru from './i18n/ru.js';
import en from './i18n/en.js';

const DICTS = { ru, en };
const RU_LIKE = ['ru', 'uk', 'be', 'kk'];   // ru/uk/be/kk -> русский, всё остальное -> английский
const KEY = 'lang';

function fromUrl() {
  try { const m = /[?&]lang=(en|ru)\b/.exec(location.search); return m ? m[1] : null; } catch (e) { return null; }
}
function stored() {
  try { const v = localStorage.getItem(KEY); return v === 'ru' || v === 'en' ? v : null; } catch (e) { return null; }
}
function fromTelegram() {
  try {
    const code = window.Telegram && Telegram.WebApp && Telegram.WebApp.initDataUnsafe && Telegram.WebApp.initDataUnsafe.user
      && Telegram.WebApp.initDataUnsafe.user.language_code;
    if (!code) return null;
    return RU_LIKE.includes(String(code).toLowerCase().split('-')[0]) ? 'ru' : 'en';
  } catch (e) { return null; }
}

let cur = fromUrl() || stored() || fromTelegram() || 'ru';
try { document.documentElement.lang = cur; } catch (e) { /* без документа */ }

export const lang = () => cur;
/** Локаль для Intl: числа, даты, дни недели. */
export const locale = () => (cur === 'en' ? 'en-US' : 'ru-RU');
/** Выбор в настройках: 'auto' (как в Telegram) | 'ru' | 'en'. */
export const langPref = () => stored() || 'auto';
/** Сохранить выбор и перезагрузить приложение. 'auto' снимает ключ. */
export function setLang(v) {
  try { if (v === 'ru' || v === 'en') localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch (e) { /* без хранилища */ }
  try {
    const u = new URL(location.href);
    u.searchParams.delete('lang');                    // ?lang= в адресе перебивает настройку
    history.replaceState(history.state, '', u.pathname + u.search + u.hash);
  } catch (e) { /* ignore */ }
  location.reload();
}

const warned = new Set();
function lookup(key) {
  const d = DICTS[cur];
  if (d && key in d) return d[key];
  if (cur !== 'ru' && !warned.has(key)) { warned.add(key); console.warn('[i18n] нет перевода:', key); }
  return key in ru ? ru[key] : key;
}
const fill = (s, vars) => (vars ? String(s).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : String(s));

export function t(key, vars) {
  const e = lookup(key);
  return fill(typeof e === 'string' ? e : (e && (e.other || e.many)) || key, vars);
}

/** Категория множественного числа: ru one/few/many, en one/other (целые числа). */
export function pluralCat(n) {
  const x = Math.abs(n);
  if (cur === 'en') return x === 1 ? 'one' : 'other';
  const a = x % 100, b = a % 10;
  return a > 10 && a < 20 ? 'many' : b === 1 ? 'one' : b >= 2 && b <= 4 ? 'few' : 'many';
}
/** tn('days', 3) -> «3 дня» / «3 days». В словаре форма-объект со строкой-шаблоном, где {n} — число. */
export function tn(key, n, vars) {
  const e = lookup(key);
  if (typeof e === 'string') return fill(e, { n, ...vars });
  const s = e[pluralCat(n)] || e.other || e.many || key;
  return fill(s, { n, ...vars });
}
