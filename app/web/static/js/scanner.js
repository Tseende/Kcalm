// Сканер штрихкода камерой. BarcodeDetector, если он есть (Android), иначе ZXing (iOS WebView его не имеет).
import { h, icon, haptic } from './ui.js';
import { t } from './i18n.js';

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e'];
const ZX_FORMATS = ['EAN_13', 'EAN_8', 'UPC_A', 'UPC_E'];
// Полный кадр проверяем каждый третий проход, между ними читаем рамку прицела.
const BAND_EVERY = 3;
const TICK_MS = 60;
const LIVE_MAX_SIDE = 960;
const PHOTO_MAX_SIDE = 1800;
let zxLoading = null;

function loadZX() {
  if (window.ZXing) return Promise.resolve(window.ZXing);
  if (!zxLoading) {
    zxLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      // ?v=1 — ручная версия: важно поднимать её при замене файла,
      // иначе у пользователей останется старый кэш.
      s.src = 'vendor/zxing.min.js?v=1';
      s.onload = () => resolve(window.ZXing);
      s.onerror = () => { zxLoading = null; reject(new Error(t('scan.loadfail'))); };
      document.head.append(s);
    });
  }
  return zxLoading;
}

/** Контрольная цифра GS1 (EAN-8/UPC-A/EAN-13/GTIN-14): отсекает ошибочные распознавания. */
export function validBarcode(code) {
  if (!/^\d+$/.test(code) || ![8, 12, 13, 14].includes(code.length)) return false;
  const d = [...code].map(Number);
  const check = d.pop();
  const sum = d.reverse().reduce((a, v, i) => a + v * (i % 2 === 0 ? 3 : 1), 0);
  return (10 - (sum % 10)) % 10 === check;
}

/**
 * Границы рамки прицела в пикселях кадра видео.
 * .scan-video лежит с object-fit: cover, поэтому берём видимую часть кадра,
 * а не просто масштабируем целиком.
 */
function frameRegion(video, frame) {
  const vr = video.getBoundingClientRect();
  const fr = frame.getBoundingClientRect();
  if (!vr.width || !vr.height) return null;
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh) return null;
  const scale = Math.max(vr.width / vw, vr.height / vh);
  const visW = vr.width / scale, visH = vr.height / scale;
  const ox = (vw - visW) / 2, oy = (vh - visH) / 2;
  const kx = visW / vr.width, ky = visH / vr.height;
  return {
    x: ox + (fr.left - vr.left) * kx,
    y: oy + (fr.top - vr.top) * ky,
    w: fr.width * kx,
    h: fr.height * ky,
  };
}

/** Нативный BarcodeDetector (Android). null, если он не умеет наши форматы. */
async function nativeDecoder(maxSide) {
  const supported = await window.BarcodeDetector.getSupportedFormats();
  const formats = FORMATS.filter((f) => supported.includes(f));
  if (!formats.length) return null;
  const det = new window.BarcodeDetector({ formats });
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  return (src, region) => new Promise((resolve) => {
    const r = region || { x: 0, y: 0, w: src.videoWidth || src.naturalWidth, h: src.videoHeight || src.naturalHeight };
    if (!r.w || !r.h) { resolve(null); return; }
    const k = Math.min(1, maxSide / Math.max(r.w, r.h));
    canvas.width = Math.max(1, Math.round(r.w * k));
    canvas.height = Math.max(1, Math.round(r.h * k));
    ctx.drawImage(src, r.x, r.y, r.w, r.h, 0, 0, canvas.width, canvas.height);
    det.detect(canvas).then((found) => resolve(found.length ? found[0].rawValue : null), () => resolve(null));
  });
}

async function zxingDecoder(maxSide) {
  const ZX = await loadZX();
  const formats = ZX_FORMATS.map((f) => ZX.BarcodeFormat[f]);
  const frameHints = new Map([[ZX.DecodeHintType.POSSIBLE_FORMATS, formats]]);
  const frameReader = new ZX.MultiFormatReader();
  frameReader.setHints(frameHints);
  const fullReader = new ZX.MultiFormatReader();
  // Более плотная выборка строк нужна для кодов далеко от центра снимка или кадра.
  fullReader.setHints(new Map([...frameHints, [ZX.DecodeHintType.TRY_HARDER, true]]));
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  return (src, region) => {
    const w = src.videoWidth || src.naturalWidth || src.width;
    const hh = src.videoHeight || src.naturalHeight || src.height;
    if (!w || !hh) return null;
    const r = region || { x: 0, y: 0, w, h: hh };
    const k = Math.min(1, maxSide / Math.max(r.w, r.h));
    canvas.width = Math.max(1, Math.round(r.w * k));
    canvas.height = Math.max(1, Math.round(r.h * k));
    ctx.drawImage(src, r.x, r.y, r.w, r.h, 0, 0, canvas.width, canvas.height);
    try {
      const bmp = new ZX.BinaryBitmap(new ZX.HybridBinarizer(new ZX.HTMLCanvasElementLuminanceSource(canvas)));
      const reader = region ? frameReader : fullReader;
      return reader.decodeWithState(bmp).getText();
    } catch (e) { return null; } // NotFoundException: в кадре кода нет
  };
}

async function makeDecoder(maxSide) {
  if ('BarcodeDetector' in window) {
    try {
      const d = await nativeDecoder(maxSide);
      if (d) return d;
    } catch (e) { /* падаем на ZXing */ }
  }
  return zxingDecoder(maxSide);
}

/** Штрихкод с фотографии (запасной путь, если камера в приложении недоступна). */
export async function decodeFile(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const decode = await makeDecoder(PHOTO_MAX_SIDE);
    const code = await decode(img, null);
    return code && validBarcode(code) ? code : null;
  } finally { URL.revokeObjectURL(url); }
}

/**
 * Открывает полноэкранный сканер. Результат: {code} | {manual:true} | {error:'camera'|'load'} | null (отмена).
 */
export function scanBarcode() {
  return new Promise((resolve) => {
    let stream = null; let finished = false; let timer = null; let track = null;
    const video = h('video', { class: 'scan-video', muted: true, autoplay: true });
    video.setAttribute('playsinline', '');
    video.muted = true;
    const hint = h('div', { class: 'scan-hint', 'aria-live': 'polite' }, t('scan.hint'));
    const torch = h('button', { class: 'scan-btn round', type: 'button', hidden: true, 'aria-label': t('scan.torch') }, icon('bolt', 22));
    const frame = h('div', { class: 'scan-frame' }, h('i', { class: 'scan-line' }));
    const overlay = h('div', { class: 'scan', role: 'dialog', 'aria-label': t('scan.title') },
      video,
      h('div', { class: 'scan-top' }, h('button', { class: 'scan-btn round', type: 'button', 'aria-label': t('common.close'), onclick: () => finish(null) }, icon('close', 22)), torch),
      frame,
      hint,
      h('div', { class: 'scan-bar' }, h('button', { class: 'scan-btn', type: 'button', onclick: () => finish({ manual: true }) }, t('scan.manual'))));
    document.body.append(overlay);

    function finish(result) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (stream) stream.getTracks().forEach((t) => t.stop());
      overlay.remove();
      resolve(result);
    }

    (async () => {
      let decode;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 },
                   // нестандартный constraint: работает на Android, на iOS молча игнорируется
                   advanced: [{ focusMode: 'continuous' }] },
          audio: false,
        });
      } catch (e) { finish({ error: 'camera' }); return; }
      if (finished) { stream.getTracks().forEach((t) => t.stop()); return; }
      video.srcObject = stream;
      try { await video.play(); } catch (e) { /* автозапуск мог уже сработать */ }
      track = stream.getVideoTracks()[0];
      try {
        if (track.getCapabilities && track.getCapabilities().torch) {
          let on = false;
          torch.hidden = false;
          torch.onclick = async () => {
            on = !on;
            try { await track.applyConstraints({ advanced: [{ torch: on }] }); } catch (e) { on = !on; }
            torch.classList.toggle('on', on);
          };
        }
      } catch (e) { /* фонарик необязателен */ }
      try { decode = await makeDecoder(LIVE_MAX_SIDE); } catch (e) { finish({ error: 'load' }); return; }
      const started = Date.now();
      let ticks = 0;
      const tick = async () => {
        if (finished) return;
        if (video.readyState >= 2) {
          // Рамка прицела, а каждый третий проход — весь кадр: код может лежать и вне рамки.
          const region = ticks % BAND_EVERY === BAND_EVERY - 1 ? null : frameRegion(video, frame);
          ticks += 1;
          try {
            const code = await decode(video, region);
            if (code && validBarcode(code)) { haptic('success'); finish({ code }); return; }
          } catch (e) { /* кадр не распознан */ }
        }
        if (Date.now() - started > 12000) hint.textContent = t('scan.slow');
        timer = setTimeout(tick, TICK_MS);
      };
      tick();
    })();
  });
}
