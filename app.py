"""
ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง
Flask + SQLite3 + pywebview  →  Desktop App (.exe)

ไฟล์ PDF เก็บไว้ใน static/uploads/  ใช้ UUID เป็นชื่อไฟล์บนดิสก์
DB เก็บเฉพาะ stored_name (UUID filename) และ filename (ชื่อต้นฉบับ)
"""

import os, sys, sqlite3, threading, time, base64, json, uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename

try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
# frontend ยังส่ง base64 อยู่ → ต้องรองรับ payload ≈ ขนาดไฟล์ × 1.33
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

BASE_DIR      = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
DB_PATH       = os.path.join(BASE_DIR, 'hr_promotion.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')


# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con

def now_str():
    return datetime.now().strftime('%d/%m/%Y %H:%M')

def dstr():
    return datetime.now().strftime('%Y%m%d')

def row_to_dict(row):
    return dict(row)

def _safe_remove(path: str):
    """ลบไฟล์โดยไม่ raise exception ถ้าไม่พบ"""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass

def _stored_path(stored_name: str) -> str:
    return os.path.join(UPLOAD_FOLDER, stored_name)


# ────────────────────────────────────────────────
# Database initialisation & migration
# ────────────────────────────────────────────────

def _migrate_blobs_to_files(con):
    """
    ย้ายไฟล์ที่เคยเก็บเป็น BLOB ใน DB → ไฟล์จริงบน UPLOAD_FOLDER
    แล้วสร้างตาราง attachments ใหม่โดยไม่มีคอลัมน์ file_data
    รองรับ schema เก่าที่อาจไม่มี mime_type / uploaded_at
    """
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    att_cols = {row[1] for row in con.execute("PRAGMA table_info(attachments)").fetchall()}

    if 'stored_name' not in att_cols:
        con.execute("ALTER TABLE attachments ADD COLUMN stored_name TEXT NOT NULL DEFAULT ''")
        att_cols.add('stored_name')

    # ย้าย BLOB → file สำหรับแถวที่ stored_name ยังว่าง
    rows = con.execute("SELECT id, filename, file_data FROM attachments").fetchall()
    for row in rows:
        stored = uuid.uuid4().hex + '.pdf'
        try:
            data = bytes(row['file_data']) if row['file_data'] else b''
            with open(_stored_path(stored), 'wb') as f:
                f.write(data)
        except Exception:
            stored = ''
        con.execute("UPDATE attachments SET stored_name=? WHERE id=?", [stored, row['id']])

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


def init_db():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    con = get_db()

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

    con.commit()
    con.close()


# ────────────────────────────────────────────────
# Record helpers
# ────────────────────────────────────────────────

RECORD_COLS = {
    'p1_title', 'p1_fname', 'p1_lname', 'p1_edu', 'p1_edu_major',
    'p2_pos_current', 'p2_pos_level', 'p2_empid', 'p2_dept', 'p2_sec', 'p2_org',
    'p3_pos', 'p3_pos_level', 'p3_empid', 'p3_dept', 'p3_sec', 'p3_org', 'p3_announcement',
    'p4_work1_title', 'p4_work1_year', 'p4_work2_title', 'p4_work2_year',
    'p4_work3_title', 'p4_work3_year', 'p4_concept_title', 'p4_work_award',
    'p5_doc_received', 'p5_work_result', 'p5_work_score', 'p5_work_eval_date',
    'eval51_rows', 'eval52_rows',
}

REQUIRED_FIELDS = {
    'p1_fname':       'ชื่อ',
    'p1_lname':       'นามสกุล',
    'p2_pos_current': 'ตำแหน่งปัจจุบัน',
    'p2_empid':       'ตำแหน่งเลขที่',
}

def sanitize_record(d: dict) -> dict:
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
    return [label for col, label in REQUIRED_FIELDS.items()
            if not allowed.get(col, '').strip()]


# ────────────────────────────────────────────────
# Routes — Records
# ────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/records', methods=['GET'])
def get_all():
    con = get_db()
    rows = con.execute('SELECT * FROM records ORDER BY id DESC').fetchall()
    con.close()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/records/<int:rid>', methods=['GET'])
def get_one(rid):
    con = get_db()
    row = con.execute('SELECT * FROM records WHERE id=?', [rid]).fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(row_to_dict(row))

@app.route('/api/records', methods=['POST'])
def add_record():
    d = request.get_json(force=True, silent=True)
    if d is None or not isinstance(d, dict):
        return jsonify({'error': 'request body ต้องเป็น JSON object ที่ถูกต้อง'}), 400
    allowed = sanitize_record(d)
    missing = validate_required(allowed)
    if missing:
        return jsonify({'error': 'กรุณากรอกข้อมูลที่จำเป็นให้ครบ: ' + ', '.join(missing)}), 422
    allowed['created'] = now_str()
    allowed['updated'] = ''
    cols = ', '.join(allowed.keys())
    plh  = ', '.join(['?'] * len(allowed))
    con  = get_db()
    try:
        cur = con.execute(f'INSERT INTO records ({cols}) VALUES ({plh})', list(allowed.values()))
        con.commit()
        return jsonify({'id': cur.lastrowid, 'ok': True}), 201
    except sqlite3.IntegrityError as e:
        con.rollback()
        return jsonify({'error': 'บันทึกไม่สำเร็จ (integrity): ' + str(e)}), 422
    except sqlite3.OperationalError as e:
        con.rollback()
        return jsonify({'error': 'DB error: ' + str(e)}), 500
    except Exception as e:
        con.rollback()
        return jsonify({'error': 'เกิดข้อผิดพลาดที่ไม่คาดคิด: ' + str(e)}), 500
    finally:
        con.close()

@app.route('/api/records/<int:rid>', methods=['PUT'])
def update_record(rid):
    d = request.get_json(force=True, silent=True)
    if d is None or not isinstance(d, dict):
        return jsonify({'error': 'request body ต้องเป็น JSON object ที่ถูกต้อง'}), 400
    allowed = sanitize_record(d)
    missing = validate_required(allowed)
    if missing:
        return jsonify({'error': 'กรุณากรอกข้อมูลที่จำเป็นให้ครบ: ' + ', '.join(missing)}), 422
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
    except sqlite3.OperationalError as e:
        con.rollback()
        return jsonify({'error': 'DB error: ' + str(e)}), 500
    except Exception as e:
        con.rollback()
        return jsonify({'error': 'เกิดข้อผิดพลาดที่ไม่คาดคิด: ' + str(e)}), 500
    finally:
        con.close()

@app.route('/api/records/<int:rid>', methods=['DELETE'])
def delete_record(rid):
    con = get_db()
    try:
        # ลบไฟล์ physical ก่อน แล้วค่อยลบ DB row (CASCADE จัดการ attachments rows)
        atts = con.execute(
            'SELECT stored_name FROM attachments WHERE record_id=?', [rid]
        ).fetchall()
        for att in atts:
            _safe_remove(_stored_path(att['stored_name']))
        con.execute('DELETE FROM records WHERE id=?', [rid])
        con.commit()
        return jsonify({'ok': True})
    except Exception as e:
        con.rollback()
        return jsonify({'error': 'ลบไม่สำเร็จ: ' + str(e)}), 500
    finally:
        con.close()

@app.route('/api/records', methods=['DELETE'])
def delete_all():
    con = get_db()
    try:
        atts = con.execute('SELECT stored_name FROM attachments').fetchall()
        for att in atts:
            _safe_remove(_stored_path(att['stored_name']))
        con.execute('DELETE FROM records')
        con.commit()
        return jsonify({'ok': True})
    except Exception as e:
        con.rollback()
        return jsonify({'error': 'ลบไม่สำเร็จ: ' + str(e)}), 500
    finally:
        con.close()


# ────────────────────────────────────────────────
# Routes — Attachments
# ────────────────────────────────────────────────

@app.route('/api/records/<int:rid>/attachments', methods=['GET'])
def list_attachments(rid):
    con = get_db()
    rows = con.execute(
        'SELECT id, record_id, book_no, filename, mime_type, file_size, uploaded_at '
        'FROM attachments WHERE record_id=? ORDER BY book_no', [rid]
    ).fetchall()
    con.close()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/records/<int:rid>/attachments', methods=['POST'])
def upload_attachment(rid):
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

    safe_name   = secure_filename(filename) or f'book_{book_no}.pdf'
    stored_name = uuid.uuid4().hex + '.pdf'
    file_path   = _stored_path(stored_name)

    # เขียนไฟล์ลงดิสก์
    try:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        with open(file_path, 'wb') as fh:
            fh.write(file_bytes)
    except Exception as e:
        return jsonify({'error': 'ไม่สามารถบันทึกไฟล์ได้: ' + str(e)}), 500

    mime = 'application/pdf'
    size = len(file_bytes)
    con  = get_db()
    try:
        # ลบไฟล์เก่าออกจาก disk ถ้ามีอยู่
        old = con.execute(
            'SELECT stored_name FROM attachments WHERE record_id=? AND book_no=?',
            [rid, book_no]
        ).fetchone()
        if old and old['stored_name']:
            _safe_remove(_stored_path(old['stored_name']))

        con.execute('DELETE FROM attachments WHERE record_id=? AND book_no=?', [rid, book_no])
        cur = con.execute(
            'INSERT INTO attachments '
            '(record_id, book_no, filename, stored_name, mime_type, file_size, uploaded_at) '
            'VALUES (?,?,?,?,?,?,?)',
            [rid, book_no, safe_name, stored_name, mime, size, now_str()]
        )
        con.commit()
        return jsonify({'id': cur.lastrowid, 'ok': True, 'filename': safe_name, 'size': size}), 201
    except Exception as e:
        con.rollback()
        _safe_remove(file_path)  # rollback ไฟล์ที่เพิ่งเขียน
        return jsonify({'error': 'อัปโหลดล้มเหลว: ' + str(e)}), 500
    finally:
        con.close()

@app.route('/api/attachments/<int:att_id>', methods=['GET'])
def download_attachment(att_id):
    con = get_db()
    row = con.execute('SELECT * FROM attachments WHERE id=?', [att_id]).fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    fp = _stored_path(row['stored_name'])
    if not os.path.isfile(fp):
        return jsonify({'error': 'ไฟล์หายจาก storage'}), 404
    return send_file(fp, mimetype=row['mime_type'],
                     as_attachment=True, download_name=row['filename'])

@app.route('/api/attachments/<int:att_id>/preview', methods=['GET'])
def preview_attachment(att_id):
    con = get_db()
    row = con.execute('SELECT * FROM attachments WHERE id=?', [att_id]).fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    fp = _stored_path(row['stored_name'])
    if not os.path.isfile(fp):
        return jsonify({'error': 'ไฟล์หายจาก storage'}), 404
    return send_file(fp, mimetype=row['mime_type'],
                     as_attachment=False, download_name=row['filename'])

@app.route('/api/attachments/<int:att_id>', methods=['DELETE'])
def delete_attachment(att_id):
    con = get_db()
    try:
        row = con.execute('SELECT stored_name FROM attachments WHERE id=?', [att_id]).fetchone()
        if row:
            _safe_remove(_stored_path(row['stored_name']))
        con.execute('DELETE FROM attachments WHERE id=?', [att_id])
        con.commit()
        return jsonify({'ok': True})
    except Exception as e:
        con.rollback()
        return jsonify({'error': 'ลบไฟล์ล้มเหลว: ' + str(e)}), 500
    finally:
        con.close()


# ────────────────────────────────────────────────
# Global error handlers
# ────────────────────────────────────────────────

@app.errorhandler(400)
def bad_request(e):
    return jsonify({'error': 'Bad Request: ' + str(e)}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not Found: ' + str(e)}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal Server Error: ' + str(e)}), 500

@app.errorhandler(413)
def request_too_large(e):
    return jsonify({'error': 'ไฟล์มีขนาดใหญ่เกินไป — กรุณาลดขนาดไฟล์ให้ต่ำกว่า 150 MB แล้วลองใหม่อีกครั้ง'}), 413


# ────────────────────────────────────────────────
# Stats & Export
# ────────────────────────────────────────────────

@app.route('/api/stats', methods=['GET'])
def stats():
    con = get_db()
    total  = con.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    passed = con.execute("SELECT COUNT(*) FROM records WHERE p5_work_result='ผ่าน'").fetchone()[0]
    by_result = con.execute(
        'SELECT p5_work_result as result, COUNT(*) as cnt FROM records GROUP BY p5_work_result'
    ).fetchall()
    by_level = con.execute(
        'SELECT p2_pos_level as level, COUNT(*) as cnt FROM records GROUP BY p2_pos_level'
    ).fetchall()
    con.close()
    return jsonify({'total': total, 'passed': passed,
                    'by_result': [dict(r) for r in by_result],
                    'by_level':  [dict(r) for r in by_level]})

@app.route('/api/export/csv')
def export_csv_server():
    import csv, io as _io
    con  = get_db()
    rows = con.execute('SELECT * FROM records ORDER BY id DESC').fetchall()
    con.close()
    if not rows:
        return '', 204
    buf = _io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows([dict(r) for r in rows])
    from flask import Response
    return Response('﻿' + buf.getvalue(), mimetype='text/csv; charset=utf-8-sig',
                    headers={'Content-Disposition': f'attachment; filename=hr_promotion_{dstr()}.csv'})


# ────────────────────────────────────────────────
# Desktop launcher
# ────────────────────────────────────────────────

PORT = 5000

def run_flask():
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

def main():
    init_db()
    if HAS_WEBVIEW:
        t = threading.Thread(target=run_flask, daemon=True)
        t.start()
        time.sleep(0.8)
        webview.create_window(
            title='ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง',
            url=f'http://127.0.0.1:{PORT}',
            width=1280, height=820, min_size=(900, 600), resizable=True,
        )
        webview.start(debug=False)
    else:
        print(f'[HR System] เปิด http://127.0.0.1:{PORT}  (Ctrl+C เพื่อหยุด)')
        app.run(host='0.0.0.0', port=PORT, debug=True)

if __name__ == '__main__':
    main()
