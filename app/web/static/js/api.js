// Fetch wrapper for the Mini App <-> bot API (see docs/V2_API.md).
import { t } from './i18n.js';

export const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
export const inTelegram = !!(tg && tg.initData);

const BASE = '/api';
const isLocalHost = ['localhost', '127.0.0.1', '[::1]', ''].includes(location.hostname);
let mockMode = /[?&]mock=1\b/.test(location.search) || location.protocol === 'file:';
let realOk = false; // at least one real response was received
const modeListeners = [];

export const isMock = () => mockMode;
export const onMockEnabled = (fn) => modeListeners.push(fn);

async function requestMock(method, path, query, body) {
  const { mockRequest } = await import('./mock.js');
  return mockRequest(method, path, query || {}, body);
}

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
    this.expired = false; // 401 внутри Telegram: initData устарел (живёт сутки), нужно открыть приложение заново
  }
}

function errorFrom(status, data) {
  const code = (data && data.error) || 'http_' + status;
  let msg = code === 'llm_timeout' ? t('err.recognition_timeout') : data && data.message;
  if (!msg) {
    if (status === 401) msg = t('err.401');
    else if (status === 403) msg = t('err.403');
    else if (status === 404) msg = t('err.404');
    else if (status === 409 && code === 'not_onboarded') msg = t('err.not_onboarded');
    else if (status === 413) msg = t('err.413');
    else if (status === 422 || status === 400) msg = t('err.400');
    else if (status === 429) msg = t('err.429');
    else if (status >= 500) msg = t('err.5xx');
    else msg = t('err.generic');
  }
  const err = new ApiError(status, code, msg);
  if (status === 401 && inTelegram && code !== 'not_allowed') {   // not_allowed: человека нет в списке теста, это не устаревшая сессия
    err.expired = true;
    err.message = t('err.expired');
  }
  return err;
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

const timedOut = () => new ApiError(0, 'timeout', t('err.timeout'));
const networkError = () => new ApiError(0, 'network', t('err.network'));

async function request(method, path, { query, body, timeout = 30000 } = {}) {
  if (mockMode) return requestMock(method, path, query, body);
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
  // Таймер снимается только после чтения тела: завис ответ посреди передачи — запрос всё равно оборвётся по таймауту.
  try {
    let res;
    try {
      res = await fetch(BASE + path + qs(query), init);
    } catch (e) {
      if (!realOk && isLocalHost) {
        enableMock();
        return requestMock(method, path, query, body);
      }
      if (e && e.name === 'AbortError') throw timedOut();
      throw networkError();
    }
    if (res.status === 204) {
      realOk = true;
      return null;
    }
    const ct = res.headers.get('content-type') || '';
    let data = null;
    if (ct.includes('json')) {
      try { data = await res.json(); } catch (e) {
        if (ctrl.signal.aborted) throw timedOut();
        data = null;
      }
    }
    if (!res.ok) {
      if (!realOk && isLocalHost && !ct.includes('json') && [404, 405, 501].includes(res.status)) {
        enableMock();
        return requestMock(method, path, query, body);
      }
      if (res.status === 401) invalidateReports();
      throw errorFrom(res.status, data);
    }
    if (data === null) throw new ApiError(res.status, 'bad_response', t('err.generic'));
    realOk = true;
    return data;
  } finally {
    clearTimeout(timer);
  }
}

// Reports stay in memory for this Mini App session. Writes clear them.
// When returning to the app, retain the view while checking for bot updates.
const reports = new Map();
const reportRequests = new Map();
let reportGeneration = 0;

function invalidateReports() {
  reports.clear();
  reportGeneration += 1;
}

export function cachedReport(path, query) {
  return reports.get(path + qs(query))?.data ?? null;
}

export function readReport(path, query) {
  const key = path + qs(query);
  const entry = reports.get(key);
  if (entry?.generation === reportGeneration) return Promise.resolve(entry.data);
  const pending = reportRequests.get(key);
  if (pending?.generation === reportGeneration) return pending.promise;

  const generation = reportGeneration;
  const promise = api.get(path, query).then((data) => {
    if (generation !== reportGeneration) return readReport(path, query);
    const signature = JSON.stringify(data);
    const current = entry?.signature === signature ? entry.data : data;
    reports.delete(key);
    reports.set(key, { data: current, signature, generation });
    if (reports.size > 18) reports.delete(reports.keys().next().value);
    return current;
  }).finally(() => {
    if (reportRequests.get(key)?.promise === promise) reportRequests.delete(key);
  });
  reportRequests.set(key, { promise, generation });
  return promise;
}

let hiddenAt = null;
globalThis.document?.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    hiddenAt = Date.now();
  } else if (hiddenAt !== null) {
    const elapsed = Date.now() - hiddenAt;
    hiddenAt = null;
    if (elapsed < 1_000 || !reports.size) return;
    reportGeneration += 1;
    window.dispatchEvent(new Event('app:reports-stale'));
  }
});

export function watchReports(load) {
  window.addEventListener('app:reports-stale', load);
  return () => window.removeEventListener('app:reports-stale', load);
}

const REPORT_WRITE = /^\/(?:meals|items|water|drinks|weight|sleep|activity|profile|targets|onboarding|week-goal|favorites)(?:\/|$)|^\/screens\/\d+\/confirm$/;

async function write(method, path, options) {
  const result = await request(method, path, options);
  if (REPORT_WRITE.test(path) || (path === '/screens' && result?.applied) || (path === '/me' && method === 'DELETE')) invalidateReports();
  if (path === '/onboarding' || path === '/onboarding/skip') {
    api._me = null;
    api.onboardingSkipped = path === '/onboarding/skip';
  }
  return result;
}

export const api = {
  get: (path, query, opts) => request('GET', path, { query, ...opts }),
  post: (path, body, opts) => write('POST', path, { body: body === undefined ? {} : body, ...opts }),
  put: (path, body) => write('PUT', path, { body }),
  patch: (path, body) => write('PATCH', path, { body }),
  del: (path, body) => write('DELETE', path, { body }),
  _me: null,
  onboardingSkipped: false,
  async me(force) {
    if (!api._me || force) {
      api._me = await api.get('/me');
      api.onboardingSkipped = !!api._me?.onboarding_skipped;
    }
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
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30000);
  const p = fetch(url, { headers: authHeaders(), signal: ctrl.signal })
    .then((r) => {
      if (!r.ok) throw new Error('img ' + r.status);
      return r.blob();
    })
    .then((b) => URL.createObjectURL(b))
    .finally(() => clearTimeout(timer));
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
    const { mockCsv } = await import('./mock.js');
    blob = new Blob([mockCsv(kind)], { type: 'text/csv;charset=utf-8' });
  } else {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30000);
    try {
      const res = await fetch(BASE + '/export.csv' + qs({ kind }), { headers: authHeaders(), signal: ctrl.signal });
      if (!res.ok) {
        const data = (res.headers.get('content-type') || '').includes('json') ? await res.json().catch(() => null) : null;
        if (res.status === 401) invalidateReports();
        throw errorFrom(res.status, data);
      }
      const m = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') || '');
      if (m) name = m[1];
      blob = new Blob([await res.blob()], { type: 'text/csv;charset=utf-8' });
    } catch (e) {
      if (ctrl.signal.aborted) throw timedOut();
      if (e instanceof ApiError) throw e;
      throw networkError();
    } finally {
      clearTimeout(timer);
    }
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
