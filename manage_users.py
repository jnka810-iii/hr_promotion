# -*- coding: utf-8 -*-
"""
จัดการบัญชีผู้ใช้ของระบบ HR — ต้องรันที่เครื่องที่เป็นเซิร์ฟเวอร์

    python manage_users.py add somchai --role admin
    python manage_users.py list
    python manage_users.py passwd somchai
    python manage_users.py role somchai staff
    python manage_users.py disable somchai
    python manage_users.py enable somchai
    python manage_users.py logout-all somchai

ทำไมยังมี CLI ทั้งที่มีแท็บจัดการผู้ใช้งานบนเว็บแล้ว (ADR 0005):

**บัญชีแรก** แท็บอยู่หลังด่านล็อกอิน มันจึงสร้างบัญชีแรกไม่ได้ และหน้าสร้างบัญชีที่ไม่
ต้องล็อกอินคือ endpoint ที่เขียนข้อมูลได้โดยไม่มีด่าน เปิดบนพอร์ตที่ทุกเครื่องในวง LAN
เข้าถึง และกลับมาเปิดอีกทุกครั้งที่ตาราง users ว่าง รวมถึงตอน restore backup ผิดไฟล์
ใครในวงที่เปิดหน้านั้นทันก่อนเจ้าของระบบก็ได้เป็นแอดมิน

**ทางกลับตอนที่ไม่เหลือใครเข้าได้** ถ้าบัญชีแอดมินทุกบัญชีถูกปิดหรือถูกล็อกพร้อมกัน
ไม่มีใครเปิดแท็บนั้นได้ enable กับ passwd ที่นี่คือทางเดียว ด้วยเหตุผลเดียวกัน
**คำสั่งในไฟล์นี้ไม่ตรวจอันดับบทบาท** คนที่นั่งหน้าเครื่องนี้อยู่นอกระบบบทบาทโดยนิยาม
(ADR 0004 ขอบเขตความปลอดภัยคือการเข้าถึงเครื่องทางกายภาพ) การเติมกฎบทบาทลงมาที่นี่
คือการปิดทางกลับทางเดียวที่มี อย่าเติม

ไม่มีคำสั่งลบบัญชี มีแต่ disable — ถ้าลบ 'somchai' ทิ้งได้ วันหน้ามีคนสร้าง 'somchai'
ใหม่ แล้วบรรทัดใน hr_promotion.log ที่เขียนว่า somchai ลบ record 17 จะชี้ไปที่คนสอง
คนที่แยกกันไม่ออก การปิดบัญชีทำให้ชื่อถูกจองไว้ตลอดไป

รหัสผ่านรับทาง prompt เท่านั้น ไม่เคยรับทาง argv — argv ไปโผล่ใน history ของ shell
และในรายการโปรเซสที่คนอื่นบนเครื่องเดียวกันอ่านได้
"""

import argparse
import getpass
import sqlite3
import sys
from contextlib import closing

import app as hr

# ข้อความไทยต้องพิมพ์ออกได้แม้ stdout ถูก redirect ลงไฟล์ (ตอนนั้น Python ใช้ encoding
# ตาม locale ของเครื่องซึ่งบน Windows ไทยมักเป็น cp874 แล้วบางตัวอักษรจะพังทั้งคำสั่ง)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def ask_new_password(username: str) -> str:
    """
    ถามรหัสผ่านใหม่สองครั้งแล้วตรวจตามนโยบายเดียวของระบบ

    Parameters:
        username (str): ชื่อผู้ใช้ ใช้แสดงใน prompt เท่านั้น

    Returns:
        str: รหัสผ่านที่ผ่านการตรวจแล้ว

    Raises:
        SystemExit: พิมพ์ไม่ตรงกัน หรือไม่ผ่านนโยบาย
    """
    first = getpass.getpass(f'รหัสผ่านใหม่ของ {username}: ')
    problem = hr.password_problem(first)
    if problem:
        sys.exit(f'ไม่สำเร็จ: {problem}')
    if first != getpass.getpass('พิมพ์รหัสผ่านอีกครั้ง: '):
        sys.exit('ไม่สำเร็จ: รหัสผ่านสองครั้งไม่ตรงกัน')
    return first


def need_user(con, username: str):
    """
    หาผู้ใช้ หรือจบโปรแกรมถ้าไม่มี

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        username (str): ชื่อที่ normalize แล้ว

    Returns:
        sqlite3.Row: แถวใน users

    Raises:
        SystemExit: ไม่มีผู้ใช้ชื่อนี้
    """
    user = hr.find_user(con, username)
    if user is None:
        sys.exit(f'ไม่สำเร็จ: ไม่มีผู้ใช้ชื่อ {username}')
    return user


def cmd_add(con, args):
    """
    สร้างบัญชีใหม่

    ตรวจชื่อซ้ำก่อนถามรหัสผ่าน แล้วยังดัก IntegrityError อีกชั้น ไม่ใช่กฎสองสำเนา:
    UNIQUE index คือกฎ ส่วนการถามก่อนมีไว้เพื่อไม่ให้คนพิมพ์รหัสผ่านสองรอบทิ้งเปล่า
    (route ฝั่งเว็บไม่มีการถามก่อน เพราะที่นั่นมันเป็น race จริงและไม่มีใครต้องพิมพ์)
    """
    username = hr.normalize_username(args.username)
    name = hr.normalize_account_name(args.name)
    # --name ไม่บังคับที่นี่ ต่างจากฟอร์มบนเว็บที่บังคับ: คำสั่งนี้คือทางเดียวที่สร้าง
    # บัญชีแรกได้ และมันถูกเขียนไว้ใน README กับหน้าล็อกอินโดยไม่มี --name
    # เพิ่มเงื่อนไขที่นี่ = วันที่ติดตั้งใหม่แล้วไม่มีใครเข้าระบบได้เลย
    problems = [hr.username_problem(username)]
    if name:
        problems.append(hr.account_name_problem(name))
    for problem in problems:
        if problem:
            sys.exit(f'ไม่สำเร็จ: {problem}')
    if hr.find_user(con, username):
        sys.exit(f'ไม่สำเร็จ: {hr.DUPLICATE_USER_MSG}')
    password = ask_new_password(username)
    try:
        hr.create_user(con, username, password, args.role, name)
    except sqlite3.IntegrityError:
        sys.exit(f'ไม่สำเร็จ: {hr.DUPLICATE_USER_MSG}')
    con.commit()
    print(f'สร้างผู้ใช้ {username} ({name + ", " if name else ""}บทบาท {args.role}) แล้ว')


def cmd_list(con, args):
    """
    แสดงบัญชีทั้งหมดพร้อมจำนวนเซสชันที่ยังไม่หมดอายุ
    """
    now = hr.ts_now()
    rows = sorted(hr.list_users(con), key=lambda r: r['username'])
    if not rows:
        print('ยังไม่มีผู้ใช้เลย — สร้างคนแรกด้วย:')
        print('    python manage_users.py add <ชื่อผู้ใช้> --role admin')
        return
    # จัดคอลัมน์เฉพาะช่องที่เป็น ASCII เท่านั้น — ความกว้างที่ f-string นับคือจำนวน
    # code point ซึ่งไม่ใช่จำนวนช่องที่ตัวอักษรไทยกินบนจอ (สระและวรรณยุกต์ซ้อนอยู่บน
    # ตัวอื่นไม่กินที่เลย) การ pad ข้อความไทยจึงได้ตารางที่เบี้ยวกว่าไม่ pad
    print(f'{"USER":<24} {"ROLE":<6} สถานะ')
    for r in rows:
        state = hr.STATUS_LABELS[hr.account_status(r, now)]
        name = (r['display_name'] or '').strip()
        print(f'{r["username"]:<24} {r["role"]:<6} {state} — '
              f'{name or "(ยังไม่ได้ตั้งชื่อ)"} ล็อกอินค้างอยู่ {r["live"]} เครื่อง')


def cmd_passwd(con, args):
    """ตั้งรหัสผ่านใหม่ให้ผู้ใช้ แล้วไล่ออกจากทุกเครื่อง"""
    username = hr.normalize_username(args.username)
    user = need_user(con, username)
    password = ask_new_password(username)
    hr.set_password(con, user['id'], password)
    # ไล่ออกทุกเครื่องโดยไม่มีข้อยกเว้น: คนที่มาสั่งคำสั่งนี้คือแอดมินที่นั่งหน้า
    # เครื่องเซิร์ฟเวอร์ ไม่ใช่เจ้าของบัญชีที่กำลังพิมพ์อยู่บนเครื่องตัวเอง และเหตุผล
    # ที่ต้องรีเซ็ตรหัสมักคือ "รหัสรู้ถึงคนอื่นแล้ว" ซึ่งแปลว่าเซสชันที่ค้างอยู่
    # น่าสงสัยทั้งหมด
    gone = hr.revoke_sessions(con, user['id'])
    con.commit()
    print(f'ตั้งรหัสผ่านใหม่ของ {username} แล้ว (ไล่ออก {gone} เครื่อง)')


def cmd_role(con, args):
    """เปลี่ยนบทบาท"""
    username = hr.normalize_username(args.username)
    user = need_user(con, username)
    hr.set_role(con, user['id'], args.role)
    con.commit()
    print(f'{username} เป็นบทบาท {args.role} แล้ว')


def cmd_disable(con, args):
    """ปิดบัญชีและไล่ออกจากทุกเครื่องทันที"""
    username = hr.normalize_username(args.username)
    user = need_user(con, username)
    hr.set_active(con, user['id'], False)
    con.commit()
    print(f'ปิดบัญชี {username} และไล่ออกจากทุกเครื่องแล้ว')


def cmd_enable(con, args):
    """เปิดบัญชีที่ถูกปิดไว้"""
    username = hr.normalize_username(args.username)
    user = need_user(con, username)
    hr.set_active(con, user['id'], True)
    con.commit()
    print(f'เปิดบัญชี {username} แล้ว (ต้องล็อกอินใหม่)')


def cmd_logout_all(con, args):
    """ไล่ผู้ใช้ออกจากทุกเครื่อง โดยไม่แตะรหัสผ่าน"""
    username = hr.normalize_username(args.username)
    user = need_user(con, username)
    gone = hr.revoke_sessions(con, user['id'])
    con.commit()
    print(f'ไล่ {username} ออกจาก {gone} เครื่องแล้ว')


def build_parser():
    """
    ประกอบ argparse ของทุกคำสั่งย่อย

    Returns:
        argparse.ArgumentParser
    """
    p = argparse.ArgumentParser(
        prog='manage_users.py',
        description='จัดการบัญชีผู้ใช้ของระบบ HR (รันที่เครื่องเซิร์ฟเวอร์)')
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('add', help='สร้างบัญชีใหม่')
    a.add_argument('username')
    a.add_argument('--name', default='', help='ชื่อ-นามสกุลที่แสดงในหน้าจัดการผู้ใช้งาน')
    a.add_argument('--role', choices=hr.ROLES, default=hr.ROLE_STAFF)
    a.set_defaults(func=cmd_add)

    sub.add_parser('list', help='แสดงบัญชีทั้งหมด').set_defaults(func=cmd_list)

    for name, func, helptext in (
        ('passwd',      cmd_passwd,     'ตั้งรหัสผ่านใหม่ (ไล่ออกทุกเครื่อง)'),
        ('disable',     cmd_disable,    'ปิดบัญชี (ไล่ออกทุกเครื่องทันที)'),
        ('enable',      cmd_enable,     'เปิดบัญชีที่ปิดไว้'),
        ('logout-all',  cmd_logout_all, 'ไล่ออกทุกเครื่อง ไม่แตะรหัสผ่าน'),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument('username')
        s.set_defaults(func=func)

    r = sub.add_parser('role', help='เปลี่ยนบทบาท')
    r.add_argument('username')
    r.add_argument('role', choices=hr.ROLES)
    r.set_defaults(func=cmd_role)

    return p


def main(argv=None):
    """
    Entry point ของ CLI

    สำรอง DB ก่อน init_db() ตามลำดับเดียวกับ app.main() — ถ้าคำสั่งนี้เป็นสิ่งแรกที่รัน
    หลังอัปเกรดจาก schema เก่า init_db() จะเป็นตัวรัน migration ซึ่งแก้ข้อมูลและลบไฟล์
    ที่ถูกแทนที่ สำเนาจึงต้องเกิดก่อน (สำเนาแรกของวันชนะ รันซ้ำไม่ทับของเดิม)

    Parameters:
        argv (list[str] | None): ส่งเข้ามาได้ตอนเทสต์ ปกติอ่านจาก sys.argv

    Returns:
        None
    """
    args = build_parser().parse_args(argv)
    hr._backup_db()
    hr.init_db()
    with closing(hr.get_db()) as con:
        args.func(con, args)


if __name__ == '__main__':
    main()
