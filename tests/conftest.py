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
"""

import io
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as hr  # noqa: E402  (ต้อง insert path ก่อน import)


@pytest.fixture
def hr_app(tmp_path, monkeypatch):
    """app จริง แต่ชี้ DB และโฟลเดอร์อัปโหลดไปที่ temp dir"""
    monkeypatch.setattr(hr, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(hr, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    os.makedirs(hr.UPLOAD_FOLDER, exist_ok=True)
    hr.init_db()
    hr.app.config['TESTING'] = True
    return hr.app


@pytest.fixture
def client(hr_app):
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


@pytest.fixture
def make_record(client):
    """สร้าง record ที่ผ่าน validate_required แล้วคืน id"""

    def _make(**overrides):
        payload = {
            'p1_fname': 'สมชาย',
            'p1_lname': 'ใจดี',
            'p2_pos_current': 'นักวิชาการคอมพิวเตอร์',
            'p2_empid': '12345',
        }
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
    """ครอบ sqlite3.Connection แล้วโยน exception ที่กำหนด เมื่อ SQL ขึ้นต้นตาม prefix"""

    def __init__(self, con, prefix, exc):
        self._con, self._prefix, self._exc = con, prefix, exc

    def execute(self, sql, *a, **kw):
        if sql.lstrip().upper().startswith(self._prefix):
            raise self._exc('simulated DB failure')
        return self._con.execute(sql, *a, **kw)

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

    def __call__(self, prefix, exc=sqlite3.OperationalError):
        hr.get_db = lambda: _FailOnConnection(self._real_get_db(), prefix.upper(), exc)

    def disarm(self):
        hr.get_db = self._real_get_db


@pytest.fixture
def fail_sql():
    injector = _SqlFaultInjector()
    yield injector
    injector.disarm()
