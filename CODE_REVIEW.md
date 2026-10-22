# Code Review: hr_promotion

**ไฟล์ที่รีวิว:** [app.py](app.py), [templates/index.html](templates/index.html)
**วันที่รีวิว:** 16/07/2026
**เกณฑ์:** ตาม persona ใน [Python-Code-Reviewer.md](Python-Code-Reviewer.md) — Quality / Performance / Security / Best practices

---

## Overall Assessment

โค้ดโดยรวมมีโครงสร้างที่ดีสำหรับ desktop app ขนาดเล็ก — มีการป้องกัน SQL injection ด้วย whitelist + placeholder อย่างถูกต้อง, ลำดับการลบไฟล์/DB ออกแบบมาอย่างคิดมาแล้ว, และ migration เขียนแบบระวังข้อมูลเดิม จุดอ่อนหลักอยู่ที่**ฝั่งความปลอดภัยเมื่อหลุดออกจากโหมด desktop** (fallback mode เปิด debug + 0.0.0.0), **Stored XSS ฝั่ง frontend** ซึ่งเป็นช่องโหว่จริงแม้ในโหมด desktop, และปัญหา compatibility/ประสิทธิภาพอีกจำนวนหนึ่ง

---

## 🔴 Critical Issues (ต้องแก้ก่อน deploy)

### 1. Fallback mode รันด้วย `debug=True` + `host='0.0.0.0'` — เสี่ยง RCE และเปิดข้อมูล HR สู่ network

- **Severity:** Critical
- **Location:** `app.py` บรรทัด ~977 ใน `main()`
- **Issue:** เมื่อเครื่องไม่มี pywebview แอปจะรัน `app.run(host='0.0.0.0', port=PORT, debug=True)` — เปิดรับ connection จากทุกเครื่องใน network พร้อม Werkzeug debugger
- **Why it matters:** ปัญหาซ้อนกัน 2 ชั้น (1) Werkzeug interactive debugger มีคอนโซลรัน Python ได้ — ใครก็ตามใน LAN ที่เดา debugger PIN สำเร็จจะรันโค้ดบนเครื่องได้ทันที (Remote Code Execution) (2) ต่อให้ไม่โดน RCE ข้อมูลบุคคล HR ทั้งหมดก็เปิดให้ทุกเครื่องใน network อ่าน/ลบได้ผ่าน API โดยไม่มี authentication เลย (รวมถึง `DELETE /api/records` ที่ลบทั้งระบบ)
- **Suggested fix:**

```python
else:
    print(f'[HR System] เปิด http://127.0.0.1:{PORT}  (Ctrl+C เพื่อหยุด)')
    debug = os.environ.get('HR_DEBUG') == '1'
    app.run(host='127.0.0.1', port=PORT, debug=debug)
```

### 2. Stored XSS — ข้อมูลผู้ใช้ถูกฉีดเข้า HTML ตรง ๆ โดยไม่ escape

- **Severity:** Critical
- **Location:** `templates/index.html` — `renderTable` (~บรรทัด 1363), `printReport` (~1144, ผ่าน `document.write`), dashboard stat cards (~1572) และ labels กราฟ
- **Issue:** ค่าจากฟอร์ม เช่น `r.p1_fname`, `r.p2_dept` ถูกต่อเข้า template string แล้วเซ็ตเป็น `innerHTML` / `document.write` โดยตรง — ถ้ามีคนกรอกชื่อเป็น `<img src=x onerror="fetch('/api/records',{method:'DELETE'})">` สคริปต์จะทำงานทุกครั้งที่เปิดตารางหรือพิมพ์รายงาน
- **Why it matters:** เป็น XSS แบบ persistent (ฝังใน DB) — สคริปต์ที่ฝังจะรันในเครื่องผู้ใช้และเรียก API ลบข้อมูลทั้งหมดได้เงียบ ๆ ยิ่งถ้าไฟล์ DB ถูกแชร์ข้ามเครื่อง payload จะติดไปด้วย
- **Suggested fix:** เพิ่มฟังก์ชัน escape แล้วครอบทุกค่าที่มาจากข้อมูลผู้ใช้ก่อนฉีดเข้า HTML

```javascript
function esc(s){
  return String(s ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
// ตัวอย่างใน renderTable:
`<td>${esc(r.p2_empid)||'-'}</td>
 <td>${esc(r.p1_title)||''}${esc(r.p1_fname)||''} ${esc(r.p1_lname)||''}</td>`
```

---

## 🟠 Important Issues (ควรแก้โดยเร็ว)

### 3. `JSON_AS_ASCII` ถูกถอดออกจาก Flask 2.3+ — ภาษาไทยใน JSON จะกลับไปเป็น `\uXXXX`

- **Severity:** Important
- **Location:** `app.py` บรรทัด ~25
- **Issue:** `app.config['JSON_AS_ASCII'] = False` เป็น config ของ Flask รุ่นเก่า ตั้งแต่ Flask 2.3 กลไก JSON เปลี่ยนเป็น `app.json` provider แล้ว config key เก่าจะถูก**เพิกเฉยเงียบ ๆ** (ไม่ error)
- **Why it matters:** โปรเจกต์นี้กำหนดว่า "Thai JSON required everywhere" — ถ้าอัปเกรด Flask เมื่อไร ภาษาไทยใน response จะกลายเป็น escape sequence ทั้งหมดโดยไม่มีสัญญาณเตือน (silent breakage)
- **Suggested fix:**

```python
app.config['JSON_AS_ASCII'] = False          # Flask < 2.3
if hasattr(app, 'json'):
    app.json.ensure_ascii = False            # Flask >= 2.2/2.3
```

### 4. `secure_filename()` ทำลายชื่อไฟล์ภาษาไทย

- **Severity:** Important
- **Location:** `app.py` บรรทัด ~708 ใน `upload_attachment`
- **Issue:** `secure_filename()` ของ Werkzeug ตัดอักขระ non-ASCII ทิ้งทั้งหมด — ไฟล์ชื่อ `ผลงานเล่มที่1.pdf` จะเหลือ `1.pdf` หรือ `pdf` เฉย ๆ แล้วชื่อที่พังนี้ถูกเก็บลง DB และใช้เป็น `download_name` ตอนดาวน์โหลด
- **Why it matters:** ระบบราชการไทยไฟล์แทบทั้งหมดตั้งชื่อภาษาไทย ผู้ใช้จะได้ไฟล์ชื่อเพี้ยน/ซ้ำกันจนแยกไม่ออก — ทั้งที่ `secure_filename` ไม่จำเป็นที่นี่ เพราะชื่อไฟล์บนดิสก์คือ UUID (`stored_name`) อยู่แล้ว ชื่อต้นฉบับไม่เคยถูกใช้เป็น path จึงไม่มีความเสี่ยง path traversal ตั้งแต่ต้น
- **Suggested fix:** เก็บชื่อต้นฉบับ โดยล้างเฉพาะอักขระอันตราย

```python
import re
def sanitize_display_name(name: str, fallback: str) -> str:
    """ล้างเฉพาะตัวคั่น path และอักขระควบคุม — เก็บภาษาไทยไว้ครบ"""
    name = re.sub(r'[\\/\x00-\x1f]', '', (name or '')).strip()
    return name or fallback

safe_name = sanitize_display_name(filename, f'book_{book_no}.pdf')
```

(Flask `send_file(download_name=...)` encode ชื่อ non-ASCII ตาม RFC 5987 ให้อยู่แล้ว)

### 5. Upload ผ่าน base64-in-JSON กิน memory มหาศาลทั้งสองฝั่ง ทั้งที่ server รองรับ multipart อยู่แล้ว

- **Severity:** Important
- **Location:** `app.py` ~687-699, ฝั่ง client `_readAndCache` / `_uploadToFlask` ใน `templates/index.html`
- **Issue:** ไฟล์ 100 MB → browser อ่านเป็น data-URL ~133 MB → `JSON.stringify` ก๊อปอีกชุด → server parse JSON 133 MB → `b64decode` ได้ bytes อีก 100 MB ค้างใน RAM พร้อมกัน (peak รวมหลายร้อย MB ต่อ 1 ไฟล์)
- **Why it matters:** เป็น bottleneck ที่ทำให้ UI ค้าง/browser tab ตายกับไฟล์ใหญ่ และเป็นเหตุที่ต้องตั้ง `MAX_CONTENT_LENGTH` สูงถึง 200 MB — ที่ย้อนแย้งคือ **server มี branch multipart รองรับอยู่แล้ว** (~บรรทัด 700-704) แต่ frontend ไม่ใช้
- **Suggested fix:** เปลี่ยน `_uploadToFlask` ให้ส่ง `FormData` (แนบ `File` object ตรง ๆ browser จะ stream ให้):

```javascript
async function _uploadToFlask(n, rid){
  const fd = fileCache[n]; if(!fd) return true;
  const form = new FormData();
  form.append('book_no', n);
  form.append('file', fd.file, fd.name);   // เก็บ File object แทน base64 ใน fileCache
  const r = await fetch(`/api/records/${rid}/attachments`, {method:'POST', body:form});
  // ...
}
```

### 6. `_migrate_blobs_to_files` ย้ายซ้ำทุกแถวเมื่อรันซ้ำ — comment กับโค้ดขัดกัน

- **Severity:** Important
- **Location:** `app.py` ~167-176
- **Issue:** comment บอกว่า "ย้าย BLOB → file สำหรับแถวที่ stored_name **ยังว่าง**" แต่ SQL จริงคือ `SELECT id, filename, file_data FROM attachments` — **ไม่มี WHERE** ทุกแถวจึงถูก dump เป็นไฟล์ UUID ใหม่และเขียนทับ `stored_name` เดิมเสมอ
- **Why it matters:** ถ้า migration รอบก่อนล้มกลางทาง (ก่อนถึงขั้น DROP TABLE) การรันซ้ำจะสร้างไฟล์ชุดใหม่ทั้งหมด ทิ้งไฟล์ชุดเก่าเป็นขยะกำพร้าบนดิสก์ และขัดกับที่ออกแบบให้ idempotent
- **Suggested fix:**

```python
rows = con.execute(
    "SELECT id, filename, file_data FROM attachments "
    "WHERE stored_name IS NULL OR stored_name = ''"
).fetchall()
```

### 7. Error response ส่ง `str(e)` ดิบกลับให้ client — information disclosure

- **Severity:** Important
- **Location:** `app.py` — `add_record`, `update_record`, `delete_record`, `upload_attachment`, error handler 500 (ทุกจุดที่ทำ `'...: ' + str(e)`)
- **Issue:** exception message ภายใน (path ของไฟล์, โครงสร้าง SQL) ถูกส่งตรงถึงผู้ใช้
- **Why it matters:** เปิดเผยข้อมูลภายในระบบให้ผู้ที่อาจใช้ประกอบการโจมตี และผู้ใช้ทั่วไปอ่านไม่รู้เรื่องอยู่ดี — ควร log ฝั่ง server แล้วส่งข้อความกลาง ๆ
- **Suggested fix:**

```python
import logging
log = logging.getLogger(__name__)

except Exception:
    con.rollback()
    log.exception('add_record failed')          # รายละเอียดเต็มอยู่ใน log
    return jsonify({'error': 'เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่'}), 500
```

### 8. ไม่ validate ว่าไฟล์ที่อัปโหลดเป็น PDF จริง และไม่จำกัดขนาดฝั่ง server

- **Severity:** Important
- **Location:** `app.py` ~687-721 ใน `upload_attachment`
- **Issue:** server รับ bytes อะไรก็ได้ ตั้งนามสกุล `.pdf` และ hardcode `mime='application/pdf'` — การเช็คนามสกุล/ขนาด 100 MB มีแค่ฝั่ง JavaScript ซึ่ง bypass ได้ง่าย
- **Why it matters:** ไฟล์อะไรก็ได้จะถูก serve กลับด้วย Content-Type ว่าเป็น PDF ผ่าน `/preview` และไฟล์เกือบ 200 MB ผ่านเข้ามาได้แม้ UI จำกัด 100 MB — validation ฝั่ง client ต้องมีสำเนาฝั่ง server เสมอ
- **Suggested fix:**

```python
MAX_PDF_SIZE = 100 * 1024 * 1024
if len(file_bytes) > MAX_PDF_SIZE:
    return jsonify({'error': 'ไฟล์ใหญ่เกิน 100 MB'}), 413
if not file_bytes.startswith(b'%PDF-'):
    return jsonify({'error': 'ไฟล์ไม่ใช่ PDF ที่ถูกต้อง'}), 400
```

### 9. GET routes ไม่มี try/finally — connection รั่วเมื่อ query พัง

- **Severity:** Important
- **Location:** `app.py` — `get_all`, `get_one`, `list_attachments`, `download_attachment`, `preview_attachment`, `stats`, `export_csv_server`
- **Issue:** route ฝั่งเขียนใช้ `try/finally: con.close()` ครบ แต่ route ฝั่งอ่านเรียก `con.close()` แบบบรรทัดตรง — ถ้า `execute()` โยน exception, connection จะไม่ถูกปิด
- **Why it matters:** SQLite โหมด WAL ที่มี connection ค้างจะ hold shared lock ทำให้ไฟล์ `-wal` โตไม่หยุด (checkpoint ไม่ได้) — desktop app ที่รันยาวหลายวันจะสะสมปัญหา
- **Suggested fix:**

```python
from contextlib import closing

@app.route('/api/records', methods=['GET'])
def get_all():
    with closing(get_db()) as con:
        rows = con.execute('SELECT * FROM records ORDER BY id DESC').fetchall()
    return jsonify([row_to_dict(r) for r in rows])
```

---

## 🟡 Suggestions (ปรับปรุงเพิ่มเติม)

### 10. CSV formula injection ใน export ทั้งสองทาง

- **Severity:** Minor
- **Location:** `app.py` `export_csv_server` (~918-924), `templates/index.html` `exportAllCSV` (~1477)
- **Issue:** ค่าที่ขึ้นต้นด้วย `=`, `+`, `-`, `@` จะถูก Excel ตีความเป็นสูตร
- **Why it matters:** เปิดไฟล์ export ใน Excel แล้วสูตรทำงานทันที (ดึงข้อมูลออก/หลอกคลิกลิงก์)
- **Suggested fix:** นำหน้าค่าเสี่ยงด้วย single quote: `if v and v[0] in '=+-@': v = "'" + v`

### 11. ตาราง `attachments` ไม่มี index บน `record_id`

- **Severity:** Minor
- **Location:** `app.py` CREATE TABLE attachments (~288-300)
- **Issue:** query หลัก (`WHERE record_id=?`) และ CASCADE delete ต้อง full scan
- **Why it matters:** ON DELETE CASCADE ที่ไม่มี index บน FK จะช้าลงแบบ O(n) เมื่อไฟล์สะสมมากขึ้น
- **Suggested fix:** เพิ่มใน `init_db()`: `con.execute('CREATE INDEX IF NOT EXISTS idx_att_record ON attachments(record_id)')`

### 12. อัปโหลดไปยัง record ที่ไม่มีอยู่ได้ 500 แทน 404

- **Severity:** Minor
- **Location:** `app.py` `upload_attachment` (~722-746)
- **Issue:** ถ้า `rid` ไม่มีจริง INSERT จะชน FK constraint → ตอบ 500 พร้อมข้อความ IntegrityError
- **Why it matters:** 500 สื่อว่า "server พัง" ทั้งที่จริงคือ client อ้างถึงของที่ไม่มี (404) — ทำให้ debug ฝั่ง frontend หลงทาง
- **Suggested fix:** `if not con.execute('SELECT 1 FROM records WHERE id=?', [rid]).fetchone(): return jsonify({'error': f'ไม่พบข้อมูล id={rid}'}), 404`

### 13. โค้ดชุด `*Ann` ซ้ำกับชุดเล่ม 1-3 เกือบทั้งหมด (~120 บรรทัด)

- **Severity:** Minor
- **Location:** `templates/index.html` — `handleFileSelectAnn` … `previewFileAnn`
- **Issue:** ฟังก์ชัน 8 ตัวถูก copy มาแก้ suffix — ต่างกันแค่ element id (`-ann`) กับ `book_no`
- **Why it matters:** ทุก bugfix ต้องแก้ 2 ที่ (ตอนนี้เริ่มไม่ sync แล้ว เช่น class ของ badge ต่างกันเล็กน้อย)
- **Suggested fix:** รวมเป็นชุดเดียวโดยให้ `n` รับได้ทั้งเลขและ `'ann'` (โครงสร้าง element id รองรับอยู่แล้ว เช่น `uzone-1`/`uzone-ann`) แล้วลบชุด Ann ทิ้ง

### 14. `time.sleep(0.8)` เดาว่า server พร้อม — เปราะ

- **Severity:** Minor
- **Location:** `app.py` `main()` (~968)
- **Issue:** เครื่องช้า/antivirus scan อาจทำให้ 0.8 วินาทีไม่พอ → webview เปิดมาเจอ connection refused
- **Why it matters:** ผู้ใช้ปลายทางเห็นหน้าขาว/error โดยไม่รู้ว่าแค่ต้องรอ — first impression ของ .exe เสีย
- **Suggested fix:**

```python
import socket
for _ in range(50):                       # รอสูงสุด ~5 วินาที
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=0.1):
            break
    except OSError:
        time.sleep(0.1)
```

### 15. โหมด LocalStorage: ไฟล์ที่เลือกหายเงียบ ๆ หลัง reload

- **Severity:** Minor
- **Location:** `templates/index.html` — submit handler + `_readAndCache`
- **Issue:** โหมด offline ไฟล์ค้างสถานะ "⏳ รอบันทึกข้อมูล" ตลอดไป — `fileCache` อยู่แค่ใน memory ไม่เคยถูกเก็บลง LocalStorage และไม่มีการแจ้งว่าโหมดนี้เก็บไฟล์ไม่ได้
- **Why it matters:** ผู้ใช้เข้าใจว่าแนบไฟล์สำเร็จแล้ว แต่เปิดโปรแกรมใหม่ไฟล์หายหมด — data loss แบบเงียบคือ UX bug ที่แย่ที่สุดประเภทหนึ่ง
- **Suggested fix:** ถ้า `!USE_FLASK` ให้ toast เตือนว่า "โหมด Offline ไม่รองรับไฟล์แนบ" ตอนเลือกไฟล์ หรือซ่อน panel อัปโหลดไปเลย

---

## ✅ Positive Highlights

- **การป้องกัน SQL injection ทำถูกหลัก** — dynamic SQL ประกอบชื่อคอลัมน์จาก whitelist `RECORD_COLS` เท่านั้น ค่าผู้ใช้ผ่าน placeholder `?` ทั้งหมด
- **ลำดับการลบไฟล์ก่อนลบ row** ใน `delete_record` คิดเรื่อง CASCADE มาแล้วอย่างถูกต้อง และ `upload_attachment` มี compensating rollback
- **เปิด `PRAGMA foreign_keys=ON` ทุก connection** — จุดที่คนใช้ SQLite พลาดบ่อย ที่นี่ทำถูก
- **BOM ใน CSV export** แก้ปัญหาภาษาไทยใน Excel ตรงจุด ทั้งฝั่ง server และ client
- **Error handler กลางตอบ JSON เสมอ** สอดคล้องกับ contract ของ frontend
- **คอมเมนต์/docstring ละเอียดและอธิบาย "ทำไม"** ทำให้ผู้ดูแลรุ่นถัดไปเข้าใจการตัดสินใจได้โดยไม่ต้องขุด git history

---

## สรุปลำดับความสำคัญ

| ลำดับ | ข้อ | เหตุผล |
|---|---|---|
| แก้ทันที | 1, 2 | RCE / network exposure และ Stored XSS |
| รอบถัดไป | 3, 4 | กระทบผู้ใช้ภาษาไทยโดยตรง (JSON + ชื่อไฟล์) |
| จัดคิว | 5-9 | ประสิทธิภาพ, migration bug, connection leak, validation |
| ตามสะดวก | 10-15 | ปรับปรุงคุณภาพระยะยาว |
