"""
ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง
Flask + SQLite3 + pywebview  →  Desktop App (.exe)
"""

import os, sys, sqlite3, threading, time, base64, json
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file
import io

try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024   # 200 MB รองรับ base64 ≈ ×1.33

BASE_DIR = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'hr_promotion.db')

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    con = get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            p1_title         TEXT NOT NULL DEFAULT '',
            p1_fname         TEXT NOT NULL DEFAULT '',
            p1_lname         TEXT NOT NULL DEFAULT '',
            p1_edu           TEXT,
            p1_edu_major     TEXT,
            p2_pos_current   TEXT NOT NULL DEFAULT '',
            p2_pos_level     TEXT,
            p2_empid         TEXT NOT NULL DEFAULT '',
            p2_dept          TEXT,
            p2_sec           TEXT,
            p2_org           TEXT,
            p3_pos           TEXT,
            p3_pos_level     TEXT,
            p3_empid         TEXT,
            p3_dept          TEXT,
            p3_sec           TEXT,
            p3_org           TEXT,
            p3_announcement  TEXT,
            p4_work1_title   TEXT,
            p4_work1_year    TEXT,
            p4_work2_title   TEXT,
            p4_work2_year    TEXT,
            p4_work3_title   TEXT,
            p4_work3_year    TEXT,
            p4_concept_title TEXT,
            p4_work_award    TEXT,
            p5_doc_received  TEXT,
            p5_work_result   TEXT,
            p5_work_score    TEXT,
            p5_work_eval_date TEXT,
            eval51_rows      TEXT NOT NULL DEFAULT '[]',
            eval52_rows      TEXT NOT NULL DEFAULT '[]',
            created          TEXT,
            updated          TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id   INTEGER NOT NULL,
            book_no     INTEGER NOT NULL,
            filename    TEXT    NOT NULL,
            mime_type   TEXT    NOT NULL DEFAULT 'application/pdf',
            file_size   INTEGER,
            file_data   BLOB    NOT NULL,
            uploaded_at TEXT,
            FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE
        )
    """)
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

def now_str():
    return datetime.now().strftime('%d/%m/%Y %H:%M')

def dstr():
    return datetime.now().strftime('%Y%m%d')

def row_to_dict(row):
    return dict(row)

def sanitize_record(d: dict) -> dict:
    """แปลง null/None -> '' และตรวจ eval_rows ให้เป็น JSON array string เสมอ"""
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


# ===== RECORDS API =====

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
        cur    = con.execute(f'INSERT INTO records ({cols}) VALUES ({plh})', list(allowed.values()))
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
            return jsonify({'error': 'ไม่พบข้อมูล id=' + str(rid) + ' (อาจถูกลบไปแล้ว)'}), 404
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
        con.execute('DELETE FROM records')
        con.commit()
        return jsonify({'ok': True})
    except Exception as e:
        con.rollback()
        return jsonify({'error': 'ลบไม่สำเร็จ: ' + str(e)}), 500
    finally:
        con.close()


# ===== ATTACHMENTS API =====

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
        book_no  = int(body.get('book_no', 1))
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
        book_no    = int(request.form.get('book_no', 1))
        filename   = f.filename or f'book_{book_no}.pdf'
        file_bytes = f.read()
    else:
        return jsonify({'error': 'no file provided'}), 400

    mime = 'application/pdf'
    size = len(file_bytes)
    con  = get_db()
    try:
        con.execute('DELETE FROM attachments WHERE record_id=? AND book_no=?', [rid, book_no])
        cur = con.execute(
            'INSERT INTO attachments '
            '(record_id, book_no, filename, mime_type, file_size, file_data, uploaded_at) '
            'VALUES (?,?,?,?,?,?,?)',
            [rid, book_no, filename, mime, size, file_bytes, now_str()]
        )
        con.commit()
        return jsonify({'id': cur.lastrowid, 'ok': True, 'filename': filename, 'size': size}), 201
    except Exception as e:
        con.rollback()
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
    return send_file(io.BytesIO(row['file_data']), mimetype=row['mime_type'],
                     as_attachment=True, download_name=row['filename'])

@app.route('/api/attachments/<int:att_id>/preview', methods=['GET'])
def preview_attachment(att_id):
    con = get_db()
    row = con.execute('SELECT * FROM attachments WHERE id=?', [att_id]).fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return send_file(io.BytesIO(row['file_data']), mimetype=row['mime_type'],
                     as_attachment=False, download_name=row['filename'])

@app.route('/api/attachments/<int:att_id>', methods=['DELETE'])
def delete_attachment(att_id):
    con = get_db()
    try:
        con.execute('DELETE FROM attachments WHERE id=?', [att_id])
        con.commit()
        return jsonify({'ok': True})
    except Exception as e:
        con.rollback()
        return jsonify({'error': 'ลบไฟล์ล้มเหลว: ' + str(e)}), 500
    finally:
        con.close()


# ===== Global error handlers =====

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


# ===== STATS & EXPORT =====

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
    import csv
    con  = get_db()
    rows = con.execute('SELECT * FROM records ORDER BY id DESC').fetchall()
    con.close()
    if not rows:
        return '', 204
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows([dict(r) for r in rows])
    from flask import Response
    bom = '﻿'
    return Response(bom + buf.getvalue(), mimetype='text/csv; charset=utf-8-sig',
                    headers={'Content-Disposition': f'attachment; filename=hr_promotion_{dstr()}.csv'})


# ===== DESKTOP LAUNCHER =====

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
