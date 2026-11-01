// Fetch wrapper for the Mini App <-> bot API (see docs/V2_API.md).
import { mockRequest, mockCsv } from './mock.js';

export const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
export const inTelegram = !!(tg && tg.initData);

const BASE = '/api';
const isLocalHost = ['localhost', '127.0.0.1', '[::1]', ''].includes(location.hostname);
let mockMode = /[?&]mock=1\b/.test(location.search) || location.protocol === 'file:';
let realOk = false; // at least one real response was received
const modeListeners = [];

export const isMock = () => mockMode;
export const onMockEnabled = (fn) => modeListeners.push(fn);

function enableMock() {
  if (mockMode) return;
  mockMode = true;
  modeListeners.forEach((fn) => fn());
}

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function errorFrom(status, data) {
  const code = (data && data.error) || 'http_' + status;
  let msg = data && data.message;
  if (!msg) {
    if (status === 401) msg = 'Откройте приложение из Telegram, чтобы войти.';
    else if (status === 403) msg = 'Нет доступа.';
    else if (status === 404) msg = 'Не найдено.';
    else if (status === 409 && code === 'not_onboarded') msg = 'Сначала пройдите настройку в чате с ботом.';
    else if (status === 413) msg = 'Файл слишком большой. Попробуйте фото поменьше.';
    else if (status === 422 || status === 400) msg = 'Проверьте введённые данные.';
    else if (status === 429) msg = 'Слишком много запросов. Подождите немного.';
    else if (status >= 500) msg = 'Ошибка на сервере. Попробуйте ещё раз чуть позже.';
    else msg = 'Не получилось выполнить запрос.';
  }
  return new ApiError(status, code, msg);
}

function qs(query) {
  if (!query) return '';
  const p = new URLSearchParams();
  Object.entries(query).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.set(k, v);
  });
  const s = p.toString();
  return s ? '?' + s : '';
}

function authHeaders() {
  return inTelegram ? { Authorization: 'tma ' + tg.initData } : {};
}

async function request(method, path, { query, body, timeout = 30000 } = {}) {
  if (mockMode) return mockRequest(method, path, query || {}, body);
  const headers = authHeaders();
  const init = { method, headers };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const ctrl = new AbortController();
  init.signal = ctrl.signal;
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(BASE + path + qs(query), init);
  } catch (e) {
    clearTimeout(timer);
    if (!realOk && isLocalHost) {
      enableMock();
      return mockRequest(method, path, query || {}, body);
    }
    if (e && e.name === 'AbortError') throw new ApiError(0, 'timeout', 'Сервер слишком долго отвечает. Попробуйте ещё раз.');
    throw new ApiError(0, 'network', 'Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.');
  }
  clearTimeout(timer);
  if (res.status === 204) {
    realOk = true;
    return null;
  }
  const ct = res.headers.get('content-type') || '';
  let data = null;
  if (ct.includes('json')) data = await res.json().catch(() => null);
  if (!res.ok) {
    if (!realOk && isLocalHost && !ct.includes('json') && [404, 405, 501].includes(res.status)) {
      enableMock();
      return mockRequest(method, path, query || {}, body);
    }
    throw errorFrom(res.status, data);
  }
  realOk = true;
  return data;
}

export const api = {
  get: (path, query, opts) => request('GET', path, { query, ...opts }),
  post: (path, body, opts) => request('POST', path, { body: body === undefined ? {} : body, ...opts }),
  patch: (path, body) => request('PATCH', path, { body }),
  del: (path) => request('DELETE', path),
  _me: null,
  async me(force) {
    if (!api._me || force) api._me = await api.get('/me');
    return api._me;
  },
};

// ---- images: <img> cannot send Authorization, so /api/ images are fetched with the
// header and shown through cached object URLs (revoked on page unload).
const imgCache = new Map(); // url -> Promise<objectUrl>

export function imageUrl(url) {
  if (!url || /^(data|blob):/.test(url)) return Promise.resolve(url);
  if (mockMode) return Promise.resolve(url);
  if (!inTelegram) return Promise.resolve(url); // dev mode (localhost) needs no header
  if (imgCache.has(url)) return imgCache.get(url);
  const p = fetch(url, { headers: authHeaders() })
    .then((r) => {
      if (!r.ok) throw new Error('img ' + r.status);
      return r.blob();
    })
    .then((b) => URL.createObjectURL(b));
  imgCache.set(url, p);
  p.catch(() => imgCache.delete(url));
  return p;
}
api.imageUrl = imageUrl;
export const blobUrl = imageUrl;

function revokeImages() {
  imgCache.forEach((p) => p.then((u) => URL.revokeObjectURL(u)).catch(() => {}));
  imgCache.clear();
}
window.addEventListener('pagehide', revokeImages);

// ---- CSV export: /api/export.csv needs the Authorization header too, so fetch a blob
function saveBlob(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}

export async function downloadCsv(kind) {
  let blob;
  let name = kind + '.csv';
  if (mockMode) {
    blob = new Blob([mockCsv(kind)], { type: 'text/csv;charset=utf-8' });
  } else {
    const res = await fetch(BASE + '/export.csv' + qs({ kind }), { headers: authHeaders() });
    if (!res.ok) {
      const data = (res.headers.get('content-type') || '').includes('json') ? await res.json().catch(() => null) : null;
      throw errorFrom(res.status, data);
    }
    const m = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') || '');
    if (m) name = m[1];
    blob = new Blob([await res.blob()], { type: 'text/csv;charset=utf-8' });
  }
  // Telegram's in-app webview often ignores <a download>: offer the system share sheet first.
  if (inTelegram && navigator.canShare && navigator.share) {
    const file = new File([blob], name, { type: 'text/csv' });
    if (navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], title: name }); return 'shared'; } catch (e) { if (e && e.name === 'AbortError') return 'cancelled'; }
    }
  }
  saveBlob(blob, name);
  return 'downloaded';
}
