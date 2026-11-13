# -*- coding: utf-8 -*-
"""
เทสต์ของแท็บจัดการผู้ใช้งาน — กฎสิทธิ์ ตาราง users และ route /api/users

ส่วนแรกของไฟล์ไม่ใช้ `client` เลยแม้แต่ตัวเดียว ซึ่งเป็นเหตุผลทั้งหมดที่
`permission_problem` ถูกทำให้บริสุทธิ์: ตารางสิทธิ์ทั้งใบทดสอบได้โดยไม่ต้องมี Flask,
ไม่ต้องมี DB และไม่ต้องมี HTTP

รัน: .venv/Scripts/python.exe -m pytest tests/test_users.py
"""

import os
import sqlite3
from contextlib import closing

import pytest

import app as hr
from conftest import AUTH_SQL_MARK


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
