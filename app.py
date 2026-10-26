"""
ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง
Flask + SQLite3 + pywebview  →  Desktop App (.exe)

ไฟล์ PDF เก็บไว้ใน static/uploads/  ใช้ UUID เป็นชื่อไฟล์บนดิสก์
DB เก็บเฉพาะ stored_name (UUID filename) และ filename (ชื่อต้นฉบับ)
"""

import os, re, sys, socket, sqlite3, threading, time, base64, json, uuid, shutil, logging
from contextlib import closing
from datetime import datetime
from logging.handlers import RotatingFileHandler
from flask import Flask, request, jsonify, render_template, send_file

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

def sanitize_display_name(name: str, fallback: str) -> str:
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
       ถ้าเขียนไฟล์แถวใดพลาด → rollback, ลบไฟล์ที่เขียนไปแล้ว, raise ทันที
       เหตุผลอยู่ที่ขั้นตอน 5 ซึ่งลบคอลัมน์ file_data ทิ้ง ถ้าปล่อยให้แถวที่พลาดผ่านไป
       BLOB ของแถวนั้นจะหายถาวรโดยไม่มีสัญญาณเตือนใด ๆ
    3. DROP ตารางชั่วคราว attachments_v2 ที่อาจค้างจาก migration ที่ล้มเหลวรอบก่อน
    4. สร้าง attachments_v2 (schema ใหม่ ไม่มี file_data) แล้ว copy ข้อมูลข้ามด้วย
       INSERT..SELECT — ใช้ src_map สร้าง expression รายคอลัมน์ เพราะ schema เก่า
       บางเครื่องไม่มี mime_type/uploaded_at ต้อง fallback เป็นค่าคงที่/NULL
    5. สลับตาราง: DROP ตัวเก่า → RENAME v2 เป็น attachments

    เหตุผลที่ต้องสร้างตารางใหม่แทน ALTER: SQLite ไม่รองรับ DROP COLUMN
    (ในเวอร์ชันเก่า) — การ rebuild ตารางคือวิธีมาตรฐานของ SQLite

    Parameters:
        con (sqlite3.Connection): connection ที่เปิดอยู่ (ฟังก์ชันนี้ commit เอง แต่ไม่ close)

    Raises:
        RuntimeError: สำรองไฟล์ DB ไม่ได้ หรือเขียนไฟล์แนบไม่ครบทุกแถว
                      ทั้งสองกรณี DB เดิมยังอยู่ครบ ผู้ใช้แก้สาเหตุแล้วเปิดแอปใหม่ได้

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

    if 'stored_name' not in att_cols:
        con.execute("ALTER TABLE attachments ADD COLUMN stored_name TEXT NOT NULL DEFAULT ''")
        att_cols.add('stored_name')

    # ย้าย BLOB → file
    rows = con.execute("SELECT id, filename, file_data FROM attachments").fetchall()
    written, failed = [], []
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
        con.rollback()                      # SQLite rollback DDL ได้ ALTER TABLE ข้างบนจึงถูกถอนด้วย
        for p in written:
            _safe_remove(p)                 # เก็บกวาดไฟล์ที่เขียนไปแล้ว ให้รันใหม่ได้จากสถานะสะอาด
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

        orphaned = _dedupe_attachment_slots(con)
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_att_record_book '
                    'ON attachments(record_id, book_no)')

        con.commit()

    # ลบไฟล์หลัง commit เสมอ ตาม invariant เดียวกับ delete_record
    for path in orphaned:
        _safe_remove(path)


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

    Returns:
        str: HTML ของ templates/index.html
    """
    return render_template('index.html', required_fields=list(REQUIRED_FIELDS.items()))

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
def delete_all():
    """
    DELETE /api/records — ล้างข้อมูลทั้งระบบ (ทุก record + ไฟล์แนบทุกไฟล์)

    ใช้หลักการเดียวกับ delete_record: เก็บรายชื่อไฟล์ไว้ก่อน → DELETE FROM records
    ให้ CASCADE ล้างตาราง attachments ตาม → commit → แล้วจึงลบไฟล์บนดิสก์
    (frontend มี confirm dialog ป้องกันการกดพลาดอยู่แล้วชั้นหนึ่ง)

    Returns:
        Response (JSON): 200 {'ok': True} หรือ 500 พร้อมข้อความ error (rollback แล้ว)
    """
    con = get_db()
    try:
        atts = con.execute('SELECT stored_name FROM attachments').fetchall()
        paths = [_stored_path(a['stored_name']) for a in atts if a['stored_name']]
        con.execute('DELETE FROM records')
        con.commit()
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

    หมายเหตุ: filename ผ่าน sanitize_display_name() ซึ่งเก็บอักษรไทยไว้ครบ
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

    safe_name   = sanitize_display_name(filename, f'book_{book_no}.pdf')
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
    รันเป็น web server ปกติให้เปิดผ่านเบราว์เซอร์ ผูกที่ 127.0.0.1 เท่านั้นเหมือนโหมด desktop
    เพราะ API ไม่มี authentication เลย ถ้าผูก 0.0.0.0 ทุกเครื่องในเครือข่ายจะอ่านและลบข้อมูล HR ได้
    debug ปิดเป็นค่าเริ่มต้น เปิดได้ด้วย HR_DEBUG=1 สำหรับตอน dev เท่านั้น เพราะ Werkzeug debugger
    มีคอนโซลรัน Python ได้ ซึ่งเท่ากับเปิดทาง RCE ถ้าหลุดออกไปอยู่บนเครื่องผู้ใช้

    Returns:
        None
    """
    _setup_logging()
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
        print(f'[HR System] เปิด http://127.0.0.1:{PORT}  (Ctrl+C เพื่อหยุด)')
        app.run(host='127.0.0.1', port=PORT, debug=os.environ.get('HR_DEBUG') == '1')

if __name__ == '__main__':
    main()
