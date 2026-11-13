# -*- coding: utf-8 -*-
"""
เทสต์ของแท็บจัดการผู้ใช้งาน — กฎสิทธิ์ ตาราง users และ route /api/users

ส่วนแรกของไฟล์ไม่ใช้ `client` เลยแม้แต่ตัวเดียว ซึ่งเป็นเหตุผลทั้งหมดที่
`permission_problem` ถูกทำให้บริสุทธิ์: ตารางสิทธิ์ทั้งใบทดสอบได้โดยไม่ต้องมี Flask,
ไม่ต้องมี DB และไม่ต้องมี HTTP

รัน: .venv/Scripts/python.exe -m pytest tests/test_users.py
"""

import os
import re
import sqlite3
import threading
from contextlib import closing

import pytest

import app as hr
from conftest import (ADMIN_PASS, ADMIN_USER, AUTH_SQL_MARK, STAFF_PASS,
                      STAFF_USER, login_as)


MAX_CLIENT_MESSAGE = 80   # toastError ยกระดับข้อความที่ยาวกว่านี้เป็น alert()

A = hr.Account
ADMIN_1 = A(1, hr.ROLE_ADMIN)
ADMIN_9 = A(9, hr.ROLE_ADMIN)
STAFF_2 = A(2, hr.ROLE_STAFF)
STAFF_8 = A(8, hr.ROLE_STAFF)


# ────────────────────────────────────────────────
# กฎสิทธิ์ทั้งใบ เป็นตารางความจริง
# ────────────────────────────────────────────────

# (actor, action, target, new_role, ทำได้หรือไม่)
MATRIX = [
    # ── list ──────────────────────────────────────────────
    (ADMIN_1, 'user.list',     None,    None,          True),
    (STAFF_2, 'user.list',     None,    None,          True),
    # ── create: ห้ามให้สิทธิ์สูงกว่าของตัวเอง ──────────────
    (ADMIN_1, 'user.create',   None,    hr.ROLE_ADMIN, True),
    (ADMIN_1, 'user.create',   None,    hr.ROLE_STAFF, True),
    (STAFF_2, 'user.create',   None,    hr.ROLE_STAFF, True),
    (STAFF_2, 'user.create',   None,    hr.ROLE_ADMIN, False),
    # ── rename: ห้ามแตะบัญชีที่สูงกว่า เท่ากันได้ ตัวเองได้ ─
    (ADMIN_1, 'user.rename',   ADMIN_9, None,          True),
    (ADMIN_1, 'user.rename',   ADMIN_1, None,          True),
    (ADMIN_1, 'user.rename',   STAFF_2, None,          True),
    (STAFF_2, 'user.rename',   STAFF_8, None,          True),
    (STAFF_2, 'user.rename',   STAFF_2, None,          True),
    (STAFF_2, 'user.rename',   ADMIN_1, None,          False),
    # ── password: ของตัวเองห้ามทุกบทบาท ────────────────────
    (ADMIN_1, 'user.password', STAFF_2, None,          True),
    (ADMIN_1, 'user.password', ADMIN_9, None,          True),
    (ADMIN_1, 'user.password', ADMIN_1, None,          False),
    (STAFF_2, 'user.password', STAFF_8, None,          True),
    (STAFF_2, 'user.password', STAFF_2, None,          False),
    (STAFF_2, 'user.password', ADMIN_1, None,          False),
    # ── role: admin เท่านั้น และไม่ใช่ของตัวเอง ────────────
    (ADMIN_1, 'user.role',     STAFF_2, hr.ROLE_ADMIN, True),
    (ADMIN_1, 'user.role',     ADMIN_9, hr.ROLE_STAFF, True),
    (ADMIN_1, 'user.role',     ADMIN_1, hr.ROLE_STAFF, False),
    (STAFF_2, 'user.role',     STAFF_8, hr.ROLE_STAFF, False),
    # ── active: admin เท่านั้น และไม่ใช่ของตัวเอง ──────────
    (ADMIN_1, 'user.active',   STAFF_2, None,          True),
    (ADMIN_1, 'user.active',   ADMIN_9, None,          True),
    (ADMIN_1, 'user.active',   ADMIN_1, None,          False),
    (STAFF_2, 'user.active',   STAFF_8, None,          False),
]


def _case_id(row):
    actor, action, target, new_role, _ = row
    if target is None:
        who = 'none'
    elif target.user_id == actor.user_id:
        who = 'self'
    else:
        who = target.role
    return f'{actor.role}-{action}-{who}' + (f'-as-{new_role}' if new_role else '')


@pytest.mark.parametrize('actor, action, target, new_role, allowed', MATRIX,
                         ids=[_case_id(r) for r in MATRIX])
def test_the_permission_matrix(actor, action, target, new_role, allowed):
    """ตารางสิทธิ์ทั้งใบ ยิงเข้า permission_problem ตรง ๆ ไม่มี Flask ไม่มี DB"""
    problem = hr.permission_problem(actor, action, target, new_role)
    assert (problem == '') is allowed, problem
    if problem:
        assert problem.strip()
        assert len(problem) <= MAX_CLIENT_MESSAGE


def test_a_denial_says_which_rule_refused():
    """ข้อห้ามเรื่องบัญชีตัวเองสามข้อมีเหตุผลคนละอย่าง ข้อความจึงต้องต่างกันสามแบบ"""
    msgs = {hr.permission_problem(ADMIN_1, a, ADMIN_1)
            for a in ('user.password', 'user.role', 'user.active')}
    assert len(msgs) == 3


def test_the_self_password_denial_points_at_the_right_page():
    """ห้ามรีเซ็ตรหัสของตัวเองทางนี้ ต้องบอกว่าไปทางไหน ไม่ใช่แค่ปฏิเสธเฉย ๆ"""
    assert 'รหัสผ่าน' in hr.permission_problem(ADMIN_1, 'user.password', ADMIN_1)


def test_a_staff_hits_the_role_rule_before_the_target_rule():
    """
    staff กดเปลี่ยนบทบาทของแอดมินต้องได้ยินว่า "ต้องเป็นผู้ดูแลระบบ" ไม่ใช่
    "ทำรายการกับบัญชีของผู้ดูแลระบบไม่ได้" ซึ่งทำให้เข้าใจว่าถ้าเป้าหมายเป็น staff
    ก็จะทำได้ ลำดับการตรวจจึงเป็นส่วนหนึ่งของคำตอบ
    """
    assert hr.permission_problem(STAFF_2, 'user.role', ADMIN_1,
                                 hr.ROLE_STAFF) == hr.FORBIDDEN_MSG


# ────────────────────────────────────────────────
# ตัวกันของทะเบียน — action ที่เจ็ดต้องไม่หลุดไปโดยไม่มีใครคิดถึง staff
# ────────────────────────────────────────────────

ALLOW, DENY = True, False

# รายการที่สองโดยตั้งใจ: การตัดสินว่า staff ทำ action หนึ่งได้หรือไม่ derive จากอะไร
# ไม่ได้เลย มันเป็นการตัดสินใจ รายการนี้บังคับให้การตัดสินใจนั้นถูกเขียนลงไฟล์เทสต์
# ไม่ใช่ซ่อนอยู่ในค่า min_role ที่ใครก็ copy แถวข้างบนมาแล้วลืมดู
EXPECTED_STAFF_VERDICT = {
    'user.list':     ALLOW,
    'user.create':   ALLOW,
    'user.rename':   ALLOW,
    'user.password': ALLOW,
    'user.role':     DENY,
    'user.active':   DENY,
}


def test_every_registered_action_has_a_staff_verdict():
    """
    เพิ่ม action ใหม่ใน USER_ACTIONS แล้วไม่คิดเรื่อง staff = เทสต์นี้แดง

    นี่คือคำตอบของ "วันหน้ามีคนเพิ่ม action ที่เจ็ดแล้วลืมคิดถึง staff":
    การคิดเรื่อง staff กลายเป็นของที่ต้องส่งมากับ diff ไม่ใช่นิสัยที่ต้องจำ
    """
    assert set(EXPECTED_STAFF_VERDICT) == set(hr.USER_ACTIONS), (
        'USER_ACTIONS กับ EXPECTED_STAFF_VERDICT ไม่ตรงกัน — '
        'เพิ่ม action แล้วต้องเขียนที่นี่ว่า staff ทำได้หรือไม่')


@pytest.mark.parametrize('action, allowed', sorted(EXPECTED_STAFF_VERDICT.items()))
def test_the_staff_verdict_matches_the_registry(action, allowed):
    """คำตัดสินที่เขียนไว้ต้องตรงกับที่ USER_ACTIONS บังคับจริง (เป้าหมายคือ staff คนอื่น)"""
    rule = hr.USER_ACTIONS[action]
    problem = hr.permission_problem(
        STAFF_2, action,
        STAFF_8 if rule.needs_target else None,
        hr.ROLE_STAFF if rule.grants_role else None)
    assert (problem == '') is allowed


def test_roles_tuple_is_ordered_most_privileged_first():
    """ROLE_RANK derive จากลำดับใน ROLES ลำดับนั้นจึงกลายเป็นของสำคัญ ไม่ใช่ของเผอิญ"""
    assert hr.ROLE_RANK[hr.ROLE_ADMIN] > hr.ROLE_RANK[hr.ROLE_STAFF]
    assert set(hr.ROLE_RANK) == set(hr.ROLES)
    assert set(hr.ROLE_LABELS) == set(hr.ROLES)


def test_every_targeted_action_names_a_target_in_the_registry():
    """TARGET_ACTIONS derive จาก needs_target ไม่ใช่รายการที่เขียนมือคู่ขนาน"""
    assert set(hr.TARGET_ACTIONS) == {a for a, r in hr.USER_ACTIONS.items()
                                      if r.needs_target}
    assert 'user.list' not in hr.TARGET_ACTIONS


# ────────────────────────────────────────────────
# คอลัมน์ display_name และ migration ของมัน
# ────────────────────────────────────────────────

# schema ของ users ก่อนมีคอลัมน์ display_name — คัดมาจาก init_db() เวอร์ชันก่อนหน้า
# เขียนไว้ตรง ๆ ไม่ derive จาก app.py เพราะเป้าหมายคือ "DB ที่ติดตั้งไว้ก่อนแล้ว"
# ซึ่งเป็นของที่หยุดนิ่งอยู่กับที่ ไม่ใช่ของที่เปลี่ยนตามโค้ดปัจจุบัน
OLD_USERS_SCHEMA = """
    CREATE TABLE users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT    NOT NULL UNIQUE,
        password_hash TEXT    NOT NULL,
        role          TEXT    NOT NULL DEFAULT 'staff'
                      CHECK (role IN ('admin', 'staff')),
        is_active     INTEGER NOT NULL DEFAULT 1,
        failed_count  INTEGER NOT NULL DEFAULT 0,
        locked_until  TEXT,
        created       TEXT,
        updated       TEXT
    )
"""

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_source() -> str:
    """ตัวหนังสือใน app.py — เทสต์ที่ถามเรื่องโครงสร้างของซอร์ส ไม่ใช่เรื่องพฤติกรรม"""
    with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
        return f.read()


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """DB ที่มีตาราง users แบบก่อนมี display_name พร้อมหนึ่งแถว — จำลองเครื่องที่อัปเกรด"""
    monkeypatch.setattr(hr, 'DB_PATH', str(tmp_path / 'old.db'))
    monkeypatch.setattr(hr, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    os.makedirs(hr.UPLOAD_FOLDER, exist_ok=True)
    con = sqlite3.connect(hr.DB_PATH)
    con.execute(OLD_USERS_SCHEMA)
    con.execute("INSERT INTO users (username, password_hash, role) "
                "VALUES ('somchai', 'x', 'admin')")
    con.commit()
    con.close()
    return hr.DB_PATH


def test_display_name_is_added_to_a_users_table_that_predates_it(old_db):
    """
    แถวที่มีอยู่ก่อนต้องได้ '' ไม่ใช่ NULL

    ถ้าเป็น NULL ทุกจุดที่อ่านต้องเช็คสองแบบ และ `display_name or username` ซึ่งเป็น
    ทางเดียวของ fallback จะยังทำงานแต่ด้วยเหตุผลที่บังเอิญ
    """
    hr.init_db()
    with closing(hr.get_db()) as con:
        row = con.execute(
            "SELECT display_name FROM users WHERE username='somchai'").fetchone()
    assert row['display_name'] == ''


def test_init_db_runs_twice_without_complaining(old_db):
    """ALTER ต้อง idempotent เหมือนลูปของ records — เปิดโปรแกรมครั้งที่สองต้องขึ้น"""
    hr.init_db()
    hr.init_db()
    with closing(hr.get_db()) as con:
        cols = {r[1] for r in con.execute('PRAGMA table_info(users)').fetchall()}
    assert 'display_name' in cols


def test_display_name_is_declared_in_exactly_one_place():
    """
    คอลัมน์ประกาศที่ NEW_USER_COLS ที่เดียว ไม่ประกาศซ้ำใน CREATE TABLE users

    ประกาศสองที่แปลว่าเครื่องที่ติดตั้งใหม่ไม่เคยเดินเส้นทาง migration เลย แล้วมันจะ
    เน่าเงียบ ๆ จนถึงวันที่มีคนอัปเกรดจริง
    """
    create_users = app_source().split('CREATE TABLE IF NOT EXISTS users')[1].split('"""')[0]
    assert 'display_name' not in create_users
    assert 'display_name' in hr.NEW_USER_COLS


def test_the_new_user_queries_do_not_look_like_the_auth_query():
    """
    AUTH_SQL_MARK ต้องปรากฏใน app.py ครั้งเดียว

    conftest ใช้สตริงนั้นให้ fault injector *ข้าม* query ของ resolve_session ถ้า SQL
    ของ get_user หรือ list_users บังเอิญมีสตริงเดียวกัน (เช่นเขียนเป็น LEFT JOIN)
    เทสต์ connection-leak ของ /api/users จะเขียวโดยไม่ได้ทดสอบอะไรเลย
    """
    assert app_source().count(AUTH_SQL_MARK) == 1


# ────────────────────────────────────────────────
# ตัวช่วยที่ CLI และ route ใช้ร่วมกัน
# ────────────────────────────────────────────────

def test_the_session_row_carries_the_display_name(hr_app, db):
    """หัวจอต้องแสดงชื่อคนได้จาก g.user ตรง ๆ ไม่ต้องยิง query ที่สองใน index()"""
    with closing(db()) as con:
        con.execute("UPDATE users SET display_name='นายสมชาย ใจดี' WHERE id=1")
        con.commit()
    with closing(hr.get_db()) as con:
        token = hr.start_session(con, 1)
        con.commit()
        assert hr.resolve_session(con, token)['display_name'] == 'นายสมชาย ใจดี'


@pytest.mark.parametrize('raw, expected', [
    ('  สมชาย   ใจดี  ', 'สมชาย ใจดี'),
    ('นางสาวมาลี ศรีสุข', 'นางสาวมาลี ศรีสุข'),
    ('', ''),
    (None, ''),
    ('\tก\nข ', 'ก ข'),
])
def test_normalize_account_name(raw, expected):
    assert hr.normalize_account_name(raw) == expected


@pytest.mark.parametrize('name, ok', [
    ('นางสาวมาลี ศรีสุข', True),
    ('', False),
    ('ก' * hr.ACCOUNT_NAME_MAX, True),
    ('ก' * (hr.ACCOUNT_NAME_MAX + 1), False),
    ('มาลี\x00ศรีสุข', False),
])
def test_account_name_problem(name, ok):
    problem = hr.account_name_problem(name)
    assert (problem == '') is ok
    assert len(problem) <= MAX_CLIENT_MESSAGE


def test_role_problem_rejects_a_value_that_is_not_a_role():
    assert hr.role_problem(hr.ROLE_ADMIN) == ''
    assert hr.role_problem('manager')
    assert hr.role_problem(None)


def test_active_problem_rejects_a_truthy_string():
    """'false' เป็นสตริงที่ truthy — รับไว้คือการเปิดบัญชีตอนที่คนสั่งปิด"""
    assert hr.active_problem(False) == ''
    assert hr.active_problem(True) == ''
    assert hr.active_problem('false')
    assert hr.active_problem(0)


@pytest.mark.parametrize('is_active, locked, expected', [
    (1, None,                  hr.STATUS_ACTIVE),
    (1, '2026-01-01 00:00:00', hr.STATUS_ACTIVE),
    (1, '2099-01-01 00:00:00', hr.STATUS_LOCKED),
    (0, None,                  hr.STATUS_DISABLED),
    # ปิดบัญชีชนะล็อกชั่วคราว ไม่งั้นแอดมินนั่งรอให้ล็อกหายเองทั้งที่มันไม่หาย
    (0, '2099-01-01 00:00:00', hr.STATUS_DISABLED),
])
def test_account_status(is_active, locked, expected):
    row = {'is_active': is_active, 'locked_until': locked}
    assert hr.account_status(row, '2026-11-13 10:00:00') == expected


def test_account_status_judges_every_row_against_one_clock():
    """`now` เป็นพารามิเตอร์บังคับ ไม่ใช่ค่าที่ฟังก์ชันไปอ่านนาฬิกาเอง"""
    with pytest.raises(TypeError):
        hr.account_status({'is_active': 1, 'locked_until': None})


@pytest.mark.parametrize('stamp, expected', [
    ('13/11/2026 09:05', '2026-11-13 09:05'),
    ('02/01/2026 00:00', '2026-01-02 00:00'),
    ('', ''),
    (None, ''),
    ('2026-11-13 09:05', ''),
    ('ขยะจาก fix_db', ''),
])
def test_sortable_stamp(stamp, expected):
    assert hr.sortable_stamp(stamp) == expected


def test_sortable_stamp_actually_sorts_by_time():
    """เหตุผลทั้งหมดที่ฟังก์ชันนี้มีอยู่: 'dd/mm/YYYY' เรียงตามตัวอักษรแล้วผิดลำดับเวลา"""
    older, newer = '31/12/2025 23:59', '02/01/2026 00:01'
    assert older > newer
    assert hr.sortable_stamp(older) < hr.sortable_stamp(newer)


def test_list_users_reports_live_sessions_and_orders_by_id(hr_app, client, db):
    """query ชุดเดียวที่ทั้ง CLI และ route อ่าน"""
    with closing(db()) as con:
        rows = hr.list_users(con)
    assert [r['id'] for r in rows] == sorted(r['id'] for r in rows)
    assert [r['username'] for r in rows] == ['tester.admin', 'tester.staff']
    assert rows[0]['live'] == 1                 # fixture client ล็อกอินค้างอยู่
    assert rows[1]['live'] == 0
    assert 'password_hash' not in rows[0].keys()


def test_get_user_returns_only_what_a_decision_needs(hr_app, db):
    with closing(db()) as con:
        row = hr.get_user(con, 1)
        assert hr.get_user(con, 999) is None
    assert set(row.keys()) == {'id', 'username', 'display_name', 'role', 'is_active'}


def test_create_user_stores_thai_unchanged(hr_app, db):
    """--name และฟอร์มบนเว็บต้องไม่ผ่านอะไรที่ตัดอักษรไทย"""
    with closing(db()) as con:
        uid = hr.create_user(con, 'malee', 'password-1', hr.ROLE_STAFF, 'นางสาวมาลี ศรีสุข')
        con.commit()
        row = hr.get_user(con, uid)
    assert row['display_name'] == 'นางสาวมาลี ศรีสุข'


def test_set_display_name_moves_updated(hr_app, db):
    """กฎ "แก้อะไรก็ต้องขยับ updated" มีเจ้าของคนเดียวต่อคอลัมน์"""
    with closing(db()) as con:
        con.execute("UPDATE users SET updated='01/01/2020 00:00' WHERE id=2")
        con.commit()
        hr.set_display_name(con, 2, 'ชื่อใหม่')
        con.commit()
        row = con.execute('SELECT display_name, updated FROM users WHERE id=2').fetchone()
    assert row['display_name'] == 'ชื่อใหม่'
    assert row['updated'] != '01/01/2020 00:00'


# ────────────────────────────────────────────────
# Route — ทะเบียน action กับ route ต้องไม่หลุดจากกัน
# ────────────────────────────────────────────────

def test_every_registered_action_is_reachable_from_a_route():
    """
    กฎที่ไม่มี route และ route ที่ไม่มีกฎ ต้องแดงทั้งคู่ — อ่านจากตัวหนังสือในซอร์ส

    action เดินทางถึง acting_on ได้สองทาง: เขียนชื่อไว้ตรง ๆ (list, create, password)
    หรือมากับช่องของ PATCH (rename, role, active) ถ้ารอให้ KeyError ดังตอน runtime
    มันจะดังเฉพาะเมื่อ route นั้นถูกเรียกเท่านั้น
    """
    named = set(re.findall(r"acting_on\('([a-z_.]+)'", app_source()))
    by_field = {a for a, r in hr.USER_ACTIONS.items() if r.field}
    assert named | by_field == set(hr.USER_ACTIONS)


def test_every_patchable_field_has_a_rule_and_a_writer():
    """
    USER_FIELDS กับช่อง field ใน USER_ACTIONS ต้องตรงกันเป๊ะ

    เพิ่ม action ที่แก้ช่องใหม่แล้วลืมเขียนกฎ normalize/เทียบ/ตรวจ/เขียนของมัน จะได้
    ช่องที่ PATCH รับไว้แล้วบันทึกไม่เคยติด โดยยังตอบ 200 ให้ด้วย
    """
    assert set(hr.USER_FIELDS) == {r.field for r in hr.USER_ACTIONS.values() if r.field}
    assert set(hr.FIELD_ACTIONS) == set(hr.USER_FIELDS)


def test_fixture_user_ids_are_stable(hr_app, db):
    """
    tester.admin = 1, tester.staff = 2 ตามลำดับที่ hr_app สร้าง

    WRITE_FAILURES ใน test_api.py เขียน /api/users/2 ไว้ตรง ๆ เพราะ lambda ของมันรับ
    แค่ (client, record_id) แล้ว query อะไรไม่ได้ เทสต์นี้คือสิ่งที่ทำให้เลข 2 นั้น
    ไม่ใช่เลขลอย
    """
    with closing(db()) as con:
        assert hr.get_user(con, 1)['username'] == ADMIN_USER
        assert hr.get_user(con, 2)['username'] == STAFF_USER


# ────────────────────────────────────────────────
# Route — สร้างบัญชี
# ────────────────────────────────────────────────

NEW_ACCOUNT = {'username': 'malee', 'password': 'password-1',
               'display_name': 'นางสาวมาลี ศรีสุข', 'role': hr.ROLE_STAFF}


def _account(**overrides):
    body = dict(NEW_ACCOUNT)
    body.update(overrides)
    return body


def test_staff_cannot_create_an_admin(staff_client):
    r = staff_client.post('/api/users', json=_account(role=hr.ROLE_ADMIN))
    assert r.status_code == 403
    assert len(r.get_json()['error']) <= MAX_CLIENT_MESSAGE


def test_staff_can_create_a_staff_account(staff_client):
    """ด่านข้างบนต้องแคบอยู่ที่บทบาทที่ขอ ไม่ใช่กลายเป็นบัญชีที่สร้างอะไรไม่ได้เลย"""
    assert staff_client.post('/api/users', json=_account()).status_code == 201


def test_admin_can_create_an_admin(client):
    """ด้านกลับ: 403 ข้างบนมาจากกฎอันดับ ไม่ใช่จาก route ที่พังไปเลย"""
    assert client.post('/api/users', json=_account(role=hr.ROLE_ADMIN)).status_code == 201


def test_a_duplicate_username_is_409(client):
    """ชื่อซ้ำคือการชนกับสถานะที่มีอยู่ ไม่ใช่ body ที่ผิดรูป"""
    assert client.post('/api/users', json=_account()).status_code == 201
    r = client.post('/api/users', json=_account(display_name='คนละคน'))
    assert r.status_code == 409
    assert r.get_json()['error'] == hr.DUPLICATE_USER_MSG


@pytest.mark.parametrize('body, why', [
    (_account(username='ก'),                  'ชื่อผู้ใช้ไทย'),
    (_account(username='ab'),                 'ชื่อผู้ใช้สั้นเกินไป'),
    (_account(password='sh0rt'),              'รหัสผ่านสั้นเกินไป'),
    (_account(role='manager'),                'บทบาทไม่มีอยู่จริง'),
    (_account(display_name=''),               'ไม่ได้กรอกชื่อ'),
    (_account(display_name='ก' * 101),        'ชื่อยาวเกินเพดาน'),
])
def test_a_bad_create_body_is_422(client, body, why):
    r = client.post('/api/users', json=body)
    assert r.status_code == 422, why
    assert len(r.get_json()['error']) <= MAX_CLIENT_MESSAGE


def test_an_unknown_role_is_422_not_403(client):
    """
    {"role": "manager"} คือ body ผิดรูป ไม่ใช่การขอสิทธิ์เกินตัว

    ตอบ 403 จะบอกว่า "มีบทบาทนั้นอยู่แต่คุณสิทธิ์ไม่ถึง" ซึ่งไม่จริง และตอบ 500
    แปลว่า ROLE_RANK ได้รับค่าที่ยังไม่ผ่านด่าน
    """
    assert client.post('/api/users', json=_account(role='manager')).status_code == 422


def test_create_keeps_thai_in_the_display_name(client, db):
    assert client.post('/api/users', json=_account()).status_code == 201
    with closing(db()) as con:
        row = con.execute("SELECT display_name FROM users WHERE username='malee'").fetchone()
    assert row['display_name'] == 'นางสาวมาลี ศรีสุข'


def test_a_form_encoded_post_creates_nothing(client, db):
    """
    SameSite=Lax กัน form POST ข้ามเว็บไม่ได้ และระบบนี้ไม่มี CSRF token (ADR-0004)
    route พวกนี้จึงต้องอ่านแค่ application/json — json_object_body คือที่เดียวที่อ่าน body
    """
    r = client.post('/api/users', data=_account())
    assert r.status_code == 400
    with closing(db()) as con:
        assert hr.find_user(con, 'malee') is None


# ────────────────────────────────────────────────
# Route — แก้บัญชี (PATCH) หนึ่งคำขอ หนึ่ง transaction
# ────────────────────────────────────────────────

def test_staff_cannot_rename_an_admin(staff_client, db):
    r = staff_client.patch('/api/users/1', json={'display_name': 'ชื่อที่ไม่ควรติด'})
    assert r.status_code == 403
    assert len(r.get_json()['error']) <= MAX_CLIENT_MESSAGE
    with closing(db()) as con:
        assert hr.get_user(con, 1)['display_name'] == ''


def test_staff_can_rename_another_staff_account(staff_client, client, db):
    assert client.post('/api/users', json=_account()).status_code == 201
    with closing(db()) as con:
        uid = hr.find_user(con, 'malee')['id']
    r = staff_client.patch(f'/api/users/{uid}', json={'display_name': 'นางมาลี ใจดี'})
    assert r.status_code == 200
    assert r.get_json()['changed'] == ['display_name']
    with closing(db()) as con:
        assert hr.get_user(con, uid)['display_name'] == 'นางมาลี ใจดี'


def test_anyone_can_rename_their_own_account(staff_client, db):
    """self_denial ของ user.rename เป็น '' — ไม่มีทางล็อกตัวเองออกด้วยการเปลี่ยนชื่อ"""
    assert staff_client.patch('/api/users/2',
                              json={'display_name': 'นายทดสอบ'}).status_code == 200
    with closing(db()) as con:
        assert hr.get_user(con, 2)['display_name'] == 'นายทดสอบ'


def test_admin_can_rename_an_admin(client, db):
    assert client.patch('/api/users/1', json={'display_name': 'นายผู้ดูแล'}).status_code == 200


def test_patching_the_same_values_twice_converges(client):
    """ยิงซ้ำได้ผลเดียวกัน ครั้งที่สองคืน changed=[] พร้อม 200 ไม่ใช่ error"""
    body = {'display_name': 'นายสมชาย ใจดี', 'role': hr.ROLE_STAFF, 'is_active': True}
    first = client.patch('/api/users/2', json=body)
    second = client.patch('/api/users/2', json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.get_json()['changed'] == ['display_name']
    assert second.get_json()['changed'] == []


def test_an_unchanged_role_in_the_body_is_not_a_role_change(staff_client):
    """
    ฟอร์มส่งมาทั้งชุดทุกครั้ง การส่ง role ที่ไม่ได้แก้มาด้วยต้องไม่นับเป็นการเปลี่ยน
    บทบาท ไม่งั้นฝั่ง client ต้องมีตรรกะว่าช่องไหนเปลี่ยน ซึ่งเป็นที่ที่มันจะเขียนผิด
    """
    r = staff_client.patch('/api/users/2', json={'display_name': 'นายทดสอบ',
                                                 'role': hr.ROLE_STAFF})
    assert r.status_code == 200
    assert r.get_json()['changed'] == ['display_name']


def test_a_partly_permitted_edit_is_refused_whole(staff_client, db):
    """
    staff ส่งชื่อ (ทำได้) มาพร้อมบทบาท (ทำไม่ได้) ในคำขอเดียว ต้องถูกปฏิเสธทั้งก้อน
    ไม่ใช่เปลี่ยนชื่อสำเร็จแล้วค่อยปฏิเสธบทบาท เหลือบัญชีที่แก้ไปครึ่งเดียว
    """
    r = staff_client.patch('/api/users/2', json={'display_name': 'นายทดสอบ',
                                                 'role': hr.ROLE_ADMIN})
    assert r.status_code == 403
    with closing(db()) as con:
        row = hr.get_user(con, 2)
    assert row['display_name'] == '' and row['role'] == hr.ROLE_STAFF


def test_staff_cannot_change_any_role(staff_client):
    assert staff_client.patch('/api/users/2',
                              json={'role': hr.ROLE_ADMIN}).status_code == 403


def test_admin_can_change_a_role(client, db):
    assert client.patch('/api/users/2', json={'role': hr.ROLE_ADMIN}).status_code == 200
    with closing(db()) as con:
        assert hr.get_user(con, 2)['role'] == hr.ROLE_ADMIN


def test_admin_cannot_demote_themselves(client, db):
    """ลดบทบาทตัวเอง = ล็อกตัวเองออกจากทุกปุ่มของ admin โดยไม่มีทางกลับ"""
    r = client.patch('/api/users/1', json={'role': hr.ROLE_STAFF})
    assert r.status_code == 403
    with closing(db()) as con:
        assert hr.get_user(con, 1)['role'] == hr.ROLE_ADMIN


def test_staff_cannot_disable_an_account(staff_client):
    assert staff_client.patch('/api/users/2',
                              json={'is_active': False}).status_code == 403


def test_admin_can_disable_and_re_enable(client, db):
    assert client.patch('/api/users/2', json={'is_active': False}).status_code == 200
    with closing(db()) as con:
        assert hr.get_user(con, 2)['is_active'] == 0
    assert client.patch('/api/users/2', json={'is_active': True}).status_code == 200
    with closing(db()) as con:
        assert hr.get_user(con, 2)['is_active'] == 1


def test_admin_cannot_disable_themselves(client):
    """set_active ไล่เซสชันตัวเองออกใน request เดียวกัน คนที่กดจะถูกเตะออกกลางการกด"""
    assert client.patch('/api/users/1', json={'is_active': False}).status_code == 403


def test_the_system_always_keeps_an_active_admin(client, db, monkeypatch):
    """
    การเขียนที่ทำให้ไม่เหลือผู้ดูแลระบบที่ใช้งานได้ ต้องถูกยกเลิกทั้งก้อน

    กฎ "ห้ามปิดหรือลดบทบาทตัวเอง" ครอบเกือบหมดแล้ว แต่ไม่ใช่ทั้งหมด: admin สองคน
    สั่งปิดบัญชีของกันและกันพร้อมกัน ต่างคนต่างผ่านด่าน "ไม่ใช่ตัวเอง" ของตัวเอง
    และต่างคนต่างไม่ได้นับจำนวน admin ด่านจริงจึงต้องเป็นการนับ **หลังเขียน** ซึ่งอยู่
    ใน write lock ของ SQLite แล้ว (นับก่อนเขียนอ่าน snapshot ที่ถ่ายไว้ก่อนได้ lock
    สอง request ที่แข่งกันจะเห็นเลขเดียวกันทั้งคู่ แล้วทั้งคู่ผ่าน)

    เทสต์นี้ยิงจังหวะนั้นด้วยการทำให้การนับหลังเขียนตอบ 0 เพราะลำดับ commit จริง
    ของสอง request สร้างขึ้นใหม่ใน test client ไม่ได้ สิ่งที่พิสูจน์คือสองอย่างที่
    พังได้จริง: route ตอบ 409 ไม่ใช่ 500 และแถวถูก rollback ไม่ใช่ค้างครึ่งทาง
    (ของจริงพิสูจน์ด้วยการแข่งสองเธรดกับเซิร์ฟเวอร์ที่รันอยู่ ดู docs/adr/0005)
    """
    # ก่อนเขียนยังมี admin อยู่ หลังเขียนไม่เหลือ — คือรูปร่างของการแข่งกันพอดี
    counts = iter([1, 0])
    monkeypatch.setattr(hr, '_active_admin_count', lambda con: next(counts))

    r = client.patch('/api/users/2', json={'display_name': 'ชื่อใหม่'})

    assert r.status_code == 409
    assert len(r.get_json()['error']) <= 80
    with closing(db()) as con:
        assert hr.get_user(con, 2)['display_name'] != 'ชื่อใหม่', \
            'การเขียนต้องถูก rollback ทั้งก้อน ไม่ใช่ commit แล้วค่อยบ่น'


def test_a_read_only_request_does_not_pay_for_the_admin_count(client, monkeypatch):
    """
    request ที่ไม่ได้เขียนอะไรต้องไม่ถูกปฏิเสธด้วยกฎที่คำตอบเปลี่ยนไม่ได้

    PATCH ที่ส่งค่าเดิมมาไม่เขียนอะไรเลย ถ้ามันยังไปนับ admin แล้วเจอ 0 (เช่น DB ที่
    restore ผิดไฟล์มาโดยไม่มี admin ที่ใช้งานได้) การแก้ชื่อบัญชีอื่นจะพังไปด้วย
    ทั้งที่ไม่ได้ทำให้อะไรแย่ลง
    """
    calls = []

    def counted(con):
        calls.append(1)
        return 0 if len(calls) > 1 else 1

    monkeypatch.setattr(hr, '_active_admin_count', counted)

    r = client.patch('/api/users/2', json={'display_name': ''})

    assert r.status_code == 200
    assert r.get_json()['changed'] == []
    assert len(calls) == 1, 'ไม่มีการเขียน ก็ไม่ต้องจ่าย query นับ admin รอบที่สอง'


def test_is_active_must_be_a_real_boolean(client, db):
    """'false' เป็นสตริงที่ truthy — รับไว้คือการเปิดบัญชีตอนที่คนสั่งปิด"""
    r = client.patch('/api/users/2', json={'is_active': 'false'})
    assert r.status_code == 422
    with closing(db()) as con:
        assert hr.get_user(con, 2)['is_active'] == 1


def test_an_unknown_field_is_422(client):
    """
    คีย์แปลกปลอมต้องไม่ถูกข้ามเงียบ ๆ: client ที่พิมพ์ชื่อช่องผิดจะได้ 200 พร้อม
    changed=[] แล้วเข้าใจว่าบันทึกแล้วทั้งที่ไม่มีอะไรเปลี่ยน
    """
    assert client.patch('/api/users/2', json={'username': 'ชื่อใหม่'}).status_code == 422
    assert client.patch('/api/users/2', json={'password': 'x' * 12}).status_code == 422


def test_an_empty_patch_body_changes_nothing(client):
    r = client.patch('/api/users/2', json={})
    assert r.status_code == 200
    assert r.get_json()['changed'] == []


def test_disabling_an_account_cuts_its_sessions_in_the_same_request(hr_app, client, db):
    """
    อะตอมจริง: ปิดบัญชีแล้วเซสชันของคนนั้นหายไปพร้อมกัน ไม่ใช่รอให้หมดอายุ

    login_as เป็นฟังก์ชันธรรมดา จึงถือ cookie jar สองใบต่อ DB เดียวได้ — ใบหนึ่งเป็น
    admin ที่สั่ง อีกใบเป็น staff ที่กำลังใช้งานอยู่
    """
    victim = login_as(hr_app, STAFF_USER, STAFF_PASS)
    assert victim.get('/api/records').status_code == 200
    assert client.patch('/api/users/2', json={'is_active': False}).status_code == 200
    assert victim.get('/api/records').status_code == 401
    with closing(db()) as con:
        assert con.execute('SELECT COUNT(*) FROM sessions WHERE user_id=2').fetchone()[0] == 0


def test_a_demoted_admin_is_already_staff_on_their_next_request(hr_app, client, db):
    """
    เซสชันไม่แบก role: resolve_session JOIN users สด ๆ ทุก request

    นี่คือเหตุผลที่การเปลี่ยนบทบาทไม่ต้อง revoke_sessions และเป็นคุณสมบัติที่ได้มาจาก
    การเก็บเซสชันฝั่ง server (docs/adr/0003) ถ้าวันหน้าใครย้าย role ไปอยู่ในเซสชัน
    เทสต์นี้จะแดง
    """
    with closing(db()) as con:
        uid = hr.create_user(con, 'second.admin', 'second-password', hr.ROLE_ADMIN)
        con.commit()
    second = login_as(hr_app, 'second.admin', 'second-password')
    assert second.delete('/api/records').status_code == 200
    assert client.patch(f'/api/users/{uid}', json={'role': hr.ROLE_STAFF}).status_code == 200
    assert second.delete('/api/records').status_code == 403


# ────────────────────────────────────────────────
# Route — ตั้งรหัสผ่านใหม่ให้คนอื่น
# ────────────────────────────────────────────────

def test_staff_cannot_reset_an_admins_password(staff_client):
    r = staff_client.put('/api/users/1/password', json={'password': 'brand-new-pass'})
    assert r.status_code == 403
    assert len(r.get_json()['error']) <= MAX_CLIENT_MESSAGE


def test_admin_can_reset_a_staffs_password(hr_app, client):
    r = client.put('/api/users/2/password', json={'password': 'brand-new-pass'})
    assert r.status_code == 200
    assert login_as(hr_app, STAFF_USER, 'brand-new-pass')


def test_staff_can_reset_another_staffs_password(staff_client, client, db):
    assert client.post('/api/users', json=_account()).status_code == 201
    with closing(db()) as con:
        uid = hr.find_user(con, 'malee')['id']
    assert staff_client.put(f'/api/users/{uid}/password',
                            json={'password': 'brand-new-pass'}).status_code == 200


@pytest.mark.parametrize('who, uid', [('client', 1), ('staff_client', 2)])
def test_nobody_resets_their_own_password_through_this_tab(request, who, uid):
    """
    การตั้งรหัสใหม่โดยไม่ต้องรู้รหัสเดิมคือสิ่งเดียวที่ /change-password กันไว้
    ข้อความต้องบอกทางไป ไม่ใช่แค่ปฏิเสธ
    """
    c = request.getfixturevalue(who)
    r = c.put(f'/api/users/{uid}/password', json={'password': 'brand-new-pass'})
    assert r.status_code == 403
    assert 'รหัสผ่าน' in r.get_json()['error']


def test_a_password_reset_revokes_the_target_and_not_the_actor(hr_app, client):
    """set_password + revoke_sessions ใน transaction เดียว และไม่ส่ง keep_token"""
    victim = login_as(hr_app, STAFF_USER, STAFF_PASS)
    r = client.put('/api/users/2/password', json={'password': 'brand-new-pass'})
    assert r.status_code == 200 and r.get_json()['revoked'] >= 1
    assert victim.get('/api/records').status_code == 401
    assert client.get('/api/records').status_code == 200


def test_a_password_reset_also_clears_a_temporary_lock(client, db):
    """set_password ล้าง failed_count/locked_until ให้ — บัญชีที่ถูกล็อกกลับมาใช้ได้ทันที"""
    with closing(db()) as con:
        con.execute("UPDATE users SET failed_count=5, locked_until=? WHERE id=2",
                    (hr.ts_after(minutes=5),))
        con.commit()
    assert client.put('/api/users/2/password',
                      json={'password': 'brand-new-pass'}).status_code == 200
    with closing(db()) as con:
        row = con.execute('SELECT failed_count, locked_until FROM users WHERE id=2').fetchone()
    assert row['failed_count'] == 0 and row['locked_until'] is None


def test_a_short_password_is_422(client):
    r = client.put('/api/users/2/password', json={'password': 'sh0rt'})
    assert r.status_code == 422
    assert len(r.get_json()['error']) <= MAX_CLIENT_MESSAGE


# ────────────────────────────────────────────────
# Route — บัญชีที่ไม่มีอยู่ และ body ที่ไม่ใช่ JSON
# ────────────────────────────────────────────────

@pytest.mark.parametrize('method, url, body', [
    ('patch', '/api/users/9999', {'display_name': 'ใครก็ไม่รู้'}),
    ('put',   '/api/users/9999/password', {'password': 'brand-new-pass'}),
])
def test_a_missing_account_is_404(client, method, url, body):
    r = getattr(client, method)(url, json=body)
    assert r.status_code == 404
    assert r.get_json()['error'] == hr.NO_SUCH_USER_MSG


@pytest.mark.parametrize('method, url', [
    ('post',  '/api/users'),
    ('patch', '/api/users/2'),
    ('put',   '/api/users/2/password'),
])
def test_a_body_that_is_not_a_json_object_is_400(client, method, url):
    r = getattr(client, method)(url, json=['ไม่ใช่ object'])
    assert r.status_code == 400


# ────────────────────────────────────────────────
# ระบบต้องเหลือผู้ดูแลระบบที่ใช้งานได้เสมอ
# ────────────────────────────────────────────────

def test_the_system_always_has_an_active_admin(hr_app, db):
    """
    แอดมินสองคนปิดบัญชีกันเองพร้อมกัน ทั้งคู่ผ่านกฎ "ห้ามปิดบัญชีตัวเอง" ของตัวเอง
    ถ้าไม่มีใครนับจำนวนแอดมินในจังหวะที่ถือ write lock อยู่ ทั้งคู่จะ commit แล้ว
    ระบบเหลือแอดมินที่ใช้งานได้ศูนย์บัญชี ซึ่งกู้คืนได้เฉพาะที่เครื่องเซิร์ฟเวอร์
    """
    with closing(db()) as con:
        second = hr.create_user(con, 'second.admin', 'second-password', hr.ROLE_ADMIN)
        con.commit()
    first_client = login_as(hr_app, ADMIN_USER, ADMIN_PASS)
    second_client = login_as(hr_app, 'second.admin', 'second-password')

    codes, lock = [], threading.Lock()
    start = threading.Barrier(2)

    def disable(c, uid):
        start.wait()
        r = c.patch(f'/api/users/{uid}', json={'is_active': False})
        with lock:
            codes.append(r.status_code)

    threads = [threading.Thread(target=disable, args=(first_client, second)),
               threading.Thread(target=disable, args=(second_client, 1))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with closing(db()) as con:
        left = hr._active_admin_count(con)
    assert left >= 1, 'ระบบเหลือแอดมินที่ใช้งานได้ศูนย์บัญชี — ล็อกตัวเองออกถาวร'
    assert codes.count(200) == 1, f'มีคนเดียวที่ควรสำเร็จ แต่ได้ {codes}'


def test_an_already_adminless_database_can_still_be_edited(hr_app, staff_client, db):
    """
    กฎข้างบนต้องแคบอยู่ที่ "คำขอนี้เป็นตัวที่ทำให้เหลือศูนย์" ไม่ใช่ "ตอนนี้เหลือศูนย์"

    DB ที่ไม่มีแอดมินที่ใช้งานได้อยู่ก่อนแล้ว (restore backup ผิดไฟล์ หรือใครไปปิด
    บัญชีด้วย CLI) ต้องยังแก้ชื่อบัญชีอื่นได้ ไม่ใช่กลายเป็นระบบที่แก้อะไรไม่ได้เลย
    """
    with closing(db()) as con:
        con.execute('UPDATE users SET is_active=0 WHERE role=?', (hr.ROLE_ADMIN,))
        con.commit()
    assert hr.ROLE_STAFF  # staff_client ยังล็อกอินอยู่ บัญชีของเขาไม่ได้ถูกปิด
    assert staff_client.patch('/api/users/2',
                              json={'display_name': 'นายทดสอบ'}).status_code == 200


# ────────────────────────────────────────────────
# สิ่งที่ไม่ควรออกไปถึง client และสิ่งที่ต้องออกไป
# ────────────────────────────────────────────────

def test_the_response_never_carries_a_password_hash(client):
    """assert ทับ text ทั้งก้อน ไม่ใช่ทีละคีย์ — SELECT * ที่ใครเพิ่มวันหน้าต้องไม่หลุดทางนี้"""
    body = client.get('/api/users').get_data(as_text=True)
    for leak in ('password_hash', 'scrypt', 'pbkdf2', 'failed_count'):
        assert leak not in body


def test_the_response_never_carries_locked_until(client):
    """client ได้ `status` ที่อ่านแล้วจบ ไม่ได้วัตถุดิบที่ต้องตีความด้วยนโยบายล็อกฉบับที่สอง"""
    assert 'locked_until' not in client.get('/api/users').get_data(as_text=True)


def test_timestamps_arrive_in_a_sortable_format(client):
    """created/updated ต้องเป็น YYYY-MM-DD HH:MM — dd/mm/YYYY กดเรียงแล้วผิดลำดับเวลา"""
    u = client.get('/api/users').get_json()['users'][0]
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', u['created'])
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', u['updated'])


def test_the_status_travels_as_a_code_with_its_label_beside_it(client, db):
    """
    แถวส่ง 'locked' ไม่ใช่ 'ล็อกชั่วคราว' — ฝั่ง client เลือกสีจากรหัส ไม่ใช่จากการ
    เทียบสตริงไทย และป้ายไทยเดินทางมาเป็น list ของคู่เพื่อรักษาลำดับ
    """
    with closing(db()) as con:
        con.execute('UPDATE users SET locked_until=? WHERE id=2', (hr.ts_after(minutes=5),))
        con.commit()
    data = client.get('/api/users').get_json()
    locked = next(u for u in data['users'] if u['id'] == 2)
    assert locked['status'] == hr.STATUS_LOCKED
    assert data['status_labels'] == [list(p) for p in hr.STATUS_LABELS.items()]
    assert data['role_labels'] == [list(p) for p in hr.ROLE_LABELS.items()]


def test_the_name_falls_back_to_the_username_at_the_read_boundary(client):
    """
    `name` คือสิ่งที่จอแสดงและใช้เรียง `display_name` คือคอลัมน์ดิบที่ฟอร์มเติม
    ถ้า fallback อยู่ในฟอร์ม ผู้ใช้จะกดบันทึกแล้วได้ชื่อผู้ใช้กลายเป็นชื่อตัวเอง
    """
    me = client.get('/api/users').get_json()['users'][0]
    assert me['display_name'] == ''
    assert me['name'] == ADMIN_USER


def test_the_password_hint_is_the_enforcement_message(client):
    """เลข 8 มีที่เดียวคือ PASSWORD_MIN_LEN ไม่ใช่สองที่ที่เลื่อนออกจากกันได้"""
    assert client.get('/api/users').get_json()['password_hint'] == hr.password_problem('')


def test_staff_sees_no_admin_role_option(staff_client):
    """grantable_roles กรองด้วยกฎอันดับ — staff ไม่เห็น 'admin' เป็นตัวเลือกเลย"""
    data = staff_client.get('/api/users').get_json()
    assert [r[0] for r in data['grantable_roles']] == [hr.ROLE_STAFF]
    assert data['can_create'] is True


def test_admin_can_grant_both_roles(client):
    data = client.get('/api/users').get_json()
    assert [r[0] for r in data['grantable_roles']] == list(hr.ROLES)


def test_staff_sees_every_account_including_admins(staff_client):
    """ตัดสินใจที่บันทึกไว้: ไม่กรองแถว admin ออก แต่ `can` ของมันว่าง"""
    rows = staff_client.get('/api/users').get_json()['users']
    admin_row = next(r for r in rows if r['role'] == hr.ROLE_ADMIN)
    assert admin_row['can'] == []


@pytest.mark.parametrize('who', ['client', 'staff_client'])
def test_can_on_each_row_matches_what_the_route_enforces(request, who):
    """
    ปุ่มที่วาดกับคำปฏิเสธมาจาก permission_problem ตัวเดียวกัน เทสต์นี้ปักว่ายังจริง

    สำหรับทุกแถวและทุก action ที่มีเป้าหมาย: `can` บอกว่าทำได้ route ต้องไม่ตอบ 403
    และบอกว่าทำไม่ได้ route ต้องตอบ 403 เป๊ะ ๆ
    """
    c = request.getfixturevalue(who)
    # ทุก probe ต้องขอค่าที่ **ต่างจากของเดิมจริง** ไม่งั้น PATCH จะไม่นับว่าเป็น action
    # นั้นเลย (ดู user_changes) แล้วเทสต์จะเทียบ 200-ที่ไม่ได้ทำอะไร กับคำว่า "ทำได้"
    probe = {
        'user.rename':   lambda row: c.patch(f'/api/users/{row["id"]}',
                                             json={'display_name': 'ชื่อทดสอบสิทธิ์'}),
        'user.role':     lambda row: c.patch(
            f'/api/users/{row["id"]}',
            json={'role': hr.ROLE_STAFF if row['role'] == hr.ROLE_ADMIN else hr.ROLE_ADMIN}),
        'user.active':   lambda row: c.patch(
            f'/api/users/{row["id"]}',
            json={'is_active': row['status'] == hr.STATUS_DISABLED}),
        'user.password': lambda row: c.put(f'/api/users/{row["id"]}/password',
                                           json={'password': 'brand-new-pass'}),
    }
    assert set(probe) == set(hr.TARGET_ACTIONS)
    for row in c.get('/api/users').get_json()['users']:
        for action, send in probe.items():
            refused = send(row).status_code == 403
            assert refused is (action not in row['can']), f'{action} id={row["id"]}'


def test_no_error_message_echoes_a_value_the_client_sent(client):
    """
    ข้อความที่ผู้ใช้เห็นต้องเป็นข้อความคงที่ ไม่มีค่าที่ client ส่งมาประกอบอยู่ข้างใน

    toastError วัดความยาวของ err.message แล้วยกระดับที่ยาวเกิน 80 เป็น alert() ที่
    ต้องกดปิด ข้อความที่ประกอบจากค่าที่ผู้ใช้พิมพ์มาจะข้ามเพดานนั้นเมื่อไหร่ก็ได้
    โดยไม่มีเทสต์ไหนจับได้ และมันยังเป็นทางให้ค่าที่ไม่น่าเชื่อถือย้อนกลับไปขึ้นจอ
    """
    sent = 'ก' * 200
    replies = [
        client.post('/api/users', json=_account(username=sent)),
        client.post('/api/users', json=_account(display_name=sent)),
        client.post('/api/users', json=_account(role=sent)),
        client.patch('/api/users/2', json={'display_name': sent}),
        client.patch('/api/users/2', json={sent: 'x'}),
        client.patch('/api/users/2', json={'role': sent}),
        client.put('/api/users/2/password', json={'password': sent[:3]}),
    ]
    for r in replies:
        msg = r.get_json()['error']
        assert sent[:10] not in msg, msg
        assert len(msg) <= MAX_CLIENT_MESSAGE, msg


# ────────────────────────────────────────────────
# audit log — ไฟล์ log เป็นที่เดียวที่บันทึกว่าใครแตะบัญชีใคร
# ────────────────────────────────────────────────

def test_each_mutation_writes_one_audit_line_naming_both_people(client, caplog, db):
    """
    CLAUDE.md บอกว่าเหตุผลที่ชื่อผู้ใช้ถูกจองตลอดไปคือบรรทัดใน log ต้องชี้ไปที่คนเดียว
    บรรทัดของแท็บนี้จึงต้องมีทั้งชื่อคนที่สั่งและชื่อบัญชีที่ถูกแตะ ไม่ใช่แค่ id
    """
    with caplog.at_level('INFO', logger='hr_promotion'):
        client.post('/api/users', json=_account())
        client.patch('/api/users/2', json={'display_name': 'นายทดสอบ'})
        client.put('/api/users/2/password', json={'password': 'brand-new-pass'})
    assert f'user={ADMIN_USER} user_create' in caplog.text
    assert 'username=malee' in caplog.text
    assert f'user={ADMIN_USER} user_patch id=2 username={STAFF_USER}' in caplog.text
    assert f'user={ADMIN_USER} user_password id=2 username={STAFF_USER}' in caplog.text


def test_a_refusal_writes_an_audit_line(staff_client, caplog):
    """errorhandler ของ UserRequestRefused เขียน audit ที่เดียว แบบที่ require_admin ทำ"""
    with caplog.at_level('INFO', logger='hr_promotion'):
        staff_client.patch('/api/users/1', json={'display_name': 'ชื่อที่ไม่ควรติด'})
    assert f'user={STAFF_USER} refused 403 PATCH /api/users/1' in caplog.text


def test_a_patch_that_changes_nothing_writes_no_audit_line(client, caplog):
    """บรรทัด audit ต้องบอกสิ่งที่เปลี่ยนจริง ไม่ใช่สิ่งที่ถูกส่งมา"""
    with caplog.at_level('INFO', logger='hr_promotion'):
        client.patch('/api/users/2', json={'role': hr.ROLE_STAFF})
    assert 'user_patch' not in caplog.text
