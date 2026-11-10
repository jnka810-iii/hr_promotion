# -*- coding: utf-8 -*-
"""
fixtures กลางสำหรับชุด test ของ hr_promotion

หลักการเดียวที่ห้ามละเมิด: **test ต้องไม่แตะ `hr_promotion.db` และ `static/uploads/` ของจริง**
ทุก test จึง monkeypatch `app.DB_PATH` กับ `app.UPLOAD_FOLDER` ไปที่ `tmp_path` ของ pytest
ซึ่งเป็นโฟลเดอร์ใหม่ต่อ test หนึ่งตัว (pytest เก็บย้อนหลังไว้ 3 รอบแล้วลบเอง)

หมายเหตุสำหรับ Windows: `send_file` ยังถือ handle ของไฟล์ไว้จนกว่า response จะถูกปิด
ถ้า test ดาวน์โหลดไฟล์แล้วไม่ `close()` การลบไฟล์ครั้งถัดไปจะล้มด้วย `PermissionError`
(ซึ่ง `_safe_remove` กลืนไว้) ทำให้ assert เรื่องไฟล์ถูกลบ fail แบบงง ๆ
ใช้ fixture `download` ที่ปิด response ให้เสมอ แทนการเรียก `client.get()` ตรง ๆ

ตั้งแต่มีระบบล็อกอิน ทุก route ตอบ 401 ถ้าไม่มีเซสชัน fixture `client` จึงล็อกอิน
เป็น admin ให้เสร็จก่อนคืนออกมา เทสต์ที่มีอยู่เดิมทั้งหมดจึงทำงานต่อได้โดยไม่ต้องแก้
ทีละไฟล์ (`make_record` / `upload` / `download` พึ่ง `client` ตัวนี้ทั้งหมด)
ใช้ `anon_client` เมื่อต้องการทดสอบสภาพ "ยังไม่ล็อกอิน" และ `staff_client` เมื่อต้องการ
ทดสอบขอบเขตของบทบาท **ไม่มี** flag ปิด auth ตอนเทสต์ และจะต้องไม่มี: flag แบบนั้นคือ
ประตูหลังที่ติดไปอยู่ในโค้ดจริงรอให้ใครตั้งผิด แล้ววันที่มันถูกตั้งผิด เทสต์ทุกตัว
จะยังเขียวอยู่
"""

import io
import os
import sqlite3
import sys
from contextlib import closing

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as hr  # noqa: E402  (ต้อง insert path ก่อน import)

# บัญชีที่ทุกเทสต์ใช้ — ชื่อชัดเจนว่ามาจากเทสต์ เผื่อโผล่ไปอยู่ใน log ที่ไหน
ADMIN_USER, ADMIN_PASS = 'tester.admin', 'test-password-1'
STAFF_USER, STAFF_PASS = 'tester.staff', 'test-password-2'

# SQL ของด่าน require_login — fault injector ต้องไม่ล้ม query นี้ (ดู _FailOnConnection)
AUTH_SQL_MARK = 'FROM sessions s JOIN users u'


def _cheap_hash(password):
    """
    แฮชแบบ "ถูก" สำหรับเทสต์เท่านั้น — ยังเป็นแฮชของ werkzeug จริง แค่ลดรอบลงเหลือ 1

    ของจริงใช้ scrypt ซึ่งจงใจคิดช้า (นั่นคือทั้งหมดของประโยชน์มัน) ถ้าปล่อยให้เทสต์
    ใช้ค่าจริง การสร้างผู้ใช้สองคนบวกการล็อกอินหนึ่งครั้งต่อหนึ่งเทสต์จะทำให้ชุดเทสต์
    ที่เคยวิ่ง 3 วินาทีกลายเป็นเกือบนาที รูปแบบสตริงที่ได้ยังเป็นของ werkzeug เหมือนเดิม
    `check_password_hash` จึงยังถูกเดินผ่านจริง ไม่ได้ถูกข้าม

    ส่วนการพิสูจน์ว่า *ของจริง* ใช้ KDF ที่ช้าพอ อยู่ใน test_auth.py แยกไว้หนึ่งตัว
    """
    return generate_password_hash(password, method='pbkdf2:sha256:1')


@pytest.fixture
def hr_app(tmp_path, monkeypatch):
    """app จริง แต่ชี้ DB และโฟลเดอร์อัปโหลดไปที่ temp dir และมีผู้ใช้สองบทบาทรออยู่"""
    monkeypatch.setattr(hr, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(hr, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    monkeypatch.setattr(hr, 'hash_password', _cheap_hash)
    os.makedirs(hr.UPLOAD_FOLDER, exist_ok=True)
    hr.init_db()
    with closing(hr.get_db()) as con:
        hr.create_user(con, ADMIN_USER, ADMIN_PASS, hr.ROLE_ADMIN)
        hr.create_user(con, STAFF_USER, STAFF_PASS, hr.ROLE_STAFF)
        con.commit()
    hr.app.config['TESTING'] = True
    return hr.app


def login_as(hr_app, username, password):
    """
    คืน test client ที่ล็อกอินแล้ว (cookie เซสชันอยู่ใน cookie jar ของ client ตัวนั้น)

    assert ที่นี่เลยเพราะถ้า fixture ล็อกอินไม่ผ่าน เทสต์ปลายทางจะ fail ด้วย 401
    กองใหญ่ที่ชี้ไปผิดที่ — ความพังควรรายงานตัวเองที่จุดที่มันเกิด
    """
    c = hr_app.test_client()
    r = c.post('/login', data={'username': username, 'password': password})
    assert r.status_code == 302, 'fixture ล็อกอินไม่ผ่าน: ' + r.get_data(as_text=True)[:200]
    return c


@pytest.fixture
def client(hr_app):
    """client ที่ล็อกอินเป็น admin — ค่าเริ่มต้นของเทสต์เกือบทั้งหมด"""
    return login_as(hr_app, ADMIN_USER, ADMIN_PASS)


@pytest.fixture
def staff_client(hr_app):
    """client ที่ล็อกอินเป็น staff — ใช้ทดสอบขอบเขตของบทบาท"""
    return login_as(hr_app, STAFF_USER, STAFF_PASS)


@pytest.fixture
def anon_client(hr_app):
    """client ที่ไม่ล็อกอินเลย — ใช้ทดสอบว่าทุก route ปิดอยู่จริง"""
    return hr_app.test_client()


@pytest.fixture
def db(hr_app):
    """คืน callable ที่เปิด connection ใหม่ไปยัง DB ของ test (row_factory=Row)"""

    def _open():
        con = sqlite3.connect(hr.DB_PATH)
        con.row_factory = sqlite3.Row
        return con

    return _open


@pytest.fixture
def uploads(hr_app):
    """คืน callable ที่ list ไฟล์จริงในโฟลเดอร์อัปโหลด (ไม่รวมไฟล์ชั่วคราวของ SQLite)"""

    def _ls():
        return sorted(os.listdir(hr.UPLOAD_FOLDER))

    return _ls


# record ที่กรอกฟิลด์บังคับครบตาม `REQUIRED_FIELDS` พอดี — ไม่มากไม่น้อยกว่านั้น
# เขียนคีย์ไว้ตรง ๆ ไม่ derive จาก `hr.REQUIRED_FIELDS` เพราะ payload ที่สร้างจาก
# ตัวที่กำลังทดสอบอยู่จะผ่านเสมอไม่ว่ารายการฝั่ง server จะเปลี่ยนไปเป็นอะไร
# ถ้าสองฝั่งหลุดจากกันเมื่อไร `test_item25_payload_matches_the_server_list` จะบอกจุดเดียวจบ
VALID_RECORD = {
    'p1_title':         'นาย',
    'p1_fname':         'สมชาย',
    'p1_lname':         'ใจดี',
    'p2_pos_current':   'นักวิชาการคอมพิวเตอร์',
    'p2_pos_level':     'ชำนาญการ',
    'p2_empid':         '12345',
    'p2_dept':          'กลุ่มสรรหาและข้อมูลบุคคล',
    'p2_sec':           'กองการเจ้าหน้าที่',
    'p3_pos':           'นักวิเคราะห์นโยบายและแผน',
    'p3_pos_level':     'ชำนาญการพิเศษ',
    'p3_empid':         '67890',
    'p4_work1_title':   'ผลงานเรื่องที่ 1',
    'p4_concept_title': 'ข้อเสนอแนวคิดการพัฒนางาน',
}


@pytest.fixture
def make_record(client):
    """สร้าง record ที่ผ่าน validate_required แล้วคืน id"""

    def _make(**overrides):
        payload = dict(VALID_RECORD)
        payload.update(overrides)
        r = client.post('/api/records', json=payload)
        assert r.status_code == 201, r.get_data(as_text=True)
        return r.get_json()['id']

    return _make


@pytest.fixture
def upload(client):
    """อัปโหลดไฟล์แบบ multipart เหมือนที่ frontend ทำจริง"""

    def _upload(rid, book_no='1', name='ผลงาน.pdf', data=b'%PDF-1.4 test'):
        return client.post(
            '/api/records/%s/attachments' % rid,
            data={'book_no': book_no, 'file': (io.BytesIO(data), name)},
            content_type='multipart/form-data',
        )

    return _upload


@pytest.fixture
def download(client):
    """ดาวน์โหลดแล้วคืน (status_code, body, headers) โดยปิด response ให้ด้วย — ดู docstring ด้านบน"""

    def _download(att_id, preview=False):
        url = '/api/attachments/%s%s' % (att_id, '/preview' if preview else '')
        r = client.get(url)
        try:
            return r.status_code, r.get_data(), dict(r.headers)
        finally:
            r.close()

    return _download


class _FailOnConnection:
    """
    ครอบ sqlite3.Connection แล้วโยน exception ที่กำหนด เมื่อ SQL ขึ้นต้นตาม prefix
    และจดไว้ว่า `close()` ถูกเรียกหรือยัง — ตัวจดนี้คือสิ่งที่ทำให้ test ข้อ 11
    มองเห็น connection ที่ route เปิดแล้วไม่ปิดได้ (ดู `_SqlFaultInjector.unclosed`)

    ยกเว้น query ของด่าน require_login ไว้หนึ่งตัว: ด่านเปิด connection ของตัวเองและ
    ยิง SELECT ก่อนทุก request ถ้า injector ล้ม query นั้นด้วย request จะตายที่ด่าน
    route ไม่ได้รันเลย แล้วเทสต์ทั้งหมดข้างล่างจะกลายเป็นการทดสอบด่านซ้ำ ๆ แทนที่จะ
    ทดสอบ route ที่มันตั้งใจทดสอบ โดยยังเขียวอยู่ทั้งแถบ — ความพังที่มองไม่เห็น
    ส่วนด่านเองมีเทสต์ของมันใน test_auth.py (บังคับให้ resolve_session ล้ม)
    """

    def __init__(self, con, prefix, exc):
        self._con, self._prefix, self._exc = con, prefix, exc
        self.closed = False

    def execute(self, sql, *a, **kw):
        if AUTH_SQL_MARK not in sql and sql.lstrip().upper().startswith(self._prefix):
            raise self._exc('simulated DB failure')
        return self._con.execute(sql, *a, **kw)

    def close(self):
        self.closed = True
        return self._con.close()

    def __getattr__(self, name):
        return getattr(self._con, name)


class _SqlFaultInjector:
    """
    fault injection สำหรับทดสอบลำดับ "ลบไฟล์หลัง commit" (ข้อ 7)
    ซึ่งเขียน test ด้วยวิธีอื่นไม่ได้ ต้องบังคับให้ DB ล้มกลางทางจริงเท่านั้น

    `disarm()` ปลดเฉพาะ `get_db` — จงใจไม่ใช้ `monkeypatch` เพราะ `monkeypatch.undo()`
    จะคืน `DB_PATH` กับ `UPLOAD_FOLDER` ที่ fixture `hr_app` ตั้งไว้ด้วย
    แล้ว test ที่เหลือจะไปเขียน `hr_promotion.db` ของจริง
    """

    def __init__(self):
        self._real_get_db = hr.get_db
        self.handed_out = []

    def __call__(self, prefix, exc=sqlite3.OperationalError):
        def _open():
            con = _FailOnConnection(self._real_get_db(), prefix.upper(), exc)
            self.handed_out.append(con)
            return con

        hr.get_db = _open

    def unclosed(self):
        """connection ที่ route เปิดไปแล้วไม่ได้ปิด — ต้องว่างเสมอหลัง request จบ"""
        return [c for c in self.handed_out if not c.closed]

    def disarm(self):
        hr.get_db = self._real_get_db


@pytest.fixture
def fail_sql():
    injector = _SqlFaultInjector()
    yield injector
    injector.disarm()
