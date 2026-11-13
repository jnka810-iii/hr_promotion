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
import {
  load, counts, setInput, serverError, noServer, serverOk, unauthorized,
} from './client_harness.mjs';

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
// เซสชันหมดอายุ (401) — "ยังไม่ล็อกอิน" ไม่ใช่โหมดการทำงานตัวที่สาม
// ────────────────────────────────────────────────

test('เซสชันหมดอายุ (401): อยู่โหมด Flask ไม่ตกไป LocalStorage', async () => {
  const { mod, el } = await load({ fetch: unauthorized() });
  const mode = await mod.detectMode();

  assert.equal(mode, 'flask-unauthorized');
  assert.equal(mod.USE_FLASK, true,
    '401 หมายถึง backend มีอยู่และข้อมูลอยู่ครบ แค่เราไม่มีสิทธิ์อ่านตอนนี้ —' +
    ' สลับไป LocalStorage ที่ว่างแล้วรายงาน 0 รายการ คือบั๊กเดิมคนละสาเหตุ');
  assert.doesNotMatch(el['mode-badge'].innerHTML, /LocalStorage/);
  assert.match(el['mode-badge'].innerHTML, /เข้าสู่ระบบ/,
    'ป้ายต้องบอกสาเหตุจริง ไม่ใช่ "อ่านข้อมูลไม่สำเร็จ" ซึ่งพาไปแก้ผิดทาง');
});

test('เซสชันหมดอายุ (401): แถวแจ้งเตือนขึ้น พร้อมทางไปล็อกอิน', async () => {
  const { mod, el } = await load({ fetch: unauthorized() });
  mod.setUseFlask(true);
  await assert.rejects(() => mod.apiFetch('/api/records'), e => e.httpStatus === 401);

  assert.equal(el['auth-bar'].style.display, 'flex',
    'toast หายเองใน 6 วินาที ถ้าไม่มีแถวค้างไว้ผู้ใช้จะกดบันทึกซ้ำแล้วงงว่าทำไมไม่ได้');
});

test('เซสชันหมดอายุ (401): ข้อมูลเดิมบนจอไม่ถูกล้าง และตัวนับไม่กลายเป็น 0', async () => {
  let expired = false;
  const { mod, el } = await load({
    fetch: async (...a) => (expired ? unauthorized()(...a) : serverOk(REC(3))(...a)),
  });
  mod.setUseFlask(true);
  await mod.loadAll();

  expired = true;
  const state = await mod.loadAll();
  mod.renderTable();

  assert.equal(state, 'stale');
  assert.equal(mod.allRecords.length, 3, '401 ไม่ใช่เหตุผลที่จะทิ้งข้อมูลบนจอ');
  assert.notEqual(counts(el).badge, 0);
});

test('401 ต้องไม่ทำให้ loadAll throw — ผู้เรียกทั้ง 7 จุดยังไม่ต้องมี catch', async () => {
  const { mod } = await load({ fetch: unauthorized() });
  mod.setUseFlask(true);
  await assert.doesNotReject(() => mod.loadAll());
});

test('onUnauthorized ไม่พาออกจากหน้า — งานที่พิมพ์ไว้ในฟอร์มต้องไม่หาย', async () => {
  const { mod, el } = await load({ fetch: unauthorized() });
  setInput(el, 'p1_fname', 'สมชาย');
  mod.setUseFlask(true);
  await assert.rejects(() => mod.apiFetch('/api/records', 'POST', { p1_fname: 'สมชาย' }));

  assert.equal(el['p1_fname'].value, 'สมชาย',
    'redirect ทันทีตอน 401 = ทิ้งฟอร์ม 13 ช่องที่ผู้ใช้เพิ่งกรอก');
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

// ────────────────────────────────────────────────
// จัดการผู้ใช้งาน
// สิทธิ์คิดที่ server แล้ว ฝั่งนี้ต้องวาดตาม `can` ที่ได้มา ไม่ใช่เดาจากบทบาท
// ────────────────────────────────────────────────

const USR = (over = {}) => ({
  id: 1, username: 'somchai.k', display_name: 'สมชาย ใจดี', name: 'สมชาย ใจดี',
  role: 'staff', status: 'active', live_sessions: 0,
  created: '2026-01-02 09:00', updated: '2026-01-02 09:00',
  is_self: false, can: [], ...over,
});

const USR_META = (users, over = {}) => ({
  users, self_id: 99, can_create: true,
  grantable_roles: [['staff', 'เจ้าหน้าที่']],
  role_labels:   [['admin', 'ผู้ดูแลระบบ'], ['staff', 'เจ้าหน้าที่']],
  status_labels: [['active', 'ใช้งานได้'], ['disabled', 'ปิดบัญชี'], ['locked', 'ล็อกชั่วคราว']],
  password_hint: 'รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร', ...over,
});

test('แถวสถานะ colspan ตรงกับจำนวนคอลัมน์จริงของ #tbl-users', async () => {
  const { mod } = await load({ fetch: serverOk([]) });
  const html = await readFile(new URL('../templates/index.html', import.meta.url), 'utf8');
  const thead = html.match(/<table[^>]*id="tbl-users"[\s\S]*?<thead>([\s\S]*?)<\/thead>/)[1];
  const cols = (thead.match(/<th/g) || []).length;

  assert.equal(mod.USR_COLS, cols,
    'USR_COLS หลุดจากหัวตารางบัญชี แถวสถานะจะกินคอลัมน์ไม่พอดีแล้วตารางเบี้ยว');
});

test('ทุก data-sort บนหัวตารางบัญชีเป็นคีย์ที่ renderUsers เรียงได้จริง', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });
  const html = await readFile(new URL('../templates/index.html', import.meta.url), 'utf8');
  const thead = html.match(/<table[^>]*id="tbl-users"[\s\S]*?<thead>([\s\S]*?)<\/thead>/)[1];
  const keys = [...thead.matchAll(/data-sort="([^"]+)"/g)].map(m => m[1]);

  assert.ok(keys.length >= 4, 'หัวตารางบัญชีไม่มีคอลัมน์ที่เรียงได้เลย');
  const row = USR();
  for (const k of keys) {
    assert.ok(k in row, `หัวตารางเรียงด้วย "${k}" ซึ่ง /api/users ไม่ได้ส่งมา`);
  }
  // เรียงด้วยทุกคีย์แล้วต้องไม่พัง และจำนวนแถวต้องไม่หาย
  mod.setUsersView({ allUsers: [USR({ id: 1 }), USR({ id: 2, username: 'a.b', name: 'ก' })],
                     usersMeta: USR_META([]), usersState: 'loaded', usrPage: 1, usrPerPage: 10 });
  for (const k of keys) {
    mod.setUsersView({ usrSort: { key: k, dir: 1 } });
    mod.renderUsers();
    assert.equal((el['usr-tbody'].innerHTML.match(/<tr/g) || []).length, 2,
      `เรียงด้วย "${k}" แล้วแถวหาย`);
  }
});

test('โหมดออฟไลน์: บอกว่าต้องต่อเซิร์ฟเวอร์ ไม่ใช่วาดตารางเปล่า', async () => {
  const { mod, el } = await load({ fetch: noServer() });
  mod.setUseFlask(false);

  const state = await mod.loadUsers();

  assert.equal(state, 'offline', 'โหมดออฟไลน์ต้องมีสถานะของตัวเอง ไม่ใช่ failed');
  mod.renderUsers();
  assert.match(el['usr-tbody'].innerHTML, /ออฟไลน์/,
    'ตารางบัญชีต้องบอกว่าทำไมว่าง ไม่ใช่เงียบ');
  assert.doesNotMatch(el['usr-tbody'].innerHTML, /ลองใหม่/,
    'ปุ่มลองใหม่ในโหมดออฟไลน์คือการชวนให้กดซ้ำกับเซิร์ฟเวอร์ที่ไม่เคยมีอยู่');
});

test('โหลดบัญชีไม่สำเร็จ: ไม่ใช่ตารางว่างเงียบ ๆ และบอกผู้ใช้', async () => {
  const { mod, el, toasts } = await load({ fetch: serverError() });
  mod.setUseFlask(true);

  const state = await mod.loadUsers();

  assert.equal(state, 'failed');
  mod.renderUsers();
  assert.match(el['usr-tbody'].innerHTML, /โหลดบัญชีผู้ใช้ไม่สำเร็จ/);
  assert.equal(el['usr-range'].textContent, '—',
    'ตัวนับต้องเป็น UNKNOWN ตอนโหลดไม่สำเร็จ ไม่ใช่ 0 ซึ่งแปลว่า "ไม่มีบัญชีเลย"');
  assert.ok(toasts.length, 'ความล้มเหลวต้องถึงผู้ใช้ ไม่ตายใน unhandled rejection');
});

test('ปุ่มในแถวมาจาก can ที่ server ส่งมา ไม่ได้คิดจากบทบาทเอง', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });
  mod.setUsersView({
    allUsers: [
      USR({ id: 1, name: 'แก้ได้', can: ['user.rename', 'user.password'] }),
      USR({ id: 2, name: 'แตะไม่ได้', role: 'admin', can: [] }),
    ],
    usersMeta: USR_META([]), usersState: 'loaded',
    usrSort: { key: 'id', dir: 1 }, usrPage: 1, usrPerPage: 10,
  });

  mod.renderUsers();
  const rows = el['usr-tbody'].innerHTML.split('<tr').slice(1);

  assert.match(rows[0], /openUserForm\('edit',1\)/);
  assert.match(rows[0], /openUserForm\('reset',1\)/);
  assert.doesNotMatch(rows[0], /toggleUserActive/,
    'can ไม่มี user.active ปุ่มปิดบัญชีจึงต้องไม่โผล่');
  assert.doesNotMatch(rows[1], /openUserForm/,
    'แถวที่ can ว่างต้องไม่มีปุ่มสักตัว แม้หน้าจอจะเป็นของแอดมิน');
});

test('ปุ่มเพิ่มผู้ใช้งานโผล่ตาม can_create ไม่ใช่ตามบทบาทที่เดาเอง', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });

  mod.setUsersView({ allUsers: [], usersMeta: USR_META([], { can_create: false }),
                     usersState: 'loaded' });
  mod.renderUsers();
  assert.equal(el['usr-add-btn'].style.display, 'none');

  mod.setUsersView({ usersMeta: USR_META([], { can_create: true }) });
  mod.renderUsers();
  assert.equal(el['usr-add-btn'].style.display, '');
});

test('ชื่อที่ผู้ใช้ตั้งถูก escape ก่อนลง innerHTML', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });
  mod.setUsersView({
    allUsers: [USR({ name: '<img src=x onerror=alert(1)>' })],
    usersMeta: USR_META([]), usersState: 'loaded', usrPage: 1, usrPerPage: 10,
  });

  mod.renderUsers();

  assert.doesNotMatch(el['usr-tbody'].innerHTML, /<img src=x/,
    'ชื่อบัญชีลง innerHTML ตรง ๆ = stored XSS ที่ทุกคนในสำนักงานโดน');
  assert.match(el['usr-tbody'].innerHTML, /&lt;img/);
});

test('แบ่งหน้า: ตัวเลขท้ายตารางกับแถวที่วาดมาจากการคิดครั้งเดียวกัน', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });
  const many = Array.from({ length: 24 }, (_, i) => USR({ id: i + 1, username: 'u' + i }));
  mod.setUsersView({ allUsers: many, usersMeta: USR_META([]), usersState: 'loaded',
                     usrSort: { key: 'id', dir: 1 }, usrPage: 1, usrPerPage: 10 });

  mod.renderUsers();

  assert.equal((el['usr-tbody'].innerHTML.match(/<tr/g) || []).length, 10);
  assert.equal(el['usr-range'].textContent, '1–10 จาก 24 รายการ');
});

test('หน้าที่เกินขอบถูกหนีบตอนอ่าน ไม่ค้างอยู่หน้าว่างทั้งที่ข้อมูลยังอยู่', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });
  // เคยอยู่หน้า 3 แล้วบัญชีลดลงเหลือพอแค่หน้าเดียว
  mod.setUsersView({ allUsers: [USR()], usersMeta: USR_META([]), usersState: 'loaded',
                     usrSort: { key: 'id', dir: 1 }, usrPage: 3, usrPerPage: 10 });

  mod.renderUsers();

  assert.equal((el['usr-tbody'].innerHTML.match(/<tr/g) || []).length, 1);
  assert.equal(el['usr-range'].textContent, '1–1 จาก 1 รายการ');
});

test('pageOf คิดช่วงที่แสดงได้ตรงกับแถวที่ตัดออกมา', async () => {
  const { mod } = await load({ fetch: serverOk([]) });
  const rows = Array.from({ length: 7 }, (_, i) => i);

  assert.deepEqual(mod.pageOf(rows, 1, 3).slice, [0, 1, 2]);
  assert.deepEqual(mod.pageOf(rows, 3, 3).slice, [6]);
  assert.equal(mod.pageOf(rows, 3, 3).from, 7);
  assert.equal(mod.pageOf(rows, 3, 3).to, 7);
  assert.equal(mod.pageOf([], 1, 10).from, 0, 'ไม่มีแถวเลยต้องเริ่มที่ 0 ไม่ใช่ 1');
  assert.equal(mod.pageOf([], 1, 10).pages, 1, 'ตารางว่างยังมีหนึ่งหน้าเสมอ');
});

test('เวลาเรียงได้จาก API กลายเป็นรูปแบบที่คนอ่าน และเรียงตามเวลาจริง', async () => {
  const { mod } = await load({ fetch: serverOk([]) });

  assert.equal(mod.fmtStamp('2026-11-10 16:53'), '10/11/2026 16:53');
  assert.equal(mod.fmtStamp(''), '');
  // 02/01/2026 ต้องมาทีหลัง 31/12/2025 ซึ่งการเทียบ dd/mm/YYYY เป็น string ทำไม่ได้
  assert.ok(mod.cmpStamp('2025-12-31 23:59', '2026-01-02 00:01') < 0);
  assert.ok(mod.cmpStamp('2026-01-02 00:01', '') < 0, 'แถวที่ยังไม่มีค่าไปอยู่ท้าย');
  assert.ok(mod.cmpStamp('', '2026-01-02 00:01') > 0);
});

test('คำไทยของบทบาทและสถานะมาจาก server ไม่มีสำเนาในหน้าเว็บ', async () => {
  const { mod, el } = await load({ fetch: serverOk([]) });
  const html = await readFile(new URL('../templates/index.html', import.meta.url), 'utf8');
  const script = html.slice(html.indexOf('loadUsers'));

  assert.ok(!script.includes("admin:'ผู้ดูแลระบบ'") && !script.includes('admin: ผู้'),
    'คำแปลบทบาทต้องมาจาก role_labels ของ server ไม่ใช่ map ซ้ำในไฟล์นี้');

  mod.setUsersView({ allUsers: [USR({ role: 'admin', status: 'locked' })],
                     usersMeta: USR_META([]), usersState: 'loaded',
                     usrSort: { key: 'id', dir: 1 }, usrPage: 1, usrPerPage: 10 });
  mod.renderUsers();
  assert.match(el['usr-tbody'].innerHTML, /ผู้ดูแลระบบ/);
  assert.match(el['usr-tbody'].innerHTML, /ล็อกชั่วคราว/);
});

test('แท็บทุกตัวมีช่องเนื้อหาจริง และรายชื่อแท็บไม่ได้เขียนซ้ำใน JS', async () => {
  const html = await readFile(new URL('../templates/index.html', import.meta.url), 'utf8');
  const tabs  = [...html.matchAll(/class="tbtn[^"]*"[^>]*data-tab="([^"]+)"/g)].map(m => m[1]);
  const panes = [...html.matchAll(/id="(tab-[^"]+)"[^>]*class="tabpane"/g)].map(m => m[1]);

  assert.ok(tabs.includes('tab-users'), 'ไม่มีปุ่มแท็บจัดการผู้ใช้งาน');
  assert.deepEqual([...tabs].sort(), [...panes].sort(),
    'ปุ่มแท็บกับช่องเนื้อหาไม่ตรงกัน แท็บที่ไม่มีช่องจะคลิกแล้วหน้าว่าง');
  assert.doesNotMatch(html, /\['tab-form','tab-list'/,
    'อาเรย์รายชื่อแท็บกลับมาแล้ว — เพิ่มแท็บแล้วลืมเติมจะได้สองแท็บพร้อมกัน');
});
