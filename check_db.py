#!/usr/bin/env python3
"""
วินิจฉัย hr_promotion.db schema
รันในขณะที่ Flask app ปิดอยู่:  python check_db.py
"""
import sqlite3, os, sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hr_promotion.db')
if not os.path.exists(DB):
    print("ไม่พบไฟล์ hr_promotion.db"); sys.exit(1)

con = sqlite3.connect(DB)
con.execute("PRAGMA journal_mode=DELETE")   # ปลด WAL lock

print("=== SCHEMA: records ===")
cols = con.execute("PRAGMA table_info(records)").fetchall()
problems = []
for cid, name, ctype, notnull, dflt, pk in cols:
    nn  = "NOT NULL" if notnull else "nullable"
    dfv = f"DEFAULT {dflt}" if dflt is not None else "NO DEFAULT"
    warn = ""
    if notnull and dflt is None and pk == 0:
        warn = "  <-- !! NOT NULL ไม่มี DEFAULT (อันตราย)"
        problems.append(name)
    print(f"  {name:25s} {ctype:6s} {nn:10s} {dfv}{warn}")

print()
if problems:
    print(f"!! พบ column ที่อาจทำให้ error: {problems}")
    print("   แนวทางแก้ไข: รัน fix_db.py")
else:
    print("✓ Schema ปกติ ไม่มี column อันตราย")
    print("  หากยังเกิด error → ปิด-เปิด app ใหม่")

con.close()
