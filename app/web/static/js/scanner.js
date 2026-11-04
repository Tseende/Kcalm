// Сканер штрихкода камерой. BarcodeDetector, если он есть (Android), иначе ZXing (iOS WebView его не имеет).
import { h, icon, haptic } from './ui.js';

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e'];
let zxLoading = null;

function loadZX() {
  if (window.ZXing) return Promise.resolve(window.ZXing);
  if (!zxLoading) {
    zxLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'vendor/zxing.min.js?v=1';
      s.onload = () => resolve(window.ZXing);
      s.onerror = () => { zxLoading = null; reject(new Error('Не удалось загрузить сканер')); };
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

async function makeDecoder(maxSide) {
  if ('BarcodeDetector' in window) {
    try {
      const supported = await window.BarcodeDetector.getSupportedFormats();
      const formats = FORMATS.filter((f) => supported.includes(f));
      if (formats.length) {
        const det = new window.BarcodeDetector({ formats });
        return async (src) => { const r = await det.detect(src); return r.length ? r[0].rawValue : null; };
      }
    } catch (e) { /* падаем на ZXing */ }
  }
  const ZX = await loadZX();
  const hints = new Map();
  hints.set(ZX.DecodeHintType.POSSIBLE_FORMATS, [ZX.BarcodeFormat.EAN_13, ZX.BarcodeFormat.EAN_8, ZX.BarcodeFormat.UPC_A, ZX.BarcodeFormat.UPC_E]);
  hints.set(ZX.DecodeHintType.TRY_HARDER, true);
  const reader = new ZX.MultiFormatReader();
  reader.setHints(hints);
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  return async (src) => {
    const w = src.videoWidth || src.naturalWidth || src.width;
    const hh = src.videoHeight || src.naturalHeight || src.height;
    if (!w || !hh) return null;
    const k = Math.min(1, maxSide / Math.max(w, hh));
    canvas.width = Math.round(w * k);
    canvas.height = Math.round(hh * k);
    ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
    try {
      const bmp = new ZX.BinaryBitmap(new ZX.HybridBinarizer(new ZX.HTMLCanvasElementLuminanceSource(canvas)));
      return reader.decode(bmp).getText();
    } catch (e) { return null; } // NotFoundException: в кадре кода нет
  };
}

/** Штрихкод с фотографии (запасной путь, если камера в приложении недоступна). */
export async function decodeFile(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const decode = await makeDecoder(1800);
    const code = await decode(img);
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
    const hint = h('div', { class: 'scan-hint', 'aria-live': 'polite' }, 'Наведите камеру на штрихкод');
    const torch = h('button', { class: 'scan-btn round', type: 'button', hidden: true, 'aria-label': 'Фонарик' }, icon('bolt', 22));
    const overlay = h('div', { class: 'scan', role: 'dialog', 'aria-label': 'Сканер штрихкода' },
      video,
      h('div', { class: 'scan-top' }, h('button', { class: 'scan-btn round', type: 'button', 'aria-label': 'Закрыть', onclick: () => finish(null) }, icon('close', 22)), torch),
      h('div', { class: 'scan-frame' }, h('i', { class: 'scan-line' })),
      hint,
      h('div', { class: 'scan-bar' }, h('button', { class: 'scan-btn', type: 'button', onclick: () => finish({ manual: true }) }, 'Ввести цифры вручную')));
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
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
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
      try { decode = await makeDecoder(960); } catch (e) { finish({ error: 'load' }); return; }
      const started = Date.now();
      const tick = async () => {
        if (finished) return;
        if (video.readyState >= 2) {
          try {
            const code = await decode(video);
            if (code && validBarcode(code)) { haptic('success'); finish({ code }); return; }
          } catch (e) { /* кадр не распознан */ }
        }
        if (Date.now() - started > 12000) hint.textContent = 'Не читается? Поднесите ближе, добавьте света или введите цифры вручную';
        timer = setTimeout(tick, 160);
      };
      tick();
    })();
  });
}
