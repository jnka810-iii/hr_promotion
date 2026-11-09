#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ตรวจสุขภาพการติดตั้งจริง. ด่านกันข้อมูลหายซ้ำรอยเดิม

ต่างจาก check_db.py ที่ตรวจ schema และเขียน PRAGMA ลง DB จริง สคริปต์นี้
**อ่านอย่างเดียว** ทุกการเชื่อมต่อใช้ mode=ro จึงรันได้ขณะแอปเปิดอยู่

ต่างจาก pytest ที่ชี้ DB_PATH ไป tmp_path และห้ามแตะไฟล์จริงเด็ดขาด สคริปต์นี้
ตรวจ **ไฟล์จริง** ซึ่งเป็นสิ่งที่ pytest ตรวจไม่ได้ตามการออกแบบ

ทำไมต้องมี: ข้อมูลจริงเคยหายเพราะ hr_promotion.db ถูก track ใน git ทุก
git checkout จึงเขียนเวอร์ชันเดือนเมษายนทับ อาการนั้นมองไม่เห็นจากในแอป
ตัวเลขที่เปิดโปงมันคือจำนวนไฟล์แนบกำพร้าเทียบกับจำนวนแถวใน attachments

    python check_health.py        # exit 0 = ผ่านหมด, 1 = มีข้อที่ไม่ผ่าน
"""
import os
import sqlite3
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'hr_promotion.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
BACKUP_DIR = os.path.join(BASE_DIR, 'backup')
DB_NAMES = ('hr_promotion.db', 'hr_promotion.db-shm', 'hr_promotion.db-wal')

failures = []


def ok(msg):
    print('  [ok]   %s' % msg)


def bad(msg):
    print('  [FAIL] %s' % msg)
    failures.append(msg)


def note(msg):
    print('         %s' % msg)


def ro(path):
    """อ่านอย่างเดียว. check_db.py เขียน journal mode ลง DB จริง ตัวนี้ต้องไม่ทำ"""
    return sqlite3.connect('file:' + path.replace(os.sep, '/') + '?mode=ro', uri=True)


def git(*args):
    """คืน (ok, stdout). ok=False เมื่อไม่ได้อยู่ใน git repo หรือคำสั่งล้ม"""
    r = subprocess.run(('git',) + args, cwd=BASE_DIR,
                       capture_output=True, text=True)
    return r.returncode == 0, r.stdout


def check_live_db():
    print('ฐานข้อมูลใช้งานจริง')
    if not os.path.exists(DB_PATH):
        bad('ไม่พบ hr_promotion.db')
        return None
    con = ro(DB_PATH)
    try:
        integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
        recs = con.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        atts = con.execute('SELECT COUNT(*) FROM attachments').fetchone()[0]
        stored = {r[0] for r in con.execute('SELECT stored_name FROM attachments')}
    finally:
        con.close()
    if integrity == 'ok':
        ok('integrity_check = ok')
    else:
        bad('integrity_check = %s' % integrity)
    note('records = %d, attachments = %d' % (recs, atts))
    return stored


def check_not_in_git():
    print('ไฟล์ข้อมูลต้องอยู่นอก version control')
    alive, _ = git('rev-parse', '--git-dir')
    if not alive:
        note('ไม่ได้อยู่ใน git repo ข้ามการตรวจส่วนนี้')
        return

    _, tracked = git('ls-files', '--', *DB_NAMES)
    if tracked.strip():
        bad('ยัง track อยู่: %s' % tracked.split())
        note('แก้: git rm --cached hr_promotion.db แล้ว commit')
    else:
        ok('ไม่ถูก track ในสาขาปัจจุบัน')

    # ประวัติ: checkout ย้อนไป commit ที่ยังมีไฟล์นี้จะเขียนข้อมูลจริงทับ
    _, refs = git('for-each-ref', '--format=%(refname:short)',
                  'refs/heads', 'refs/remotes')
    dirty = []
    for ref in refs.split():
        _, tree = git('ls-tree', '-r', '--name-only', ref)
        if any(n in tree.split() for n in DB_NAMES):
            dirty.append(ref)
    if dirty:
        bad('ยังมีไฟล์ข้อมูลในประวัติของ %d ref: %s' % (len(dirty), dirty))
        note('สลับไป ref เหล่านั้นจะเขียนข้อมูลจริงทับ')
    else:
        ok('ไม่มีไฟล์ข้อมูลในประวัติของทุก ref')

    _, ignored = git('check-ignore', '--', DB_PATH)
    if ignored.strip():
        ok('.gitignore ครอบไฟล์ข้อมูลแล้ว')
    else:
        bad('.gitignore ไม่ครอบไฟล์ข้อมูล จึงเผลอ git add ได้')


def check_orphans(stored):
    print('ไฟล์แนบเทียบกับแถวในฐานข้อมูล')
    if stored is None:
        note('อ่านฐานข้อมูลไม่ได้ ข้ามการตรวจส่วนนี้')
        return
    if not os.path.isdir(UPLOAD_DIR):
        bad('ไม่พบโฟลเดอร์ %s' % UPLOAD_DIR)
        return
    on_disk = {n for n in os.listdir(UPLOAD_DIR) if n.lower().endswith('.pdf')}

    orphan_files = sorted(on_disk - stored)
    missing_files = sorted(stored - on_disk)

    if orphan_files:
        size = sum(os.path.getsize(os.path.join(UPLOAD_DIR, n))
                   for n in orphan_files)
        bad('ไฟล์กำพร้า %d ไฟล์ (%.0f MB) ไม่มีแถวใดชี้ถึง'
            % (len(orphan_files), size / 1e6))
        note('ผู้ใช้เปิดดูไม่ได้และลบไม่ได้ ตัวเลขนี้คือระยะที่ DB หลุดจากดิสก์')
    else:
        ok('ไม่มีไฟล์กำพร้า')

    if missing_files:
        bad('มี %d แถวชี้ไปยังไฟล์ที่ไม่มีอยู่' % len(missing_files))
        note('ร้ายกว่าไฟล์กำพร้า ผู้ใช้กดดาวน์โหลดแล้วได้ error')
    else:
        ok('ทุกแถวชี้ไปยังไฟล์ที่มีอยู่จริง')


def check_backups():
    print('สำเนาสำรองรายวัน')
    if not os.path.isdir(BACKUP_DIR):
        bad('ไม่พบโฟลเดอร์ backup/')
        note('จะถูกสร้างตอนเปิดแอปครั้งถัดไป ถ้าไม่เกิดขึ้นแปลว่า _backup_db ไม่ทำงาน')
        return
    names = sorted(n for n in os.listdir(BACKUP_DIR)
                   if n.startswith('hr_promotion-') and n.endswith('.db'))
    if not names:
        bad('โฟลเดอร์ backup/ ว่าง')
        return
    ok('มี %d สำเนา ใหม่สุด %s' % (len(names), names[-1]))

    newest = os.path.join(BACKUP_DIR, names[-1])
    con = ro(newest)
    try:
        integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
        recs = con.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        mode = con.execute('PRAGMA journal_mode').fetchone()[0]
    finally:
        con.close()
    if integrity == 'ok':
        ok('สำเนาใหม่สุดอ่านได้ integrity ok (records = %d)' % recs)
    else:
        bad('สำเนาใหม่สุด integrity = %s' % integrity)
    if mode.lower() == 'wal':
        bad('สำเนาอยู่โหมด WAL จึงต้องพา -shm/-wal ไปด้วย คัดลอกแล้วได้ไม่ครบ')
    else:
        ok('สำเนาเป็นไฟล์เดียวจบ (journal_mode = %s)' % mode)


def main():
    print('ตรวจสุขภาพการติดตั้ง hr_promotion (อ่านอย่างเดียว)')
    print('=' * 62)
    stored = check_live_db()
    print()
    check_not_in_git()
    print()
    check_orphans(stored)
    print()
    check_backups()
    print()
    print('=' * 62)
    if failures:
        print('ไม่ผ่าน %d ข้อ' % len(failures))
        for f in failures:
            print('  - %s' % f)
        return 1
    print('ผ่านทุกข้อ')
    return 0


if __name__ == '__main__':
    sys.exit(main())
