/**
 * เทสต์พฤติกรรมฝั่ง client ของการโหลดข้อมูล — รันด้วย `node --test tests/`
 *
 * ขอบเขต: บั๊ก "โปรแกรมเรียกข้อมูลขึ้นมาแสดงไม่ได้ เป็น 0 รายการ"
 * `0` เคยแทนความหมายสามอย่างที่ต่างกันสิ้นเชิง (ว่างจริง / โหลดไม่สำเร็จ /
 * ยังไม่ได้โหลด) แล้วแสดงผลเหมือนกันหมด ผู้ใช้จึงแยกไม่ออกว่าข้อมูลหายหรือระบบพัง
 * ดู GLOSSARY.md และ docs/adr/0001-fallback-only-when-there-is-no-backend.md
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { load, counts, setInput, serverError, noServer, serverOk } from './client_harness.mjs';

const REC = n => Array.from({ length: n }, (_, i) => ({ id: i + 1, p1_fname: 'ชื่อ' + i }));
const DB_ERROR = 'เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง';

// ────────────────────────────────────────────────
// ข้อความ error ต้องถึงผู้ใช้ ไม่ตายใน unhandled rejection
// ────────────────────────────────────────────────

test('โหลดล้มเหลว: ข้อความไทยจาก server ถึงผู้ใช้ ไม่หายไปเฉย ๆ', async () => {
  const { mod, toasts } = await load({ fetch: serverError() });
  mod.setUseFlask(true);
  const state = await mod.loadAll();

  assert.equal(state, 'failed');
  assert.ok(toasts.includes(DB_ERROR),
    'ผู้ใช้ต้องเห็นข้อความจาก server แต่ toast ที่เกิดขึ้นคือ ' + JSON.stringify(toasts));
});

test('loadAll ไม่ throw — ผู้เรียกทั้ง 7 จุดจึงไม่มีทางปล่อยให้เงียบได้อีก', async () => {
  const { mod } = await load({ fetch: serverError() });
  mod.setUseFlask(true);
  // ถ้า reject บรรทัดนี้จะโยนออกไปและเทสต์แดง — นี่คือสัญญาของ loadAll
  await assert.doesNotReject(() => mod.loadAll());
});

// ────────────────────────────────────────────────
// "ไม่รู้" ต้องไม่แสดงเป็น 0 — ทั้งสามจุด
// ────────────────────────────────────────────────

test('โหลดล้มเหลว: ตัวนับทั้งสามจุดแสดง UNKNOWN ไม่ใช่ 0', async () => {
  const { mod, el } = await load({ fetch: serverError() });
  mod.setUseFlask(true);
  await mod.loadAll();
  mod.renderTable();

  const c = counts(el);
  // เทียบกับ mod.UNKNOWN เพียงอย่างเดียวเป็นการวนอ้างตัวเอง — ถ้าใครตั้ง
  // UNKNOWN = '0' มันจะยังผ่าน ต้องปักหมุดว่า "ไม่ใช่ศูนย์" แยกออกมาด้วย
  for (const [k, v] of Object.entries(c)) {
    assert.ok(!['0', 0, '', null, undefined].includes(v),
      `${k} แสดง ${JSON.stringify(v)} — 0 คือการตอบคำถามที่ตอบไม่ได้`);
  }
  assert.deepEqual(c, { badge: mod.UNKNOWN, shown: mod.UNKNOWN, total: mod.UNKNOWN },
    'ทั้งสามจุดต้องใช้เครื่องหมายเดียวกัน ได้จริง: ' + JSON.stringify(c));
});

test('โหลดสำเร็จและว่างจริง: 0 ถูกต้อง และต้องอ่านออกว่าว่าง ไม่ใช่ตารางเปล่า', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });
  mod.setUseFlask(true);
  await mod.loadAll();
  mod.renderTable();

  assert.equal(mod.loadState, 'loaded');
  assert.deepEqual(counts(el), { badge: 0, shown: 0, total: 0 });
  assert.match(el['tbody'].innerHTML, /ยังไม่มีข้อมูลในระบบ/,
    'tbody ว่างเปล่าอ่านไม่ออกว่าว่างจริงหรือพัง');
});

test('โหลดสำเร็จ: ตัวนับเป็นจำนวนจริง', async () => {
  const { mod, el } = await load({ fetch: serverOk(REC(4)) });
  mod.setUseFlask(true);
  await mod.loadAll();
  mod.renderTable();

  assert.deepEqual(counts(el), { badge: 4, shown: 4, total: 4 });
});

// ────────────────────────────────────────────────
// แถวสถานะในตาราง — toast หายเองใน 2-3 วินาที แถวนี้ไม่หาย
// ────────────────────────────────────────────────

test('โหลดล้มเหลว: ตารางมีแถวบอกเหตุ + ปุ่มลองใหม่', async () => {
  const { mod, el } = await load({ fetch: serverError() });
  mod.setUseFlask(true);
  await mod.loadAll();
  mod.renderTable();

  const tb = el['tbody'].innerHTML;
  assert.match(tb, /โหลดข้อมูลไม่สำเร็จ/);
  assert.match(tb, /onclick="retryLoad\(\)"/, 'ไม่มีปุ่มลองใหม่ ผู้ใช้ต้องเดาวิธีแก้เอง');
  assert.ok(tb.includes('colspan="' + mod.TBL_COLS + '"'));
});

test('แถวสถานะ colspan ตรงกับจำนวนคอลัมน์จริงของ #tbl', async () => {
  const { mod } = await load({ fetch: serverOk([]) });
  const html = await readFile(new URL('../templates/index.html', import.meta.url), 'utf8');
  const thead = html.match(/<table[^>]*id="tbl"[\s\S]*?<thead>([\s\S]*?)<\/thead>/)[1];
  const cols = (thead.match(/<th>/g) || []).length;

  assert.equal(mod.TBL_COLS, cols,
    'TBL_COLS หลุดจากหัวตาราง แถวสถานะจะกินคอลัมน์ไม่พอดีแล้วตารางเบี้ยว');
});

// ────────────────────────────────────────────────
// ข้อมูลเก่า: คงไว้แต่ติดป้าย ไม่ล้างทิ้ง
// ────────────────────────────────────────────────

test('โหลดสำเร็จแล้วรอบถัดไปล้ม: ข้อมูลเดิมยังอยู่ ติดป้ายว่าเก่า', async () => {
  let fail = false;
  const { mod, el } = await load({
    fetch: async (...a) => (fail ? serverError()(...a) : serverOk(REC(4))(...a)),
  });
  mod.setUseFlask(true);

  await mod.loadAll();
  assert.equal(mod.loadState, 'loaded');

  fail = true;
  const state = await mod.loadAll();
  mod.renderTable();

  assert.equal(state, 'stale');
  assert.equal(mod.allRecords.length, 4, 'ล้างข้อมูลทิ้ง = ทำลายชุดเดียวที่ผู้ใช้มีอยู่บนจอ');
  assert.equal(counts(el).badge, 4, 'stale รู้จำนวน (ของรอบก่อน) จึงแสดงตัวเลขได้');
  assert.match(el['tbody'].innerHTML, /เป็นของการโหลดครั้งก่อน/,
    'ข้อมูลเก่าที่ไม่ติดป้ายทำให้ผู้ใช้เข้าใจว่าสด');
  assert.match(el['tbody'].innerHTML, /ชื่อ0/, 'แถวจริงต้องยังแสดงอยู่');
});

// ────────────────────────────────────────────────
// นโยบาย fallback: "ไม่มี backend" ไม่เท่ากับ "backend ล้ม"
// ────────────────────────────────────────────────

test('ไม่มี backend เลย (fetch reject): เข้าโหมด LocalStorage ไฟแดง', async () => {
  const { mod, el } = await load({ fetch: noServer() });
  const mode = await mod.detectMode();

  assert.equal(mode, 'offline');
  assert.equal(mod.USE_FLASK, false);
  assert.match(el['mode-badge'].innerHTML, /dot dr/);
});

test('backend ล้ม (500): อยู่โหมด Flask ไฟเหลือง ไม่ตกไป LocalStorage', async () => {
  const { mod, el } = await load({ fetch: serverError() });
  const mode = await mod.detectMode();

  assert.equal(mode, 'flask-error');
  assert.equal(mod.USE_FLASK, true,
    '500 หมายถึง backend มีอยู่และมีข้อมูล สลับไปอ่าน LocalStorage ที่ว่าง' +
    ' = เอาที่เก็บผิดอันมาตอบแล้วรายงานว่า 0 รายการ');
  assert.match(el['mode-badge'].innerHTML, /dot dy/);
  assert.doesNotMatch(el['mode-badge'].innerHTML, /LocalStorage/);
});

test('backend ปกติ: ไฟเขียว โหมด Flask', async () => {
  const { mod, el } = await load({ fetch: serverOk(REC(4)) });
  const mode = await mod.detectMode();

  assert.equal(mode, 'flask');
  assert.equal(mod.USE_FLASK, true);
  assert.match(el['mode-badge'].innerHTML, /dot dg/);
});

test('apiFetch ติด httpStatus เฉพาะตอน server ตอบ — นี่คือสิ่งที่แยกสองกรณีนั้น', async () => {
  const a = await load({ fetch: serverError() });
  await assert.rejects(() => a.mod.apiFetch('/api/records'), e => e.httpStatus === 500);

  const b = await load({ fetch: noServer() });
  await assert.rejects(() => b.mod.apiFetch('/api/records'), e => e.httpStatus === undefined);
});

// ────────────────────────────────────────────────
// รูปร่างข้อมูลผิด
// ────────────────────────────────────────────────

test('server ตอบของที่ไม่ใช่ array: ถือว่าโหลดไม่สำเร็จ ไม่ปล่อยไปพังที่อื่น', async () => {
  const { mod, toasts } = await load({ fetch: serverOk({ error: 'อะไรบางอย่าง' }) });
  mod.setUseFlask(true);
  const state = await mod.loadAll();

  assert.equal(state, 'failed');
  assert.ok(toasts.length, 'พังเงียบคือสิ่งที่งานนี้กำลังแก้');
});

// ────────────────────────────────────────────────
// ว่างเพราะกรอง ไม่เท่ากับ ว่างเพราะไม่มีข้อมูล
// ────────────────────────────────────────────────

test('กรองจนไม่เหลือ: บอกว่าไม่ตรงเงื่อนไข ไม่ใช่บอกว่าไม่มีข้อมูล', async () => {
  const { mod, el } = await load({ fetch: serverOk(REC(4)) });
  mod.setUseFlask(true);
  await mod.loadAll();
  setInput(el, 'search-input', 'ไม่มีชื่อนี้อยู่จริง');
  mod.renderTable();

  assert.match(el['tbody'].innerHTML, /ไม่พบรายการที่ตรงกับคำค้นหา/);
  assert.doesNotMatch(el['tbody'].innerHTML, /ยังไม่มีข้อมูลในระบบ/,
    'สองสาเหตุนี้แก้คนละวิธี ผู้ใช้ต้องรู้ว่าตัวเองเจออันไหน');
  assert.deepEqual(counts(el), { badge: 4, shown: 0, total: 4 });
});
