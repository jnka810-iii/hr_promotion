# -*- coding: utf-8 -*-
"""
เทสต์ของระบบล็อกอิน — ตัวตน เซสชัน บทบาท และด่านตรวจ

ตัวที่สำคัญที่สุดในไฟล์นี้คือ `test_every_route_is_closed_without_a_session`:
มันไล่ทุก rule ใน url_map ของ Flask เอง ไม่ใช่รายการที่เขียนมือ เพราะฉะนั้น route
ใหม่ที่ลืมใส่ auth จะทำให้เทสต์นี้แดงทันทีโดยไม่ต้องมีใครนึกขึ้นได้ว่าต้องมาเพิ่มเทสต์
(วิธีเดียวกับที่ `fail_sql.unclosed()` คุม invariant เรื่องปิด connection ทั้ง 14 route
ด้วยเทสต์ชุดเดียว แทนการไล่ดูทีละจุด)
"""

import re
import sqlite3

import pytest

from conftest import ADMIN_PASS, ADMIN_USER, STAFF_PASS, STAFF_USER, login_as
import app as hr


def concrete_url(rule) -> str:
    """
    แปลง rule ของ Flask ให้เป็น URL ที่ยิงได้จริง โดยแทนพารามิเตอร์ด้วย 1

    Parameters:
        rule: werkzeug Rule

    Returns:
        str: เช่น '/api/records/<int:rid>' → '/api/records/1'
    """
    return re.sub(r'<[^>]+>', '1', rule.rule)


def protected_requests():
    """
    คู่ (method, url) ของทุก route ที่ต้องล็อกอิน — อ่านจาก url_map ไม่ใช่รายการเขียนมือ

    Returns:
        list[tuple[str, str]]
    """
    out = []
    for rule in hr.app.url_map.iter_rules():
        if rule.endpoint in hr.PUBLIC_ENDPOINTS:
            continue
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            out.append((method, concrete_url(rule)))
    return sorted(out)


PROTECTED = protected_requests()


# ────────────────────────────────────────────────
# ด่านตรวจ — ไม่มีเครื่องใดและไม่มี route ใดได้รับการยกเว้น
# ────────────────────────────────────────────────

@pytest.mark.parametrize('method, url', PROTECTED, ids=[f'{m} {u}' for m, u in PROTECTED])
def test_every_route_is_closed_without_a_session(anon_client, method, url):
    """
    ทุก route ทุก method ต้องไม่ให้ผ่านเมื่อไม่มีเซสชัน

    /api/* ตอบ 401 (ให้ client จัดการเองได้) ส่วนหน้าเว็บ redirect ไปหน้าล็อกอิน
    """
    r = anon_client.open(url, method=method)
    if url.startswith('/api/'):
        assert r.status_code == 401, f'{method} {url} ไม่ได้ปิด'
        assert r.get_json()['error']
    else:
        assert r.status_code == 302, f'{method} {url} ไม่ได้ปิด'
        assert '/login' in r.headers['Location']


def test_protected_list_is_not_empty():
    """กันเทสต์ข้างบนกลายเป็นชุดว่างเปล่าที่ผ่านเพราะไม่มีอะไรให้ตรวจ"""
    assert len(PROTECTED) >= 14
    assert ('DELETE', '/api/records') in PROTECTED


def test_401_body_is_short_enough_for_a_toast(anon_client):
    """ข้อความยาวเกิน 80 ตัวอักษรทำให้ toastError ฝั่ง client ยกระดับเป็น alert()"""
    msg = anon_client.get('/api/records').get_json()['error']
    assert 0 < len(msg) <= 80


def test_no_session_means_no_data_not_an_empty_store(anon_client):
    """
    401 ต้องมาพร้อมข้อความ ไม่ใช่ลิสต์ว่าง — ADR-0001: backend ยังอยู่และข้อมูลยังอยู่ครบ
    ถ้า route ตอบ [] ฝั่ง client จะรายงาน "0 รายการ" ทั้งที่ SQLite ถือข้อมูลอยู่เต็ม
    """
    r = anon_client.get('/api/records')
    assert r.status_code == 401
    assert r.get_json() != []
    assert 'login' in r.get_json()


def test_loopback_gets_no_exemption(anon_client):
    """
    เครื่องตัวเองไม่ได้รับการยกเว้น — request ของ test client มาจาก 127.0.0.1 อยู่แล้ว
    ถ้าวันหนึ่งมีใครเติมทางยกเว้น loopback เข้ามา เทสต์นี้จะแดง
    """
    assert anon_client.get('/api/records').status_code == 401
    assert anon_client.get('/').status_code == 302


def test_empty_users_table_still_requires_login(hr_app, db):
    """
    ตาราง users ว่าง = เข้าไม่ได้ ไม่ใช่ = ไม่ต้องล็อกอิน

    ถ้า "ยังไม่มีบัญชี" ถูกตีความว่า "ยังไม่ต้องตรวจ" ประตูจะเปิดทุกครั้งที่ตารางว่าง
    รวมถึงตอน restore backup ผิดไฟล์ หน้าล็อกอินได้แต่ *บอกทาง* ว่าต้องไปสร้างบัญชี
    ที่เครื่องเซิร์ฟเวอร์ ไม่มีปุ่มสร้างให้กดจากหน้านั้น
    """
    con = db()
    con.execute('DELETE FROM users')
    con.commit()
    con.close()

    anon = hr_app.test_client()
    assert anon.get('/api/records').status_code == 401
    assert anon.get('/').status_code == 302
    page = anon.get('/login').get_data(as_text=True)
    assert 'manage_users.py add' in page           # บอกคำสั่งที่ต้องไปรัน
    assert '<form method="post" action="/login"' in page
    assert 'ยังไม่มีบัญชีผู้ใช้ในระบบ' in page


def test_uploads_are_not_served_by_the_static_route(anon_client, client, make_record, upload):
    """
    static/uploads/ คือที่เก็บไฟล์แนบ ไม่ใช่ asset ของหน้าเว็บ

    มันบังเอิญอยู่ใต้ static/ จึงถูก static route ของ Flask เสิร์ฟให้ฟรี ๆ ทั้งที่แอป
    ไม่เคยลิงก์ไปที่นั้น (ดาวน์โหลดจริงไปทาง /api/attachments/<id>) ถ้าไม่ปิด ไฟล์ PDF
    ทุกใบจะอ่านได้โดยไม่ต้องล็อกอิน สำหรับคนที่รู้ชื่อไฟล์
    """
    rid = make_record()
    upload(rid)
    with hr.app.app_context():
        pass
    import os
    name = sorted(n for n in os.listdir(hr.UPLOAD_FOLDER) if n.endswith('.pdf'))[0]

    assert anon_client.get('/static/uploads/' + name).status_code == 404
    assert client.get('/static/uploads/' + name).status_code == 404   # ล็อกอินแล้วก็ยังปิด
    assert anon_client.get('/static/vendor/sarabun.css').status_code == 200


def test_the_gate_closes_its_connection_when_the_session_lookup_fails(
        anon_client, fail_sql, monkeypatch):
    """
    ด่านตรวจเปิด connection ของตัวเองทุก request มันต้องปิดทุกเส้นทางเหมือน route อื่น

    fault injector ยกเว้น query ของด่านไว้ (ไม่งั้นเทสต์ route อื่นจะกลายเป็นเทสต์ด่าน
    ทั้งแถบ — ดู conftest) จึงบังคับให้ล้มที่นี่ด้วยการ patch resolve_session ตรง ๆ
    """
    monkeypatch.setitem(hr.app.config, 'PROPAGATE_EXCEPTIONS', False)

    def boom(con, token):
        raise sqlite3.OperationalError('simulated DB failure')

    fail_sql('NOTHING MATCHES THIS')      # เปิดการนับ connection โดยไม่ล้ม query ใด
    monkeypatch.setattr(hr, 'resolve_session', boom)

    assert anon_client.get('/api/records').status_code == 500
    assert fail_sql.handed_out, 'ด่านไม่ได้เปิด connection เลย — เทสต์นี้ไม่พิสูจน์อะไร'
    assert fail_sql.unclosed() == []


# ────────────────────────────────────────────────
# บทบาท
# ────────────────────────────────────────────────

def test_staff_cannot_wipe_the_database(staff_client, make_record):
    """DELETE /api/records ลบทุก record พร้อมไฟล์แนบ — เฉพาะ admin"""
    make_record()
    r = staff_client.delete('/api/records')
    assert r.status_code == 403
    assert len(r.get_json()['error']) <= 80


def test_staff_can_do_the_day_to_day_work(staff_client, make_record, upload):
    """403 ต้องแคบอยู่ที่ route เดียว ไม่ใช่กลายเป็นบัญชีที่ทำงานไม่ได้"""
    from conftest import VALID_RECORD
    rid = make_record()
    assert staff_client.get('/api/records').status_code == 200
    assert staff_client.get('/api/records/%d' % rid).status_code == 200
    assert staff_client.post('/api/records', json=VALID_RECORD).status_code == 201
    assert staff_client.put('/api/records/%d' % rid, json=VALID_RECORD).status_code == 200
    assert upload(rid).status_code == 201
    assert staff_client.get('/api/stats').status_code == 200
    assert staff_client.get('/api/export/csv').status_code == 200
    assert staff_client.delete('/api/records/%d' % rid).status_code == 200


def test_admin_can_wipe_the_database(client, make_record):
    """ด้านกลับ: 403 ข้างบนต้องมาจากบทบาท ไม่ใช่จาก route ที่พังไปเลย"""
    make_record()
    assert client.delete('/api/records').status_code == 200


def test_role_cannot_hold_a_value_outside_the_two(db):
    """กฎของ role อยู่ใน schema (CHECK) ไม่ใช่แค่ในโค้ดที่เรียก set_role"""
    con = db()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE users SET role='manager' WHERE username=?", (STAFF_USER,))
        con.commit()
    con.close()


# ────────────────────────────────────────────────
# ลบข้อมูลทั้งหมด ต้องไม่กินบัญชี
# ────────────────────────────────────────────────

def test_wiping_all_records_leaves_accounts_and_sessions_alone(client, make_record, upload, db):
    """
    ถ้า "ล้างข้อมูลทั้งหมด" ลบบัญชีไปด้วย คนที่กดจะล็อกตัวเองออกจากระบบถาวร
    และไม่มีทางเข้ามาแก้อะไรได้อีกเลย
    """
    rid = make_record()
    upload(rid)
    con = db()
    users_before = con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    sess_before = con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
    con.close()
    assert users_before == 2 and sess_before >= 1

    assert client.delete('/api/records').status_code == 200

    con = db()
    assert con.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0
    assert con.execute('SELECT COUNT(*) FROM users').fetchone()[0] == users_before
    assert con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == sess_before
    con.close()

    assert client.get('/api/records').status_code == 200   # ยังล็อกอินอยู่


def test_deleting_a_record_never_touches_accounts(client, make_record, db):
    """record กับผู้ใช้เป็นของคนละอย่าง — ไม่มี FK เชื่อมกัน"""
    rid = make_record()
    assert client.delete('/api/records/%d' % rid).status_code == 200
    con = db()
    assert con.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 2
    con.close()


# ────────────────────────────────────────────────
# หน้าล็อกอิน
# ────────────────────────────────────────────────

def test_login_page_loads_no_application_javascript(anon_client):
    """
    ประตูหน้าบ้านไม่พึ่งชิ้นส่วนที่ซับซ้อนที่สุดในแอป

    ถ้าหน้านี้โหลดสคริปต์ของหน้าหลัก มันจะยิง /api/* ได้ 401 กองหนึ่งก่อนใครจะพิมพ์อะไร
    แล้ว detectMode() จะไปตัดสินโหมดจากความพังนั้น
    """
    page = anon_client.get('/login').get_data(as_text=True)
    # ไม่ตรวจด้วยการหาคำในหน้า เพราะคอมเมนต์ที่อธิบายกฎข้อนี้เองก็มีคำพวกนั้นอยู่
    # ตรวจสิ่งที่เป็นจริง ๆ แทน: หน้านี้ไม่มีแท็ก <script> เลยแม้แท็กเดียว
    assert '<script' not in page.lower()
    assert '<form method="post"' in page
    assert 'name="password"' in page


def test_every_login_failure_looks_identical(hr_app, db):
    """
    ไม่มีอยู่จริง / รหัสผิด / บัญชีถูกปิด / ถูกล็อก ต้องตอบเหมือนกันหมด
    ไม่งั้นหน้าล็อกอินกลายเป็นเครื่องมือไล่ถามว่าในระบบมีใครอยู่
    """
    con = db()
    con.execute('UPDATE users SET is_active=0 WHERE username=?', (STAFF_USER,))
    con.commit()
    con.close()

    bodies = []
    for username, password in (
        ('ghost.user', 'password123'),      # ไม่มีอยู่จริง
        (ADMIN_USER, 'wrong-password'),     # รหัสผิด
        (STAFF_USER, STAFF_PASS),           # บัญชีถูกปิด (รหัสถูก!)
    ):
        r = hr_app.test_client().post('/login',
                                      data={'username': username, 'password': password})
        assert r.status_code == 401
        bodies.append(r.get_data(as_text=True).replace(username, ''))

    assert hr.LOGIN_FAILED_MSG in bodies[0]
    assert len(set(bodies)) == 1, 'หน้าตอบกลับต่างกันตามสาเหตุ = บอกใบ้ว่าบัญชีไหนมีจริง'


def test_login_is_case_insensitive_on_the_username(hr_app):
    """'Somchai' กับ 'somchai' ต้องเป็นบัญชีเดียวกัน ไม่ใช่สองบัญชีที่คนมองว่าเหมือนกัน"""
    r = hr_app.test_client().post('/login', data={'username': ADMIN_USER.upper(),
                                                  'password': ADMIN_PASS})
    assert r.status_code == 302


def test_account_locks_for_a_while_after_repeated_failures(hr_app, db):
    """ผิดครบ LOGIN_MAX_FAILS ครั้ง → ล็อกชั่วคราว แม้จะกรอกรหัสถูกก็เข้าไม่ได้"""
    c = hr_app.test_client()
    for _ in range(hr.LOGIN_MAX_FAILS):
        assert c.post('/login', data={'username': ADMIN_USER,
                                      'password': 'wrong'}).status_code == 401

    assert c.post('/login', data={'username': ADMIN_USER,
                                  'password': ADMIN_PASS}).status_code == 401
    con = db()
    assert con.execute('SELECT locked_until FROM users WHERE username=?',
                       (ADMIN_USER,)).fetchone()[0] > hr.ts_now()
    con.close()


def test_the_lock_releases_itself(hr_app, db):
    """
    ล็อกชั่วคราว ไม่ใช่ล็อกถาวรรอแอดมินปลด — ถ้าถาวร ใครก็กดให้เพื่อนร่วมงาน
    ทำงานไม่ได้ทั้งวันด้วยการพิมพ์ผิดห้าครั้ง
    """
    c = hr_app.test_client()
    for _ in range(hr.LOGIN_MAX_FAILS):
        c.post('/login', data={'username': ADMIN_USER, 'password': 'wrong'})

    con = db()
    con.execute('UPDATE users SET locked_until=? WHERE username=?',
                (hr.ts_after(minutes=-1), ADMIN_USER))
    con.commit()
    con.close()

    assert c.post('/login', data={'username': ADMIN_USER,
                                  'password': ADMIN_PASS}).status_code == 302


def test_a_successful_login_clears_the_failure_count(hr_app, db):
    """ไม่งั้นการพิมพ์ผิดสะสมข้ามวันจะไปล็อกคนที่ใช้งานปกติ"""
    c = hr_app.test_client()
    c.post('/login', data={'username': ADMIN_USER, 'password': 'wrong'})
    c.post('/login', data={'username': ADMIN_USER, 'password': ADMIN_PASS})
    con = db()
    assert con.execute('SELECT failed_count FROM users WHERE username=?',
                       (ADMIN_USER,)).fetchone()[0] == 0
    con.close()


def test_login_redirect_target_must_be_local():
    """
    ?next= ที่ไม่กรองทำให้หน้าล็อกอินของเราพาคนไปเว็บอื่นได้ตามที่ลิงก์สั่ง
    '//host' คือ URL ที่ยืมสคีมของหน้าปัจจุบัน ไม่ใช่เส้นทางภายใน
    """
    assert hr._safe_next('/records?tab=2') == '/records?tab=2'
    assert hr._safe_next('//evil.example') == '/'
    assert hr._safe_next('http://evil.example') == '/'
    assert hr._safe_next('/\\evil.example') == '/'
    assert hr._safe_next(None) == '/'


def test_login_honours_a_local_next(hr_app):
    """คนที่ถูกเด้งออกกลางทางต้องกลับไปที่หน้าที่เขาอยู่ ไม่ใช่กลับไปหน้าแรกเสมอ"""
    r = hr_app.test_client().post('/login', data={
        'username': ADMIN_USER, 'password': ADMIN_PASS, 'next': '/?tab=3'})
    assert r.headers['Location'] == '/?tab=3'


# ────────────────────────────────────────────────
# เซสชันและ cookie
# ────────────────────────────────────────────────

def test_session_cookie_is_httponly_and_samesite_lax(hr_app):
    """
    SameSite=Lax เป็นเกราะ CSRF ตัวเดียวที่ระบบนี้มี (ไม่มี CSRF token)
    ถ้าใครแก้เป็น None เว็บอื่นจะสั่งเบราว์เซอร์ของคนในสำนักงานยิง POST อัปโหลด
    ไฟล์มาที่ระบบนี้ได้พร้อม cookie
    secure ต้องไม่ถูกตั้งเพราะระบบวิ่งบน HTTP เปล่าในวง LAN (ADR-0004) —
    ถ้าตั้ง เบราว์เซอร์จะไม่ส่ง cookie เลยและล็อกอินจะไม่ติดทั้งระบบ
    """
    r = hr_app.test_client().post('/login', data={'username': ADMIN_USER,
                                                  'password': ADMIN_PASS})
    cookie = r.headers['Set-Cookie']
    assert hr.SESSION_COOKIE in cookie
    assert 'HttpOnly' in cookie
    assert 'SameSite=Lax' in cookie
    assert 'Secure' not in cookie


def test_remember_me_changes_only_the_lifetime(hr_app, db):
    """ไม่ติ๊ก = จบวันทำงาน, ติ๊ก = 30 วัน และ DB ต้องจำว่าเลือกอะไรไว้"""
    plain = hr_app.test_client().post('/login', data={'username': ADMIN_USER,
                                                      'password': ADMIN_PASS})
    kept = hr_app.test_client().post('/login', data={'username': ADMIN_USER,
                                                     'password': ADMIN_PASS,
                                                     'remember': '1'})
    assert plain.status_code == kept.status_code == 302
    con = db()
    rows = con.execute('SELECT remember, expires_at FROM sessions ORDER BY remember').fetchall()
    con.close()
    assert [r['remember'] for r in rows] == [0, 1]
    assert rows[0]['expires_at'] < rows[1]['expires_at']


def test_the_token_is_not_a_signed_cookie(hr_app):
    """
    ADR-0003: เซสชันเป็น token สุ่มที่ต้องเอาไปหาในตาราง ไม่ใช่ cookie เซ็นชื่อ
    ระบบนี้จึงไม่มี SECRET_KEY ให้ต้องเก็บหรือหมุน และไม่มีความลับที่รั่วแล้ว
    ปลอม session ของ admin ได้
    """
    assert hr_app.secret_key is None
    assert 'SECRET_KEY' not in hr_app.config or not hr_app.config['SECRET_KEY']


def test_an_expired_session_is_rejected_and_removed(hr_app, db):
    """เซสชันหมดอายุต้องใช้ไม่ได้ ไม่ว่าแถวจะยังค้างอยู่ใน DB หรือไม่"""
    c = login_as(hr_app, ADMIN_USER, ADMIN_PASS)
    con = db()
    con.execute('UPDATE sessions SET expires_at=?', (hr.ts_after(minutes=-1),))
    con.commit()
    con.close()

    assert c.get('/api/records').status_code == 401
    con = db()
    assert con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
    con.close()


def test_an_unknown_token_is_rejected(hr_app):
    """token ที่ไม่มีในตารางคือ token ที่ใช้ไม่ได้ — ไม่มีลายเซ็นให้ตรวจแทน"""
    c = hr_app.test_client()
    c.set_cookie(hr.SESSION_COOKIE, 'a-token-that-was-never-issued')
    assert c.get('/api/records').status_code == 401


def test_session_expiry_slides_while_someone_is_working(hr_app, db):
    """
    คนกรอกฟอร์ม 13 ช่องใช้เวลาเป็นสิบ ๆ นาที การหมดอายุตายตัวจากเวลาล็อกอินจะเด้งเขา
    กลางฟอร์ม แต่ต้องไม่เขียน DB ทุก request — เลื่อนเฉพาะเมื่ออายุเหลือน้อยกว่าครึ่ง
    """
    c = login_as(hr_app, ADMIN_USER, ADMIN_PASS)

    con = db()
    before = con.execute('SELECT expires_at FROM sessions').fetchone()[0]
    con.close()
    assert c.get('/api/records').status_code == 200
    con = db()
    assert con.execute('SELECT expires_at FROM sessions').fetchone()[0] == before, \
        'เลื่อนอายุทั้งที่ยังเหลือเกินครึ่ง = จับ write lock ของ SQLite ทุก request'
    con.close()

    con = db()
    con.execute('UPDATE sessions SET expires_at=?', (hr.ts_after(hours=1),))
    con.commit()
    con.close()
    assert c.get('/api/records').status_code == 200
    con = db()
    assert con.execute('SELECT expires_at FROM sessions').fetchone()[0] > hr.ts_after(hours=2)
    con.close()


def test_logout_deletes_the_session_row(hr_app, db):
    """
    logout ต้องลบของจริง ไม่ใช่แค่ลบ cookie ฝั่งเบราว์เซอร์ — ถ้าลบแค่ cookie
    ใครที่ก็อป token ไว้ก่อนหน้านั้นยังใช้งานต่อได้จนหมดอายุ
    """
    c = login_as(hr_app, ADMIN_USER, ADMIN_PASS)
    r = c.post('/logout')
    assert r.status_code == 302 and '/login' in r.headers['Location']
    con = db()
    assert con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
    con.close()
    assert c.get('/api/records').status_code == 401


def test_logout_is_not_reachable_by_get(client):
    """GET /logout ทำให้รูปหรือลิงก์ที่ไหนก็ได้เตะคนออกจากระบบ"""
    assert client.get('/logout').status_code == 405


def test_disabling_an_account_kicks_it_out_immediately(hr_app, db):
    """
    คนที่ลาออกแล้วต้องออกทันที ไม่ใช่ทำงานต่อได้อีก 30 วันเพราะเคยติ๊ก "จำฉันไว้"
    """
    c = login_as(hr_app, STAFF_USER, STAFF_PASS)
    assert c.get('/api/records').status_code == 200

    con = hr.get_db()
    user = hr.find_user(con, STAFF_USER)
    hr.set_active(con, user['id'], False)
    con.commit()
    con.close()

    assert c.get('/api/records').status_code == 401
    con = db()
    assert con.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',
                       (user['id'],)).fetchone()[0] == 0
    con.close()


# ────────────────────────────────────────────────
# รหัสผ่าน
# ────────────────────────────────────────────────

def test_real_password_hashing_uses_a_slow_kdf():
    """
    เทสต์ตัวอื่นใช้แฮชแบบถูกเพื่อให้ชุดเทสต์ไม่ช้า (ดู conftest._cheap_hash)
    ตัวนี้คือที่เดียวที่ยืนยันว่า *ของจริง* ยังเป็น KDF ที่คิดช้าจริง
    ห้ามเป็น sha256 เปล่า ๆ ซึ่งเร็วเกินไปสำหรับการเก็บรหัสผ่าน
    """
    import hashlib
    digest = hr.hash_password('password123')
    assert digest.startswith('scrypt:')
    assert hashlib.sha256(b'password123').hexdigest() not in digest
    from werkzeug.security import check_password_hash
    assert check_password_hash(digest, 'password123')
    assert not check_password_hash(digest, 'password124')


def test_the_same_password_hashes_differently_every_time():
    """salt อยู่ในสตริงผลลัพธ์ — สองคนที่ใช้รหัสเดียวกันต้องไม่มีแฮชเหมือนกัน"""
    assert hr.hash_password('password123') != hr.hash_password('password123')


def test_password_policy_is_length_only():
    """ไม่บังคับรูปแบบ กฎยุ่บยั่บได้ Password1! ที่จดแปะข้างจอ"""
    assert hr.password_problem('x' * (hr.PASSWORD_MIN_LEN - 1))
    assert hr.password_problem('') and hr.password_problem(None)
    assert hr.password_problem('x' * hr.PASSWORD_MIN_LEN) == ''
    assert hr.password_problem('ทั้งหมดเป็นภาษาไทยเลย') == ''
    assert len(hr.password_problem('short')) <= 80


def test_username_rules_reject_lookalikes():
    """
    ชื่อผู้ใช้ไปอยู่ในบรรทัด log ที่ชี้ว่าใครลบอะไร และถูกจองตลอดไป
    ถ้ารับอักษรนอก ASCII จะมีคู่ที่หน้าตาเหมือนกันสนิทแต่เป็นคนละบัญชี
    """
    assert hr.username_problem('somchai') == ''
    assert hr.username_problem('nipaporn.k') == ''
    assert hr.username_problem('ab')                       # สั้นเกิน
    assert hr.username_problem('x' * 33)                   # ยาวเกิน
    assert hr.username_problem('สมชาย')                     # ไทย
    assert hr.username_problem('аdmin')                    # a ซีริลลิก
    assert hr.username_problem('some one')                 # เว้นวรรค
    assert hr.normalize_username('  SomChai  ') == 'somchai'


def test_changing_your_password_needs_the_old_one(client, db):
    """
    ถ้าไม่ต้องกรอกรหัสเดิม คนที่นั่งหน้าเครื่องที่เปิดค้างไว้ยึดบัญชีได้ทันที
    """
    before = db().execute('SELECT password_hash FROM users WHERE username=?',
                          (ADMIN_USER,)).fetchone()[0]
    r = client.post('/change-password', data={'current': 'wrong',
                                              'new1': 'new-password-1',
                                              'new2': 'new-password-1'})
    assert r.status_code == 401
    assert db().execute('SELECT password_hash FROM users WHERE username=?',
                        (ADMIN_USER,)).fetchone()[0] == before


def test_changing_your_password_keeps_this_machine_and_kicks_the_others(hr_app, db):
    """
    เหตุผลหลักที่คนเปลี่ยนรหัสคือ "รหัสรู้ถึงคนอื่นแล้ว" ถ้าเซสชันเก่ายังอยู่ต่ออีก
    30 วัน การเปลี่ยนรหัสก็ไม่ได้แก้อะไรเลย แต่ต้องไม่เด้งเครื่องที่กำลังพิมพ์อยู่
    """
    here = login_as(hr_app, ADMIN_USER, ADMIN_PASS)
    elsewhere = login_as(hr_app, ADMIN_USER, ADMIN_PASS)

    r = here.post('/change-password', data={'current': ADMIN_PASS,
                                            'new1': 'new-password-1',
                                            'new2': 'new-password-1'})
    assert r.status_code == 302

    assert here.get('/api/records').status_code == 200
    assert elsewhere.get('/api/records').status_code == 401

    assert hr_app.test_client().post('/login', data={
        'username': ADMIN_USER, 'password': ADMIN_PASS}).status_code == 401
    assert hr_app.test_client().post('/login', data={
        'username': ADMIN_USER, 'password': 'new-password-1'}).status_code == 302


def test_change_password_rejects_a_mismatch_and_a_short_one(client):
    """ตรวจที่เดียวกับที่ CLI ใช้ (password_problem) ไม่ใช่สำเนาที่เลื่อนออกจากกันได้"""
    assert client.post('/change-password', data={'current': ADMIN_PASS,
                                                 'new1': 'new-password-1',
                                                 'new2': 'new-password-2'}).status_code == 400
    assert client.post('/change-password', data={'current': ADMIN_PASS,
                                                 'new1': 'short',
                                                 'new2': 'short'}).status_code == 400


# ────────────────────────────────────────────────
# หน้าหลักรู้ว่าใครกำลังใช้งาน
# ────────────────────────────────────────────────

def test_the_page_shows_who_is_logged_in_and_a_way_out(client):
    """
    ถ้าหน้าจอไม่บอกว่ากำลังทำงานในนามใคร คนที่ใช้เครื่องรวมกันจะบันทึกข้อมูลใน
    ชื่อของคนก่อนหน้าโดยไม่รู้ตัว — และ log จะชี้ไปที่คนผิด
    """
    page = client.get('/').get_data(as_text=True)
    assert ADMIN_USER in page
    assert 'action="/logout"' in page
    assert 'method="post"' in page           # ออกจากระบบต้องไม่ใช่ลิงก์ GET
    assert '/change-password' in page


def test_the_wipe_button_is_hidden_from_staff(client, staff_client):
    """
    ซ่อนปุ่มเป็นเรื่องความสะอาดของหน้าจอ ไม่ใช่การบังคับสิทธิ์ (ด่านจริงคือ 403)
    แต่การโชว์ปุ่มที่กดแล้วได้ 403 ทุกครั้งก็คือการวางกับดักไว้ให้ผู้ใช้
    """
    # ตรวจที่ปุ่ม ไม่ใช่ที่ชื่อฟังก์ชัน — ตัวฟังก์ชันยังอยู่ในสคริปต์ของทุกคน
    assert 'onclick="clearAll()"' in client.get('/').get_data(as_text=True)
    assert 'onclick="clearAll()"' not in staff_client.get('/').get_data(as_text=True)


# ────────────────────────────────────────────────
# audit log
# ────────────────────────────────────────────────

def test_the_log_says_who_did_it(client, make_record, caplog):
    """
    ไฟล์ log เป็นที่เดียวที่บันทึกว่าใครลบอะไร (ไม่มีตาราง audit) ถ้าบรรทัดนั้น
    ไม่มีชื่อผู้ใช้ มันก็ตอบคำถามที่มันถูกสร้างมาเพื่อตอบไม่ได้
    """
    with caplog.at_level('INFO', logger='hr_promotion'):
        rid = make_record()
        client.delete('/api/records/%d' % rid)
    assert f'user={ADMIN_USER} add_record id={rid}' in caplog.text
    assert f'user={ADMIN_USER} delete_record id={rid}' in caplog.text


def test_a_rejected_attempt_is_logged_with_the_name(staff_client, caplog):
    """คนที่พยายามทำสิ่งที่บทบาทตัวเองทำไม่ได้ ต้องปรากฏใน log"""
    with caplog.at_level('INFO', logger='hr_promotion'):
        staff_client.delete('/api/records')
    assert f'user={STAFF_USER} denied DELETE /api/records' in caplog.text


def test_a_failed_login_is_logged_without_the_password(hr_app, caplog):
    """log ต้องบอกว่ามีคนพยายามเข้า แต่ต้องไม่เก็บรหัสที่เขาพิมพ์ไว้"""
    with caplog.at_level('INFO', logger='hr_promotion'):
        hr_app.test_client().post('/login', data={'username': ADMIN_USER,
                                                  'password': 'hunter2-secret'})
    assert 'login failed' in caplog.text
    assert ADMIN_USER in caplog.text
    assert 'hunter2-secret' not in caplog.text
