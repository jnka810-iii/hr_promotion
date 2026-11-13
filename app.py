"""
ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง
Flask + SQLite3 + pywebview  →  Desktop App (.exe)

ไฟล์ PDF เก็บไว้ใน static/uploads/  ใช้ UUID เป็นชื่อไฟล์บนดิสก์
DB เก็บเฉพาะ stored_name (UUID filename) และ filename (ชื่อต้นฉบับ)
"""

import os, re, sys, socket, sqlite3, threading, time, base64, json, uuid, shutil, logging, secrets
from contextlib import closing
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, g, abort
from werkzeug.security import generate_password_hash, check_password_hash

# pywebview เป็น optional dependency: มี = เปิดเป็นหน้าต่าง desktop,
# ไม่มี = fallback รันเป็น web server ธรรมดา (ดู main())
try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

app = Flask(__name__)
# ปิดการ escape non-ASCII ใน jsonify — ให้ JSON ตอบกลับเป็นภาษาไทยอ่านออกตรง ๆ
# (ค่า default ของ Flask จะแปลงไทยเป็น \uXXXX ทั้งหมด ซึ่งทำให้อักษรไทย 1 ตัว
# โตจาก 3 ไบต์เป็น 6 ไบต์ และ response ดิบอ่านด้วยตาไม่ได้เลย)
#
# ต้องเซ็ตที่ app.json ไม่ใช่ app.config['JSON_AS_ASCII'] — Flask ย้ายกลไก JSON
# ไปที่ json provider ตั้งแต่ 2.3 แล้ว config key เดิมถูกเพิกเฉยแบบเงียบ ๆ ไม่ error ให้รู้ตัว
# (โปรเจกต์นี้ pin flask==3.1.3 จึงอยู่เลยจุดนั้นมานานแล้ว — ดู requirements.txt)
app.json.ensure_ascii = False
# frontend ส่งเป็น multipart แล้ว payload จึงเท่าขนาดไฟล์บวก overhead ไม่กี่ร้อยไบต์
# ตั้งเพดาน 110 MB = ขีดจำกัดจริงที่ต้องการ 100 MB บวก headroom
# หมายเหตุ: route อัปโหลดยังรับ base64-in-JSON ไว้สำหรับเครื่องมืออื่น (เช่น curl)
# ซึ่งเส้นทางนั้น payload บวม ~33% จึงอัปไฟล์ได้จริงประมาณ 82 MB เท่านั้น
app.config['MAX_CONTENT_LENGTH'] = 110 * 1024 * 1024
# เพดานขนาดไฟล์ PDF ที่ยอมรับจริง ต้องตรงกับที่ UI บอกผู้ใช้ (100 MB ใน templates/index.html)
# MAX_CONTENT_LENGTH ข้างบนกันแค่ขนาด request ทั้งก้อน ซึ่งเป็นเพดานคนละตัวและสูงกว่านี้
# (110 MB เผื่อ overhead ของ multipart) การตรวจฝั่ง client ยกเลิกได้ด้วยการยิง API ตรง
# จึงต้องมีสำเนาฝั่ง server เสมอ
MAX_PDF_SIZE = 100 * 1024 * 1024

# ช่องไฟล์แนบที่ UI มีจริง: เล่ม 1-3 และประกาศ ('ann')
# `book_no` มาจาก client ตรง ๆ ถ้าไม่จำกัดไว้ client จะสร้างช่องชื่ออะไรก็ได้ไม่จำกัดจำนวน
# ซึ่งกลายเป็นไฟล์ที่หน้าจอไม่แสดงและผู้ใช้ลบออกไม่ได้ ได้แต่กินพื้นที่ไปเรื่อย ๆ
BOOK_SLOTS = frozenset({'1', '2', '3', 'ann'})

# รายละเอียด exception ทั้งหมดไปอยู่ที่นี่ แทนที่จะส่งกลับให้ client (ดู fail())
# handler ถูกต่อใน _setup_logging() ซึ่ง main() เรียก — import เปล่าจึงไม่สร้างไฟล์
log = logging.getLogger('hr_promotion')

# BASE_DIR ต้องชี้ถูกทั้ง 2 โหมด:
# - รันปกติ (python app.py)     → โฟลเดอร์ของไฟล์ .py นี้
# - แพ็กเป็น .exe (PyInstaller) → โฟลเดอร์ของตัว .exe (sys.frozen ถูกเซ็ตโดย PyInstaller)
#   เพื่อให้ DB และไฟล์อัปโหลดอยู่ข้างตัวโปรแกรมเสมอ ไม่หลงไปอยู่ใน temp ที่ PyInstaller แตกไฟล์
BASE_DIR      = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
DB_PATH       = os.path.join(BASE_DIR, 'hr_promotion.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
# สำเนาสำรองรายวันของ DB — อยู่ข้างตัวโปรแกรม ไม่ผ่าน git (ดู _backup_db)
BACKUP_DIR    = os.path.join(BASE_DIR, 'backup')
BACKUP_KEEP   = 14

# ────────────────────────────────────────────────
# ตัวตนและสิทธิ์ (ดู docs/adr/0003 และ 0004)
# ────────────────────────────────────────────────

# ชื่อ cookie ที่ถือ token ของเซสชัน — ค่าใน cookie เป็นเลขสุ่มล้วน ไม่มีความหมายในตัวเอง
# ฝั่ง server เอาไปหาเจ้าของในตาราง sessions (ไม่ใช่ cookie เซ็นชื่อของ Flask จึงไม่มี
# SECRET_KEY ให้ต้องเก็บที่ไหน และ logout ลบแถวได้จริง — ดู docs/adr/0003)
SESSION_COOKIE = 'hr_session'
# อายุเซสชัน: ปกติจบวันทำงาน ติ๊ก "จำฉันไว้" ได้ 30 วัน
# เลื่อนอายุออกใหม่ทุก request ที่ผ่าน (sliding) เพราะฟอร์มนี้มี 13 ช่องบังคับ 5 ส่วน
# คนกรอกจริงใช้เวลาเป็นสิบ ๆ นาที การหมดอายุตายตัวจากเวลาล็อกอินจะเด้งคนกลางฟอร์ม
SESSION_HOURS          = 12
SESSION_REMEMBER_DAYS  = 30
# ผิดครบ 5 ครั้ง → ล็อกบัญชีนั้น 5 นาที แล้วปลดเอง ไม่ล็อกถาวรรอแอดมินปลด:
# ใครก็ตามที่เดาชื่อผู้ใช้ของเพื่อนร่วมงานได้ จะล็อกเขาออกจากงานได้ทั้งวันด้วยการพิมพ์ผิด
# 5 ครั้ง นั่นคือเครื่องมือกลั่นแกล้งที่เราสร้างให้เอง ส่วน 5 ครั้งต่อ 5 นาทีก็ทำให้
# การไล่เดารหัสเป็นไปไม่ได้ในทางปฏิบัติอยู่แล้ว
LOGIN_MAX_FAILS        = 5
LOGIN_LOCK_MINUTES     = 5
# ตรวจแค่ความยาว ไม่บังคับ "ต้องมีตัวใหญ่+ตัวเลข+อักขระพิเศษ" และไม่บังคับเปลี่ยนตามรอบ:
# กฎรูปแบบยุ่บยั่บได้ Password1! ที่จดแปะข้างจอ ซึ่งในสำนักงานที่ภัยคือคนในวง LAN
# การแปะกระดาษคือการพังจริง ไม่ใช่การพังทางทฤษฎี
PASSWORD_MIN_LEN       = 8
# บทบาททั้งหมดที่มี — กฎนี้อยู่ใน schema ด้วย (CHECK constraint ของ users.role)
ROLE_ADMIN, ROLE_STAFF = 'admin', 'staff'
ROLES                  = (ROLE_ADMIN, ROLE_STAFF)


# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────

def get_db():
    """
    เปิด SQLite connection ใหม่ 1 ตัวต่อการเรียก 1 ครั้ง (fresh connection per request)

    แนวคิด:
    - ไม่ใช้ connection pool / global connection เพราะ SQLite ผูก connection กับ thread
      ที่สร้างมัน — Flask อาจ serve แต่ละ request คนละ thread การเปิดใหม่ทุกครั้งจึงปลอดภัยสุด
    - journal_mode=WAL (Write-Ahead Logging): ให้อ่านพร้อมกับเขียนได้โดยไม่ block กัน
      เหมาะกับ desktop app ที่ UI ยิงหลาย request พร้อมกัน (เช่น โหลดตาราง + อัปโหลดไฟล์)
    - foreign_keys=ON: SQLite ปิด FK enforcement เป็นค่า default ต้องเปิดเองทุก connection
      เพื่อให้ ON DELETE CASCADE ของตาราง attachments ทำงานจริง
    - row_factory=sqlite3.Row: ทำให้เข้าถึงคอลัมน์ด้วยชื่อได้ (row['id']) และแปลงเป็น dict ง่าย

    Returns:
        sqlite3.Connection: connection ที่พร้อมใช้งาน (ผู้เรียกต้องรับผิดชอบ .close() เอง)
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con

def fail(context: str, message: str, status: int = 500):
    """
    ตอบ error กลับให้ client โดยไม่เปิดเผยรายละเอียดภายในระบบ

    เดิมทุก route ต่อ str(e) เข้าไปในข้อความ ทำให้ path ของไฟล์บนเครื่องผู้ใช้และ
    โครงสร้าง SQL หลุดถึงหน้าจอ และเพราะ `toastError` (templates/index.html) เปิด
    `alert()` ซ้อนเมื่อข้อความยาวเกิน 80 ตัวอักษร ข้อความ exception ที่มักยาวกว่านั้น
    จึงกลายเป็น popup ที่ผู้ใช้ต้องกดปิด พร้อม path ภายในเครื่องเต็ม ๆ

    การตัดสินใจว่า "client เห็นอะไรได้" อยู่ที่ฟังก์ชันนี้ที่เดียว ส่วนรายละเอียดจริง
    ไปอยู่ใน log ฝั่ง server ทั้งหมด (ดู `_setup_logging`) เวลาผู้ใช้แจ้งปัญหาจึงยังมี
    traceback ให้ดู

    ต้องเรียกจากใน except block เท่านั้น — `log.exception()` อ่าน exception ที่กำลัง
    ถูกจับอยู่จาก sys.exc_info() ถ้าเรียกนอก except จะได้แค่ 'NoneType: None'

    Parameters:
        context (str): ชื่อจุดที่ล้ม ใช้ค้นใน log เช่น 'add_record'
        message (str): ข้อความภาษาไทยที่ผู้ใช้จะเห็น ต้องสั้นกว่า 80 ตัวอักษร
        status (int): HTTP status code (default 500)

    Returns:
        tuple: (Response JSON, status) พร้อม return ออกจาก route ได้ตรง ๆ
    """
    log.exception(context)
    return jsonify({'error': message}), status

def now_str():
    """
    คืนวันเวลาปัจจุบันเป็น string รูปแบบไทยที่อ่านง่าย

    Returns:
        str: วันเวลาในรูปแบบ 'dd/mm/YYYY HH:MM' เช่น '11/07/2026 14:30'
             (เก็บลงคอลัมน์ created/updated ตรง ๆ — DB นี้ไม่ใช้ datetime type)
    """
    return datetime.now().strftime('%d/%m/%Y %H:%M')

def dstr():
    """
    คืนวันที่ปัจจุบันแบบกระชับสำหรับใช้ตั้งชื่อไฟล์ export

    Returns:
        str: วันที่รูปแบบ 'YYYYMMDD' เช่น '20260711' (เรียงตามตัวอักษร = เรียงตามเวลา)
    """
    return datetime.now().strftime('%Y%m%d')

# เวลาที่ "เทียบกันได้" — คนละตัวกับ now_str() และห้ามสลับกัน
# now_str() คืน 'dd/mm/YYYY HH:MM' ซึ่งเอาไปเทียบ < > ไม่ได้เลย: '02/01/2026' < '31/12/2025'
# ตามตัวอักษร คอลัมน์ที่ต้องถามว่า "หมดอายุหรือยัง" (sessions.expires_at, users.locked_until)
# จึงเก็บรูปแบบนี้ ที่เรียงตามตัวอักษรแล้วตรงกับเรียงตามเวลา เทียบใน SQL ได้ตรง ๆ
TS_FMT = '%Y-%m-%d %H:%M:%S'

def ts_now() -> str:
    """
    คืนเวลาปัจจุบันในรูปแบบที่เทียบกันได้ (ดู TS_FMT)

    Returns:
        str: เวลาท้องถิ่นรูปแบบ 'YYYY-MM-DD HH:MM:SS'
    """
    return datetime.now().strftime(TS_FMT)

def ts_after(**delta) -> str:
    """
    คืนเวลาในอนาคตในรูปแบบที่เทียบกันได้ ใช้คำนวณวันหมดอายุ

    เวลาท้องถิ่นแบบไม่มี timezone เหมือนที่เหลือทั้งไฟล์ — ไทยไม่มี DST เซสชันจึงไม่
    ยืดหรือหายไปหนึ่งชั่วโมงตามฤดู ถ้าวันหนึ่งระบบนี้ไปอยู่เขตเวลาที่มี DST ต้องแก้ตรงนี้

    Parameters:
        **delta: ส่งต่อให้ timedelta ตรง ๆ เช่น ts_after(hours=12) หรือ ts_after(days=30)

    Returns:
        str: เวลารูปแบบ 'YYYY-MM-DD HH:MM:SS'
    """
    return (datetime.now() + timedelta(**delta)).strftime(TS_FMT)

def row_to_dict(row):
    """
    แปลง sqlite3.Row → dict เพื่อให้ jsonify serialize ได้

    Parameters:
        row (sqlite3.Row): แถวผลลัพธ์จาก query (ต้องเปิด row_factory=sqlite3.Row ไว้แล้ว)

    Returns:
        dict: mapping ชื่อคอลัมน์ → ค่า
    """
    return dict(row)

def csv_cell(value) -> str:
    """
    ทำให้ค่าหนึ่ง cell ปลอดภัยพอจะเปิดใน Excel โดยไม่ถูกตีความเป็นสูตร

    Excel อ่าน cell ที่ขึ้นต้นด้วย = + - @ tab หรือ CR เป็นสูตรและรันทันทีที่เปิดไฟล์
    การครอบด้วย " ตามกฎ CSV ไม่ช่วย เพราะ Excel ถอดเครื่องหมายคำพูดออกก่อนแล้วค่อยประเมิน
    วิธีมาตรฐานคือนำหน้าด้วย single quote ซึ่ง Excel อ่านเป็น "บังคับให้เป็นข้อความ"
    และไม่แสดงตัว quote นั้นใน cell

    ตัวนี้ต้องอยู่ตรงจุด export ไม่ใช่ฝากไว้กับ `.strip()` ขาเข้าของ sanitize_record
    เพราะ export อ่านจาก DB ตรง ๆ และ DB แก้ด้วยมือผ่าน fix_db.py ได้

    ฝั่ง client มีสำเนาตรรกะเดียวกันใน `exportAllCSV` (templates/index.html)
    แก้ที่นี่แล้วต้องแก้ที่นั่นด้วย ไม่อย่างนั้นไฟล์ที่ได้จากสองปุ่มจะไม่เหมือนกัน

    Parameters:
        value: ค่าดิบจาก DB (None ถือเป็นค่าว่าง)

    Returns:
        str: ค่าที่ Excel จะแสดงเป็นข้อความเสมอ
    """
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text

def _safe_remove(path: str):
    """
    ลบไฟล์บนดิสก์แบบ "best-effort" — ไม่ raise exception ไม่ว่ากรณีใด

    เหตุผล: ใช้ตอนลบไฟล์แนบ/rollback ซึ่งถ้าไฟล์หายไปก่อนแล้ว (ถูกลบมือ, ดิสก์มีปัญหา)
    เราไม่อยากให้ transaction ฝั่ง DB ล้มตามไปด้วย — ข้อมูลใน DB สำคัญกว่าไฟล์กำพร้า

    Parameters:
        path (str): path เต็มของไฟล์ที่จะลบ (ยอมรับค่าว่าง/None ได้ จะข้ามไปเฉย ๆ)

    Returns:
        None
    """
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass

def sanitize_attachment_name(name: str, fallback: str) -> str:
    """
    ล้างชื่อไฟล์ที่ผู้ใช้ตั้ง ให้เหลือเฉพาะส่วนที่ปลอดภัยพอจะเก็บลง DB และส่งเป็น download_name

    เหตุผลที่ไม่ใช้ secure_filename() ของ Werkzeug: มันตัดอักขระ non-ASCII ทิ้งทั้งหมด
    ชื่อไทยจึงพังหมด ('ผลงานเล่มที่1.pdf' เหลือ '1.pdf', 'ประกาศ.pdf' เหลือ 'pdf'
    ซึ่งไม่มีนามสกุลให้ Windows รู้จักด้วยซ้ำ) และเอกสารราชการไทยตั้งชื่อไฟล์เป็นไทยแทบทั้งหมด

    ที่สำคัญคือชื่อนี้ไม่เคยถูกใช้ประกอบ path บนดิสก์ — ไฟล์จริงใช้ UUID (stored_name)
    จึงไม่มีความเสี่ยง path traversal ให้ต้องป้องกันตั้งแต่ต้น ตัดทิ้งแค่ตัวคั่น path
    กับอักขระควบคุมก็พอ ส่วน Flask จะ encode ชื่อ non-ASCII ตาม RFC 5987 ให้เองตอนส่ง header

    Parameters:
        name (str):     ชื่อไฟล์ต้นฉบับจาก client (รับค่าว่าง/None ได้)
        fallback (str): ชื่อที่ใช้แทนเมื่อล้างแล้วไม่เหลืออะไร

    Returns:
        str: ชื่อที่ปลอดภัย ยาวไม่เกิน 255 ตัวอักษร (กัน row ใน DB และ header บวมผิดปกติ)
    """
    cleaned = re.sub(r'[\\/\x00-\x1f\x7f]', '', name or '').strip()
    return cleaned[:255] or fallback

def _stored_path(stored_name: str) -> str:
    """
    ประกอบ path เต็มของไฟล์แนบใน UPLOAD_FOLDER จากชื่อไฟล์ที่เก็บใน DB

    Parameters:
        stored_name (str): ชื่อไฟล์บนดิสก์ (UUID hex + '.pdf' — ไม่ใช่ชื่อไฟล์ต้นฉบับ)

    Returns:
        str: absolute path เช่น '<BASE_DIR>/static/uploads/ab12...cd.pdf'
    """
    return os.path.join(UPLOAD_FOLDER, stored_name)


# ────────────────────────────────────────────────
# Database initialisation & migration
# ────────────────────────────────────────────────

def _migrate_blobs_to_files(con):
    """
    Migration ครั้งเดียว: ย้ายไฟล์ PDF ที่เคยเก็บเป็น BLOB ในตาราง attachments
    → เขียนเป็นไฟล์จริงบน UPLOAD_FOLDER แล้วสร้างตารางใหม่ที่ไม่มีคอลัมน์ file_data

    เหตุผลที่ย้ายออกจาก BLOB:
    - BLOB ขนาดใหญ่ทำให้ไฟล์ .db บวมและ query ช้า (SQLite อ่านทั้ง row)
    - เก็บเป็นไฟล์แล้ว serve ผ่าน send_file() ได้โดยตรง ประหยัดหน่วยความจำ

    อัลกอริทึม (ทำครบทุกแถวหรือไม่ทำเลย — ไม่มีสถานะกลาง):
    0. สำเนาไฟล์ .db ไว้เป็น <db>.pre-migration.bak ก่อนแตะอะไรทั้งสิ้น
    1. ตรวจ schema เก่าด้วย PRAGMA table_info แล้วเติมคอลัมน์ stored_name ถ้ายังไม่มี
    2. loop ทุกแถว: dump BLOB → ไฟล์ UUID.pdf แล้วบันทึกชื่อไฟล์กลับลง stored_name
       ถ้าเขียนไฟล์แถวใดพลาด → raise ทันที (การเก็บกวาดอยู่ที่ except ที่เดียว)
       เหตุผลอยู่ที่ขั้นตอน 5 ซึ่งลบคอลัมน์ file_data ทิ้ง ถ้าปล่อยให้แถวที่พลาดผ่านไป
       BLOB ของแถวนั้นจะหายถาวรโดยไม่มีสัญญาณเตือนใด ๆ
    3. DROP ตารางชั่วคราว attachments_v2 ที่อาจค้างจาก migration ที่ล้มเหลวรอบก่อน
    4. สร้าง attachments_v2 (schema ใหม่ ไม่มี file_data) แล้ว copy ข้อมูลข้ามด้วย
       INSERT..SELECT — ใช้ src_map สร้าง expression รายคอลัมน์ เพราะ schema เก่า
       บางเครื่องไม่มี mime_type/uploaded_at ต้อง fallback เป็นค่าคงที่/NULL
    5. สลับตาราง: DROP ตัวเก่า → RENAME v2 เป็น attachments

    เหตุผลที่ต้องสร้างตารางใหม่แทน ALTER: SQLite ไม่รองรับ DROP COLUMN
    (ในเวอร์ชันเก่า) — การ rebuild ตารางคือวิธีมาตรฐานของ SQLite

    **Invariant:** ล้มที่ขั้นไหนก็ตาม ต้องไม่เหลือไฟล์ที่ไม่มีแถวไหนชี้ถึงอยู่บนดิสก์
    ทุกขั้นหลังขั้นที่ 0 อยู่ใน transaction เดียว DB จึงถอนตัวเองกลับได้ แต่ไฟล์บนดิสก์
    ไม่มี transaction ให้ถอน การเก็บกวาดจึงต้องเขียนเอง และต้องครอบทุกทางที่ล้มได้
    ไม่ใช่แค่ทางที่เขียนไฟล์พลาด (วัดก่อนแก้ ด้วยการให้ INSERT..SELECT ล้ม: ล้มหนึ่งรอบ
    เหลือไฟล์กำพร้าเท่าจำนวนแถว รันใหม่อีกรอบได้ไฟล์เป็นสองเท่าของแถว)

    นี่คือความหมายที่ใช้ได้จริงของคำว่า "รันซ้ำได้" ของฟังก์ชันนี้ ไม่ใช่การเติม
    WHERE stored_name = '' เพื่อข้ามแถวที่เคยย้ายแล้ว — เงื่อนไขนั้นกรองอะไรไม่ได้เลย
    เพราะไม่มีสถานะกลางให้กรอง: ทุกรอบที่ล้ม rollback คืนค่า stored_name เป็นค่าว่าง
    ให้ทุกแถวอยู่แล้ว สิ่งที่รอดจาก rollback คือไฟล์ ไม่ใช่ค่าในคอลัมน์

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่ (ฟังก์ชันนี้ commit เอง แต่ไม่ close)

    Raises:
        RuntimeError: สำรองไฟล์ DB ไม่ได้ หรือเขียนไฟล์แนบไม่ครบทุกแถว
        Exception: ข้อผิดพลาดของ DB ในขั้นที่ 3-5 ถูกโยนต่อตามเดิมเพื่อเก็บ traceback
                   ของต้นเหตุไว้ (log ที่ main() ตั้งไว้เป็นที่เดียวที่เหลือให้สืบ)
        ทุกกรณี DB เดิมยังอยู่ครบและไม่มีไฟล์กำพร้าค้าง ผู้ใช้แก้สาเหตุแล้วเปิดแอปใหม่ได้

    Returns:
        None (side effect: โครงสร้างตาราง attachments เปลี่ยน + ไฟล์ถูกเขียนลงดิสก์)
    """
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    # สำรองไฟล์ DB ก่อนแตะข้อมูล — ขั้นตอนท้ายของฟังก์ชันนี้ DROP คอลัมน์ file_data ทิ้งทั้งคอลัมน์
    # ซึ่งเป็นที่เดียวที่เก็บ PDF ไว้ ถ้าพลาดโดยไม่มีสำเนา ผลงานของผู้ใช้หายถาวร กู้ไม่ได้เลย
    backup = DB_PATH + '.pre-migration.bak'
    if not os.path.exists(backup):
        try:
            shutil.copy2(DB_PATH, backup)
        except Exception as e:
            raise RuntimeError(
                f'สำรองไฟล์ DB ไม่สำเร็จ จึงไม่เริ่ม migration เพื่อความปลอดภัยของข้อมูล: {e}'
            )

    att_cols = {row[1] for row in con.execute("PRAGMA table_info(attachments)").fetchall()}

    # ไฟล์ที่เขียนลงดิสก์แล้วในรอบนี้ — ต้องเก็บกวาดถ้าอะไรข้างล่างล้ม ไม่ว่าล้มที่ขั้นไหน
    written = []
    try:
        if 'stored_name' not in att_cols:
            con.execute("ALTER TABLE attachments ADD COLUMN stored_name TEXT NOT NULL DEFAULT ''")
            att_cols.add('stored_name')

        # ย้าย BLOB → file
        rows = con.execute("SELECT id, filename, file_data FROM attachments").fetchall()
        failed = []
        for row in rows:
            stored = uuid.uuid4().hex + '.pdf'
            path   = _stored_path(stored)
            try:
                data = bytes(row['file_data']) if row['file_data'] else b''
                with open(path, 'wb') as f:
                    f.write(data)
            except Exception as e:
                failed.append(f"id={row['id']} ({row['filename']}): {e}")
                continue
            written.append(path)
            con.execute("UPDATE attachments SET stored_name=? WHERE id=?", [stored, row['id']])

        # เขียนไฟล์ไม่ครบ = ห้ามไปต่อเด็ดขาด เพราะขั้นถัดไปลบคอลัมน์ file_data ทิ้ง
        # เวอร์ชันก่อนหน้าตั้ง stored_name='' แล้วเดินหน้าต่อ ทำให้ PDF ของแถวที่เขียนพลาดหายถาวร
        # โดยผู้ใช้ไม่รู้ตัวจนกดดาวน์โหลดแล้วเจอ 'ไฟล์หายจาก storage' ซึ่งสายไปแล้ว
        # ยอมให้แอปเปิดไม่ขึ้นแล้วผู้ใช้ไปแก้สาเหตุ (ดิสก์เต็ม / สิทธิ์ / antivirus) ดีกว่าข้อมูลหาย
        if failed:
            raise RuntimeError(
                f'migration ล้มเหลว เขียนไฟล์แนบไม่สำเร็จ {len(failed)} จาก {len(rows)} รายการ '
                f'ข้อมูลเดิมยังอยู่ครบ สำเนาอยู่ที่ {backup} — ' + ' | '.join(failed)
            )

        # ลบตารางชั่วคราวถ้าค้างจาก migration ที่ล้มเหลวก่อนหน้า
        con.execute("DROP TABLE IF EXISTS attachments_v2")

        # สร้างตารางใหม่โดยไม่มี file_data
        con.execute("""
            CREATE TABLE attachments_v2 (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id   INTEGER NOT NULL,
                book_no     TEXT    NOT NULL,
                filename    TEXT    NOT NULL,
                stored_name TEXT    NOT NULL DEFAULT '',
                mime_type   TEXT    NOT NULL DEFAULT 'application/pdf',
                file_size   INTEGER,
                uploaded_at TEXT,
                FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE
            )
        """)

        # INSERT เฉพาะคอลัมน์ที่มีอยู่จริงใน schema เก่า
        src_map = {
            'id':          'id',
            'record_id':   'record_id',
            'book_no':     'CAST(book_no AS TEXT)',
            'filename':    'filename',
            'stored_name': "COALESCE(stored_name, '')",
            'mime_type':   "'application/pdf'" if 'mime_type'   not in att_cols else 'mime_type',
            'file_size':   'file_size'          if 'file_size'  in att_cols     else 'NULL',
            'uploaded_at': 'NULL'               if 'uploaded_at' not in att_cols else 'uploaded_at',
        }
        dst_cols = ', '.join(src_map.keys())
        src_expr = ', '.join(src_map.values())
        con.execute(f"INSERT INTO attachments_v2 ({dst_cols}) SELECT {src_expr} FROM attachments")
        con.execute("DROP TABLE attachments")
        con.execute("ALTER TABLE attachments_v2 RENAME TO attachments")
        con.commit()
    except Exception:
        # ล้มที่ขั้นไหนก็ตามมาจบที่นี่ที่เดียว: rollback คืนสภาพ DB (SQLite ถอน DDL ได้
        # ALTER TABLE ข้างบนจึงถูกถอนด้วย) แต่ไฟล์ที่เขียนลงดิสก์แล้วไม่มี transaction
        # ให้ถอน ถ้าปล่อยไว้มันจะไม่มีแถวไหนชี้ถึงตลอดไป และการรันใหม่จะเขียนชุดใหม่
        # ด้วย UUID ใหม่ = เปลืองพื้นที่เพิ่มขึ้นเท่าจำนวนแถวต่อการล้มหนึ่งรอบ
        con.rollback()
        for p in written:
            _safe_remove(p)
        raise


def _dedupe_attachment_slots(con) -> list:
    """
    รวบแถว attachments ที่ซ้ำคู่ (record_id, book_no) ให้เหลือแถวใหม่สุดแถวเดียว

    ต้องทำก่อนสร้าง UNIQUE index เสมอ: DB ที่ใช้งานมาก่อนหน้านี้ไม่มีอะไรกันแถวซ้ำ
    ถ้าเจอซ้ำแล้วไปสร้าง index ตรง ๆ `CREATE UNIQUE INDEX` จะโยน IntegrityError
    กลางทาง `init_db()` ซึ่งแปลว่าแอปเปิดไม่ขึ้นเลย ไม่ใช่แค่ฟีเจอร์เดียวพัง

    เลือกแถว id มากสุดของแต่ละช่องเป็นผู้รอด เพราะ id เป็น AUTOINCREMENT
    แถวที่ id สูงกว่าคือไฟล์ที่ผู้ใช้อัปโหลดทีหลัง ซึ่งเป็นตัวที่ UI แสดงอยู่แล้ว
    (`loadAttachments` วนทับ uploadedMeta ตามลำดับที่ได้มา แถวท้ายจึงชนะ)

    คืน path ของไฟล์ที่ไม่มีแถวชี้ถึงแล้ว ให้ผู้เรียกลบ "หลัง commit" ตาม invariant
    ของ file storage — ลบก่อน commit แล้ว rollback จะได้แถวที่ชี้ไฟล์หาย

    Parameters:
        con (sqlite3.Connection): connection ที่อยู่ใน transaction ของ init_db

    Returns:
        list[str]: path เต็มของไฟล์ที่ต้องลบหลัง commit
    """
    doomed = con.execute(
        'SELECT stored_name FROM attachments WHERE id NOT IN '
        '(SELECT MAX(id) FROM attachments GROUP BY record_id, book_no)'
    ).fetchall()
    if not doomed:
        return []
    con.execute('DELETE FROM attachments WHERE id NOT IN '
                '(SELECT MAX(id) FROM attachments GROUP BY record_id, book_no)')
    return [_stored_path(r[0]) for r in doomed if r[0]]

def init_db():
    """
    เตรียมฐานข้อมูลตอนแอปเริ่มทำงาน — สร้างตาราง + migrate schema แบบ additive

    แนวคิดการ migrate (เลือกวิธี "ALTER TABLE เพิ่มคอลัมน์" แทน migration tool):
    - แอปนี้เป็น desktop .exe ที่ผู้ใช้ทั่วไปรัน — ไม่มีขั้นตอน deploy จึงต้อง
      อัปเกรด schema อัตโนมัติทุกครั้งที่เปิดโปรแกรม โดยห้ามทำข้อมูลเดิมหาย
    - CREATE TABLE IF NOT EXISTS: สร้างเฉพาะเมื่อยังไม่มี (เครื่องใหม่)
    - เทียบคอลัมน์จริงจาก PRAGMA table_info กับ dict `new_cols` แล้ว ALTER TABLE
      ADD COLUMN เฉพาะตัวที่ขาด → backward compatible กับ DB จากเวอร์ชันเก่าทุกรุ่น
    - กรณีพิเศษ: ถ้าพบคอลัมน์ file_data ในตาราง attachments แปลว่าเป็น schema
      ยุค BLOB → เรียก _migrate_blobs_to_files() ย้ายเป็น file-based ก่อน

    ตาราง users / sessions ถูกสร้างที่นี่ด้วย DB ที่อัปเกรดมาจากเวอร์ชันก่อนล็อกอินจึงได้
    ตารางเปล่า และ **ไม่มีใครเข้าแอปได้จนรัน manage_users.py add** ซึ่งเป็นพฤติกรรมที่
    ตั้งใจ ไม่ใช่บั๊ก (ถ้า "ไม่มีผู้ใช้" แปลว่า "ไม่ต้องล็อกอิน" ประตูจะเปิดทุกครั้งที่
    ตารางว่าง รวมถึงตอน restore backup ผิดไฟล์ — ดู require_login)

    Parameters: ไม่มี (ใช้ค่า global DB_PATH / UPLOAD_FOLDER)

    Returns:
        None (side effect: ไฟล์ DB และโฟลเดอร์ uploads ถูกสร้าง/อัปเดตให้พร้อมใช้)
    """
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    with closing(get_db()) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                p1_title          TEXT NOT NULL DEFAULT '',
                p1_fname          TEXT NOT NULL DEFAULT '',
                p1_lname          TEXT NOT NULL DEFAULT '',
                p1_edu            TEXT,
                p1_edu_major      TEXT,
                p2_pos_current    TEXT NOT NULL DEFAULT '',
                p2_pos_level      TEXT,
                p2_empid          TEXT NOT NULL DEFAULT '',
                p2_dept           TEXT,
                p2_sec            TEXT,
                p2_org            TEXT,
                p3_pos            TEXT,
                p3_pos_level      TEXT,
                p3_empid          TEXT,
                p3_dept           TEXT,
                p3_sec            TEXT,
                p3_org            TEXT,
                p3_announcement   TEXT,
                p4_work1_title    TEXT,
                p4_work1_year     TEXT,
                p4_work2_title    TEXT,
                p4_work2_year     TEXT,
                p4_work3_title    TEXT,
                p4_work3_year     TEXT,
                p4_concept_title  TEXT,
                p4_work_award     TEXT,
                p5_doc_received   TEXT,
                p5_work_result    TEXT,
                p5_work_score     TEXT,
                p5_work_eval_date TEXT,
                eval51_rows       TEXT NOT NULL DEFAULT '[]',
                eval52_rows       TEXT NOT NULL DEFAULT '[]',
                created           TEXT,
                updated           TEXT
            )
        """)

        # ตรวจสอบ attachments table
        att_exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='attachments'"
        ).fetchone()

        if att_exists:
            att_cols = {row[1] for row in con.execute("PRAGMA table_info(attachments)").fetchall()}
            if 'file_data' in att_cols:
                # schema เก่า → migrate BLOB → files
                _migrate_blobs_to_files(con)
        else:
            # สร้างตารางใหม่ (ไม่มี file_data BLOB)
            con.execute("""
                CREATE TABLE attachments (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id   INTEGER NOT NULL,
                    book_no     TEXT    NOT NULL,
                    filename    TEXT    NOT NULL,
                    stored_name TEXT    NOT NULL DEFAULT '',
                    mime_type   TEXT    NOT NULL DEFAULT 'application/pdf',
                    file_size   INTEGER,
                    uploaded_at TEXT,
                    FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE
                )
            """)

        # เพิ่มคอลัมน์ใน records ที่อาจขาดหาย (backward compat)
        existing = {row[1] for row in con.execute("PRAGMA table_info(records)").fetchall()}
        new_cols = {
            'p3_pos': 'TEXT', 'p3_pos_level': 'TEXT', 'p3_empid': 'TEXT',
            'p3_dept': 'TEXT', 'p3_sec': 'TEXT', 'p3_org': 'TEXT',
            'p3_announcement': 'TEXT',
            'p4_work1_title': 'TEXT', 'p4_work1_year': 'TEXT',
            'p4_work2_title': 'TEXT', 'p4_work2_year': 'TEXT',
            'p4_work3_title': 'TEXT', 'p4_work3_year': 'TEXT',
            'p4_concept_title': 'TEXT', 'p4_work_award': 'TEXT',
            'p5_doc_received': 'TEXT', 'p5_work_result': 'TEXT',
            'p5_work_score': 'TEXT', 'p5_work_eval_date': 'TEXT',
            'eval51_rows': "TEXT NOT NULL DEFAULT '[]'",
            'eval52_rows': "TEXT NOT NULL DEFAULT '[]'",
            'p1_title':       "TEXT NOT NULL DEFAULT ''",
            'p1_fname':       "TEXT NOT NULL DEFAULT ''",
            'p1_lname':       "TEXT NOT NULL DEFAULT ''",
            'p1_edu': 'TEXT', 'p1_edu_major': 'TEXT',
            'p2_pos_current': "TEXT NOT NULL DEFAULT ''",
            'p2_pos_level': 'TEXT',
            'p2_empid':       "TEXT NOT NULL DEFAULT ''",
            'p2_dept': 'TEXT', 'p2_sec': 'TEXT', 'p2_org': 'TEXT',
        }
        for col, ctype in new_cols.items():
            if col not in existing:
                con.execute(f'ALTER TABLE records ADD COLUMN {col} {ctype}')

        # ── ตัวตนและสิทธิ์ ──────────────────────────────
        # อยู่ในไฟล์ DB เดียวกับ records โดยตั้งใจ: _backup_db() สำรองไฟล์เดียว และเทสต์
        # monkeypatch DB_PATH ตัวเดียว ไฟล์ที่สองคือ path ที่สองที่คนลืมสำรองและลืม patch
        # ไม่มี FK ไปหา records เพราะ "ผู้ใช้" (คนที่ล็อกอิน) กับ record (ผู้ถูกประเมิน)
        # เป็นของคนละอย่าง แม้บางทีจะเป็นคนเดียวกัน — ลบ record ต้องไม่กระทบบัญชีใคร
        #
        # กฎของ role อยู่ใน schema ไม่ใช่แค่ในโค้ดที่เขียนแถวนี้: CHECK กัน role พิมพ์ผิด
        # อย่าง 'Admin' หรือ 'manager' ไม่ให้ลงไปนอนใน DB แล้วกลายเป็นบัญชีที่ผ่านการ
        # ตรวจ "ไม่ใช่ admin" ทุกข้อ (SQLite เพิ่ม CHECK ทีหลังด้วย ALTER ไม่ได้)
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                role          TEXT    NOT NULL DEFAULT '{ROLE_STAFF}'
                              CHECK (role IN ('{ROLE_ADMIN}', '{ROLE_STAFF}')),
                is_active     INTEGER NOT NULL DEFAULT 1,
                failed_count  INTEGER NOT NULL DEFAULT 0,
                locked_until  TEXT,
                created       TEXT,
                updated       TEXT
            )
        """)
        # token เป็น PRIMARY KEY เพราะทุก request ค้นด้วย token
        # ON DELETE CASCADE ไม่เคยได้ใช้ในทางปฏิบัติ (ไม่มีคำสั่งลบผู้ใช้ มีแต่ปิดบัญชี)
        # แต่ใส่ไว้ให้ schema พูดความจริงว่าเซสชันไม่มีความหมายถ้าไม่มีเจ้าของ
        con.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                expires_at TEXT    NOT NULL,
                remember   INTEGER NOT NULL DEFAULT 0,
                created    TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # จำเป็นจริง ไม่ใช่ index เผื่อไว้: การเพิกถอน "ทุกเครื่องของคนนี้" เกิดขึ้นที่
        # logout-all, เปลี่ยนรหัสผ่าน และปิดบัญชี ทั้งสามเส้นทางค้นด้วย user_id ซึ่ง
        # PRIMARY KEY ที่ token ช่วยไม่ได้เลย
        con.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)')

        orphaned = _dedupe_attachment_slots(con)
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_att_record_book '
                    'ON attachments(record_id, book_no)')
        # เก็บกวาดเซสชันหมดอายุตอนเปิดโปรแกรม (อีกที่คือตอนล็อกอินสำเร็จ) — ไม่มี thread
        # เฝ้า เพราะแอปนี้เปิดปิดเป็นรอบตามวันทำงาน และตารางโตหนึ่งแถวต่อการล็อกอินหนึ่งครั้ง
        # แถวที่หมดอายุแต่ยังค้างอยู่ใช้ล็อกอินไม่ได้อยู่แล้ว การตรวจเทียบเวลาจริงทุกครั้ง
        # ไม่ได้พึ่งว่าแถวถูกลบไปหรือยัง
        con.execute('DELETE FROM sessions WHERE expires_at < ?', (ts_now(),))

        con.commit()

    # ลบไฟล์หลัง commit เสมอ ตาม invariant เดียวกับ delete_record
    for path in orphaned:
        _safe_remove(path)


# ────────────────────────────────────────────────
# ผู้ใช้ เซสชัน และรหัสผ่าน
#
# ทุกฟังก์ชันในส่วนนี้รับ `con` เข้ามาและ **ไม่ commit เอง** ผู้เรียกเป็นเจ้าของ
# transaction ทั้งก้อน (เหมือนทุก write path ในไฟล์นี้) เพื่อให้ "ตั้งรหัสใหม่ +
# ไล่เซสชันเก่าออก" เกิดพร้อมกันหรือไม่เกิดเลย ไม่ใช่สองก้อนที่ล้มกลางทางได้
#
# manage_users.py import ส่วนนี้ไปใช้ตรง ๆ กฎรหัสผ่านและกฎชื่อผู้ใช้จึงมีที่เดียว
# ไม่ใช่สองสำเนาที่ค่อย ๆ เลื่อนออกจากกันระหว่าง CLI กับหน้าเว็บ
# ────────────────────────────────────────────────

# ชื่อผู้ใช้: ตัวพิมพ์เล็ก ตัวเลข จุด ขีด ขีดล่าง 3-32 ตัว ไม่รับอักษรไทยและไม่รับ
# อักษรนอก ASCII เลย — ชื่อผู้ใช้ไปปรากฏในบรรทัด log ที่ใช้ชี้ว่าใครลบอะไร และมันถูก
# จองไว้ตลอดไป (ไม่มีคำสั่งลบบัญชี) ถ้ารับอักษรนอก ASCII จะมีคู่ที่หน้าตาเหมือนกันสนิท
# แต่เป็นคนละบัญชี เช่น 'admin' ที่ a เป็นอักษรซีริลลิก — คนอ่าน log แยกไม่ออกด้วยตา
USERNAME_RE = re.compile(r'^[a-z0-9._-]{3,32}$')

# ใช้เผาเวลาให้เท่ากันตอนชื่อผู้ใช้ไม่มีอยู่จริง (ดู authenticate)
# สร้างครั้งแรกที่ต้องใช้ ไม่ใช่ตอน import — scrypt จงใจคิดช้า การสร้างตอน import
# จะหน่วงการเปิดโปรแกรมทุกครั้งเพื่อของที่อาจไม่ถูกใช้เลย
_dummy_hash = None


def normalize_username(name) -> str:
    """
    ทำชื่อผู้ใช้ให้เป็นรูปแบบมาตรฐานก่อนเทียบหรือบันทึก

    ตัวพิมพ์ใหญ่-เล็กไม่แยกกัน: 'Somchai' กับ 'somchai' ต้องเป็นบัญชีเดียวกัน ไม่ใช่
    สองบัญชีที่ UNIQUE index มองว่าต่างกันแต่คนมองว่าเหมือนกัน

    Parameters:
        name: ค่าที่รับมาจากฟอร์มหรือ argv (อะไรก็ได้ รวมถึง None)

    Returns:
        str: ชื่อที่ตัดช่องว่างหัวท้ายและแปลงเป็นตัวพิมพ์เล็กแล้ว
    """
    return str(name or '').strip().lower()


def username_problem(username: str) -> str:
    """
    ตรวจชื่อผู้ใช้ตามกฎของ USERNAME_RE

    Parameters:
        username (str): ชื่อที่ผ่าน normalize_username() มาแล้ว

    Returns:
        str: ข้อความบอกปัญหา หรือ '' ถ้าผ่าน
    """
    if not USERNAME_RE.match(username):
        return 'ชื่อผู้ใช้ต้องเป็น a-z 0-9 . _ - ยาว 3-32 ตัว'
    return ''


def password_problem(password) -> str:
    """
    ตรวจรหัสผ่านตามนโยบายเดียวของระบบ — ความยาวเท่านั้น (ดู PASSWORD_MIN_LEN)

    Parameters:
        password: รหัสผ่านดิบที่ผู้ใช้กรอก

    Returns:
        str: ข้อความบอกปัญหา (สั้นกว่า 80 ตัวอักษรเสมอ) หรือ '' ถ้าผ่าน
    """
    if len(str(password or '')) < PASSWORD_MIN_LEN:
        return f'รหัสผ่านต้องยาวอย่างน้อย {PASSWORD_MIN_LEN} ตัวอักษร'
    return ''


def hash_password(password: str) -> str:
    """
    แฮชรหัสผ่านด้วย werkzeug (scrypt เป็นค่าเริ่มต้น salt อยู่ในสตริงผลลัพธ์แล้ว)

    ใช้ของที่ติดมากับ flask อยู่แล้ว ไม่เพิ่ม dependency และไม่เขียน crypto เอง
    ห้ามมี hashlib.sha256(password) ที่ไหนในไฟล์นี้ — SHA ออกแบบมาให้เร็ว ซึ่งตรงข้าม
    กับสิ่งที่ต้องการตอนเก็บรหัสผ่าน

    Parameters:
        password (str): รหัสผ่านดิบ

    Returns:
        str: สตริงแฮชพร้อมเก็บลง users.password_hash
    """
    return generate_password_hash(password)


def find_user(con, username: str):
    """
    หาผู้ใช้จากชื่อ (ชื่อถูก normalize แล้ว)

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        username (str): ชื่อผู้ใช้

    Returns:
        sqlite3.Row | None: แถวใน users หรือ None ถ้าไม่มี
    """
    return con.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()


def count_users(con) -> int:
    """
    นับจำนวนบัญชีทั้งหมด (รวมที่ปิดอยู่)

    ใช้เพื่อ *บอกทาง* ตอนยังไม่มีบัญชีเลย ไม่ใช่เพื่อข้ามการตรวจสิทธิ์
    ห้ามเขียน `if count_users(con) == 0: ผ่านเลย` ที่ไหนทั้งสิ้น — นั่นคือประตูเปิด
    ที่กลับมาทุกครั้งที่ตารางว่าง รวมถึงตอน restore backup ผิดไฟล์

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่

    Returns:
        int: จำนวนแถวในตาราง users
    """
    return con.execute('SELECT COUNT(*) FROM users').fetchone()[0]


def create_user(con, username: str, password: str, role: str = ROLE_STAFF) -> int:
    """
    สร้างบัญชีใหม่ (ผู้เรียก commit เอง)

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        username (str): ชื่อผู้ใช้ที่ normalize และตรวจแล้ว
        password (str): รหัสผ่านดิบที่ตรวจแล้ว
        role (str): ต้องเป็นค่าใน ROLES (schema มี CHECK กันไว้อีกชั้น)

    Returns:
        int: id ของผู้ใช้ที่สร้าง

    Raises:
        ValueError: role ไม่อยู่ใน ROLES
        sqlite3.IntegrityError: ชื่อผู้ใช้ซ้ำ (UNIQUE index)
    """
    if role not in ROLES:
        raise ValueError(f'role ต้องเป็นหนึ่งใน {ROLES}')
    cur = con.execute(
        'INSERT INTO users (username, password_hash, role, created, updated) VALUES (?,?,?,?,?)',
        (username, hash_password(password), role, now_str(), now_str()))
    return cur.lastrowid


def set_password(con, user_id: int, password: str):
    """
    ตั้งรหัสผ่านใหม่และล้างสถานะล็อก (ผู้เรียก commit เอง)

    **ไม่** เพิกถอนเซสชันให้ — ผู้เรียกต้องเรียก revoke_sessions() เอง เพราะแต่ละ
    เส้นทางเก็บเซสชันไว้ไม่เหมือนกัน: ผู้ใช้เปลี่ยนรหัสเองต้องไม่เด้งตัวเองออกจาก
    เครื่องที่กำลังพิมพ์อยู่ ส่วนแอดมินรีเซ็ตให้คนอื่นต้องไล่ออกทุกเครื่อง

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        user_id (int): เจ้าของรหัสผ่าน
        password (str): รหัสผ่านดิบที่ตรวจแล้ว

    Returns:
        None
    """
    con.execute('UPDATE users SET password_hash=?, failed_count=0, locked_until=NULL, '
                'updated=? WHERE id=?', (hash_password(password), now_str(), user_id))


def set_role(con, user_id: int, role: str):
    """
    เปลี่ยนบทบาทของผู้ใช้ (ผู้เรียก commit เอง)

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        user_id (int): ผู้ใช้
        role (str): ต้องเป็นค่าใน ROLES

    Returns:
        None

    Raises:
        ValueError: role ไม่อยู่ใน ROLES
    """
    if role not in ROLES:
        raise ValueError(f'role ต้องเป็นหนึ่งใน {ROLES}')
    con.execute('UPDATE users SET role=?, updated=? WHERE id=?', (role, now_str(), user_id))


def set_active(con, user_id: int, active: bool):
    """
    ปิดหรือเปิดบัญชี (ผู้เรียก commit เอง)

    ปิดบัญชีไล่เซสชันของคนนั้นออกทันทีในคำสั่งเดียวกัน ไม่ใช่รอให้หมดอายุเอง — ถ้า
    คนที่ลาออกแล้วยังทำงานต่อได้อีก 30 วันเพราะเคยติ๊ก "จำฉันไว้" การปิดบัญชีก็ไม่ได้
    ปิดอะไร (นี่คือเหตุผลหลักที่เซสชันอยู่ฝั่ง server — ดู docs/adr/0003)

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        user_id (int): ผู้ใช้
        active (bool): True = เปิด, False = ปิด

    Returns:
        None
    """
    con.execute('UPDATE users SET is_active=?, updated=? WHERE id=?',
                (1 if active else 0, now_str(), user_id))
    if not active:
        revoke_sessions(con, user_id)


def revoke_sessions(con, user_id: int, keep_token: str = None) -> int:
    """
    ลบเซสชันของผู้ใช้หนึ่งคน (ผู้เรียก commit เอง)

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        user_id (int): เจ้าของเซสชัน
        keep_token (str | None): เซสชันที่ไม่ต้องลบ ใช้ตอนคนเปลี่ยนรหัสผ่านของตัวเอง
                                 เพื่อไม่ให้เด้งตัวเองออกจากเครื่องที่กำลังใช้อยู่

    Returns:
        int: จำนวนเซสชันที่ถูกลบ
    """
    if keep_token:
        cur = con.execute('DELETE FROM sessions WHERE user_id=? AND token!=?',
                          (user_id, keep_token))
    else:
        cur = con.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
    return cur.rowcount


def session_window(remember) -> dict:
    """
    คืนความยาวอายุเซสชันตามว่าติ๊ก "จำฉันไว้" ไว้หรือไม่

    เก็บแค่ธง remember ใน DB ไม่ได้เก็บจำนวนวินาที — ถ้าวันหน้าปรับ SESSION_HOURS
    เซสชันที่ยังมีชีวิตอยู่จะได้อายุใหม่ตามไปด้วย ไม่ใช่ค้างอยู่กับค่าที่หมดสมัยแล้ว

    Parameters:
        remember: ค่าจากคอลัมน์ sessions.remember (หรือ bool จากฟอร์ม)

    Returns:
        dict: keyword arguments สำหรับ timedelta / ts_after()
    """
    return {'days': SESSION_REMEMBER_DAYS} if remember else {'hours': SESSION_HOURS}


def start_session(con, user_id: int, remember: bool = False) -> str:
    """
    สร้างเซสชันใหม่หนึ่งแถวแล้วคืน token (ผู้เรียก commit เอง)

    token มาจาก secrets.token_urlsafe ไม่ใช่ uuid4 หรือ random: token_urlsafe ใช้
    ตัวสร้างเลขสุ่มเชิงรหัสลับของ OS ตัวนี้เดาไม่ได้แม้เห็น token อื่นมาแล้วกี่ตัวก็ตาม
    ส่วน uuid4 ของบางแพลตฟอร์มมีส่วนที่คาดเดาได้ และ token นี้คือสิ่งเดียวที่กั้นข้อมูล
    ทั้งหมดไว้ — ไม่มีลายเซ็นหรือความลับอื่นประกอบเลย

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        user_id (int): เจ้าของเซสชัน
        remember (bool): True = อายุ 30 วัน, False = 12 ชั่วโมง

    Returns:
        str: token ที่ต้องเอาไปใส่ cookie
    """
    token = secrets.token_urlsafe(32)
    con.execute('INSERT INTO sessions (token, user_id, expires_at, remember, created) '
                'VALUES (?,?,?,?,?)',
                (token, user_id, ts_after(**session_window(remember)),
                 1 if remember else 0, ts_now()))
    return token


def resolve_session(con, token: str):
    """
    แปลง token เป็นผู้ใช้ และเลื่อนอายุเซสชันเมื่อควร (ผู้เรียก commit เอง)

    คืน None ในทุกกรณีที่ใช้ต่อไม่ได้ — ไม่มี token, ไม่มีแถวนั้น, หมดอายุแล้ว, หรือ
    บัญชีถูกปิด แถวที่หมดอายุถูกลบทิ้งตรงนั้น และบัญชีที่ถูกปิดถูกไล่ออกทุกเครื่อง
    (set_active ทำไปแล้วครั้งหนึ่ง อันนี้คือด่านที่สองเผื่อมีใครแก้ is_active ด้วยมือ
    ผ่าน fix_db.py)

    การเลื่อนอายุ (sliding) ไม่ได้เขียน DB ทุก request: เลื่อนเฉพาะเมื่ออายุที่เหลือ
    น้อยกว่าครึ่งหนึ่งของกรอบ ทำให้หนึ่งเซสชันถูกเขียนมากที่สุดครั้งเดียวต่อครึ่งกรอบ
    (6 ชั่วโมง หรือ 15 วัน) แทนที่จะจับ write lock ของ SQLite ทุกครั้งที่ใครโหลดตาราง

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        token (str): ค่าจาก cookie

    Returns:
        sqlite3.Row | None: แถวที่มี user_id, username, role, token, remember
    """
    if not token:
        return None
    row = con.execute("""
        SELECT s.token, s.expires_at, s.remember,
               u.id AS user_id, u.username, u.role, u.is_active
          FROM sessions s JOIN users u ON u.id = s.user_id
         WHERE s.token = ?
    """, (token,)).fetchone()
    if row is None:
        return None
    if row['expires_at'] <= ts_now():
        con.execute('DELETE FROM sessions WHERE token=?', (token,))
        return None
    if not row['is_active']:
        revoke_sessions(con, row['user_id'])
        return None

    window = session_window(row['remember'])
    left = datetime.strptime(row['expires_at'], TS_FMT) - datetime.now()
    if left < timedelta(**window) / 2:
        con.execute('UPDATE sessions SET expires_at=? WHERE token=?',
                    (ts_after(**window), token))
    return row


def authenticate(con, username: str, password: str):
    """
    ตรวจชื่อผู้ใช้กับรหัสผ่าน และจัดการตัวนับที่ผิด (ผู้เรียก commit เอง)

    คืนเหตุผลแยกกันเพื่อ *เขียน log* เท่านั้น — ข้อความที่ผู้ใช้เห็นต้องเป็นอันเดียวกัน
    ทุกกรณี ไม่งั้นหน้าล็อกอินจะกลายเป็นเครื่องมือไล่ถามว่าในระบบมีใครอยู่

    เวลาที่ใช้ก็ต้องใกล้เคียงกันด้วย ชื่อที่ไม่มีอยู่จริงจึงยังถูกนำไปตรวจกับแฮชหลอก
    หนึ่งครั้ง ถ้าข้ามไปเลย การตอบกลับเร็วผิดสังเกตจะบอกได้เองว่าชื่อนั้นไม่มีในระบบ

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่
        username (str): ชื่อที่ผ่าน normalize_username() แล้ว
        password (str): รหัสผ่านดิบ

    Returns:
        tuple[sqlite3.Row | None, str]: (ผู้ใช้, เหตุผล) เหตุผลเป็นหนึ่งใน
        'ok' / 'no_user' / 'disabled' / 'locked' / 'bad_password'
    """
    global _dummy_hash
    user = find_user(con, username)
    if user is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password(secrets.token_urlsafe(16))
        check_password_hash(_dummy_hash, str(password or ''))
        return None, 'no_user'
    if not user['is_active']:
        return None, 'disabled'
    if user['locked_until'] and user['locked_until'] > ts_now():
        return None, 'locked'

    if not check_password_hash(user['password_hash'], str(password or '')):
        fails = (user['failed_count'] or 0) + 1
        if fails >= LOGIN_MAX_FAILS:
            con.execute('UPDATE users SET failed_count=0, locked_until=? WHERE id=?',
                        (ts_after(minutes=LOGIN_LOCK_MINUTES), user['id']))
        else:
            con.execute('UPDATE users SET failed_count=? WHERE id=?', (fails, user['id']))
        return None, 'bad_password'

    if user['failed_count'] or user['locked_until']:
        con.execute('UPDATE users SET failed_count=0, locked_until=NULL WHERE id=?',
                    (user['id'],))
    con.execute('DELETE FROM sessions WHERE expires_at < ?', (ts_now(),))
    return user, 'ok'


# ────────────────────────────────────────────────
# Record helpers
# ────────────────────────────────────────────────

# Whitelist ชื่อคอลัมน์ที่ยอมรับจาก client — เป็นด่านป้องกัน SQL injection ชั้นแรก:
# ชื่อคอลัมน์ใน INSERT/UPDATE ประกอบจาก set นี้เท่านั้น (ค่า data ใช้ placeholder ? เสมอ)
# client ส่ง key แปลกปลอมมาก็จะถูกทิ้งเงียบ ๆ ใน sanitize_record()
RECORD_COLS = {
    'p1_title', 'p1_fname', 'p1_lname', 'p1_edu', 'p1_edu_major',
    'p2_pos_current', 'p2_pos_level', 'p2_empid', 'p2_dept', 'p2_sec', 'p2_org',
    'p3_pos', 'p3_pos_level', 'p3_empid', 'p3_dept', 'p3_sec', 'p3_org', 'p3_announcement',
    'p4_work1_title', 'p4_work1_year', 'p4_work2_title', 'p4_work2_year',
    'p4_work3_title', 'p4_work3_year', 'p4_concept_title', 'p4_work_award',
    'p5_doc_received', 'p5_work_result', 'p5_work_score', 'p5_work_eval_date',
    'eval51_rows', 'eval52_rows',
}

# ฟิลด์บังคับ — map คอลัมน์ → ป้ายชื่อภาษาไทย ใช้ประกอบข้อความ error ให้ผู้ใช้อ่านรู้เรื่อง
#
# **Invariant:** นี่คือ "ที่เดียว" ที่กฎนี้ถูกเขียนไว้ หน้าเว็บรับรายการนี้ไปทาง
# index() แล้วใช้ทั้งการขึ้นดอกจัน การตั้ง attribute required และการตรวจก่อน submit
# ของเดิมบังคับแค่ 4 ช่องขณะที่ฟอร์มบังคับ 13 ช่อง คือกฎเดียวกันเขียนไว้สองที่
# ด้วยค่าไม่เท่ากัน ซึ่งเพี้ยนเพิ่มทุกครั้งที่มีคนแก้ฝั่งเดียว
#
# ลำดับคีย์เรียงตามลำดับช่องในฟอร์ม เพราะข้อความ error ไล่ชื่อฟิลด์ตามลำดับนี้
# ป้ายต้องไม่ซ้ำกัน: `ตำแหน่งเลขที่` มีสองช่อง ถ้าป้ายเหมือนกันผู้ใช้จะไม่รู้ว่าช่องไหน
REQUIRED_FIELDS = {
    'p1_title':         'คำนำหน้า',
    'p1_fname':         'ชื่อ',
    'p1_lname':         'นามสกุล',
    'p2_pos_current':   'ตำแหน่งปัจจุบัน',
    'p2_pos_level':     'ระดับตำแหน่งปัจจุบัน',
    'p2_empid':         'ตำแหน่งเลขที่ (ส่วนที่ 2)',
    'p2_dept':          'กลุ่มงาน (ส่วนที่ 2)',
    'p2_sec':           'สำนัก/กอง/ศูนย์ (ส่วนที่ 2)',
    'p3_pos':           'ตำแหน่งที่ได้รับเลือก',
    'p3_pos_level':     'ระดับตำแหน่ง (ส่วนที่ 3)',
    'p3_empid':         'ตำแหน่งเลขที่ (ส่วนที่ 3)',
    'p4_work1_title':   'ผลงานที่ผ่านมาเรื่องที่ 1',
    'p4_concept_title': 'เรื่องที่เป็นข้อเสนอแนวคิด',
}

def sanitize_record(d: dict) -> dict:
    """
    กรองและ normalize ข้อมูล record ที่รับจาก client ก่อนบันทึกลง DB

    แนวคิด "whitelist ก่อน trust": วน loop ตาม RECORD_COLS (ไม่ใช่ตาม key ที่ client
    ส่งมา) ดังนั้น key แปลกปลอมหลุดเข้า SQL ไม่ได้ และทุกคอลัมน์มีค่าเสมอ
    (ที่ขาดจะถูกเติมเป็น '' หรือ '[]') — ทำให้ INSERT/UPDATE มีจำนวนคอลัมน์คงที่

    กรณีพิเศษ eval51_rows / eval52_rows (ตารางรอบประเมินจาก UI แบบ dynamic rows):
    เก็บใน DB เป็น JSON string เสมอ โดยรองรับ input 3 แบบ:
    - list   → dumps ตรง ๆ (ensure_ascii=False เพื่อให้ภาษาไทยอ่านออกใน DB)
    - str    → ลอง parse ก่อน ถ้า parse ได้และเป็น list ค่อย dumps กลับ
               (round-trip นี้ทั้ง validate JSON และกัน type แปลก ๆ เช่น dict/number)
    - อื่น ๆ / parse ไม่ผ่าน → fallback เป็น '[]' เพื่อไม่ให้ JSON เสียหลุดลง DB
      แล้วไปพัง JSON.parse ฝั่ง frontend ทีหลัง

    ฟิลด์ทั่วไป: cast เป็น str + strip ช่องว่างหัวท้าย (None → '')

    Parameters:
        d (dict): JSON body ดิบจาก request (อาจมี key เกิน/ขาด/type ผิดได้)

    Returns:
        dict: มี key ครบทุกตัวใน RECORD_COLS ค่าเป็น str ทั้งหมด พร้อมใช้เป็น
              พารามิเตอร์ของ INSERT/UPDATE ได้ทันที
    """
    allowed = {}
    for k in RECORD_COLS:
        val = d.get(k)
        if k in ('eval51_rows', 'eval52_rows'):
            if isinstance(val, list):
                allowed[k] = json.dumps(val, ensure_ascii=False)
            elif isinstance(val, str) and val.strip():
                try:
                    parsed = json.loads(val)
                    allowed[k] = json.dumps(parsed if isinstance(parsed, list) else [],
                                            ensure_ascii=False)
                except (ValueError, TypeError):
                    allowed[k] = '[]'
            else:
                allowed[k] = '[]'
        else:
            allowed[k] = str(val).strip() if val is not None else ''
    return allowed

def validate_required(allowed: dict) -> list:
    """
    ตรวจว่าฟิลด์บังคับตาม REQUIRED_FIELDS ถูกกรอกครบหรือไม่

    Parameters:
        allowed (dict): ข้อมูลที่ผ่าน sanitize_record() แล้ว (ทุกค่าเป็น str)

    Returns:
        list[str]: รายชื่อป้ายภาษาไทยของฟิลด์ที่ยังว่าง เช่น ['ชื่อ', 'นามสกุล']
                   — list ว่าง = ผ่านการตรวจ (ผู้เรียกใช้เช็ค truthiness ได้เลย)
    """
    return [label for col, label in REQUIRED_FIELDS.items()
            if not allowed.get(col, '').strip()]

def required_error(missing: list):
    """
    ประกอบ response 422 จากรายการฟิลด์ที่ยังว่าง

    `error` ต้องสั้นกว่า 80 ตัวอักษรตาม invariant ของ `toastError` ฝั่ง frontend
    (ยาวกว่านั้นมันเปิด `alert()` ทับหน้าจอ) การไล่ชื่อครบทั้ง 13 ช่องยาว 303
    ตัวอักษร จึงบอกแค่จำนวนเมื่อขาดหลายช่อง และบอกชื่อตรง ๆ เมื่อขาดช่องเดียว
    (กรณียาวสุดของสองรูปนี้คือ 45 และ 36 ตัวอักษร)

    รายชื่อเต็มไม่ได้หายไป แต่ย้ายไปอยู่ใน `missing` ให้ client ที่อ่านรายละเอียด
    เองได้ (เช่น curl) ใช้ ส่วนฟอร์มมีด่านของตัวเองที่ไล่ชื่อทุกช่องให้ผู้ใช้อยู่แล้ว

    Parameters:
        missing (list[str]): ป้ายภาษาไทยของฟิลด์ที่ยังว่าง (จาก validate_required)

    Returns:
        tuple: (Response JSON, 422)
    """
    if len(missing) == 1:
        msg = 'กรุณากรอก' + missing[0]
    else:
        msg = 'กรุณากรอกข้อมูลที่จำเป็นให้ครบ ยังขาด %d ช่อง' % len(missing)
    return jsonify({'error': msg, 'missing': missing}), 422


# ────────────────────────────────────────────────
# Routes — เข้าสู่ระบบ
#
# ด่านตรวจเป็น before_request ไม่ใช่ decorator ติดทีละ route โดยตั้งใจ: decorator
# ที่ลืมติดทำให้ route ใหม่ "เปิด" โดยไม่มีอะไรส่งเสียง ส่วน before_request ทำให้
# route ใหม่ "ปิด" เป็นค่าเริ่มต้น คนที่อยากเปิดต้องมาเติมชื่อใน PUBLIC_ENDPOINTS
# ซึ่งเป็นการกระทำที่เห็นได้ใน diff
# ────────────────────────────────────────────────

# endpoint ที่เข้าได้โดยไม่ต้องล็อกอิน — สั้นที่สุดเท่าที่หน้าล็อกอินต้องใช้
# 'static' อยู่ในนี้เพราะหน้าล็อกอินต้องโหลด Bootstrap กับฟอนต์จาก static/vendor
# แต่ static/uploads/ ถูกกันไว้ใน require_login (ดูเหตุผลที่นั่น)
PUBLIC_ENDPOINTS = frozenset({'login', 'static'})

# ข้อความเดียวสำหรับทุกสาเหตุที่ล็อกอินไม่ผ่าน — ไม่มีอยู่จริง รหัสผิด บัญชีถูกปิด
# หรือถูกล็อกชั่วคราว ตอบเหมือนกันหมด ไม่งั้นหน้าล็อกอินจะกลายเป็นเครื่องมือไล่ถาม
# ว่าในระบบมีใครอยู่ (สาเหตุจริงไปอยู่ใน log)
LOGIN_FAILED_MSG = 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง'
# ข้อความที่ /api/* ได้รับเมื่อยังไม่ล็อกอิน — ต้องสั้นกว่า 80 ตัวอักษร ไม่งั้น
# toastError ฝั่ง client จะยกระดับเป็น alert() ที่ต้องกดปิด
UNAUTHORIZED_MSG = 'เซสชันหมดอายุ กรุณาเข้าสู่ระบบอีกครั้ง'
FORBIDDEN_MSG    = 'ต้องเป็นผู้ดูแลระบบจึงจะทำรายการนี้ได้'


def audit(action: str, detail: str = ''):
    """
    เขียนหนึ่งบรรทัดลง log ว่าใครทำอะไร

    ไม่มีตาราง audit ใน DB โดยตั้งใจ: ตารางที่ไม่มีหน้าจอให้ดูคือตารางที่ไม่มีใครอ่าน
    และมันจะมาพร้อมคำถามที่ยังไม่มีคำตอบ (ใครดูได้ ลบได้ไหม สำรองยังไง) ส่วนไฟล์ log
    มี RotatingFileHandler ต่ออยู่แล้วและเป็นที่เดียวที่เก็บรายละเอียดที่ client
    ไม่ได้เห็นอีกต่อไป ข้อจำกัดที่ยอมรับ: หมุนทิ้งเมื่อครบ 1 MB × 3

    Parameters:
        action (str): สิ่งที่เกิดขึ้น เช่น 'delete_record'
        detail (str): รายละเอียดเพิ่ม เช่น 'id=17'

    Returns:
        None
    """
    who = g.user['username'] if getattr(g, 'user', None) else '-'
    log.info('audit user=%s %s %s', who, action, detail)


def _set_session_cookie(resp, token: str, remember):
    """
    ใส่ cookie เซสชันลงใน response

    httponly: JavaScript อ่าน cookie นี้ไม่ได้ ถ้ามีช่อง XSS หลุดเข้ามาในหน้า
    อย่างน้อย token ก็ไม่ถูกอ่านออกไปด้วยสคริปต์

    samesite='Lax': เบราว์เซอร์จะไม่แนบ cookie นี้ไปกับ request ข้ามเว็บที่เป็น POST
    เลย ซึ่ง **เป็นเกราะ CSRF ตัวเดียวที่ระบบนี้มี** — ห้ามแก้เป็น 'None' เด็ดขาด
    ช่องที่โดนได้จริงถ้าแก้คือ POST อัปโหลดไฟล์แบบ multipart (เว็บอื่นสั่งเบราว์เซอร์
    ของคนในสำนักงานยิงมาได้โดยไม่ต้องขออนุญาตก่อน) ส่วน PUT/DELETE และ POST ที่เป็น
    application/json ถูกกฎ preflight ของเบราว์เซอร์กันไว้อีกชั้น

    ไม่ตั้ง secure=True เพราะระบบนี้วิ่งบน HTTP เปล่าในวง LAN (ดู docs/adr/0004) —
    ถ้าตั้ง เบราว์เซอร์จะไม่ส่ง cookie เลยและล็อกอินจะไม่ติดทั้งระบบ วันที่ย้ายไป
    HTTPS ต้องกลับมาตั้งที่นี่

    Parameters:
        resp: Flask response object
        token (str): token จาก start_session()
        remember: ติ๊ก "จำฉันไว้" หรือไม่

    Returns:
        None
    """
    window = session_window(remember)
    resp.set_cookie(SESSION_COOKIE, token,
                    max_age=int(timedelta(**window).total_seconds()),
                    httponly=True, samesite='Lax', secure=False, path='/')


def _safe_next(target) -> str:
    """
    กรองค่า ?next= ให้เป็นเส้นทางภายในเว็บนี้เท่านั้น

    ถ้าไม่กรอง หน้าล็อกอินของเราจะพาคนไปเว็บอื่นได้ตามที่ลิงก์สั่ง (open redirect)
    ซึ่งใช้หลอกคนว่า "ลิงก์นี้เริ่มจากระบบ HR จริง ๆ" ได้ '//' ถูกปฏิเสธเพราะเป็น
    URL ที่ยืมสคีมของหน้าปัจจุบัน (`//example.com` = `http://example.com`) และ
    backslash ถูกปฏิเสธเพราะบางเบราว์เซอร์แปลงเป็น '/' ก่อนตีความ

    Parameters:
        target: ค่าที่รับมาจาก query string หรือฟอร์ม

    Returns:
        str: เส้นทางที่ปลอดภัย ไม่ผ่านการกรองคืน '/'
    """
    t = str(target or '')
    if t.startswith('/') and not t.startswith('//') and '\\' not in t:
        return t
    return '/'


def _render_login(mode: str = 'login', error: str = '', ok: str = '', username: str = ''):
    """
    วาดหน้าประตูหน้าบ้าน (ฟอร์มล็อกอิน และฟอร์มเปลี่ยนรหัสผ่าน)

    ทั้งสองฟอร์มอยู่ใน template เดียวเพราะเป็นหน้าเดียวกันในสายตาผู้ใช้ และทั้งคู่
    ต้องไม่โหลด JavaScript ของแอปเลย

    Parameters:
        mode (str): 'login' หรือ 'change'
        error (str): ข้อความแดง
        ok (str): ข้อความเขียว
        username (str): ค่าที่กรอกไว้ ใส่กลับไปให้ไม่ต้องพิมพ์ใหม่

    Returns:
        str: HTML ของ templates/login.html
    """
    return render_template(
        'login.html', mode=mode, error=error, ok=ok, username=username,
        next=_safe_next(request.values.get('next')),
        lock_hint=f'กรอกผิด {LOGIN_MAX_FAILS} ครั้งติดกัน บัญชีจะถูกล็อก '
                  f'{LOGIN_LOCK_MINUTES} นาที',
        no_users=getattr(g, 'no_users', False),
        current_user=g.user['username'] if getattr(g, 'user', None) else '')


@app.before_request
def require_login():
    """
    ด่านตรวจของทุก request — ไม่มีเครื่องใดได้รับการยกเว้น

    ไม่มีข้อยกเว้นให้ loopback: การเขียน `if request.remote_addr in LOOPBACK_HOSTS:
    ผ่านเลย` ดูเหมือนความสะดวกของเครื่องเจ้าของ แต่วันที่ระบบนี้ไปอยู่หลัง reverse
    proxy remote_addr จะกลายเป็น 127.0.0.1 ของทุกคนพร้อมกัน และประตูจะเปิดหมดโดยไม่มี
    อะไรส่งเสียง ความสะดวกแก้ด้วย "จำฉันไว้ 30 วัน" ไม่ใช่ด้วยการเจาะรู

    ไม่มีข้อยกเว้นให้ตาราง users ที่ว่างเปล่าด้วย: ถ้า "ยังไม่มีบัญชี" แปลว่า
    "ยังไม่ต้องล็อกอิน" ประตูจะเปิดทุกครั้งที่ตารางว่าง รวมถึงตอน restore backup
    ผิดไฟล์ กรณีนั้นจึงกลายเป็นข้อความบอกทางบนหน้าล็อกอินแทน (ดู no_users)

    Returns:
        Response | None: None = ให้ request ไปต่อ, Response = ตีกลับที่ด่านนี้
    """
    g.user = None

    if request.endpoint in PUBLIC_ENDPOINTS:
        # static/uploads/ คือที่เก็บไฟล์แนบ ไม่ใช่ asset ของหน้าเว็บ มันบังเอิญอยู่ใต้
        # static/ จึงถูก static route ของ Flask เสิร์ฟให้ฟรี ๆ ทั้งที่แอปไม่เคยลิงก์
        # ไปที่นั้นเลย (ดาวน์โหลดจริงไปทาง /api/attachments/<id>) ถ้าไม่ปิด ไฟล์ PDF
        # ทุกใบจะอ่านได้โดยไม่ต้องล็อกอินสำหรับคนที่รู้ชื่อไฟล์
        if request.endpoint == 'static':
            name = str((request.view_args or {}).get('filename', ''))
            if name.replace('\\', '/').lower().startswith('uploads/'):
                abort(404)
        return None

    con = get_db()
    try:
        ses = resolve_session(con, request.cookies.get(SESSION_COOKIE))
        if ses is None:
            # ใช้บอกทางตอนเพิ่งอัปเกรดมาจากเวอร์ชันก่อนล็อกอิน (ตารางเปล่า) เท่านั้น
            # ไม่ได้ใช้ตัดสินว่าต้องล็อกอินหรือไม่
            g.no_users = count_users(con) == 0
        con.commit()
    except sqlite3.Error:
        con.rollback()
        raise
    finally:
        con.close()

    if ses is None:
        if request.path.startswith('/api/'):
            # 401 ไม่ใช่ "ไม่มี backend": backend อยู่ครบและข้อมูลอยู่ครบ ฝั่ง client
            # ต้องคงโหมด Flask ไว้แล้วพาไปล็อกอิน ไม่ใช่สลับไปอ่าน LocalStorage ที่
            # ว่างเปล่าแล้วรายงานว่ามี 0 รายการ (ดู docs/adr/0001)
            return jsonify({'error': UNAUTHORIZED_MSG, 'login': url_for('login')}), 401
        return redirect(url_for('login', next=request.full_path.rstrip('?')))

    g.user = ses
    return None


@app.after_request
def renew_session_cookie(resp):
    """
    ต่ออายุ cookie ทุก response ที่ล็อกอินอยู่ และลบ cookie ตอนออกจากระบบ

    ต่ออายุทุกครั้งเพราะฝั่ง server เลื่อนอายุแบบ sliding (ดู resolve_session) ถ้า
    cookie ค้างอยู่กับเวลาหมดอายุตอนล็อกอินครั้งแรก เบราว์เซอร์จะทิ้ง cookie ก่อนที่
    เซสชันฝั่ง server จะหมดอายุ แล้วคนที่ใช้งานอยู่ตลอดจะถูกเด้งออกกลางวัน

    ทิศทางที่ผิดคือ cookie ตายก่อน session ส่วน cookie ที่อยู่นานกว่า session ไม่เป็น
    ปัญหา — ฝั่ง server เป็นผู้ตัดสินเสมอ token ที่หมดอายุแล้วก็ได้ 401 แล้วไปล็อกอิน

    Parameters:
        resp: Flask response object

    Returns:
        Response: resp ตัวเดิม
    """
    if getattr(g, 'drop_cookie', False):
        resp.delete_cookie(SESSION_COOKIE, path='/')
    elif getattr(g, 'user', None) and request.endpoint != 'static':
        _set_session_cookie(resp, g.user['token'], g.user['remember'])
    return resp


def require_admin(view):
    """
    decorator สำหรับ route ที่ staff ทำไม่ได้

    ใช้กับ DELETE /api/records (ลบทั้งฐาน) เป็นหลัก — route ที่น่ากลัวที่สุดในแอปนี้
    ถ้าทุกคนที่ล็อกอินได้ทำทุกอย่างได้ เจ้าหน้าที่ที่กดพลาดหนึ่งครั้งก็ลบงานทั้งปี

    Parameters:
        view: view function

    Returns:
        function: view ที่ตรวจบทบาทก่อน
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, 'user', None) or g.user['role'] != ROLE_ADMIN:
            audit('denied', f'{request.method} {request.path}')
            return jsonify({'error': FORBIDDEN_MSG}), 403
        return view(*args, **kwargs)
    return wrapped


@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    GET /login — ฟอร์มล็อกอิน, POST /login — ตรวจแล้วเปิดเซสชัน

    เป็น form POST ธรรมดา ไม่ผ่าน apiFetch และไม่พึ่ง JavaScript เลย: ประตูหน้าบ้าน
    ไม่ควรพึ่งชิ้นส่วนที่ซับซ้อนที่สุดในแอป ถ้าสคริปต์ใน index.html พังทั้งไฟล์
    คนก็ยังต้องล็อกอินเข้ามาเห็นหน้าพัง ๆ นั้นได้

    Returns:
        Response: HTML ฟอร์ม, 401 พร้อมฟอร์มเดิมเมื่อไม่ผ่าน, หรือ redirect เมื่อผ่าน
    """
    if request.method == 'GET':
        with closing(get_db()) as con:
            g.no_users = count_users(con) == 0
        return _render_login()

    username = normalize_username(request.form.get('username'))
    password = request.form.get('password') or ''
    remember = bool(request.form.get('remember'))

    con = get_db()
    try:
        user, why = authenticate(con, username, password)
        if user is None:
            con.commit()  # ตัวนับครั้งที่ผิดและเวลาล็อกต้องถูกบันทึกไว้จริง
            log.info('login failed user=%s reason=%s from=%s',
                     username, why, request.remote_addr)
            return _render_login(error=LOGIN_FAILED_MSG, username=username), 401
        token = start_session(con, user['id'], remember)
        con.commit()
    except sqlite3.Error:
        con.rollback()
        log.exception('login')
        return _render_login(error='ระบบขัดข้อง กรุณาลองอีกครั้ง', username=username), 500
    finally:
        con.close()

    log.info('audit user=%s login role=%s remember=%s from=%s',
             user['username'], user['role'], int(remember), request.remote_addr)
    resp = redirect(_safe_next(request.form.get('next')))
    _set_session_cookie(resp, token, remember)
    return resp


@app.route('/logout', methods=['POST'])
def logout():
    """
    POST /logout — ลบเซสชันของเครื่องนี้ออกจาก DB จริง

    เป็น POST ไม่ใช่ GET เพราะมันเปลี่ยนสถานะของระบบ (GET /logout ทำให้รูปภาพหรือ
    ลิงก์ที่ไหนก็ได้เตะคนออกจากระบบได้)

    Returns:
        Response: redirect ไปหน้าล็อกอิน
    """
    token = request.cookies.get(SESSION_COOKIE)
    con = get_db()
    try:
        con.execute('DELETE FROM sessions WHERE token=?', (token,))
        con.commit()
    except sqlite3.Error:
        con.rollback()
        log.exception('logout')
    finally:
        con.close()
    audit('logout')
    g.drop_cookie = True
    return redirect(url_for('login'))


@app.route('/change-password', methods=['GET', 'POST'])
def change_password():
    """
    GET/POST /change-password — เปลี่ยนรหัสผ่านของตัวเอง

    ต้องกรอกรหัสเดิมด้วย: ถ้าไม่ต้อง ใครที่นั่งหน้าเครื่องที่เปิดค้างไว้โดยไม่ล็อก
    หน้าจอจะยึดบัญชีนั้นได้ทันที ซึ่งในสำนักงานแบบนี้เกิดได้จริงกว่าการเจาะผ่านเน็ต

    เปลี่ยนสำเร็จแล้วไล่ทุกเครื่องอื่นของคนนี้ออก เหลือเฉพาะเครื่องที่กำลังพิมพ์อยู่ —
    เหตุผลหลักที่คนเปลี่ยนรหัสคือ "รหัสรู้ถึงคนอื่นแล้ว" ถ้าเซสชันเก่ายังอยู่ต่ออีก
    30 วัน การเปลี่ยนรหัสก็ไม่ได้แก้อะไรเลย

    Returns:
        Response: HTML ฟอร์ม, ฟอร์มพร้อม error, หรือ redirect เมื่อสำเร็จ
    """
    if request.method == 'GET':
        return _render_login(mode='change',
                            ok='เปลี่ยนรหัสผ่านแล้ว' if request.args.get('ok') else '')

    current = request.form.get('current') or ''
    new1    = request.form.get('new1') or ''
    new2    = request.form.get('new2') or ''

    if new1 != new2:
        return _render_login(mode='change', error='รหัสผ่านใหม่สองช่องไม่ตรงกัน'), 400
    problem = password_problem(new1)
    if problem:
        return _render_login(mode='change', error=problem), 400

    con = get_db()
    try:
        user, why = authenticate(con, g.user['username'], current)
        if user is None:
            con.commit()  # รหัสเดิมผิดก็นับเป็นการกรอกผิดเหมือนกัน
            log.info('change_password failed user=%s reason=%s',
                     g.user['username'], why)
            return _render_login(mode='change', error='รหัสผ่านเดิมไม่ถูกต้อง'), 401
        set_password(con, user['id'], new1)
        gone = revoke_sessions(con, user['id'], keep_token=g.user['token'])
        con.commit()
    except sqlite3.Error:
        con.rollback()
        log.exception('change_password')
        return _render_login(mode='change', error='ระบบขัดข้อง กรุณาลองอีกครั้ง'), 500
    finally:
        con.close()

    audit('change_password', f'revoked={gone}')
    return redirect(url_for('change_password', ok=1))


# ────────────────────────────────────────────────
# Routes — Records
# ────────────────────────────────────────────────

@app.route('/')
def index():
    """
    หน้าแรก — serve Single Page Application ทั้งหมดจาก template เดียว
    (frontend จัดการ tab/ฟอร์ม/ตารางเองด้วย JavaScript แล้วคุยกลับมาทาง /api/*)

    ส่ง REQUIRED_FIELDS ไปกับหน้าเว็บด้วย เพื่อให้ดอกจัน การตรวจของ browser และ
    การตรวจก่อน submit อ่านกฎชุดเดียวกับที่ validate_required() ใช้จริง
    (ฝังเป็น JSON ในหน้า ไม่ใช่ endpoint แยก เพราะหน้าเว็บต้องใช้ตั้งแต่ก่อน
    probe รู้ว่าคุย API ได้หรือไม่ และ endpoint ที่ล้มจะทำให้ฟอร์มไม่มีกฎเลย)

    ส่งเป็น list ของคู่ ไม่ใช่ dict เพราะ `tojson` ของ Jinja เรียงคีย์ใหม่ตาม
    `app.json.sort_keys` ซึ่งค่าเริ่มต้นเป็น True ลำดับช่องในข้อความ error ฝั่ง
    client จะกลายเป็นเรียงตามตัวอักษรแทนลำดับช่องในฟอร์ม ไม่ตรงกับฝั่ง server
    list เก็บลำดับไว้ในตัวโครงสร้างเอง ไม่ขึ้นกับ flag ของ serializer

    ส่งชื่อผู้ใช้และบทบาทไปด้วย เพื่อให้หัวหน้าจอแสดงว่ากำลังทำงานในนามใคร และซ่อน
    ปุ่มที่บทบาทนั้นกดไม่ได้ การซ่อนปุ่มเป็นเรื่องความสะอาดของหน้าจอเท่านั้น
    ไม่ใช่การบังคับสิทธิ์ — ด่านจริงอยู่ที่ require_admin ฝั่ง server เสมอ

    Returns:
        str: HTML ของ templates/index.html
    """
    return render_template('index.html',
                           required_fields=list(REQUIRED_FIELDS.items()),
                           current_user=g.user['username'],
                           current_role=g.user['role'],
                           is_admin=g.user['role'] == ROLE_ADMIN)

@app.route('/api/records', methods=['GET'])
def get_all():
    """
    GET /api/records — ดึงรายการ record ทั้งหมด

    เรียงด้วย id DESC (รายการใหม่สุดขึ้นก่อน) — ปริมาณข้อมูลระดับหน่วยงาน
    ไม่มากพอที่จะต้องทำ pagination จึงส่งทั้งหมดในครั้งเดียวเพื่อให้
    frontend ค้นหา/กรองฝั่ง client ได้ทันทีโดยไม่ต้อง round-trip

    Returns:
        Response: JSON array ของทุก record (eval51_rows/eval52_rows ยังเป็น
                  JSON string ตามที่เก็บใน DB — frontend parse เอง)
    """
    with closing(get_db()) as con:
        rows = con.execute('SELECT * FROM records ORDER BY id DESC').fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/records/<int:rid>', methods=['GET'])
def get_one(rid):
    """
    GET /api/records/<id> — ดึง record เดี่ยวตาม id

    Parameters:
        rid (int): id ของ record (Flask converter <int:> คัดกรอง type ให้แล้ว)

    Returns:
        Response: JSON object ของ record นั้น (200)
                  หรือ {'error': 'not found'} (404) ถ้าไม่มี id นี้
    """
    with closing(get_db()) as con:
        row = con.execute('SELECT * FROM records WHERE id=?', [rid]).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(row_to_dict(row))

@app.route('/api/records', methods=['POST'])
def add_record():
    """
    POST /api/records — สร้าง record ใหม่จาก JSON body

    ขั้นตอน: ตรวจ body เป็น dict → sanitize (whitelist) → ตรวจฟิลด์บังคับ
    → INSERT แบบ dynamic

    จุดสำคัญด้านความปลอดภัยของ SQL แบบ dynamic:
    ชื่อคอลัมน์ใน f-string มาจาก sanitize_record() ซึ่งวนตาม RECORD_COLS
    (ค่าคงที่ในโค้ด) เท่านั้น ส่วน "ค่า" ของผู้ใช้ผ่าน placeholder ? ทั้งหมด
    → ไม่มีทางเกิด SQL injection แม้ query จะประกอบ string

    Returns:
        Response (JSON):
        - 201 {'id': <rowid ใหม่>, 'ok': True} เมื่อสำเร็จ
        - 400 body ไม่ใช่ JSON object
        - 422 ฟิลด์บังคับไม่ครบ หรือชน integrity constraint
        - 500 DB error / ข้อผิดพลาดไม่คาดคิด (rollback ให้แล้ว)
    """
    d = request.get_json(force=True, silent=True)
    if d is None or not isinstance(d, dict):
        return jsonify({'error': 'request body ต้องเป็น JSON object ที่ถูกต้อง'}), 400
    allowed = sanitize_record(d)
    missing = validate_required(allowed)
    if missing:
        return required_error(missing)
    allowed['created'] = now_str()
    allowed['updated'] = ''
    cols = ', '.join(allowed.keys())
    plh  = ', '.join(['?'] * len(allowed))
    con  = get_db()
    try:
        cur = con.execute(f'INSERT INTO records ({cols}) VALUES ({plh})', list(allowed.values()))
        con.commit()
        audit('add_record', f'id={cur.lastrowid}')
        return jsonify({'id': cur.lastrowid, 'ok': True}), 201
    except sqlite3.IntegrityError:
        con.rollback()
        return fail('add_record integrity', 'บันทึกไม่สำเร็จ เนื่องจากข้อมูลขัดกับเงื่อนไขของระบบ', 422)
    except Exception:
        con.rollback()
        return fail('add_record', 'บันทึกข้อมูลไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')
    finally:
        con.close()

@app.route('/api/records/<int:rid>', methods=['PUT'])
def update_record(rid):
    """
    PUT /api/records/<id> — อัปเดต record เดิมทั้งแถว (full replace ตาม whitelist)

    เป็นการอัปเดตแบบ "ทับทุกคอลัมน์": frontend ส่งฟอร์มทั้งชุดมาเสมอ ฟิลด์ที่
    ผู้ใช้ลบค่าออกจึงถูกเซ็ตเป็น '' จริง ๆ ใน DB (ไม่ใช่ partial PATCH)
    ตรวจว่ามี id นี้อยู่ก่อนด้วย SELECT เพื่อแยก 404 (ไม่พบ) ออกจาก 422
    (อัปเดตไม่สำเร็จ) ให้ frontend แจ้งผู้ใช้ได้ตรงสาเหตุ

    Parameters:
        rid (int): id ของ record ที่จะแก้ไข

    Returns:
        Response (JSON):
        - 200 {'ok': True} เมื่อสำเร็จ (พร้อม stamp เวลาใน updated)
        - 400 body ไม่ใช่ JSON object / 404 ไม่พบ id
        - 422 ฟิลด์บังคับไม่ครบ หรือ rowcount=0
        - 500 DB error (rollback ให้แล้ว)
    """
    d = request.get_json(force=True, silent=True)
    if d is None or not isinstance(d, dict):
        return jsonify({'error': 'request body ต้องเป็น JSON object ที่ถูกต้อง'}), 400
    allowed = sanitize_record(d)
    missing = validate_required(allowed)
    if missing:
        return required_error(missing)
    allowed['updated'] = now_str()
    sets = ', '.join([f'{k}=?' for k in allowed.keys()])
    con  = get_db()
    try:
        exists = con.execute('SELECT id FROM records WHERE id=?', [rid]).fetchone()
        if not exists:
            return jsonify({'error': 'ไม่พบข้อมูล id=' + str(rid)}), 404
        cur = con.execute(f'UPDATE records SET {sets} WHERE id=?', [*allowed.values(), rid])
        con.commit()
        if cur.rowcount == 0:
            return jsonify({'error': 'ไม่สามารถอัปเดตได้ (rowcount=0)'}), 422
        audit('update_record', f'id={rid}')
        return jsonify({'ok': True})
    except Exception:
        con.rollback()
        return fail('update_record', 'แก้ไขข้อมูลไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')
    finally:
        con.close()

@app.route('/api/records/<int:rid>', methods=['DELETE'])
def delete_record(rid):
    """
    DELETE /api/records/<id> — ลบ record หนึ่งรายการพร้อมไฟล์แนบทั้งหมด

    ลำดับการลบสำคัญ 2 ชั้น:
    1. ต้อง "อ่าน" รายชื่อไฟล์ (stored_name) ก่อนลบ row เพราะเมื่อ row ถูกลบ
       ON DELETE CASCADE จะกวาดแถว attachments ทิ้งทันที ไม่เหลือทางตามหาชื่อไฟล์อีก
    2. แต่ต้อง "ลบไฟล์จริง" หลัง commit เท่านั้น ถ้าลบก่อนแล้ว DB ล้มกลางทาง
       rollback จะคืน row ที่ชี้ไปยังไฟล์ซึ่งถูกลบไปแล้ว ผู้ใช้เห็นว่ามีไฟล์แนบ
       แต่กดดาวน์โหลดได้ 404 ซึ่งแย่กว่าไฟล์กำพร้าที่เสียแค่พื้นที่ดิสก์
    (ถ้าลบไฟล์พลาด _safe_remove จะกลืน error → อย่างแย่คือไฟล์กำพร้า
    แต่ข้อมูลใน DB ยัง consistent เสมอ)

    และจงใจตอบ 200 แม้ไม่พบ id เหมือน delete_attachment เพราะ DELETE เป็น
    idempotent: สิ่งที่ client ขอคือ "ให้ record นี้ไม่มีอยู่" ซึ่งเป็นจริงอยู่แล้ว
    ต่างจาก update_record ที่ตอบ 404 เพราะ PUT ขอให้ record ถือค่าใหม่
    ซึ่งทำให้เป็นจริงไม่ได้เลยถ้าไม่มีแถวอยู่ ความต่างนี้จึงไม่ใช่การลืมเช็ก
    (มี test กันไว้ทั้งสองฝั่ง — `-k item20`)

    Parameters:
        rid (int): id ของ record ที่จะลบ

    Returns:
        Response (JSON): 200 {'ok': True} หรือ 500 พร้อมข้อความ error (rollback แล้ว)
    """
    con = get_db()
    try:
        # อ่านชื่อไฟล์เก็บไว้ก่อน เพราะ CASCADE จะกวาดแถว attachments ทิ้งพร้อม record
        atts = con.execute(
            'SELECT stored_name FROM attachments WHERE record_id=?', [rid]
        ).fetchall()
        paths = [_stored_path(a['stored_name']) for a in atts if a['stored_name']]
        con.execute('DELETE FROM records WHERE id=?', [rid])
        con.commit()
        audit('delete_record', f'id={rid} files={len(paths)}')
        # ลบไฟล์หลัง commit เท่านั้น ถ้าลบก่อนแล้ว DB ล้ม rollback จะคืน row ที่ชี้ไฟล์หาย
        for p in paths:
            _safe_remove(p)
        return jsonify({'ok': True})
    except Exception:
        con.rollback()
        return fail('delete_record', 'ลบข้อมูลไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')
    finally:
        con.close()

@app.route('/api/records', methods=['DELETE'])
@require_admin
def delete_all():
    """
    DELETE /api/records — ล้างข้อมูลทั้งระบบ (ทุก record + ไฟล์แนบทุกไฟล์)

    ใช้หลักการเดียวกับ delete_record: เก็บรายชื่อไฟล์ไว้ก่อน → DELETE FROM records
    ให้ CASCADE ล้างตาราง attachments ตาม → commit → แล้วจึงลบไฟล์บนดิสก์
    (frontend มี confirm dialog ป้องกันการกดพลาดอยู่แล้วชั้นหนึ่ง)

    เฉพาะ admin เท่านั้น (ดู require_admin) — นี่คือ route ที่น่ากลัวที่สุดในแอป
    และ **ต้องไม่แตะตาราง users หรือ sessions**: DELETE FROM records ลบเฉพาะข้อมูล
    HR ถ้าวันหนึ่งใครเผลอเขียนให้มันล้างบัญชีไปด้วย คนที่กดจะล็อกตัวเองออกจากระบบ
    ถาวรและไม่มีทางเข้ามาแก้อะไรได้อีก (มีเทสต์กันไว้)

    Returns:
        Response (JSON): 200 {'ok': True} หรือ 500 พร้อมข้อความ error (rollback แล้ว)
    """
    con = get_db()
    try:
        atts = con.execute('SELECT stored_name FROM attachments').fetchall()
        paths = [_stored_path(a['stored_name']) for a in atts if a['stored_name']]
        gone = con.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        con.execute('DELETE FROM records')
        con.commit()
        audit('delete_all', f'records={gone} files={len(paths)}')
        # ลบไฟล์หลัง commit ด้วยเหตุผลเดียวกับ delete_record
        for p in paths:
            _safe_remove(p)
        return jsonify({'ok': True})
    except Exception:
        con.rollback()
        return fail('delete_all', 'ลบข้อมูลทั้งหมดไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')
    finally:
        con.close()


# ────────────────────────────────────────────────
# Routes — Attachments
# ────────────────────────────────────────────────

@app.route('/api/records/<int:rid>/attachments', methods=['GET'])
def list_attachments(rid):
    """
    GET /api/records/<id>/attachments — รายการไฟล์แนบ (เฉพาะ metadata)

    จงใจ SELECT รายคอลัมน์โดย "ไม่รวม stored_name" — ชื่อไฟล์บนดิสก์เป็น
    รายละเอียดภายใน server ไม่จำเป็นและไม่ควรเปิดเผยให้ client
    (client อ้างไฟล์ผ่าน att id → /api/attachments/<id> แทน)

    Parameters:
        rid (int): id ของ record เจ้าของไฟล์แนบ

    Returns:
        Response: JSON array ของ metadata ไฟล์แนบ เรียงตาม book_no
                  (record ที่ไม่มีไฟล์จะได้ [] — ไม่ถือเป็น error)
    """
    with closing(get_db()) as con:
        rows = con.execute(
            'SELECT id, record_id, book_no, filename, mime_type, file_size, uploaded_at '
            'FROM attachments WHERE record_id=? ORDER BY book_no', [rid]
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/records/<int:rid>/attachments', methods=['POST'])
def upload_attachment(rid):
    """
    POST /api/records/<id>/attachments — อัปโหลด PDF หนึ่งเล่ม (upsert ต่อช่อง book_no)

    รองรับ input 2 รูปแบบ (ตรวจจาก Content-Type):
    1. multipart/form-data field 'file' + 'book_no' — เส้นทางหลักที่ frontend ใช้
       Werkzeug สตรีมไฟล์ลง temp ให้ ไม่ต้องโหลดทั้งก้อนเข้า memory ก่อน
    2. JSON body: {book_no, filename, data:<base64 หรือ data-URL>} — คงไว้สำหรับ
       เครื่องมืออื่น เช่น curl รองรับทั้ง base64 เปล่าและ data-URL จึงตัด prefix
       'data:...;base64,' ด้วย split(',')[-1] ก่อน decode
       เส้นทางนี้ payload บวม ~33% และกิน memory ทั้งฝั่ง client และ server
       อัปได้จริงประมาณ 82 MB ตาม MAX_CONTENT_LENGTH 110 MB

    อัลกอริทึม (ออกแบบให้ DB เป็น source of truth เสมอ):
    1. decode/อ่าน bytes → ตรวจว่าไม่ว่าง ไม่เกิน MAX_PDF_SIZE และมี header '%PDF-'
       ใน 1024 ไบต์แรก → ตรวจว่า record มีจริง ทั้งหมดนี้ทำ "ก่อน" แตะดิสก์
       เพื่อไม่ให้มีไฟล์ขยะเขียนลงไปแล้วต้องตามลบ
    2. เขียนไฟล์ใหม่ลงดิสก์ด้วยชื่อ UUID ก่อนแตะ DB
       (ใช้ UUID เพื่อกันชื่อชนกันและตัดปัญหาชื่อไฟล์ภาษาไทย/อักขระพิเศษบนดิสก์)
    3. จำ path ของไฟล์เก่าในช่อง (record_id, book_no) เดียวกันไว้ แต่ยังไม่ลบ
    4. DELETE row เก่า (อ่านชื่อไฟล์เก่าจาก RETURNING ของ DELETE เอง) + INSERT row ใหม่
       = พฤติกรรม "replace" ส่วนกฎ 1 ช่อง 1 ไฟล์ บังคับที่ schema แล้วด้วย
       UNIQUE index idx_att_record_book ไม่ได้อาศัยลำดับคำสั่งตรงนี้อย่างเดียว
    5. commit ผ่านแล้วจึงลบไฟล์เก่า — ลำดับนี้สำคัญ ถ้าลบก่อน commit แล้วเกิด
       exception ระหว่างทาง rollback จะคืน row เก่าที่ชี้ไปยังไฟล์ที่หายไปแล้ว
    6. ถ้า DB ล้มเหลว: rollback แล้วลบไฟล์ที่เพิ่งเขียนทิ้ง (ขั้นตอน compensate)
       เพื่อไม่ให้เกิดไฟล์กำพร้าที่ไม่มี row ชี้ถึง ส่วนไฟล์เก่ายังอยู่ครบคู่กับ row เดิม

    หมายเหตุ: filename ผ่าน sanitize_attachment_name() ซึ่งเก็บอักษรไทยไว้ครบ
    (ใช้เป็น download_name ตอนดาวน์โหลด) ชื่อนี้ไม่เคยถูกใช้ประกอบ path บนดิสก์

    Parameters:
        rid (int): id ของ record เจ้าของไฟล์

    Returns:
        Response (JSON):
        - 201 {'id', 'ok', 'filename', 'size'} เมื่อสำเร็จ
        - 400 ไม่มีไฟล์ / ไฟล์ว่าง / ไม่ใช่ PDF / base64 เสียหาย / body ผิดรูปแบบ
              / book_no ไม่ใช่ช่องที่ UI มีจริง (ดู BOOK_SLOTS)
        - 404 ไม่พบ record id นี้ (เช็กก่อนเขียนไฟล์ และดักกรณี race ที่ FK constraint ด้วย)
        - 413 ไฟล์เกิน MAX_PDF_SIZE (100 MB)
        - 500 เขียนไฟล์หรือ DB ล้มเหลว (พร้อม rollback ทั้งไฟล์และ DB)
    """
    if request.is_json:
        body = request.get_json(force=True, silent=True)
        if body is None or not isinstance(body, dict):
            return jsonify({'error': 'request body ต้องเป็น JSON object ที่ถูกต้อง'}), 400
        book_no  = str(body.get('book_no', '1'))
        filename = body.get('filename', f'book_{book_no}.pdf')
        b64      = body.get('data', '')
        if not b64:
            return jsonify({'error': 'ไม่พบข้อมูล base64 (field: data)'}), 400
        try:
            file_bytes = base64.b64decode(b64.split(',')[-1])
        except Exception:
            return jsonify({'error': 'invalid base64 — ข้อมูลไฟล์เสียหาย'}), 400
    elif 'file' in request.files:
        f          = request.files['file']
        book_no    = str(request.form.get('book_no', '1'))
        filename   = f.filename or f'book_{book_no}.pdf'
        file_bytes = f.read()
    else:
        return jsonify({'error': 'no file provided'}), 400

    # ตรวจเนื้อหาไฟล์ก่อนแตะดิสก์ — ของพวกนี้เช็กฝั่ง client อยู่แล้วแต่ยกเลิกได้ด้วยการยิง API ตรง
    # และถ้าหลุดเข้ามาได้ ไฟล์จะถูก serve กลับด้วย Content-Type: application/pdf ผ่าน /preview
    # (mime ด้านล่าง hardcode ไว้ ความถูกต้องของมันจึงขึ้นอยู่กับด่านนี้ทั้งหมด)
    if book_no not in BOOK_SLOTS:
        return jsonify({'error': 'ช่องไฟล์แนบไม่ถูกต้อง รองรับเฉพาะเล่ม 1-3 และประกาศเท่านั้น'}), 400
    if not file_bytes:
        return jsonify({'error': 'ไฟล์ว่าง ไม่มีข้อมูลให้บันทึก'}), 400
    if len(file_bytes) > MAX_PDF_SIZE:
        return jsonify({'error': 'ไฟล์มีขนาดใหญ่เกินไป — กรุณาลดขนาดไฟล์ให้ต่ำกว่า 100 MB แล้วลองใหม่อีกครั้ง'}), 413
    # มองหา header '%PDF-' ใน 1024 ไบต์แรก ไม่บังคับว่าต้องอยู่ไบต์ที่ 0
    # เพราะไฟล์จริงจากเครื่องสแกนหรือโปรแกรมบางตัวมี BOM/ขยะนำหน้า และ PDF reader ทุกตัว
    # ก็สแกนช่วงต้นไฟล์แบบเดียวกัน (ISO 32000-1 กำหนดไบต์ที่ 0 แต่ของจริงผ่อนปรนกว่านั้น)
    if b'%PDF-' not in file_bytes[:1024]:
        return jsonify({'error': 'ไฟล์ไม่ใช่ PDF ที่ถูกต้อง — รองรับเฉพาะไฟล์ .pdf เท่านั้น'}), 400

    # เช็กว่า record มีจริง "ก่อน" เขียนไฟล์ลงดิสก์ ไม่ใช่รอให้ INSERT ชน FK constraint
    # แล้วตอบ 500 ซึ่งสื่อว่า server พัง ทั้งที่จริงคือ client อ้างถึงของที่ไม่มีอยู่
    # (ยังมีช่องว่างเล็ก ๆ ที่ record ถูกลบหลังเช็กแต่ก่อน INSERT — ดักด้วย
    # except sqlite3.IntegrityError ด้านล่าง ซึ่งตอบ 404 เหมือนกันและลบไฟล์ที่เพิ่งเขียนทิ้ง)
    with closing(get_db()) as con:
        if not con.execute('SELECT 1 FROM records WHERE id=?', [rid]).fetchone():
            return jsonify({'error': 'ไม่พบข้อมูล id=' + str(rid)}), 404

    safe_name   = sanitize_attachment_name(filename, f'book_{book_no}.pdf')
    stored_name = uuid.uuid4().hex + '.pdf'
    file_path   = _stored_path(stored_name)

    # เขียนไฟล์ลงดิสก์
    try:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        with open(file_path, 'wb') as fh:
            fh.write(file_bytes)
    except Exception:
        return fail('upload_attachment write', 'บันทึกไฟล์ลงเครื่องไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')

    mime = 'application/pdf'
    size = len(file_bytes)
    con  = get_db()
    old_path = None
    try:
        # อ่านชื่อไฟล์เก่าจากตัว DELETE เอง ไม่ใช่ SELECT แยกก่อนหน้า:
        # SELECT แยกจะอ่านใน snapshot ของ request ตัวเองซึ่งเกิด "ก่อน" ได้ write lock
        # ถ้ามีหลาย request อัปช่องเดียวกันพร้อมกัน ทุกตัวจะเห็นชื่อไฟล์เก่าตัวเดียวกัน
        # แล้วไฟล์ที่ผู้แพ้แทนที่ไปจะไม่มีใครลบ กลายเป็นไฟล์กำพร้าเงียบ ๆ
        # DELETE ยึด write lock ก่อนทำงาน ค่าที่ RETURNING คืนจึงเป็นแถวที่ถูกลบไปจริง ๆ
        # (ยังไม่ลบไฟล์ตรงนี้ ต้องรอ commit ผ่านก่อนเหมือนเดิม)
        old = con.execute(
            'DELETE FROM attachments WHERE record_id=? AND book_no=? RETURNING stored_name',
            [rid, book_no]
        ).fetchone()
        if old and old['stored_name']:
            old_path = _stored_path(old['stored_name'])
        cur = con.execute(
            'INSERT INTO attachments '
            '(record_id, book_no, filename, stored_name, mime_type, file_size, uploaded_at) '
            'VALUES (?,?,?,?,?,?,?)',
            [rid, book_no, safe_name, stored_name, mime, size, now_str()]
        )
        con.commit()
        # commit ผ่านแล้วจึงลบไฟล์เก่า ถ้าลบก่อนแล้วมี exception ระหว่างทาง rollback จะคืน
        # row เก่ากลับมาชี้ไปยังไฟล์ที่ถูกลบไปแล้ว ผู้ใช้เห็นว่ามีไฟล์แนบแต่กดดาวน์โหลดได้ 404
        # ไฟล์กำพร้าเสียแค่พื้นที่ดิสก์ แต่ row ที่ชี้ไฟล์หายทำให้ผู้ใช้เข้าใจผิดว่ายังมีผลงานอยู่
        _safe_remove(old_path)
        audit('upload_attachment', f'record={rid} book={book_no} size={size}')
        return jsonify({'id': cur.lastrowid, 'ok': True, 'filename': safe_name, 'size': size}), 201
    except sqlite3.IntegrityError:
        # ชน FK constraint = record หายไประหว่างที่เช็กกับที่ INSERT (ผู้ใช้อีกหน้าต่างลบทิ้ง)
        # สาเหตุคือ client อ้างถึงของที่ไม่มีอยู่ ไม่ใช่ server พัง จึงตอบ 404 ไม่ใช่ 500
        con.rollback()
        _safe_remove(file_path)
        return jsonify({'error': 'ไม่พบข้อมูล id=' + str(rid)}), 404
    except Exception:
        con.rollback()
        _safe_remove(file_path)  # rollback ไฟล์ที่เพิ่งเขียน
        return fail('upload_attachment', 'อัปโหลดไฟล์ไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')
    finally:
        con.close()

@app.route('/api/attachments/<int:att_id>', methods=['GET'])
def download_attachment(att_id):
    """
    GET /api/attachments/<att_id> — ดาวน์โหลดไฟล์ PDF

    as_attachment=True → ส่ง header Content-Disposition: attachment ให้เบราว์เซอร์
    เปิด dialog บันทึกไฟล์ โดยตั้งชื่อไฟล์ที่ผู้ใช้เห็นเป็นชื่อต้นฉบับ (filename)
    ไม่ใช่ชื่อ UUID บนดิสก์ — ผู้ใช้ไม่ต้องรู้ว่า server เก็บไฟล์ชื่ออะไร

    ตรวจ 2 ชั้น: row ใน DB ต้องมี และไฟล์บนดิสก์ต้องอยู่จริง (กันกรณี
    ไฟล์ถูกลบมือ/migration เขียนไฟล์พลาด) — ทั้งสองกรณีตอบ 404 พร้อมเหตุผลต่างกัน

    Parameters:
        att_id (int): id ของแถวในตาราง attachments

    Returns:
        Response: ไฟล์ PDF (200) หรือ JSON error (404)
    """
    with closing(get_db()) as con:
        row = con.execute('SELECT * FROM attachments WHERE id=?', [att_id]).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    fp = _stored_path(row['stored_name'])
    if not os.path.isfile(fp):
        return jsonify({'error': 'ไฟล์หายจาก storage'}), 404
    return send_file(fp, mimetype=row['mime_type'],
                     as_attachment=True, download_name=row['filename'])

@app.route('/api/attachments/<int:att_id>/preview', methods=['GET'])
def preview_attachment(att_id):
    """
    GET /api/attachments/<att_id>/preview — เปิดดู PDF ในเบราว์เซอร์ (inline)

    ต่างจาก download_attachment แค่จุดเดียว: as_attachment=False →
    Content-Disposition: inline ทำให้เบราว์เซอร์ render PDF ในแท็บใหม่
    แทนการดาวน์โหลด (frontend ใช้กับปุ่ม "ดูตัวอย่าง PDF")

    Parameters:
        att_id (int): id ของแถวในตาราง attachments

    Returns:
        Response: ไฟล์ PDF แสดง inline (200) หรือ JSON error (404)
    """
    with closing(get_db()) as con:
        row = con.execute('SELECT * FROM attachments WHERE id=?', [att_id]).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    fp = _stored_path(row['stored_name'])
    if not os.path.isfile(fp):
        return jsonify({'error': 'ไฟล์หายจาก storage'}), 404
    return send_file(fp, mimetype=row['mime_type'],
                     as_attachment=False, download_name=row['filename'])

@app.route('/api/attachments/<int:att_id>', methods=['DELETE'])
def delete_attachment(att_id):
    """
    DELETE /api/attachments/<att_id> — ลบไฟล์แนบเดี่ยว (ทั้งไฟล์บนดิสก์และ row)

    อ่านชื่อไฟล์ก่อนลบ row แต่ลบไฟล์จริงหลัง commit (เหตุผลเดียวกับ delete_record)
    และจงใจตอบ 200 แม้ไม่พบ att_id เพราะ DELETE เป็น idempotent:
    ผลลัพธ์สุดท้ายที่ client ต้องการคือ "ไม่มีไฟล์นี้แล้ว" ซึ่งเป็นจริง

    Parameters:
        att_id (int): id ของแถวในตาราง attachments

    Returns:
        Response (JSON): 200 {'ok': True} หรือ 500 พร้อมข้อความ error (rollback แล้ว)
    """
    con = get_db()
    try:
        row = con.execute('SELECT stored_name FROM attachments WHERE id=?', [att_id]).fetchone()
        path = _stored_path(row['stored_name']) if row and row['stored_name'] else None
        con.execute('DELETE FROM attachments WHERE id=?', [att_id])
        con.commit()
        audit('delete_attachment', f'id={att_id}')
        # ลบไฟล์หลัง commit ด้วยเหตุผลเดียวกับ delete_record
        _safe_remove(path)
        return jsonify({'ok': True})
    except Exception:
        con.rollback()
        return fail('delete_attachment', 'ลบไฟล์แนบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')
    finally:
        con.close()


# ────────────────────────────────────────────────
# Global error handlers
# ────────────────────────────────────────────────
# บังคับให้ error ทุกชนิดตอบกลับเป็น JSON แทนหน้า HTML default ของ Flask —
# เพราะ frontend เรียกผ่าน fetch() และคาดหวัง response.json() เสมอ
# (ถ้าปล่อยเป็น HTML ฝั่ง JS จะ parse พังและผู้ใช้เห็น error ที่อ่านไม่รู้เรื่อง)

# ไม่ใช้ str(e) เหมือนข้อ 9 — ของ Werkzeug เป็น boilerplate อังกฤษยาวเกิน 80 ตัวอักษร
# ซึ่ง toastError เปิด alert() ให้ และไม่ได้บอกผู้ใช้ว่าต้องทำอะไรต่อ
# ไม่ต้อง log ซ้ำที่นี่: exception ที่ไม่มีใครจับ Flask log ให้เองทาง app.logger

@app.errorhandler(400)
def bad_request(e):
    return jsonify({'error': 'คำขอไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง'}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'ไม่พบรายการที่เรียก'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง'}), 500

@app.errorhandler(413)
def request_too_large(e):
    # 413 เกิดเมื่อ payload เกิน MAX_CONTENT_LENGTH (110 MB)
    # frontend ส่ง multipart แล้ว payload ≈ ขนาดไฟล์ ตัวเลขที่แจ้งจึงตรงกับเพดาน 100 MB ใน UI
    return jsonify({'error': 'ไฟล์มีขนาดใหญ่เกินไป — กรุณาลดขนาดไฟล์ให้ต่ำกว่า 100 MB แล้วลองใหม่อีกครั้ง'}), 413


# ────────────────────────────────────────────────
# Stats & Export
# ────────────────────────────────────────────────

@app.route('/api/stats', methods=['GET'])
def stats():
    """
    GET /api/stats — สถิติสรุปสำหรับหน้า dashboard

    ใช้ GROUP BY ให้ SQLite นับแทนการดึงทุก row มานับใน Python —
    เร็วกว่าและส่งข้อมูลข้ามน้อยกว่า ('ผ่าน' hardcode ตรงกับตัวเลือก
    ในฟอร์มฝั่ง frontend)

    Returns:
        Response (JSON): {
            'total':     จำนวน record ทั้งหมด,
            'passed':    จำนวนที่ p5_work_result = 'ผ่าน',
            'by_result': [{result, cnt}, ...]  แยกตามผลการพิจารณา,
            'by_level':  [{level, cnt}, ...]   แยกตามระดับตำแหน่งปัจจุบัน
        }
    """
    with closing(get_db()) as con:
        total  = con.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        passed = con.execute("SELECT COUNT(*) FROM records WHERE p5_work_result='ผ่าน'").fetchone()[0]
        by_result = con.execute(
            'SELECT p5_work_result as result, COUNT(*) as cnt FROM records GROUP BY p5_work_result'
        ).fetchall()
        by_level = con.execute(
            'SELECT p2_pos_level as level, COUNT(*) as cnt FROM records GROUP BY p2_pos_level'
        ).fetchall()
    return jsonify({'total': total, 'passed': passed,
                    'by_result': [dict(r) for r in by_result],
                    'by_level':  [dict(r) for r in by_level]})

@app.route('/api/export/csv')
def export_csv_server():
    """
    GET /api/export/csv — export ทุก record เป็นไฟล์ CSV ให้ดาวน์โหลด

    จุดสำคัญคือภาษาไทย + Excel: นำหน้าเนื้อหาด้วย BOM (U+FEFF, ตัวอักษร
    ที่มองไม่เห็นหน้า '﻿') เพราะ Excel บน Windows จะเดา encoding เป็น
    ANSI ถ้าไม่มี BOM → ภาษาไทยกลายเป็นตัวอักษรขยะ BOM คือสัญญาณบอก
    Excel ว่า "ไฟล์นี้เป็น UTF-8"

    ใช้ csv.DictWriter เขียนลง StringIO ใน memory (ไม่สร้างไฟล์ชั่วคราว)
    โดย fieldnames เอาจาก keys ของแถวแรก — ทุกแถวมีคอลัมน์ชุดเดียวกัน
    เพราะมาจาก SELECT * ตารางเดียว

    Returns:
        Response: ไฟล์ CSV (Content-Disposition: attachment ชื่อไฟล์ติดวันที่)
                  หรือ 204 No Content ถ้ายังไม่มีข้อมูลเลย
    """
    import csv, io as _io
    with closing(get_db()) as con:
        rows = con.execute('SELECT * FROM records ORDER BY id DESC').fetchall()
    if not rows:
        return '', 204
    buf = _io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows([{k: csv_cell(v) for k, v in dict(r).items()} for r in rows])
    from flask import Response
    # mimetype ต้องไม่มี parameter ติดมาด้วย — Werkzeug เติม charset ของตัวเองต่อท้ายเสมอ
    # ใส่มาเองจะได้ charset สองตัวในหัวเดียว ('text/csv; charset=X; charset=utf-8')
    # ซึ่ง client ที่อ่านตัวแรกจะเจอค่าที่ไม่รู้จักแล้ว fallback ไป encoding อื่น
    return Response('﻿' + buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=hr_promotion_{dstr()}.csv'})


# ────────────────────────────────────────────────
# Desktop launcher
# ────────────────────────────────────────────────

PORT = 5000

def _backup_db():
    """
    คัดลอก DB เป็นสำเนาสำรองรายวันไว้ที่ BACKUP_DIR/hr_promotion-YYYYMMDD.db

    ทำไมต้องมี: ไฟล์ DB เคยถูก track ไว้ใน git ทุกครั้งที่ `git checkout` หรือสลับสาขา
    ข้อมูลจริงจึงถูกเขียนทับด้วยเวอร์ชันที่ commit ไว้ ขณะที่ `static/uploads/*.pdf`
    ถูก ignore ไว้จึงรอด ผลคือไฟล์ PDF กำพร้าสะสมโดยไม่มีแถวไหนชี้ถึง
    เอา DB ออกจาก git แล้วก็ยังไม่พอ เพราะ commit เก่าทุกตัวยังมีไฟล์นี้อยู่ใน tree
    การ checkout ย้อนไปที่ commit เหล่านั้นจะเขียนทับอีก สำเนาสำรองคือชั้นที่รอด
    เหตุการณ์นั้น และมันต้องไม่อยู่ใน git เพราะ git คือต้นเหตุ

    ใช้ `sqlite3.Connection.backup()` ไม่ใช่ `shutil.copy` — การคัดลอกไฟล์ของ DB
    ที่เปิดอยู่ในโหมด WAL ได้สำเนาที่ไม่สอดคล้องกับ -wal ซึ่งเป็นวิธีทำข้อมูลพังที่
    มองไม่ออกจนสาย ส่วน backup() ทำงานผ่าน SQLite เองจึงได้ snapshot ที่ consistent

    ไฟล์ละวัน: ถ้าของวันนี้มีแล้วไม่เขียนทับ สำเนาแรกของวันจึงเป็นสภาพ "ก่อนถูกแตะ"
    ของวันนั้น ซึ่งเป็นสภาพที่มีค่าที่สุดเวลาต้องกู้

    ความล้มเหลวที่นี่ต้องไม่ทำให้โปรแกรมเปิดไม่ขึ้น — บันทึก log แล้วไปต่อ
    การสำรองไม่ได้ยังดีกว่าผู้ใช้เปิดโปรแกรมทำงานไม่ได้

    Returns:
        str | None: path ของสำเนาที่เขียน, None เมื่อข้ามหรือล้มเหลว
    """
    if not os.path.exists(DB_PATH):
        return None                                  # ยังไม่มี DB ไม่มีอะไรให้สำรอง
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        dest = os.path.join(BACKUP_DIR,
                            'hr_promotion-%s.db' % datetime.now().strftime('%Y%m%d'))
        if os.path.exists(dest):
            return dest                              # ของวันนี้มีแล้ว ไม่เขียนทับ
        with closing(sqlite3.connect(DB_PATH)) as src:
            with closing(sqlite3.connect(dest)) as dst:
                src.backup(dst)
                # สำเนาสืบทอดโหมด WAL จากต้นฉบับ แล้วทิ้ง -shm/-wal ไว้ข้างทุกไฟล์
                # สำเนาสำรองต้องเป็นไฟล์เดียวที่คัดลอกไปไหนก็ครบ ไม่ใช่สามไฟล์ที่ต้องไปด้วยกัน
                dst.execute('PRAGMA journal_mode=DELETE')
        _prune_backups()
        log.info('สำรอง DB ไว้ที่ %s', dest)
        return dest
    except Exception:
        log.exception('สำรอง DB ไม่สำเร็จ')
        return None


def _prune_backups():
    """
    เก็บสำเนาสำรองไว้ BACKUP_KEEP ไฟล์ล่าสุด ลบที่เก่ากว่านั้น

    เรียงตามชื่อไฟล์ได้เพราะรูปแบบเป็น YYYYMMDD ซึ่งลำดับตัวอักษรตรงกับลำดับเวลา
    ไม่ใช้ mtime เพราะการคัดลอกโฟลเดอร์หรือกู้คืนไฟล์ทำให้ mtime เปลี่ยนได้
    """
    try:
        names = sorted(n for n in os.listdir(BACKUP_DIR)
                       if n.startswith('hr_promotion-') and n.endswith('.db'))
        for n in names[:-BACKUP_KEEP] if len(names) > BACKUP_KEEP else []:
            _safe_remove(os.path.join(BACKUP_DIR, n))
    except Exception:
        log.exception('ลบสำเนาสำรองเก่าไม่สำเร็จ')


def _setup_logging():
    """
    ตั้ง log ไฟล์ไว้ข้างตัวโปรแกรม (BASE_DIR/hr_promotion.log) หมุนไฟล์ที่ 1 MB เก็บ 3 รุ่น

    เรียกจาก `main()` เท่านั้น ไม่ใช่ตอน import module — เพราะ test import app.py
    ถ้าตั้ง handler ตอน import ทุกครั้งที่รัน test จะมีไฟล์ log งอกในโฟลเดอร์โปรเจกต์
    (ตอน test ไม่มี handler ข้อความระดับ ERROR จะไปออก stderr ตาม lastResort ของ
    logging ซึ่ง pytest เก็บให้อยู่แล้ว)

    ต่อ handler เข้าทั้ง log ของเรา (จาก fail()) และ app.logger ของ Flask (traceback
    ของ exception ที่ไม่มีใครจับ) ไม่อย่างนั้นฝั่งหลังจะหายไปกับ stderr ซึ่งโหมด
    windowed ของ .exe ไม่มีให้เขียน

    ไฟล์ log เป็นที่เดียวที่ยังมีรายละเอียด exception เต็ม ๆ หลังข้อ 9 ตัด str(e)
    ออกจาก response — เวลาผู้ใช้แจ้งปัญหา ให้ขอไฟล์นี้

    Returns:
        None
    """
    handler = RotatingFileHandler(
        os.path.join(BASE_DIR, 'hr_promotion.log'),
        maxBytes=1024 * 1024, backupCount=3, encoding='utf-8',
    )
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    for target in (log, app.logger):
        target.addHandler(handler)
        target.setLevel(logging.INFO)

# เพดานเวลารอ server ตอนเปิดโปรแกรม ตั้งไว้กว้างเพราะเครื่องผู้ใช้ปลายทาง
# อาจมี antivirus สแกน .exe ที่เพิ่งแตกไฟล์อยู่ ซึ่งกินเวลาหลายวินาที
STARTUP_WAIT_SECONDS = 10.0

def _wait_for_port(port: int, timeout: float = STARTUP_WAIT_SECONDS,
                   host: str = '127.0.0.1') -> bool:
    """
    รอจนกว่า port จะรับ connection ได้จริง

    คืน False เมื่อหมดเวลาแทนการ raise หรือวนต่อไม่สิ้นสุด เพราะผู้เรียกต้อง
    เปิดหน้าต่างให้ได้อยู่ดี — หน้าต่างที่ไม่เคยเปิดแย่กว่าหน้าต่างที่โหลดช้า

    Parameters:
        port (int): port ที่รอ
        timeout (float): เพดานเวลารวม (วินาที)
        host (str): host ที่ลองต่อ

    Returns:
        bool: True = ต่อติดแล้ว, False = หมดเวลา
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False

def run_flask():
    """
    รัน Flask server สำหรับโหมด desktop (ถูกเรียกใน background thread)

    - host=127.0.0.1: รับเฉพาะ local — ป้องกันเครื่องอื่นใน network เข้าถึงข้อมูล HR
    - use_reloader=False: จำเป็นมาก เพราะ reloader ของ Flask ทำงานด้วยการ
      fork/spawn process ใหม่ ซึ่งทำไม่ได้ใน thread ลูก (จะ crash ทันที)

    Returns:
        None (blocking — วนรับ request จนกว่า process จะจบ)
    """
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

LOOPBACK_HOSTS = ('127.0.0.1', 'localhost', '::1')


def _server_bind():
    """
    เลือก host และ debug ของโหมด fallback จาก environment

    HR_HOST ไม่ตั้ง = 127.0.0.1 รับเฉพาะเครื่องตัวเอง ตั้ง HR_HOST=0.0.0.0 = เปิดให้ทุกเครื่อง
    ในเครือข่ายเข้าได้ ผู้ใช้เลือกเปิดเองโดยรู้ว่า API ไม่มี authentication เลย ใครเข้าถึง
    port ได้ก็อ่าน แก้ และลบข้อมูล HR ได้ทั้งหมด รวมถึง DELETE /api/records ที่ลบทุกแถว

    debug เปิดได้ด้วย HR_DEBUG=1 แต่ถูกบังคับปิดเมื่อ host ไม่ใช่ loopback เพราะ Werkzeug
    debugger มีคอนโซลรัน Python ได้ เปิดคู่กับ LAN เท่ากับให้ทุกเครื่องรันโค้ดบนเครื่องนี้ได้
    ความเสี่ยงนั้นหนักกว่าข้อมูลรั่ว และไม่มีใครตั้งใจเลือกมัน

    Returns:
        tuple[str, bool]: (host, debug)
    """
    host = os.environ.get('HR_HOST', '').strip() or '127.0.0.1'
    debug = os.environ.get('HR_DEBUG') == '1'
    if debug and host not in LOOPBACK_HOSTS:
        log.warning('ปิด HR_DEBUG เพราะ HR_HOST=%s เปิดให้เครื่องอื่นเข้าถึง', host)
        debug = False
    return host, debug


def main():
    """
    Entry point ของแอป — เตรียม DB แล้วเลือกโหมดการรันตาม environment

    สถาปัตยกรรมโหมด desktop (มี pywebview):
    Flask รันใน daemon thread ส่วน webview (หน้าต่าง native) รันใน main thread
    เพราะ GUI library ส่วนใหญ่บังคับให้ event loop อยู่ main thread เท่านั้น
    การตั้ง daemon=True ทำให้ Flask thread ถูกเก็บกวาดอัตโนมัติเมื่อผู้ใช้
    ปิดหน้าต่าง (ไม่ต้องจัดการ shutdown เอง)
    รอด้วย _wait_for_port ไม่ใช่ sleep คงที่ เพราะเวลาที่ server ใช้เปิด port
    ขึ้นกับเครื่องผู้ใช้ ไม่ใช่ค่าที่เดาไว้ล่วงหน้าได้

    โหมด fallback (ไม่มี pywebview เช่น รันบนเครื่อง dev):
    รันเป็น web server ปกติให้เปิดผ่านเบราว์เซอร์ host และ debug มาจาก _server_bind()
    ค่าเริ่มต้นผูก 127.0.0.1 เหมือนโหมด desktop การเปิดให้ LAN ต้องตั้ง HR_HOST เอง
    (run_server.bat ทำให้) ดูเหตุผลและข้อจำกัดที่ _server_bind()

    Returns:
        None
    """
    _setup_logging()
    # สำรองก่อน init_db() เสมอ — init_db() มี migration ที่แก้ข้อมูล
    # (_dedupe_attachment_slots ลบไฟล์ที่ถูกแทนที่ด้วย) สำเนาจึงต้องเป็นสภาพก่อนนั้น
    _backup_db()
    init_db()
    if HAS_WEBVIEW:
        t = threading.Thread(target=run_flask, daemon=True)
        t.start()
        # เปิดหน้าต่างต่อแม้รอไม่ทัน: หน้าขาวที่ refresh ได้ยังดีกว่าโปรแกรม
        # ที่กดเปิดแล้วไม่มีอะไรขึ้นมาเลย และ log เป็นร่องรอยเดียวที่เหลือไว้สืบ
        if not _wait_for_port(PORT):
            log.warning('server ยังไม่รับ connection ใน %.0f วินาที เปิดหน้าต่างต่อ',
                        STARTUP_WAIT_SECONDS)
        webview.create_window(
            title='ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง',
            url=f'http://127.0.0.1:{PORT}',
            width=1280, height=820, min_size=(900, 600), resizable=True,
        )
        webview.start(debug=False)
    else:
        host, debug = _server_bind()
        if host in LOOPBACK_HOSTS:
            print(f'[HR System] เปิด http://127.0.0.1:{PORT}  (Ctrl+C เพื่อหยุด)')
        else:
            log.warning('เปิดให้เครื่องอื่นเข้าถึงได้ที่ %s:%d — ไม่มีการล็อกอิน', host, PORT)
            print(f'[HR System] เปิดให้เครื่องอื่นในเครือข่ายเข้าได้ ({host}:{PORT})')
            print(f'[HR System] เครื่องอื่นเปิด http://<IP ของเครื่องนี้>:{PORT}  (ดู IP ด้วย ipconfig)')
            print('[HR System] คำเตือน: ไม่มีการล็อกอิน ทุกเครื่องในเครือข่ายอ่าน แก้ และลบข้อมูลได้')
        app.run(host=host, port=PORT, debug=debug)

if __name__ == '__main__':
    main()
