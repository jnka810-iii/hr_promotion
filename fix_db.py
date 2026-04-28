#!/usr/bin/env python3
"""
แก้ไข column NOT NULL ที่ไม่มี DEFAULT ใน hr_promotion.db
รันในขณะที่ Flask app ปิดอยู่:  python fix_db.py
"""
import sqlite3, os, sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hr_promotion.db')
con = sqlite3.connect(DB)
con.execute("PRAGMA journal_mode=DELETE")
con.execute("PRAGMA foreign_keys=OFF")

# ดึง column ปัญหา
cols = con.execute("PRAGMA table_info(records)").fetchall()
bad = [(name, ctype) for cid, name, ctype, notnull, dflt, pk in cols
       if notnull and dflt is None and pk == 0]

if not bad:
    print("✓ ไม่พบ column ปัญหา ไม่ต้องแก้ไข")
    con.close(); sys.exit(0)

print(f"พบ column ที่ต้องแก้ไข: {[b[0] for b in bad]}")

# SQLite ไม่รองรับ ALTER COLUMN → ต้อง recreate table
# ดึง schema เดิม
schema_row = con.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='records'"
).fetchone()
old_sql = schema_row[0]

# แทนที่ NOT NULL (ที่ไม่มี DEFAULT) ด้วย NOT NULL DEFAULT ''
import re
def add_default(sql):
    # เพิ่ม DEFAULT '' หลัง NOT NULL ถ้ายังไม่มี DEFAULT
    return re.sub(
        r"(NOT NULL)(?!\s+DEFAULT)",
        r"\1 DEFAULT ''",
        sql, flags=re.IGNORECASE
    )

new_sql = add_default(old_sql).replace("CREATE TABLE records", "CREATE TABLE records_new")
print("สร้างตารางใหม่...")
con.execute(new_sql)

# copy ข้อมูล
col_names = [c[1] for c in cols]
cols_str   = ", ".join(col_names)
con.execute(f"INSERT INTO records_new ({cols_str}) SELECT {cols_str} FROM records")

# swap
con.execute("DROP TABLE records")
con.execute("ALTER TABLE records_new RENAME TO records")
con.execute("PRAGMA foreign_keys=ON")
con.commit()
con.close()
print("✓ แก้ไข schema เรียบร้อย  — เปิดแอปได้เลย")
