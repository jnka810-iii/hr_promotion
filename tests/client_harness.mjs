/**
 * Harness สำหรับรันโค้ด client จริงจาก templates/index.html ใน node
 *
 * ทำไมต้องมี: บั๊ก "โหลดข้อมูลไม่ได้แล้วแสดง 0 รายการ" เป็นบั๊กที่โค้ดอ่านดูถูก
 * แต่พฤติกรรมผิด (ข้อความ error ตายใน unhandled rejection) การ assert ตัวหนังสือ
 * ในซอร์สจับมันไม่ได้เลย ต้องรันจริงเท่านั้น
 *
 * ไม่ใช้ jsdom/jest — ใช้ `node --test` ที่มีมาในตัว node 22 บวก DOM ปลอม
 * เท่าที่โค้ดต้องใช้ โปรเจกต์นี้จึงไม่มี dependency ฝั่ง JS เลยแม้แต่ตัวเดียว
 *
 * โค้ดที่รันคือสตริงที่อ่านจาก index.html ตรง ๆ ไม่ใช่สำเนา — แก้ index.html
 * แล้วเทสต์เห็นทันที ถ้าใครลบ/เปลี่ยนชื่อสิ่งที่ harness พึ่ง จะ throw ตรงนี้
 * พร้อมบอกว่าอะไรหาย ไม่ใช่ผ่านเงียบ ๆ แบบที่ไม่ได้ทดสอบอะไร
 */
import { readFile } from 'node:fs/promises';

const TEMPLATE = new URL('../templates/index.html', import.meta.url);

/** สิ่งที่ harness ต้องเอาออกมาจาก index.html — ขาดตัวใดตัวหนึ่งถือว่าเทสต์ชุดนี้ล้าสมัย */
const WANT = [
  'apiFetch', 'dbGetAll', 'loadAll', 'detectMode', 'renderTable', 'statusRow',
  'retryLoad', 'byId', 'esc', 'fmtDate', 'toastError', 'onUnauthorized',
  'loadState', 'allRecords', 'USE_FLASK', 'UNKNOWN', 'TBL_COLS',
];

/** นับรอบการ load เพื่อ bust cache ของ data: URL (ดูคอมเมนต์ที่จุด import) */
let loadSeq = 0;

/** องค์ประกอบ DOM ปลอม — getElementById คืนตัวนี้สำหรับทุก id ที่ถาม */
function fakeEl(id) {
  return {
    id, textContent: '', innerHTML: '', value: '', title: '',
    style: {}, classList: { add() {}, remove() {}, contains: () => false },
    addEventListener() {}, appendChild() {}, click() {}, focus() {},
    // คืน element ปลอม ไม่ใช่ null — โค้ดตอน load (addEval51/52) ลูก chain
    // `clone.querySelector(...).querySelector(...)` ซึ่ง null จะพังทันที
    querySelectorAll: () => [], querySelector: sel => fakeEl(sel),
    insertAdjacentHTML() {}, remove() {}, setAttribute() {},
    content: { cloneNode: () => fakeEl('clone') },
  };
}

/**
 * load — สร้างสภาพแวดล้อมใหม่หนึ่งชุดแล้ว import โค้ด client จริงเข้าไป
 *
 * @param {object} opts
 * @param {Function} opts.fetch  stub ของ fetch — บังคับ เพราะทุกเทสต์ต้องระบุเองว่า server ตอบอะไร
 * @returns {Promise<{mod: object, el: object, toasts: string[], alerts: string[]}>}
 */
export async function load({ fetch }) {
  const html = await readFile(TEMPLATE, 'utf8');

  const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
    .map(m => m[1]);
  if (!blocks.length) throw new Error('harness: ไม่พบ inline <script> ใน index.html');
  let src = blocks.reduce((a, b) => (a.length >= b.length ? a : b));

  // ตัด startup IIFE ออก — เทสต์เรียก detectMode/loadAll เองเพื่อคุมลำดับได้
  const iife = /\(async\(\)=>\{\s*await detectMode\(\);\s*await loadAll\(\);\s*\}\)\(\);/;
  if (!iife.test(src)) {
    throw new Error('harness: หา startup IIFE ไม่เจอ — index.html เปลี่ยนโครงไปแล้ว');
  }
  src = src.replace(iife, '');

  // เก็บชื่อที่ประกาศไว้ทั้งหมดรอบเดียว แล้วเทียบด้วย Set — ไม่สร้าง RegExp จาก
  // สตริงต่อชื่อ เพราะ escape ของ template literal กับของ regex ชนกันแล้วเงียบ
  const declared = new Set(
    [...src.matchAll(/\b(?:async\s+)?(?:function|let|const|var)\s+([A-Za-z_$][\w$]*)/g)]
      .map(m => m[1]),
  );
  for (const name of WANT) {
    if (!declared.has(name)) {
      throw new Error(`harness: index.html ไม่มี \`${name}\` แล้ว — เทสต์ชุดนี้ต้องตามไปแก้`);
    }
  }
  // `export { let }` ใน ES module เป็น live binding — อ่านค่าหลังเรียกฟังก์ชันได้ตรง ๆ
  // แต่ namespace object เป็น read-only จึงต้องมี setter สำหรับสวิตช์ที่เทสต์ต้องตั้งเอง
  src += `\nexport function setUseFlask(v){ USE_FLASK = v; }\n`;
  src += `\nexport { ${WANT.join(', ')} };\n`;

  // ── สภาพแวดล้อมปลอม ──
  const el = {};
  const toasts = [];
  const alerts = [];
  const store = {};

  globalThis.document = {
    getElementById: id => (el[id] ??= fakeEl(id)),
    querySelectorAll: () => [],
    createElement: () => fakeEl('created'),
    addEventListener() {},
  };
  globalThis.fetch = fetch;
  globalThis.bootstrap = { Toast: class { show() {} } };
  globalThis.Chart = class { destroy() {} };
  globalThis.alert = m => alerts.push(m);
  globalThis.localStorage = {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  };
  globalThis.window = { open: () => null, print() {}, scrollTo() {} };
  if (typeof globalThis.URL.createObjectURL !== 'function') {
    globalThis.URL.createObjectURL = () => 'blob:stub';
  }

  // ดัก toast โดยไม่แตะโค้ดจริง: toast() เขียนข้อความลง #toast-msg.textContent
  const toastMsg = (el['toast-msg'] = fakeEl('toast-msg'));
  Object.defineProperty(toastMsg, 'textContent', {
    get() { return this._t ?? ''; },
    set(v) { this._t = v; toasts.push(v); },
    configurable: true,
  });

  // node cache โมดูลตาม specifier และ data: URL ก็ไม่ยกเว้น — ซอร์สเดียวกันทุกเทสต์
  // จะได้ instance เดียวกัน แล้ว allRecords/loadState/USE_FLASK รั่วข้ามเทสต์
  // ต่อท้ายคอมเมนต์ที่ไม่ซ้ำเพื่อให้แต่ละ load() ได้สภาพแวดล้อมสะอาดจริง
  src += `\n// load #${++loadSeq}\n`;
  const mod = await import('data:text/javascript;charset=utf-8;base64,' +
    Buffer.from(src, 'utf8').toString('base64'));
  return { mod, el, toasts, alerts };
}

/** ตั้งค่าในช่องกรอก สร้าง element ปลอมให้ถ้ายังไม่มีใครถามถึง id นั้น */
export const setInput = (el, id, value) => {
  (el[id] ??= fakeEl(id)).value = value;
};

/** ค่า textContent ที่ผู้ใช้เห็นบนตัวนับทั้งสามจุด */
export const counts = el => ({
  badge: el['badge-count']?.textContent,
  shown: el['rec-shown']?.textContent,
  total: el['rec-total']?.textContent,
});

/** stub fetch: server ตอบ 500 + JSON ไทย (ตรงกับที่ repro ฝั่ง pytest ยืนยัน) */
export const serverError = () => async () => ({
  ok: false,
  status: 500,
  json: async () => ({ error: 'เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง' }),
});

/** stub fetch: ไม่มี server เลย — fetch reject เอง จึงไม่มี httpStatus */
export const noServer = () => async () => { throw new TypeError('Failed to fetch'); };

/**
 * stub fetch: server ตอบ 401 เพราะเซสชันหมดอายุ
 * ต่างจาก noServer() คนละเรื่อง: มี backend อยู่และข้อมูลอยู่ครบ แค่เราไม่มีสิทธิ์
 * ตอนนี้ — ซึ่งเป็นเหตุผลทั้งหมดที่ 401 ต้องไม่พาไปโหมด LocalStorage
 */
export const unauthorized = () => async () => ({
  ok: false,
  status: 401,
  json: async () => ({ error: 'เซสชันหมดอายุ กรุณาเข้าสู่ระบบอีกครั้ง', login: '/login' }),
});

/** stub fetch: ตอบ rows สำเร็จ */
export const serverOk = rows => async () => ({ ok: true, status: 200, json: async () => rows });
