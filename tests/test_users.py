# -*- coding: utf-8 -*-
"""
เทสต์ของแท็บจัดการผู้ใช้งาน — กฎสิทธิ์ ตาราง users และ route /api/users

ส่วนแรกของไฟล์ไม่ใช้ `client` เลยแม้แต่ตัวเดียว ซึ่งเป็นเหตุผลทั้งหมดที่
`permission_problem` ถูกทำให้บริสุทธิ์: ตารางสิทธิ์ทั้งใบทดสอบได้โดยไม่ต้องมี Flask,
ไม่ต้องมี DB และไม่ต้องมี HTTP

รัน: .venv/Scripts/python.exe -m pytest tests/test_users.py
"""

import pytest

import app as hr


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
