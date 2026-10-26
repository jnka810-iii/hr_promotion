# -*- coding: utf-8 -*-
"""
ชุด test ที่กันข้อที่แก้แล้วใน CODE_REVIEW_2026-10-22.md ไม่ให้ย้อนกลับมา

ขอบเขตที่เลือก: เอาเฉพาะเส้นทางที่ "พังเงียบ" ได้ คือพังแล้วไม่มีใครรู้จนผู้ใช้เจอเอง
- ข้อ 5  ภาษาไทยใน JSON ถูก escape กลับเป็น \\uXXXX (UI ยังทำงาน payload โตเงียบ ๆ)
- ข้อ 6  ชื่อไฟล์ไทยถูกตัดทิ้ง
- ข้อ 7  ลำดับลบไฟล์เทียบกับ commit (ต้อง fault-inject เท่านั้นถึงจะเห็น)
- ข้อ 10 server ไม่ตรวจว่าไฟล์เป็น PDF จริงและไม่จำกัดขนาด (ด่านฝั่ง client ยกเลิกได้ง่าย)
- ข้อ 19 อัปโหลดไป record ที่ไม่มีอยู่ ต้องตอบ 404 ไม่ใช่ 500 และต้องไม่ทิ้งไฟล์ขยะไว้

รัน: .venv/Scripts/python.exe -m pytest
"""

import base64
import os
import sqlite3

import pytest

import app as hr


# ────────────────────────────────────────────────
# ข้อ 5 — ภาษาไทยใน JSON ต้องไม่ถูก escape
# ────────────────────────────────────────────────

def test_item5_json_provider_configured():
    """ต้องเซ็ตที่ app.json ไม่ใช่ config['JSON_AS_ASCII'] ซึ่ง Flask >= 2.3 เพิกเฉย"""
    assert hr.app.json.ensure_ascii is False


def test_item5_thai_readable_in_raw_list_response(client, make_record):
    make_record()
    r = client.get('/api/records')
    body = r.get_data(as_text=True)
    assert 'สมชาย' in body
    assert '\\u0e2a' not in body          # 'ส' ในรูป escape
    assert r.headers['Content-Type'].startswith('application/json')


def test_item5_thai_readable_in_single_record_and_eval_rows(client, make_record):
    rid = make_record()
    rows = [{'date': '2026-04-21', 'score': '90', 'grade': 'ผ่าน (≥60%)'}]
    r = client.put('/api/records/%d' % rid, json={
        'p1_fname': 'สมหญิง', 'p1_lname': 'ใจดี',
        'p2_pos_current': 'นักวิชาการคอมพิวเตอร์', 'p2_empid': '12345',
        'eval51_rows': rows,
    })
    assert r.status_code == 200
    body = client.get('/api/records/%d' % rid).get_data(as_text=True)
    assert 'สมหญิง' in body
    assert 'ผ่าน' in body                 # ไทยที่ซ้อนอยู่ใน JSON string ของ eval51_rows
    assert '\\u0e1c' not in body


def test_item5_escaping_costs_payload_size(client, make_record):
    """ยืนยันว่าไม่ใช่เรื่องความสวยงามเท่านั้น — escape แล้ว payload โตจริง"""
    make_record()
    size_readable = len(client.get('/api/records').get_data())
    hr.app.json.ensure_ascii = True
    try:
        size_escaped = len(client.get('/api/records').get_data())
    finally:
        hr.app.json.ensure_ascii = False
    assert size_escaped > size_readable


# ────────────────────────────────────────────────
# ข้อ 6 — ชื่อไฟล์ภาษาไทยต้องรอด
# ────────────────────────────────────────────────

@pytest.mark.parametrize('raw, expected', [
    ('ผลงานเล่มที่1.pdf', 'ผลงานเล่มที่1.pdf'),
    ('รายงาน ประจำปี 2569.pdf', 'รายงาน ประจำปี 2569.pdf'),
    ('ประกาศ.pdf', 'ประกาศ.pdf'),          # secure_filename เคยคืน 'pdf' — นามสกุลหายทั้งก้อน
    ('a\\b/c\x00\x1f.pdf', 'abc.pdf'),      # ตัดตัวคั่น path + อักขระควบคุม
    ('  เล่ม 1.pdf  ', 'เล่ม 1.pdf'),
    ('', 'book_1.pdf'),                     # ว่างแล้วใช้ fallback
    ('   ', 'book_1.pdf'),
    (None, 'book_1.pdf'),
])
def test_item6_sanitize_display_name(raw, expected):
    assert hr.sanitize_display_name(raw, 'book_1.pdf') == expected


def test_item6_sanitize_strips_path_separators():
    got = hr.sanitize_display_name('../../etc/passwd.pdf', 'book_1.pdf')
    assert '/' not in got and '\\' not in got


def test_item6_sanitize_clamps_length():
    assert len(hr.sanitize_display_name('ก' * 300 + '.pdf', 'book_1.pdf')) == 255


def test_item6_thai_filename_survives_upload_and_download(client, make_record, upload, db, download):
    rid = make_record()
    r = upload(rid, name='ผลงานเล่มที่1.pdf')
    assert r.status_code == 201
    assert r.get_json()['filename'] == 'ผลงานเล่มที่1.pdf'

    row = db().execute('SELECT id, filename, stored_name FROM attachments WHERE record_id=?',
                       [rid]).fetchone()
    assert row['filename'] == 'ผลงานเล่มที่1.pdf'
    assert row['stored_name'].endswith('.pdf') and len(row['stored_name']) == 36  # uuid4().hex + '.pdf'

    status, body, headers = download(row['id'])
    assert status == 200
    assert body == b'%PDF-1.4 test'
    # Flask encode ชื่อ non-ASCII ตาม RFC 5987 ให้เอง เบราว์เซอร์จึงเซฟเป็นชื่อไทย
    assert "filename*=UTF-8''%E0%B8%9C%E0%B8%A5%E0%B8%87" in headers['Content-Disposition']


def test_item6_list_attachments_never_leaks_stored_name(client, make_record, upload):
    rid = make_record()
    upload(rid, name='ประกาศ.pdf')
    items = client.get('/api/records/%d/attachments' % rid).get_json()
    assert items[0]['filename'] == 'ประกาศ.pdf'
    assert 'stored_name' not in items[0]


# ────────────────────────────────────────────────
# ข้อ 7 — ลบไฟล์บนดิสก์หลัง commit เท่านั้น
# ────────────────────────────────────────────────

def test_item7_replace_removes_old_file_after_commit(client, make_record, upload, db, uploads):
    rid = make_record()
    upload(rid, name='เล่มเก่า.pdf', data=b'%PDF-1.4 old')
    old_stored = db().execute('SELECT stored_name FROM attachments WHERE record_id=?',
                              [rid]).fetchone()['stored_name']

    assert upload(rid, name='เล่มใหม่.pdf', data=b'%PDF-1.4 new').status_code == 201

    rows = db().execute('SELECT filename, stored_name FROM attachments WHERE record_id=?',
                        [rid]).fetchall()
    assert len(rows) == 1                                   # upsert ต่อช่อง book_no
    assert rows[0]['filename'] == 'เล่มใหม่.pdf'
    assert not os.path.isfile(hr._stored_path(old_stored))  # ไฟล์เก่าถูกลบ
    assert os.path.isfile(hr._stored_path(rows[0]['stored_name']))
    assert len(uploads()) == 1                              # ไม่มีไฟล์กำพร้าค้าง


def test_item7_insert_failure_keeps_old_file_reachable(
        client, make_record, upload, db, uploads, download, fail_sql):
    """DB ล้มกลางทาง: row เก่าต้องยังชี้ไปยังไฟล์ที่มีอยู่จริง ไม่ใช่ไฟล์ที่ถูกลบไปแล้ว"""
    rid = make_record()
    upload(rid, name='เล่มเก่า.pdf', data=b'%PDF-1.4 old')
    before_row = db().execute('SELECT id, filename, stored_name FROM attachments WHERE record_id=?',
                              [rid]).fetchone()
    before_files = uploads()

    fail_sql('INSERT INTO ATTACHMENTS')
    r = upload(rid, name='เล่มใหม่.pdf', data=b'%PDF-1.4 new')
    assert r.status_code == 500

    after_row = db().execute('SELECT id, filename, stored_name FROM attachments WHERE record_id=?',
                             [rid]).fetchone()
    assert dict(after_row) == dict(before_row)              # rollback คืน row เดิมครบ
    assert os.path.isfile(hr._stored_path(after_row['stored_name']))
    assert download(after_row['id'])[1] == b'%PDF-1.4 old'      # ดาวน์โหลดได้ ไม่ใช่ 404
    assert uploads() == before_files                        # ไฟล์ใหม่ที่เพิ่งเขียนถูกเก็บกวาด


def test_item7_delete_record_failure_keeps_files(
        client, make_record, upload, db, uploads, fail_sql):
    rid = make_record()
    upload(rid, name='ผลงาน.pdf')
    before_files = uploads()

    fail_sql('DELETE FROM RECORDS')
    assert client.delete('/api/records/%d' % rid).status_code == 500

    assert db().execute('SELECT 1 FROM records WHERE id=?', [rid]).fetchone() is not None
    assert uploads() == before_files


def test_item7_delete_record_success_removes_files(client, make_record, upload, db, uploads):
    rid = make_record()
    upload(rid, book_no='1')
    upload(rid, book_no='ann', name='ประกาศ.pdf')
    assert len(uploads()) == 2

    assert client.delete('/api/records/%d' % rid).status_code == 200
    assert uploads() == []
    # CASCADE กวาดแถว attachments ตาม record
    assert db().execute('SELECT count(*) c FROM attachments').fetchone()['c'] == 0


def test_item7_delete_attachment_failure_then_retry(
        client, make_record, upload, db, uploads, fail_sql):
    rid = make_record()
    upload(rid)
    att_id = db().execute('SELECT id FROM attachments WHERE record_id=?', [rid]).fetchone()['id']
    before_files = uploads()

    fail_sql('DELETE FROM ATTACHMENTS')
    assert client.delete('/api/attachments/%d' % att_id).status_code == 500
    assert uploads() == before_files        # ไฟล์ยังอยู่คู่กับ row เดิม
    assert db().execute('SELECT 1 FROM attachments WHERE id=?', [att_id]).fetchone() is not None

    # ปลด fault injection แล้วลองใหม่ — ต้องสำเร็จและไฟล์หายไปจริง (สถานะไม่ค้างครึ่ง ๆ)
    fail_sql.disarm()
    assert client.delete('/api/attachments/%d' % att_id).status_code == 200
    assert uploads() == []
    assert db().execute('SELECT 1 FROM attachments WHERE id=?', [att_id]).fetchone() is None


def test_item7_delete_all_success_removes_every_file(client, make_record, upload, db, uploads):
    rid1, rid2 = make_record(), make_record(p2_empid='67890')
    upload(rid1)
    upload(rid2, name='ประกาศ.pdf')
    assert len(uploads()) == 2

    assert client.delete('/api/records').status_code == 200
    assert uploads() == []
    assert db().execute('SELECT count(*) c FROM records').fetchone()['c'] == 0


# ────────────────────────────────────────────────
# เส้นทางพื้นฐานที่ควรมี guard ไว้
# ────────────────────────────────────────────────

def test_required_fields_rejected_with_thai_labels(client):
    r = client.post('/api/records', json={'p1_fname': 'สมชาย'})
    assert r.status_code == 422
    assert 'นามสกุล' in r.get_json()['error']


def test_update_missing_record_returns_404(client):
    r = client.put('/api/records/9999', json={
        'p1_fname': 'ก', 'p1_lname': 'ข', 'p2_pos_current': 'ค', 'p2_empid': '1'})
    assert r.status_code == 404


def test_non_json_body_returns_400(client):
    assert client.post('/api/records', data='not json',
                       content_type='application/json').status_code == 400


def test_stats_counts_records(client, make_record):
    make_record()
    assert client.get('/api/stats').get_json()['total'] == 1


def test_csv_export_has_bom_for_excel(client, make_record):
    make_record()
    body = client.get('/api/export/csv').get_data()
    assert body.startswith(b'\xef\xbb\xbf')     # UTF-8 BOM ไม่งั้น Excel อ่านไทยเป็นขยะ


# ────────────────────────────────────────────────
# ข้อ 10 — server ต้องตรวจเนื้อหาและขนาดไฟล์เอง ไม่เชื่อ client
# ────────────────────────────────────────────────

def test_item10_rejects_non_pdf_content(client, make_record, upload, db, uploads):
    rid = make_record()
    r = upload(rid, name='virus.exe', data=b'MZ\x90\x00 this is a PE binary')
    assert r.status_code == 400
    assert 'PDF' in r.get_json()['error']
    # ด่านนี้ต้องอยู่ก่อนแตะดิสก์ ไม่ใช่เขียนแล้วตามลบ
    assert uploads() == []
    assert db().execute('SELECT count(*) c FROM attachments').fetchone()['c'] == 0


def test_item10_rejects_pdf_extension_with_fake_content(client, make_record, upload):
    """ตั้งชื่อ .pdf แต่ข้างในไม่ใช่ PDF ต้องไม่ผ่าน — นามสกุลเป็นสิ่งที่ client ควบคุมได้"""
    r = upload(make_record(), name='ผลงาน.pdf', data=b'<html>not a pdf at all</html>')
    assert r.status_code == 400


def test_item10_rejects_empty_file(client, make_record, upload, uploads):
    r = upload(make_record(), data=b'')
    assert r.status_code == 400
    assert uploads() == []


def test_item10_accepts_pdf_with_leading_bom(client, make_record, upload):
    """ไฟล์จริงจากเครื่องสแกน/โปรแกรมบางตัวมี BOM นำหน้า — PDF reader ก็ยังอ่านได้ จึงต้องไม่บล็อก"""
    r = upload(make_record(), data=b'\xef\xbb\xbf%PDF-1.7 scanned')
    assert r.status_code == 201


def test_item10_rejects_pdf_header_too_deep_in_file(client, make_record, upload):
    """%PDF- ที่โผล่หลังไบต์ที่ 1024 ไม่นับ — ไม่งั้นแค่แปะสตริงท้ายไฟล์อะไรก็ผ่าน"""
    r = upload(make_record(), data=b'\x00' * 2048 + b'%PDF-1.4')
    assert r.status_code == 400


def test_item10_size_limit_matches_what_the_ui_promises():
    assert hr.MAX_PDF_SIZE == 100 * 1024 * 1024     # templates/index.html บอกผู้ใช้ว่า 100 MB


def test_item10_rejects_oversized_file(client, make_record, upload, uploads, monkeypatch):
    """ลดเพดานลงมาเพื่อทดสอบ branch โดยไม่ต้องยิงไฟล์ 100 MB ผ่าน test client"""
    monkeypatch.setattr(hr, 'MAX_PDF_SIZE', 1024)
    r = upload(make_record(), data=b'%PDF-1.4' + b'x' * 2048)
    assert r.status_code == 413
    assert '100 MB' in r.get_json()['error']
    assert uploads() == []


def test_item10_base64_path_validated_too(client, make_record):
    """เส้นทาง base64-in-JSON ที่เหลือไว้ให้ curl ต้องผ่านด่านเดียวกับ multipart"""
    rid = make_record()
    r = client.post('/api/records/%d/attachments' % rid, json={
        'book_no': '1', 'filename': 'x.pdf',
        'data': base64.b64encode(b'MZ not a pdf').decode(),
    })
    assert r.status_code == 400


# ────────────────────────────────────────────────
# ข้อ 19 — อัปโหลดไป record ที่ไม่มีอยู่ ต้องเป็น 404 ไม่ใช่ 500
# ────────────────────────────────────────────────

def test_item19_upload_to_missing_record_returns_404(client, upload, uploads, db):
    r = upload(9999)
    assert r.status_code == 404
    assert 'id=9999' in r.get_json()['error']
    # เช็กก่อนเขียนไฟล์ จึงต้องไม่มีไฟล์ขยะให้ตามลบเลย
    assert uploads() == []
    assert db().execute('SELECT count(*) c FROM attachments').fetchone()['c'] == 0


def test_item19_record_deleted_mid_upload_also_404(client, make_record, upload, uploads, fail_sql):
    """
    ช่องว่างระหว่าง "เช็กว่า record มีจริง" กับ "INSERT" — ถ้า record ถูกลบตรงนั้น
    FK constraint จะยิง IntegrityError ซึ่งต้องกลายเป็น 404 เหมือนกัน ไม่ใช่ 500
    """
    rid = make_record()
    fail_sql('INSERT INTO ATTACHMENTS', sqlite3.IntegrityError)
    r = upload(rid)
    assert r.status_code == 404
    assert uploads() == []                          # ไฟล์ที่เพิ่งเขียนถูกลบทิ้ง


def test_item19_other_db_errors_still_500(client, make_record, upload, fail_sql):
    """กันการแก้เกินเลย — DB ล้มด้วยสาเหตุอื่นต้องยังเป็น 500 ไม่ใช่ 404"""
    rid = make_record()
    fail_sql('INSERT INTO ATTACHMENTS')             # OperationalError
    assert upload(rid).status_code == 500


# ────────────────────────────────────────────────
# ข้อ 9 — ข้อความ error ที่ส่งถึง client ต้องไม่มีรายละเอียดภายในระบบ
# ────────────────────────────────────────────────

# เพดาน 80 ตัวอักษรไม่ใช่ตัวเลขลอย ๆ — `toastError` ใน templates/index.html เปิด
# `alert()` ซ้อน toast เมื่อข้อความยาวเกินนี้ ข้อความยาวจาก exception จึงกลายเป็น
# popup ที่ผู้ใช้ต้องกดปิดทุกครั้ง
MAX_CLIENT_MESSAGE = 80

# (ชื่อเคส, คำสั่งที่จะให้ล้ม, ฟังก์ชันยิง request)
WRITE_FAILURES = [
    ('add_record',        'INSERT INTO RECORDS',
     lambda c, rid: c.post('/api/records', json={'p1_fname': 'ก', 'p1_lname': 'ข',
                                                 'p2_pos_current': 'ค', 'p2_empid': '1'})),
    ('update_record',     'UPDATE RECORDS',
     lambda c, rid: c.put('/api/records/%d' % rid, json={'p1_fname': 'ก', 'p1_lname': 'ข',
                                                         'p2_pos_current': 'ค', 'p2_empid': '1'})),
    ('delete_record',     'DELETE FROM RECORDS',
     lambda c, rid: c.delete('/api/records/%d' % rid)),
    ('delete_all',        'DELETE FROM RECORDS',
     lambda c, rid: c.delete('/api/records')),
    ('delete_attachment', 'DELETE FROM ATTACHMENTS',
     lambda c, rid: c.delete('/api/attachments/1')),
]


@pytest.mark.parametrize('name, sql, send', WRITE_FAILURES, ids=[w[0] for w in WRITE_FAILURES])
def test_item9_db_failure_never_leaks_exception_text(
        client, make_record, fail_sql, name, sql, send):
    """
    ทุก route ที่เขียน DB: เมื่อ DB ล้ม client ต้องได้ข้อความกลาง ๆ ที่อ่านรู้เรื่อง
    ไม่ใช่ข้อความของ exception ('simulated DB failure' คือข้อความที่ fault injector โยน
    ถ้ามันโผล่ใน response แปลว่า str(e) ยังรั่วอยู่)
    """
    rid = make_record()
    fail_sql(sql)
    r = send(client, rid)
    msg = r.get_json()['error']
    assert r.status_code == 500
    assert 'simulated DB failure' not in msg
    assert len(msg) <= MAX_CLIENT_MESSAGE
    assert msg.strip()                              # ต้องยังบอกผู้ใช้อะไรบ้าง ไม่ใช่ค่าว่าง


def test_item9_upload_db_failure_never_leaks_exception_text(
        client, make_record, upload, fail_sql):
    """upload มี except คนละชุดกับ route อื่น (แยก IntegrityError ออกเป็น 404) จึงเทสต์แยก"""
    rid = make_record()
    fail_sql('INSERT INTO ATTACHMENTS')
    r = upload(rid)
    msg = r.get_json()['error']
    assert r.status_code == 500
    assert 'simulated DB failure' not in msg
    assert len(msg) <= MAX_CLIENT_MESSAGE


def test_item9_file_write_failure_never_leaks_the_upload_path(
        client, make_record, upload, monkeypatch, tmp_path):
    """
    เส้นทางเขียนไฟล์ล้ม (ไม่เกี่ยว DB) เคยตอบ str(OSError) ซึ่งมี path เต็มของเครื่องผู้ใช้
    จำลองด้วยการวาง "ไฟล์" ไว้ตรงตำแหน่งที่ต้องเป็นโฟลเดอร์ → os.makedirs ล้ม
    """
    rid = make_record()
    blocked = tmp_path / 'blocked_uploads'
    blocked.write_bytes(b'this is a file, not a folder')
    monkeypatch.setattr(hr, 'UPLOAD_FOLDER', str(blocked))
    r = upload(rid)
    msg = r.get_json()['error']
    assert r.status_code == 500
    # เทียบด้วยชื่อโฟลเดอร์ ไม่ใช่ str(blocked) ทั้งก้อน — OSError.__str__ ใส่ path
    # ผ่าน repr() ซึ่ง escape backslash ซ้ำ การเทียบ path เต็มจึงไม่เคย match บน Windows
    assert blocked.name not in msg
    assert tmp_path.name not in msg
    assert 'WinError' not in msg
    assert len(msg) <= MAX_CLIENT_MESSAGE


def test_item9_detail_still_reaches_the_server_log(client, make_record, fail_sql, caplog):
    """
    กันการแก้แบบ "เงียบไปเลย" — ซ่อนจาก client ได้ แต่ฝั่ง server ต้องยังเห็น
    traceback เต็ม ไม่อย่างนั้นเวลาผู้ใช้โทรมาแจ้งปัญหาจะไม่มีอะไรให้ดู
    """
    rid = make_record()
    fail_sql('UPDATE RECORDS')
    with caplog.at_level('ERROR'):
        client.put('/api/records/%d' % rid, json={'p1_fname': 'ก', 'p1_lname': 'ข',
                                                  'p2_pos_current': 'ค', 'p2_empid': '1'})
    assert 'simulated DB failure' in caplog.text
    assert 'Traceback' in caplog.text


# ────────────────────────────────────────────────
# ข้อ 11 — route ต้องปิด connection ทุกครั้ง แม้ query จะพัง
# ────────────────────────────────────────────────

# route ฝั่งอ่านทั้ง 7 ตัว ซึ่งเคยเรียก con.close() เป็นบรรทัดตรง ๆ หลัง execute()
# เมื่อ execute() โยน exception บรรทัดนั้นถูกข้าม connection จึงค้างเปิด
READ_ROUTES = [
    '/api/records',
    '/api/records/1',
    '/api/records/1/attachments',
    '/api/attachments/1',
    '/api/attachments/1/preview',
    '/api/stats',
    '/api/export/csv',
]


@pytest.mark.parametrize('url', READ_ROUTES)
def test_item11_read_route_closes_connection_when_query_fails(
        client, make_record, upload, fail_sql, url, monkeypatch):
    """
    SQLite โหมด WAL: connection ที่ค้างเปิดถือ shared lock ไว้ ทำให้ checkpoint
    ตัด -wal ไม่ได้ ไฟล์จึงโตไม่หยุดใน desktop app ที่เปิดค้างหลายวัน

    ต้องปิด PROPAGATE_EXCEPTIONS ที่ `TESTING=True` เปิดให้ ไม่อย่างนั้น exception
    จะทะลุ test client ออกมาแทนที่จะกลายเป็น 500 ซึ่งไม่ตรงกับที่ผู้ใช้เจอจริง
    """
    monkeypatch.setitem(hr.app.config, 'PROPAGATE_EXCEPTIONS', False)
    rid = make_record()
    upload(rid)
    fail_sql('SELECT')                              # ทุก route ฝั่งอ่านเริ่มด้วย SELECT
    r = client.get(url)
    assert r.status_code == 500                     # ถ้าไม่ใช่ 500 แปลว่า query ไม่ได้ล้มจริง
    assert fail_sql.handed_out, 'route ไม่ได้เปิด connection เลย — test นี้จะไม่พิสูจน์อะไร'
    assert fail_sql.unclosed() == []


@pytest.mark.parametrize('name, sql, send', WRITE_FAILURES, ids=[w[0] for w in WRITE_FAILURES])
def test_item11_write_route_closes_connection_when_query_fails(
        client, make_record, fail_sql, name, sql, send):
    """route ฝั่งเขียนมี try/finally ครบอยู่แล้ว — ข้อนี้กันไม่ให้หลุดหายตอนแก้ route อื่น"""
    rid = make_record()
    fail_sql(sql)
    send(client, rid)
    assert fail_sql.handed_out
    assert fail_sql.unclosed() == []


def test_item11_read_route_closes_connection_on_success(client, make_record, fail_sql):
    """
    ด้านกลับของเคสข้างบน: ทางที่สำเร็จก็ต้องปิด — ยืนยันว่า `unclosed()` มองเห็น
    การปิดจริง ไม่ได้ว่างเพราะไม่มีอะไรให้นับ
    """
    make_record()
    fail_sql('NOTHING MATCHES THIS')
    assert client.get('/api/records').status_code == 200
    assert fail_sql.handed_out
    assert fail_sql.unclosed() == []
