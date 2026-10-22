#!/usr/bin/env python3
"""
แก้ไข column NOT NULL ที่ไม่มี DEFAULT ใน hr_promotion.db
รันในขณะที่ Flask app ปิดอยู่:  python fix_db.py

SQLite ไม่รองรับ ALTER COLUMN จึงต้องสร้างตารางใหม่แล้ว copy ข้อมูลข้าม
ซึ่งแปลว่าสคริปต์นี้ DROP ตาราง records ของจริง จึงสำรองไฟล์ DB ก่อนเสมอ
และทำงานทั้งหมดในทรานแซกชันเดียว ถ้าพังกลางทางข้อมูลเดิมยังอยู่ครบ
"""
import os, re, shutil, sqlite3, sys
from datetime import datetime

# ข้อความมีทั้งภาษาไทยและอักขระนอก ASCII — ถ้าไม่บังคับ UTF-8 การ redirect output
# ลงไฟล์หรือ pipe จะพังด้วย UnicodeEncodeError บน Windows ที่ใช้ codepage ไทย
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hr_promotion.db')

# ต้องเช็กก่อน connect เพราะ sqlite3.connect สร้างไฟล์เปล่าให้เองถ้าไม่มี
# แล้วสคริปต์จะรายงานว่า "ไม่พบ column ปัญหา" ทั้งที่เพิ่งทิ้งไฟล์ขยะ 0 ไบต์ไว้
if not os.path.exists(DB):
    print('ไม่พบไฟล์ hr_promotion.db — ต้องรันสคริปต์นี้ในโฟลเดอร์เดียวกับ app.py')
    sys.exit(1)

# DEFAULT ต้องเลือกตามชนิดคอลัมน์ การใส่ '' ให้คอลัมน์ INTEGER ทำให้ SQLite
# เก็บค่าเป็น text จริง ๆ (type affinity ไม่แปลง '' เป็น 0) แล้วการคำนวณฝั่งแอปจะเพี้ยน
TYPE_DEFAULTS = {'TEXT': "''", 'INTEGER': '0', 'REAL': '0', 'NUMERIC': '0', 'BLOB': "x''"}

def default_for(ctype):
    """คืน literal ที่ใช้เป็น DEFAULT ของชนิดนี้ หรือ None ถ้าไม่รู้จัก"""
    return TYPE_DEFAULTS.get((ctype or 'TEXT').upper().split('(')[0].strip())

def add_default(sql, name, literal):
    """
    เติม DEFAULT ให้คอลัมน์ชื่อ name ตัวเดียว

    ผูก pattern กับชื่อคอลัมน์เสมอ ไม่ใช่ replace คำว่า NOT NULL ทั้งไฟล์
    ไม่งั้นจะไปโดน NOT NULL ของคอลัมน์อื่นและของ FOREIGN KEY clause ด้วย
    """
    pat = re.compile(rf'("?\b{re.escape(name)}\b"?[^,()]*?NOT\s+NULL)(?!\s+DEFAULT)',
                     re.IGNORECASE)
    new, n = pat.subn(rf'\1 DEFAULT {literal}', sql, count=1)
    if n != 1:
        raise RuntimeError(f'หา column {name} ในสคีมาไม่เจอ ไม่กล้าแก้ต่อ')
    return new

con = sqlite3.connect(DB)
con.isolation_level = None          # คุมทรานแซกชันเอง ไม่ให้ sqlite3 เปิดให้อัตโนมัติ
con.execute("PRAGMA journal_mode=DELETE")   # ปลด WAL lock

cols = con.execute("PRAGMA table_info(records)").fetchall()
if not cols:
    print('ไม่พบตาราง records ในไฟล์นี้ — อาจไม่ใช่ DB ของ hr_promotion')
    con.close(); sys.exit(1)

bad = [(name, ctype) for cid, name, ctype, notnull, dflt, pk in cols
       if notnull and dflt is None and pk == 0]

if not bad:
    print('[OK] ไม่พบ column ปัญหา ไม่ต้องแก้ไข')
    con.close(); sys.exit(0)

unknown = [(n, t) for n, t in bad if default_for(t) is None]
if unknown:
    print(f'ไม่รู้ว่าควรใส่ DEFAULT อะไรให้ column เหล่านี้: {unknown}')
    print('หยุดการทำงาน ไม่แก้ไขอะไรทั้งสิ้น')
    con.close(); sys.exit(1)

print(f'พบ column ที่ต้องแก้ไข: {bad}')

# สำรองแบบติดเวลา ไม่ทับสำเนาของรอบก่อน เผื่อรอบก่อนคือตัวที่ยังดีอยู่
backup = f'{DB}.bak-{datetime.now():%Y%m%d-%H%M%S}'
shutil.copy2(DB, backup)
print(f'สำรองไฟล์เดิมไว้ที่ {backup}')

old_sql = con.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='records'"
).fetchone()[0]

new_sql = old_sql
for name, ctype in bad:
    new_sql = add_default(new_sql, name, default_for(ctype))
new_sql = re.sub(r'(CREATE\s+TABLE\s+)["\'`\[]?records["\'`\]]?',
                 r'\1records_new', new_sql, count=1, flags=re.IGNORECASE)

# ต้องสั่งนอกทรานแซกชัน SQLite เพิกเฉย pragma ตัวนี้เงียบ ๆ ถ้าอยู่ข้างใน
con.execute("PRAGMA foreign_keys=OFF")

col_list = ', '.join(f'"{c[1]}"' for c in cols)
try:
    con.execute("BEGIN")
    # ล้างตารางชั่วคราวที่อาจค้างจากการรันรอบก่อนที่ล้มกลางทาง
    # (วิธีเดียวกับที่ _migrate_blobs_to_files ทำกับ attachments_v2)
    con.execute("DROP TABLE IF EXISTS records_new")
    con.execute(new_sql)
    con.execute(f'INSERT INTO records_new ({col_list}) SELECT {col_list} FROM records')
    moved = con.execute("SELECT COUNT(*) FROM records_new").fetchone()[0]
    con.execute("DROP TABLE records")
    con.execute("ALTER TABLE records_new RENAME TO records")
    con.execute("COMMIT")
except Exception as e:
    con.execute("ROLLBACK")
    con.close()
    print(f'แก้ไขไม่สำเร็จ: {e}')
    print(f'ข้อมูลเดิมยังอยู่ครบ (rollback แล้ว) สำเนาอยู่ที่ {backup}')
    sys.exit(1)

con.execute("PRAGMA foreign_keys=ON")       # นอกทรานแซกชันแล้ว pragma จึงมีผลจริง
orphans = con.execute("PRAGMA foreign_key_check").fetchall()
con.close()

print(f'[OK] ย้ายข้อมูล {moved} แถวเข้าตารางใหม่เรียบร้อย')
if orphans:
    print(f'[คำเตือน] พบแถวที่ FK ชี้ไปยังข้อมูลที่ไม่มีอยู่ {len(orphans)} รายการ: {orphans[:5]}')
else:
    print('[OK] ตรวจ foreign key แล้วไม่พบแถวกำพร้า')
print('[OK] แก้ไข schema เรียบร้อย — เปิดแอปได้เลย')
print(f'ถ้าผลลัพธ์ผิดไปจากที่คาด กู้คืนด้วยการคัดลอก {backup} ทับ {DB}')
