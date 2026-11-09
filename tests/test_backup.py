# -*- coding: utf-8 -*-
"""
เทสต์สำเนาสำรองรายวันของ DB (`_backup_db`)

ทำไมถึงมีฟีเจอร์นี้: `hr_promotion.db` เคยถูก track ไว้ใน git ทุกครั้งที่ `git checkout`
หรือสลับสาขา ข้อมูลจริงถูกเขียนทับด้วยเวอร์ชันที่ commit ไว้ ขณะที่ `static/uploads/*.pdf`
ถูก ignore จึงรอด ผลที่วัดได้คือ PDF 54 ไฟล์บนดิสก์ แต่ `attachments` มี 0 แถว
การเอา DB ออกจาก git ปิดช่องสำหรับ commit ใหม่ แต่ commit เก่าทุกตัวยังมีไฟล์นี้ใน tree
สำเนาสำรองคือชั้นเดียวที่รอดการ checkout ย้อนหลัง
"""

import os
import sqlite3

import pytest

import app as hr


@pytest.fixture
def backup_app(tmp_path, monkeypatch):
    """
    เหมือน `hr_app` แต่ชี้ `BACKUP_DIR` ไป temp dir ด้วย

    ต้องมีแยกเพราะ `hr_app` ไม่ patch `BACKUP_DIR` — ถ้าใช้ `hr_app` เฉย ๆ
    เทสต์จะเขียนสำเนาสำรองลง `backup/` จริงของ repo ทุกครั้งที่รัน
    """
    monkeypatch.setattr(hr, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(hr, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    monkeypatch.setattr(hr, 'BACKUP_DIR', str(tmp_path / 'backup'))
    os.makedirs(hr.UPLOAD_FOLDER, exist_ok=True)
    hr.init_db()
    return hr.app


def _add(n):
    """ใส่ n แถวลง DB ปัจจุบัน"""
    con = sqlite3.connect(hr.DB_PATH)
    for i in range(n):
        con.execute('INSERT INTO records (p1_fname, p1_lname) VALUES (?, ?)',
                    ('ชื่อ%d' % i, 'สกุล%d' % i))
    con.commit()
    con.close()


def _backups():
    if not os.path.isdir(hr.BACKUP_DIR):
        return []
    return sorted(n for n in os.listdir(hr.BACKUP_DIR) if n.endswith('.db'))


def test_backup_writes_one_file_per_day(backup_app):
    """สำเนาแรกของวันถูกเขียน และตั้งชื่อตามวันที่"""
    _add(3)
    dest = hr._backup_db()

    assert dest is not None
    assert len(_backups()) == 1
    assert _backups()[0].startswith('hr_promotion-')


def test_backup_is_a_single_self_contained_file(backup_app):
    """
    สำเนาต้องไม่อยู่ในโหมด WAL

    ต้นฉบับเปิดโหมด WAL และสำเนาสืบทอดโหมดนั้นมา ซึ่งหมายความว่ามันต้องพา
    -shm/-wal ไปด้วยเสมอ คนที่คัดลอกแค่ `.db` ไปเก็บจะได้สำเนาที่ไม่ครบ
    สำเนาสำรองจึงต้องเป็นไฟล์เดียวจบ

    เทียบที่ `journal_mode` ไม่ใช่ที่การมีอยู่ของไฟล์ -shm/-wal เพราะ SQLite
    ลบสองไฟล์นั้นให้เองเมื่อ connection สุดท้ายปิดลงอย่างสะอาด การเช็กว่าไฟล์
    ไม่มีอยู่จึงผ่านทั้งสองทางและไม่ได้ทดสอบอะไรเลย
    """
    _add(2)
    dest = hr._backup_db()

    con = sqlite3.connect('file:%s?mode=ro' % dest.replace('\\', '/'), uri=True)
    try:
        mode = con.execute('PRAGMA journal_mode').fetchone()[0]
    finally:
        con.close()
    assert mode.lower() != 'wal', 'สำเนายังอยู่โหมด WAL จึงต้องพา -shm/-wal ไปด้วย'


def test_backup_copy_holds_the_same_rows(backup_app):
    """
    สำเนาต้องอ่านได้และมีข้อมูลครบ ไม่ใช่ไฟล์เปล่าหรือไฟล์พัง
    ใช้ `sqlite3.Connection.backup()` ไม่ใช่ copy ไฟล์ เพราะ DB เปิดโหมด WAL
    การคัดลอกไฟล์ดิบได้สำเนาที่ไม่สอดคล้องกับ -wal
    """
    _add(3)
    dest = hr._backup_db()

    con = sqlite3.connect('file:%s?mode=ro' % dest.replace('\\', '/'), uri=True)
    try:
        assert con.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 3
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        con.close()


def test_backup_keeps_the_first_snapshot_of_the_day(backup_app):
    """
    เรียกซ้ำในวันเดียวกันต้องไม่เขียนทับ — สำเนาแรกของวันคือสภาพ "ก่อนถูกแตะ"
    ซึ่งเป็นสภาพที่มีค่าที่สุดเวลาต้องกู้ ถ้าเขียนทับทุกครั้งที่เปิดโปรแกรม
    การเปิดโปรแกรมหลังข้อมูลถูกล้างจะทับสำเนาที่ดีด้วยสำเนาที่ว่าง
    """
    _add(3)
    dest = hr._backup_db()
    _add(5)                      # ข้อมูลเปลี่ยนหลังสำรองรอบแรก
    again = hr._backup_db()

    assert again == dest
    assert len(_backups()) == 1
    con = sqlite3.connect('file:%s?mode=ro' % dest.replace('\\', '/'), uri=True)
    try:
        assert con.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 3
    finally:
        con.close()


def test_backup_prunes_to_the_keep_limit(backup_app):
    """เก็บ BACKUP_KEEP ไฟล์ล่าสุด ลบที่เก่ากว่า เรียงตามชื่อ (YYYYMMDD เรียงตรงกับเวลา)"""
    os.makedirs(hr.BACKUP_DIR, exist_ok=True)
    for d in range(1, hr.BACKUP_KEEP + 6):
        open(os.path.join(hr.BACKUP_DIR, 'hr_promotion-2026%02d%02d.db' % (1, d)), 'wb').close()
    before = _backups()

    hr._prune_backups()

    after = _backups()
    assert len(after) == hr.BACKUP_KEEP
    assert after == before[-hr.BACKUP_KEEP:], 'ต้องเก็บไฟล์ใหม่สุด ไม่ใช่เก่าสุด'


def test_backup_does_not_stop_the_app_when_it_fails(backup_app, monkeypatch):
    """
    ความล้มเหลวของการสำรองต้องไม่ทำให้โปรแกรมเปิดไม่ขึ้น
    สำรองไม่ได้ยังดีกว่าผู้ใช้เปิดโปรแกรมทำงานไม่ได้
    """
    _add(1)
    monkeypatch.setattr(hr.os, 'makedirs',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('ดิสก์เต็ม')))

    assert hr._backup_db() is None


def test_backup_skips_when_there_is_no_db_yet(backup_app):
    """ยังไม่มีไฟล์ DB = ไม่มีอะไรให้สำรอง ต้องไม่สร้างไฟล์เปล่าทิ้งไว้"""
    os.remove(hr.DB_PATH)

    assert hr._backup_db() is None
    assert _backups() == []


def test_main_backs_up_before_init_db(backup_app, monkeypatch):
    """
    ลำดับสำคัญ: `init_db()` มี migration ที่แก้ข้อมูล (`_dedupe_attachment_slots`
    ลบไฟล์ที่ถูกแทนที่ด้วย) สำเนาจึงต้องถ่ายก่อน ไม่ใช่หลัง
    """
    order = []
    monkeypatch.setattr(hr, '_setup_logging', lambda: order.append('log'))
    monkeypatch.setattr(hr, '_backup_db', lambda: order.append('backup'))
    monkeypatch.setattr(hr, 'init_db', lambda: order.append('init_db'))
    monkeypatch.setattr(hr, 'HAS_WEBVIEW', False)
    monkeypatch.setattr(hr.app, 'run', lambda **k: order.append('run'))

    hr.main()

    assert order == ['log', 'backup', 'init_db', 'run']


def test_db_file_is_not_tracked_by_git():
    """
    ไฟล์ข้อมูลจริงต้องไม่อยู่ใน version control

    นี่คือต้นตอของบั๊ก "เห็นแต่ข้อมูลทดลอง": ไฟล์ถูก track ไว้ ทุก `git checkout`
    หรือสลับสาขาจึงเขียนข้อมูลจริงทับด้วยเวอร์ชันในคอมมิต (เมษายน 2026, 4 แถว)
    `.gitignore` มี `*.db` อยู่แล้วแต่ไม่มีผลกับไฟล์ที่ track แล้ว
    ถ้าใครเผลอ `git add -f hr_promotion.db` เทสต์ตัวนี้คือตัวที่บอก
    """
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(['git', 'ls-files', '--', 'hr_promotion.db'],
                         cwd=root, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip('ไม่ได้อยู่ใน git repo')
    assert out.stdout.strip() == '', (
        'hr_promotion.db ถูก track อยู่ใน git — git checkout จะเขียนข้อมูลจริงทับ')
