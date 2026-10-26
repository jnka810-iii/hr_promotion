# Code review: hr_promotion (22/10/2026)

**ไฟล์ที่รีวิว:** [app.py](app.py) (980 บรรทัด), [templates/index.html](templates/index.html) (1602 บรรทัด), [check_db.py](check_db.py), [fix_db.py](fix_db.py)
**เกณฑ์:** persona ใน [Python-Code-Reviewer.md](Python-Code-Reviewer.md) — Quality / Performance / Security / Best practices
**ความสัมพันธ์กับรีวิวเดิม:** รีวิวรอบก่อน [CODE_REVIEW.md](CODE_REVIEW.md) (16/07/2026) ยังไม่มีการแก้โค้ดใด ๆ
ทุกข้อในรีวิวเดิมจึงถูกตรวจซ้ำกับโค้ดปัจจุบันในรอบนี้ ไฟล์เดิมเก็บไว้เป็นประวัติ ให้ใช้ไฟล์นี้เป็นฉบับปัจจุบัน

**สิ่งที่เพิ่มขึ้นจากรอบก่อน:** พบประเด็นใหม่ 10 ข้อ (ข้อ 1, 4, 7, 13, 14, 17, 18, 20, 24, 25),
ยืนยัน 3 ข้อด้วยการรันโค้ดจริงแทนการอ่านเอา (ข้อ 5, 6, 14), ปรับระดับความรุนแรงของข้อ 5 ใหม่ตามผลที่วัดได้,
และแก้ข้อที่รีวิวเดิมระบุเกินจริง 2 จุด (ดูหัวข้อ "ข้อแก้ไขจากรีวิว 16/07/2026")

---

## Overall assessment

แกนหลักของโค้ดยังแข็งอยู่ การป้องกัน SQL injection ทำถูกหลัก ลำดับการลบไฟล์กับ DB คิดมาแล้ว
และคอมเมนต์อธิบาย "ทำไม" ได้ดีกว่าโปรเจกต์ขนาดนี้ทั่วไป ปัญหาที่พบไม่ใช่เรื่องสไตล์โค้ด
แต่เป็นเรื่องที่สมมติฐานของโค้ดกับความจริงไม่ตรงกัน จุดที่หนักที่สุดคือโปรเจกต์นี้ตั้งเป้าเป็น
desktop `.exe` สำหรับใช้ในหน่วยงาน แต่ frontend ดึง Bootstrap, Chart.js, Font Awesome และ font Sarabun
จาก CDN ภายนอกทั้งหมด แอปจึงใช้งานไม่ได้จริงเมื่อเครื่องไม่มีอินเทอร์เน็ต ซึ่งเป็นสถานการณ์ปกติ
ของเครื่องในหน่วยงานราชการ ถัดมาคือ fallback mode ที่เปิด `debug=True` บน `0.0.0.0`,
Stored XSS ที่ยังไม่ได้แก้, migration ที่ทำลายข้อมูล BLOB ที่เขียนไฟล์ไม่สำเร็จ,
และคอมเมนต์ในโค้ดหลายจุดที่อ้างคุณสมบัติ (idempotent, DB เป็น source of truth) ที่โค้ดจริงไม่มี

---

## ผลที่ยืนยันด้วยการรันโค้ดจริง

สามข้อนี้ไม่ใช่การอนุมานจากการอ่านโค้ด แต่รันกับ `.venv` ของโปรเจกต์ (Flask 3.1.3) แล้วบันทึก output จริง

| สิ่งที่ทดสอบ | ผลที่วัดได้ |
|---|---|
| `jsonify({'name': 'สมชาย'})` | `{"name":"สมชาย"}` — `app.json.ensure_ascii` เป็น `True` ทั้งที่ตั้ง `JSON_AS_ASCII = False` |
| `secure_filename('ผลงานเล่มที่1.pdf')` | `'1.pdf'` |
| `secure_filename('ประกาศ.pdf')` | `'pdf'` — นามสกุลไฟล์หายทั้งหมด |
| header ของ `GET /api/export/csv` | `text/csv; charset=utf-8-sig; charset=utf-8` — มี `charset` สองตัว |

---

## Critical issues (ต้องแก้ก่อนส่งมอบ)

> **อัปเดต 22/10/2026** Critical ทั้ง 4 ข้อแก้และยืนยันด้วย repro จริงครบแล้ว

### 1. แอปที่ขายเป็น desktop .exe พึ่ง CDN ภายนอก 4 ตัว และพังเมื่อออฟไลน์

- **สถานะ:** แก้แล้ว 22/10/2026 ย้ายไลบรารีทั้งหมดมา self-host ใน `static/vendor/` (25 ไฟล์ รวม 1.1 MB)
  ได้แก่ Bootstrap 5.3.2 (CSS + JS bundle), Chart.js 4.4.1, Font Awesome 6.4.2 (CSS + webfonts 4 ไฟล์)
  และ Sarabun จาก Google Fonts (CSS + woff2 16 ไฟล์ ครบ 4 น้ำหนัก × 4 subset รวม thai)
  `templates/index.html` อ้างผ่าน `url_for('static', ...)` ทุกจุด ส่วน `printReport` ใช้
  `${location.origin}/static/vendor/sarabun.css` เพราะ popup เป็น `about:blank` ซึ่ง relative URL อาจไม่ resolve
  ยืนยันในเบราว์เซอร์จริงว่า `performance.getEntriesByType('resource')` มี host เดียวคือ `127.0.0.1:5050`
  จาก 14 request ไม่มี host ภายนอกเลย `typeof Chart` เป็น `function`, `typeof bootstrap` เป็น `object`,
  `document.fonts.check` ผ่านทั้ง Sarabun และ Font Awesome 6 Free, กราฟวาดได้ 4 ตัว,
  `.row` เป็น flex และ `.btn` มี border-radius 8px (Bootstrap CSS ทำงาน), ไอคอน `i.fa` กว้าง 30px
  **ยังต้องทำต่อตอน build** PyInstaller ต้องแพ็ก `static/vendor` ไปด้วย ไม่งั้น `.exe` จะกลับไปพังแบบเดิม
  ดูคำสั่งใน [spec.md](spec.md)
- **Severity:** Critical
- **Location:** `templates/index.html:8-12`, `:500`, `:1146` (CDN), `:635` (`new bootstrap.Toast`), `:1522`, `:1538`, `:1586` (`new Chart`)
- **Issue:** หน้าเดียวนี้โหลด Bootstrap CSS, Bootstrap JS bundle, Font Awesome, Chart.js และ Google Fonts Sarabun
  จาก `cdnjs.cloudflare.com` และ `fonts.googleapis.com` ทั้งหมด โค้ดเรียก global `bootstrap` และ `Chart`
  รวม 4 จุด โดยไม่มี guard ใดเลย ตรวจแล้วด้วย grep ว่าไม่มี `typeof Chart` หรือ `typeof bootstrap` อยู่ในไฟล์
- **Why it matters:** ผลเมื่อเครื่องไม่มีเน็ตไม่ใช่แค่ "หน้าตาเพี้ยน" แต่ฟังก์ชันตายเป็นลำดับ
  Bootstrap CSS หายทำให้ layout ทั้งหน้าพัง, Chart.js หายทำให้ `new Chart(...)` โยน ReferenceError
  แท็บ dashboard จึงใช้ไม่ได้, และที่หนักสุดคือ `toast()` เรียก `new bootstrap.Toast` ซึ่งหมายความว่า
  **ช่องทางแจ้ง error ให้ผู้ใช้เองก็พังไปด้วย** ผู้ใช้จะเจอฟอร์มที่กดบันทึกแล้วไม่มีอะไรเกิดขึ้น
  ไม่มีข้อความบอกสาเหตุ ทั้งหมดนี้ขัดกับเป้าหมายของโปรเจกต์ที่ระบุใน CLAUDE.md ว่าแพ็กเป็น `.exe` ให้ผู้ใช้รัน
  นอกจากนี้การไม่มี `integrity` (SRI) บน `<script>` ทำให้ไฟล์ที่ถูกแก้บน CDN รันโค้ดได้บนเครื่องผู้ใช้
  โดยที่ API ฝั่ง local ไม่มี authentication กั้นเลย
- **Suggested fix:** ดาวน์โหลดไลบรารีทั้ง 4 ลง `static/vendor/` แล้วอ้างผ่าน `url_for` เพื่อให้ PyInstaller แพ็กไปด้วย

```html
<link rel="stylesheet" href="{{ url_for('static', filename='vendor/bootstrap.min.css') }}">
<link rel="stylesheet" href="{{ url_for('static', filename='vendor/fontawesome/all.min.css') }}">
<script src="{{ url_for('static', filename='vendor/chart.umd.min.js') }}"></script>
<script src="{{ url_for('static', filename='vendor/bootstrap.bundle.min.js') }}"></script>
```

font Sarabun ก็ควร self-host เป็นไฟล์ `.woff2` ใน `static/vendor/fonts/` ด้วย เพราะรายงานที่พิมพ์ออกมา
(`printReport`) พึ่ง font นี้โดยตรง ถ้าโหลดไม่ได้จะพิมพ์ด้วย font สำรองที่จัดบรรทัดไทยต่างไปจากที่ออกแบบ

### 2. Fallback mode รันด้วย `debug=True` บน `host='0.0.0.0'`

- **สถานะ:** แก้แล้ว 22/10/2026 `app.py:980` ตอนนี้เป็น
  `app.run(host='127.0.0.1', port=PORT, debug=os.environ.get('HR_DEBUG') == '1')`
  ยืนยันด้วย repro ที่ดัก kwargs ของ `app.run` ก่อนแก้ได้ `host='0.0.0.0', debug=True` หลังแก้ได้
  `host='127.0.0.1', debug=False` และเมื่อตั้ง `HR_DEBUG=1` ได้ `host='127.0.0.1', debug=True`
  คือ debugger เปิดได้เฉพาะตอนที่ขอ และยังเข้าถึงได้จากเครื่องตัวเองเท่านั้น
- **Severity:** Critical
- **Location:** `app.py:977`
- **Issue:** `app.run(host='0.0.0.0', port=PORT, debug=True)` ยังอยู่ครบตามรีวิวเดิม ยังไม่มีการแก้
- **Why it matters:** ปัญหาซ้อนสองชั้น ชั้นแรกคือ Werkzeug interactive debugger มีคอนโซลรัน Python ได้
  ใครในวง LAN ที่เดา debugger PIN ผ่านจะรันโค้ดบนเครื่องได้ทันที ชั้นที่สองคือแม้ไม่โดน RCE
  ข้อมูลบุคคลทั้งหมดก็เปิดให้ทุกเครื่องในเครือข่ายอ่านและลบผ่าน API ได้โดยไม่มี authentication
  รวมถึง `DELETE /api/records` ที่ล้างข้อมูลทั้งระบบ
  ที่ต้องย้ำคือ `run_flask()` (บรรทัด 944) ผูก `127.0.0.1` ไว้ถูกแล้วและคอมเมนต์ก็อธิบายเหตุผลไว้ด้วย
  แต่ branch fallback ที่อยู่ห่างกัน 34 บรรทัดทำตรงข้ามทุกอย่าง
- **Suggested fix:**

```python
else:
    print(f'[HR System] เปิด http://127.0.0.1:{PORT}  (Ctrl+C เพื่อหยุด)')
    app.run(host='127.0.0.1', port=PORT, debug=os.environ.get('HR_DEBUG') == '1')
```

### 3. Stored XSS ใน `renderTable` และ `printReport`

- **สถานะ:** แก้แล้ว 22/10/2026 เพิ่มฟังก์ชัน `esc()` ใน `templates/index.html:627-630`
  แล้วครอบทุกค่าที่มาจากผู้ใช้ใน `renderTable` (10 จุด) และ `printReport` (25 จุด รวมค่าในแถว 5.1/5.2)
  ยืนยันในเบราว์เซอร์จริงด้วย payload `<img src=x onerror=...>` ที่ฝังใน DB
  ก่อนแก้ `renderTable` สร้าง `<img>` จริง 2 ตัวและ `onerror` ทำงาน 2 ครั้ง ส่วน `printReport`
  ส่ง HTML ที่มี `<img src=x onerror=` ดิบ 2 จุดเข้า `document.write`
  หลังแก้ทั้งสองจุดเป็น 0 และ `printReport` ได้ `&lt;img src=x onerror=` 2 จุดแทน
  **ไม่ได้แก้** `onclick="editRec('${rid}')"` และ `delRec` ที่บรรทัด 1387-1388 เพราะ `rid` ไม่ใช่ข้อมูลจากผู้ใช้
  (เป็น integer ของ SQLite หรือ id ที่ `uid()` สร้างซึ่งมีแต่ตัวอักษรกับตัวเลข) และการใส่ `esc()`
  ตรงนั้นจะให้ความอุ่นใจผิด ๆ เพราะ `&#39;` ถูกถอดกลับเป็น `'` ก่อน JavaScript จะ parse
  ถ้าจะปิดช่องนี้ให้ถาวรต้องเปลี่ยนไปใช้ `data-rid` กับ event delegation ซึ่งอยู่นอกขอบเขตของข้อนี้
- **Severity:** Critical
- **Location:** `templates/index.html:1363` (`tbody.innerHTML`), `:1144` (`win.document.write`)
- **Issue:** ค่าจากฟอร์มถูกต่อเข้า template string แล้วเซ็ตเป็น `innerHTML` และ `document.write` โดยไม่ escape
  ใน `renderTable` ได้แก่ `p2_empid`, `p1_title`, `p1_fname`, `p1_lname`, `p2_pos_current`, `p2_pos_level`,
  `p2_dept`, `p2_sec`, `created` ใน `printReport` ครอบทุกฟิลด์ในฟอร์มรวมถึงค่าในแถวประเมิน 5.1/5.2
  (`date`, `score`, `grade`, `remark`) ซึ่งมาจาก JSON ที่ server เก็บโดยไม่ตรวจเนื้อหาภายใน list
- **Why it matters:** เป็น XSS แบบ persistent ที่ฝังใน DB กรอกชื่อเป็น
  `<img src=x onerror="fetch('/api/records',{method:'DELETE'})">` แล้วสคริปต์จะรันทุกครั้งที่เปิดแท็บรายการ
  หรือสั่งพิมพ์รายงาน API ไม่มี authentication จึงเรียกลบข้อมูลทั้งระบบได้สำเร็จ และถ้าไฟล์ `.db`
  ถูกส่งต่อข้ามเครื่อง payload จะติดไปด้วย
- **Suggested fix:** เพิ่มฟังก์ชัน escape ตัวเดียวแล้วครอบทุกค่าที่มาจากผู้ใช้ก่อนต่อเข้า HTML

```javascript
function esc(s){
  return String(s ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
```

จุดที่ปลอดภัยอยู่แล้วและไม่ต้องแก้คือ `showFileUI` (`:848`) และ `toast` (`:629`) เพราะใช้ `textContent`

### 4. Migration ทำลาย BLOB ที่เขียนไฟล์ไม่สำเร็จ แล้ว commit ต่อเหมือนไม่มีอะไรเกิดขึ้น

- **สถานะ:** แก้แล้ว 22/10/2026 `app.py:170-177` สำรอง `.db` เป็น `<db>.pre-migration.bak` ก่อนแตะข้อมูล
  และถ้าสำรองไม่ได้จะไม่เริ่ม migration เลย ส่วน `app.py:205-212` เปลี่ยนจาก "ตั้ง `stored_name=''` แล้วไปต่อ"
  เป็นเก็บรายการที่พลาดไว้ แล้ว `con.rollback()` + ลบไฟล์ที่เขียนไปแล้ว + `raise RuntimeError`
  ที่บอกชื่อไฟล์ สาเหตุจาก OS และตำแหน่งไฟล์สำรอง
  ยืนยันด้วย repro ที่ fault-inject ให้การเขียนไฟล์แถวที่ 2 ล้มด้วย `OSError(28, 'No space left on device')`

  | | ก่อนแก้ | หลังแก้ |
  |---|---|---|
  | `init_db()` | ทำจนจบแล้ว commit | `RuntimeError` พร้อมเหตุผล |
  | คอลัมน์ `file_data` | ถูก DROP ทิ้ง | ยังอยู่ |
  | PDF ของเล่ม 2 (815 bytes) | หายถาวร กู้ไม่ได้ | กู้ได้ |
  | ไฟล์สำรอง `.db` | ไม่มี | มี |
  | ไฟล์ค้างบนดิสก์ | 1 ไฟล์กำพร้า | 0 (เก็บกวาดแล้ว) |

  ทดสอบ happy path ด้วยว่า migration ปกติยังย้าย BLOB ได้ครบ ไฟล์บนดิสก์ตรงกับต้นฉบับทุกไบต์
  ชื่อไฟล์ภาษาไทยยังอยู่ และเปิดแอปรอบสองไม่ทำให้ไฟล์งอกเพิ่ม
  ผลพลอยได้คือ **ข้อ 8 หมดพิษไปด้วย** เพราะเมื่อ migration เป็น all-or-nothing แล้ว
  สถานะ "ย้ายไปได้ครึ่งทาง" ที่ทำให้รันซ้ำแล้วเกิดไฟล์กำพร้าก็เกิดไม่ได้อีก
  แต่ SQL ที่ขาด `WHERE` ตามข้อ 8 ยังอยู่เหมือนเดิม ยังไม่ได้แก้
- **Severity:** Critical (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `app.py:169-176` (`except: stored = ''`), `:210-211` (`DROP TABLE attachments` แล้ว `RENAME`)
- **Issue:** ใน `_migrate_blobs_to_files` ถ้าเขียนไฟล์ลงดิสก์ล้มเหลว โค้ดจับ exception แล้วตั้ง `stored_name = ''`
  แล้วเดินหน้าต่อ ไม่กี่บรรทัดถัดมาคือ `DROP TABLE attachments` ซึ่งลบคอลัมน์ `file_data` ทั้งคอลัมน์
  BLOB ที่เขียนไม่สำเร็จจึงหายไปตลอดกาล และ `con.commit()` ปิดท้ายให้
- **Why it matters:** เงื่อนไขที่ทำให้เขียนไฟล์ไม่สำเร็จคือดิสก์เต็ม, antivirus ล็อกโฟลเดอร์, สิทธิ์เขียนไม่พอ
  ซึ่งทั้งสามข้อเกิดบ่อยบนเครื่องผู้ใช้ปลายทาง ไม่ใช่กรณีทฤษฎี ผลคือ PDF ผลงานหายถาวรแบบที่ผู้ใช้ไม่รู้ตัว
  เห็นแค่ข้อความ "ไฟล์หายจาก storage" ตอนกดดาวน์โหลด ซึ่งเป็นเวลาที่สายไปแล้ว
  คอมเมนต์ที่บรรทัด 143 เขียนว่า "ถ้าเขียนไฟล์พลาด ให้ stored_name='' — ข้อมูล metadata ยังอยู่ ไฟล์ค่อยอัปโหลดใหม่"
  แสดงว่าผู้เขียนรู้ว่าไฟล์จะหาย แต่ migration ไม่ได้บอกผู้ใช้ และไม่มีการสำรองไฟล์ `.db` ก่อนทำลายข้อมูล
- **Suggested fix:** ให้ migration ล้มทั้งยกเมื่อเขียนไฟล์ไหนไม่ผ่าน แล้วปล่อยให้ `.db` เดิมอยู่ครบเพื่อลองใหม่ได้

```python
import shutil
shutil.copy2(DB_PATH, DB_PATH + '.pre-migration.bak')   # สำรองก่อนแตะข้อมูล

failed = []
for row in rows:
    stored = uuid.uuid4().hex + '.pdf'
    try:
        data = bytes(row['file_data']) if row['file_data'] else b''
        with open(_stored_path(stored), 'wb') as f:
            f.write(data)
    except Exception as e:
        failed.append((row['id'], str(e)))
        continue
    con.execute("UPDATE attachments SET stored_name=? WHERE id=?", [stored, row['id']])

if failed:
    con.rollback()
    raise RuntimeError(f'migration ล้มเหลว เขียนไฟล์ไม่สำเร็จ {len(failed)} รายการ: {failed}')
```

---

## Important issues (ควรแก้ในรอบถัดไป)

> **อัปเดต 22/10/2026** แก้แล้ว 6 ข้อจาก 9 ข้อ (ข้อ 5, 6, 7, 10, 12, 13) ที่เหลือคือข้อ 8, 9, 11

### 5. `JSON_AS_ASCII = False` ไม่มีผลแล้วบน Flask 3.1.3 ที่ติดตั้งอยู่จริง

- **สถานะ:** แก้แล้ว 22/10/2026 เปลี่ยน `app.config['JSON_AS_ASCII'] = False` เป็น
  `app.json.ensure_ascii = False` ([app.py](app.py):21-28) ไม่เก็บ config key เดิมไว้ควบคู่
  เพราะ `requirements.txt` pin `flask==3.1.3` แล้ว (ข้อ 24) การทิ้ง key ที่ Flask เพิกเฉยไว้
  คือการทิ้งโค้ดที่ดูเหมือนทำงานแต่ไม่ทำอะไรเลย ซึ่งเป็นต้นเหตุของข้อนี้ตั้งแต่แรก

  ยืนยันด้วยการรันจริง `app.json.ensure_ascii` เป็น `False` และ
  `jsonify({'name': 'สมชาย'})` ส่ง byte เป็น UTF-8 ตรง ๆ (`\xe0\xb8\xaa...`) ไม่ใช่ `\uXXXX` อีกแล้ว
  วัดขนาด `GET /api/records` กับ `hr_promotion.db` จริง (อ่านอย่างเดียว ไม่เขียน) 4 records

  | `ensure_ascii` | ขนาด response |
  |---|---|
  | `True` (พฤติกรรมเดิม) | 13,668 bytes |
  | `False` (ตอนนี้) | 10,557 bytes |

  เล็กลง 3,111 bytes = 22.8% จากชุดข้อมูลจริงชุดเดียวกัน และ response ดิบอ่านเป็นไทยได้ด้วยตา
  ทั้งฟิลด์ปกติและไทยที่ซ้อนอยู่ใน JSON string ของ `eval51_rows`/`eval52_rows`
  มี test กันย้อนกลับ 4 ตัวใน `tests/test_api.py` (`-k item5`) ซึ่งพิสูจน์แล้วว่าแดงจริงถ้าย้อนการแก้นี้
- **Severity:** Important (รีวิวเดิมจัดเป็นความเสี่ยงในอนาคต ของจริงคือพังอยู่แล้ววันนี้)
- **Location:** `app.py:25`, `requirements.txt`
- **Issue:** รันทดสอบกับ `.venv` ของโปรเจกต์แล้วได้ `app.json.ensure_ascii` เป็น `True`
  และ `jsonify({'name': 'สมชาย'})` ตอบกลับเป็น `{"name":"สมชาย"}`
  เพราะ Flask ย้ายกลไก JSON ไปที่ `app.json` provider ตั้งแต่ 2.3 แล้ว config key เดิมถูกเพิกเฉยแบบไม่ error
  `requirements.txt` pin ไว้ที่ `flask>=3.1` ซึ่งอยู่เลยจุดนั้นมานานแล้ว
- **Why it matters:** ต้องพูดให้ตรง ผลกระทบต่อ UI ปัจจุบันเท่ากับศูนย์ เพราะ frontend ใช้ `response.json()`
  และ `JSON.parse` ถอด `\uXXXX` กลับเป็นไทยได้ถูกต้อง ราคาที่จ่ายจริงมีสองอย่าง
  อย่างแรกคือ payload โตขึ้น อักษรไทยหนึ่งตัวกลายจาก 3 ไบต์เป็น 6 ไบต์ ทำให้ `GET /api/records`
  ที่ส่งทุก record พร้อมกันหนักขึ้นเกือบเท่าตัวในส่วนที่เป็นข้อความไทย
  อย่างที่สองคือ CLAUDE.md ประกาศว่า "Thai JSON `JSON_AS_ASCII = False` — required everywhere"
  ซึ่งเป็นข้อตกลงที่เอกสารบอกว่าใช้ได้แต่โค้ดไม่ได้ทำ ใครมาอ่าน API ตรง ๆ หรือต่อ client ตัวอื่นในอนาคต
  จะเจอของที่ไม่ตรงกับเอกสาร จึงควรแก้เพื่อให้เอกสารกับโค้ดกลับมาพูดเรื่องเดียวกัน ไม่ใช่เพราะ UI พัง
- **Suggested fix:**

```python
app.config['JSON_AS_ASCII'] = False          # Flask < 2.3
if hasattr(app, 'json'):
    app.json.ensure_ascii = False            # Flask >= 2.3
```

### 6. `secure_filename()` ทำลายชื่อไฟล์ภาษาไทย และบางกรณีตัดนามสกุลหายด้วย

- **สถานะ:** แก้แล้ว 22/10/2026 เลิกใช้ `secure_filename()` เปลี่ยนไปใช้ `sanitize_display_name()`
  ([app.py](app.py):116-136) ที่ตัดเฉพาะตัวคั่น path (`\` `/`) อักขระควบคุม (`\x00-\x1f`) และ `\x7f`
  แล้ว `strip()` กับ clamp ความยาวไว้ที่ 255 ตัวอักษร ถ้าล้างแล้วไม่เหลืออะไรจึงใช้ fallback
  `book_<book_no>.pdf` และลบ `from werkzeug.utils import secure_filename` ออกจาก import แล้ว —
  ทั้งโปรเจกต์ไม่มีจุดใดเรียก `secure_filename` อีก

  ยืนยันด้วยการรันจริงผ่าน Flask test client บน DB ชั่วคราวใน temp dir (ไม่แตะ `hr_promotion.db`)

  | ชื่อไฟล์ที่ผู้ใช้ส่งมา | `secure_filename` (ของเดิม) | `sanitize_display_name` (ตอนนี้) |
  |---|---|---|
  | `ผลงานเล่มที่1.pdf` | `1.pdf` | `ผลงานเล่มที่1.pdf` |
  | `รายงาน ประจำปี 2569.pdf` | `2569.pdf` | `รายงาน ประจำปี 2569.pdf` |
  | `ประกาศ.pdf` | `pdf` | `ประกาศ.pdf` |
  | `../../etc/passwd.pdf` | `etc_passwd.pdf` | `....etcpasswd.pdf` |
  | `a\b/c` + NUL + US + `.pdf` | `a_b_c_.pdf` | `abc.pdf` |
  | `'   '` (ช่องว่างล้วน) | `''` | `book_1.pdf` |

  end-to-end: อัปโหลด `ผลงานเล่มที่1.pdf` ได้ 201 คอลัมน์ `filename` ใน DB เก็บชื่อไทยครบทุกตัวอักษร
  ตอนดาวน์โหลด Flask ส่ง header `attachment; filename=1.pdf;
  filename*=UTF-8''%E0%B8%9C%E0%B8%A5%E0%B8%87%E0%B8%B2%E0%B8%99...` คือมีพารามิเตอร์ RFC 5987
  กำกับไว้ เบราว์เซอร์จึงเซฟเป็นชื่อไทย (ส่วน `filename=` ตัว ASCII fallback ยังเป็น `1.pdf`
  เพราะ Werkzeug สร้างให้เองสำหรับเบราว์เซอร์เก่า ฝั่งเราควบคุมไม่ได้และไม่มีผลกับเบราว์เซอร์ปัจจุบัน)
  กรณีสุดโต่งที่ชื่อไทยยาวเกิน 255 ตัวจะถูกตัดและนามสกุลหาย ยอมรับได้เพราะชื่อนี้เป็นแค่ display name
  path จริงบนดิสก์คือ UUID
- **Severity:** Important
- **Location:** `app.py:708`
- **Issue:** รันจริงกับชื่อไฟล์ไทยได้ผลตามนี้
  `'ผลงานเล่มที่1.pdf'` กลายเป็น `'1.pdf'`, `'รายงาน ประจำปี 2569.pdf'` กลายเป็น `'2569.pdf'`,
  และ `'ประกาศ.pdf'` กลายเป็น `'pdf'` ซึ่งไม่มีจุดนามสกุลเหลืออยู่เลย
  ค่าที่พังนี้ถูกเก็บลงคอลัมน์ `filename` และใช้เป็น `download_name` ตอนดาวน์โหลด
- **Why it matters:** เอกสารราชการไทยตั้งชื่อไฟล์เป็นภาษาไทยแทบทั้งหมด ผู้ใช้จะได้ไฟล์ชื่อ `1.pdf`, `2.pdf`
  ที่แยกจากกันไม่ออก และกรณี `'pdf'` หนักกว่านั้นเพราะ Windows จะไม่รู้ว่าเป็นไฟล์ PDF จึงเปิดไม่ขึ้น
  ที่สำคัญคือ `secure_filename` ไม่จำเป็นที่จุดนี้ตั้งแต่ต้น เพราะชื่อไฟล์บนดิสก์คือ UUID (`stored_name`)
  ชื่อต้นฉบับไม่เคยถูกใช้ประกอบ path จึงไม่มีความเสี่ยง path traversal ให้ป้องกันอยู่แล้ว
- **Suggested fix:** ล้างเฉพาะตัวคั่น path และอักขระควบคุม เก็บภาษาไทยไว้ทั้งหมด

```python
import re

def sanitize_display_name(name: str, fallback: str) -> str:
    """ล้างเฉพาะตัวคั่น path และอักขระควบคุม — เก็บอักษรไทยไว้ครบ"""
    cleaned = re.sub(r'[\\/\x00-\x1f]', '', (name or '')).strip()
    return cleaned or fallback

safe_name = sanitize_display_name(filename, f'book_{book_no}.pdf')
```

`send_file(download_name=...)` ของ Flask encode ชื่อ non-ASCII ตาม RFC 5987 ให้อยู่แล้ว

### 7. `upload_attachment` ลบไฟล์เก่าทิ้งก่อน commit ถ้า DB ล้ม row จะชี้ไปที่ไฟล์ที่ไม่มีอยู่

- **สถานะ:** แก้แล้ว 22/10/2026 เลื่อนการลบไฟล์ไปอยู่หลัง `commit()` ครบทั้ง 4 จุดที่แตะไฟล์บนดิสก์
  - `upload_attachment` ([app.py](app.py):795-816) อ่าน `stored_name` เก่าเก็บไว้ใน `old_path`
    แต่ยังไม่ลบ → DELETE + INSERT → `commit()` → แล้วจึง `_safe_remove(old_path)`
    ส่วน compensating step เดิม (`_safe_remove(file_path)` ใน `except`) ยังอยู่ ทำหน้าที่ลบไฟล์ใหม่ที่เพิ่งเขียน
  - `delete_record` (`:649-657`) และ `delete_all` (`:677-685`) อ่าน `stored_name` ทุกแถวเก็บเป็น list
    ของ path ก่อน (จำเป็น เพราะ `ON DELETE CASCADE` จะกวาดแถว attachments ทิ้งพร้อม record)
    แล้วลบไฟล์วนทีหลัง commit
  - `delete_attachment` (`:897-902`) ใช้ลำดับเดียวกัน
  - docstring ทั้ง 4 จุดเขียนใหม่ให้ตรงกับโค้ด และระบุเหตุผลว่าทำไมต้องลบหลัง commit
    แทนคำอ้าง "DB เป็น source of truth" ลอย ๆ ที่โค้ดเดิมไม่ได้ทำจริง

  ยืนยันด้วย fault injection จริง — ครอบ connection ด้วย proxy ที่โยน `sqlite3.OperationalError`
  เมื่อเจอ SQL ที่กำหนด แล้วตรวจสถานะไฟล์กับ DB หลังจากนั้น (DB ชั่วคราวใน temp dir)

  | สถานการณ์ | ผลที่วัดได้ |
  |---|---|
  | อัปโหลดทับช่องเดิมสำเร็จ | 201, เหลือ row เดียว, ไฟล์เก่าถูกลบ, ไฟล์ที่ row ชี้มีอยู่จริง, ไฟล์บนดิสก์ 1 ไฟล์ |
  | `INSERT INTO attachments` ล้ม | 500, row เก่ายังอยู่ครบ (`stored_name` เดิม), ไฟล์เก่ายังอยู่บนดิสก์, ดาวน์โหลดได้ 200 และ byte ตรงกับของเดิม, ไฟล์ใหม่ที่เพิ่งเขียนถูกเก็บกวาด (ไม่มีไฟล์กำพร้า) |
  | `DELETE FROM records` ล้ม | 500, record ยังอยู่, ไฟล์แนบยังอยู่, ดาวน์โหลดได้ 200 |
  | ลบ record สำเร็จ | 200, ไฟล์ของ record นั้นถูกลบหมด, โฟลเดอร์ uploads ว่าง |
  | `DELETE FROM attachments` ล้ม (ลบไฟล์เดี่ยว) | 500, ไฟล์ยังอยู่, row ยังอยู่ — แล้วลองใหม่ได้ 200 และไฟล์ถูกลบจริง |
  | `delete_all` ล้มและสำเร็จ | ล้ม = 500 ไฟล์ยังอยู่ / สำเร็จ = 200 ไฟล์ถูกลบ โฟลเดอร์ว่าง |

  ไม่มีเคสใดเหลือ row ที่ชี้ไปยังไฟล์ที่ไม่มีอยู่อีก กรณีแย่สุดที่ยังเป็นไปได้คือไฟล์กำพร้า
  (เช่น Windows ล็อกไฟล์ที่กำลังถูก stream อยู่ `_safe_remove` จะกลืน `PermissionError`)
  ซึ่งเสียแค่พื้นที่ดิสก์ตามที่ออกแบบไว้
- **Severity:** Important (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `app.py:730` (`_safe_remove` ไฟล์เก่า) เทียบกับ `:739` (`con.commit()`)
- **Issue:** ลำดับในฟังก์ชันคือ อ่าน `stored_name` เก่า แล้วลบไฟล์เก่าออกจากดิสก์ แล้วจึง `DELETE` + `INSERT` แล้ว commit
  ถ้า DB ล้มระหว่างทาง `except` จะ `rollback()` ซึ่งคืน row เก่ากลับมาใน DB แต่ไฟล์เก่าถูกลบไปแล้วและกู้ไม่ได้
  ส่วน compensating step ที่มีอยู่ (`_safe_remove(file_path)`) ลบแค่ไฟล์ใหม่ที่เพิ่งเขียน ไม่ได้แก้ปัญหานี้
- **Why it matters:** docstring บรรทัด 666 เขียนว่า "ออกแบบให้ DB เป็น source of truth เสมอ"
  และบรรทัด 673 ว่า "เพื่อไม่ให้เกิดไฟล์กำพร้าที่ไม่มี row ชี้ถึง" แต่โค้ดสร้างปัญหาด้านตรงข้ามไว้
  คือ row ที่ชี้ไปยังไฟล์ที่ไม่มีอยู่ ซึ่งแย่กว่าไฟล์กำพร้า เพราะไฟล์กำพร้าเสียแค่พื้นที่ดิสก์
  แต่ row ที่ชี้ไฟล์หายทำให้ผู้ใช้เห็นว่า "มีไฟล์แนบอยู่" แล้วกดดาวน์โหลดได้ 404 ส่วน
  ปัญหาเดียวกันนี้อยู่ใน `delete_record` (`:590`) และ `delete_all` (`:616`) ด้วย ทั้งสองลบไฟล์ก่อน commit เหมือนกัน
- **Suggested fix:** เลื่อนการลบไฟล์เก่าไปหลัง commit สำเร็จ โดยจำ path ไว้ก่อน

```python
old_path = None
try:
    old = con.execute(
        'SELECT stored_name FROM attachments WHERE record_id=? AND book_no=?',
        [rid, book_no]
    ).fetchone()
    if old and old['stored_name']:
        old_path = _stored_path(old['stored_name'])

    con.execute('DELETE FROM attachments WHERE record_id=? AND book_no=?', [rid, book_no])
    cur = con.execute(...)
    con.commit()
    _safe_remove(old_path)        # commit ผ่านแล้วค่อยลบ ไฟล์กำพร้าแย่กว่า row ที่ชี้ไฟล์หาย
    return jsonify({...}), 201
```

### 8. `_migrate_blobs_to_files` ย้ายซ้ำทุกแถวเมื่อรันซ้ำ ขัดกับที่ docstring อ้างว่า idempotent

- **Severity:** Important
- **Location:** `app.py:167`
- **Issue:** คอมเมนต์บรรทัด 166 เขียนว่า "ย้าย BLOB → file สำหรับแถวที่ stored_name ยังว่าง"
  แต่ SQL จริงคือ `SELECT id, filename, file_data FROM attachments` ซึ่งไม่มี `WHERE`
  ทุกแถวจึงถูก dump เป็นไฟล์ UUID ใหม่และเขียนทับ `stored_name` เดิมทุกครั้ง
  และ docstring บรรทัด 140 ก็อ้างว่า "ออกแบบให้ idempotent — รันซ้ำได้ถ้าครั้งก่อนล้มกลางทาง"
- **Why it matters:** ถ้า migration รอบก่อนล้มก่อนถึง `DROP TABLE` การรันซ้ำจะสร้างไฟล์ชุดใหม่ทั้งหมด
  ทิ้งไฟล์ชุดเก่าเป็นขยะกำพร้าบนดิสก์ที่ไม่มีใครลบ และกินพื้นที่เท่าตัวทุกรอบที่ล้ม
- **Suggested fix:**

```python
rows = con.execute(
    "SELECT id, filename, file_data FROM attachments "
    "WHERE stored_name IS NULL OR stored_name = ''"
).fetchall()
```

### 9. ส่ง `str(e)` ดิบกลับให้ client ใน 8 จุด — **แก้แล้ว 26/10/2026**

- **Severity:** Important
- **Location:** `app.py` ใน `add_record` (`:508`, `:511`, `:514`), `update_record` (`:559`, `:562`),
  `delete_record` (`:596`), `delete_all` (`:622`), `upload_attachment` (`:718`, `:744`), `delete_attachment` (`:828`)
- **Issue:** ข้อความ exception ภายในซึ่งมี path ของไฟล์และโครงสร้าง SQL ถูกส่งตรงถึงผู้ใช้
- **Why it matters:** เปิดเผยรายละเอียดภายในระบบให้คนที่อาจใช้ประกอบการโจมตี และผู้ใช้ทั่วไปอ่านไม่รู้เรื่องอยู่ดี
  ที่ทำให้แย่ขึ้นคือ `toastError` (`:644`) เปิด `alert()` ซ้ำเมื่อข้อความยาวเกิน 80 ตัวอักษร
  ซึ่ง stack message ของ sqlite3 มักยาวเกินนั้น ผู้ใช้จึงเจอ popup ที่เต็มไปด้วย path ภายในเครื่อง
- **Suggested fix:** log รายละเอียดเต็มฝั่ง server แล้วตอบข้อความกลาง ๆ ให้ client

```python
import logging
log = logging.getLogger(__name__)

except Exception:
    con.rollback()
    log.exception('add_record failed')
    return jsonify({'error': 'เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่'}), 500
```

### 10. server ไม่ตรวจว่าไฟล์เป็น PDF จริงและไม่จำกัดขนาด

- **สถานะ:** แก้แล้ว 22/10/2026 เพิ่มด่านตรวจใน `upload_attachment` ([app.py](app.py):790-801)
  ทำงาน "ก่อน" เขียนไฟล์ลงดิสก์ ครอบทั้งเส้นทาง multipart และ base64-in-JSON เพราะอยู่หลังจุดที่ branch รวมกัน
  - ไฟล์ว่าง → 400
  - เกิน `MAX_PDF_SIZE` (ค่าคงที่ใหม่ `app.py:34`, 100 MB ตรงกับที่ UI บอกผู้ใช้) → 413
  - ไม่พบ header `%PDF-` ใน 1024 ไบต์แรก → 400

  เลือกสแกน 1024 ไบต์แรกแทนการบังคับว่าต้องอยู่ไบต์ที่ 0 เพราะไฟล์จริงจากเครื่องสแกน
  หรือโปรแกรมบางตัวมี BOM นำหน้า และ PDF reader ก็ผ่อนปรนแบบเดียวกัน (ISO 32000-1 กำหนดไบต์ที่ 0
  แต่ของจริงยอมให้เลื่อนได้) ส่วน `mime = 'application/pdf'` ที่ hardcode ไว้ตอนนี้เป็นจริงแล้ว
  เพราะไฟล์ทุกไฟล์ที่ผ่านเข้ามามี header PDF จริง

  แก้ฝั่ง frontend ด้วย: `_uploadToFlask` และ `_uploadToFlaskAnn` เดิมกลืนเหตุผลจาก server
  แล้วโชว์แค่ "✗ อัปโหลดล้มเหลว" ตอนนี้ `toast()` ข้อความจาก server ให้ผู้ใช้เห็น
  (ด่านฝั่ง client ดูแค่นามสกุลกับขนาด ไฟล์ที่ตั้งชื่อ `.pdf` แต่ข้างในไม่ใช่ PDF
  จะผ่านด่านนั้นมาถึง server ได้ ถ้าไม่โชว์เหตุผลผู้ใช้จะไม่รู้ว่าต้องแก้อะไร)

  ยืนยันด้วย HTTP จริง (รัน Flask บน port 5055 ชี้ DB/uploads ไป temp dir แล้วยิงด้วย `curl`)

  | ที่ส่งไป | ผล |
  |---|---|
  | PDF จริง (multipart) | 201 — ไฟล์ลงดิสก์ ดาวน์โหลดกลับมาเทียบไบต์ตรงกับต้นฉบับ |
  | PDF จริง ชื่อไทยผ่าน JSON | 201 — `filename` เก็บไทยครบ + header RFC 5987 ถูกต้อง |
  | `MZ\x90\x00 ...` ตั้งชื่อ `fake.pdf` | 400 `ไฟล์ไม่ใช่ PDF ที่ถูกต้อง` |
  | ไฟล์ขนาด 0 ไบต์ | 400 `ไฟล์ว่าง ไม่มีข้อมูลให้บันทึก` |
  | base64 ของไฟล์ที่ไม่ใช่ PDF | 400 — เส้นทาง curl ก็ผ่านด่านเดียวกัน |
  | PDF 101 MB | 413 (จาก `MAX_PDF_SIZE`) |
  | PDF 120 MB | 413 (จาก `MAX_CONTENT_LENGTH` ข้อความเดียวกัน) |

  ทุกเคสที่ถูกปฏิเสธไม่มีไฟล์เหลือใน `uploads/` และไม่มี row ใน `attachments`
  มี test 8 ตัวใน `tests/test_api.py` (`-k item10`) รวมเคสที่ต้องผ่าน (BOM นำหน้า)
  และเคสที่ต้องไม่ผ่าน (`%PDF-` โผล่หลังไบต์ที่ 1024)
- **Severity:** Important
- **Location:** `app.py:688-708`
- **Issue:** server รับ bytes อะไรก็ได้ ตั้งชื่อลงท้าย `.pdf` และ hardcode `mime = 'application/pdf'` (`:720`)
  การตรวจนามสกุลและเพดาน 100 MB มีแค่ใน `handleFileSelect` (`templates/index.html:879-881`) ฝั่ง JavaScript
- **Why it matters:** ไฟล์อะไรก็ได้จะถูก serve กลับด้วย `Content-Type: application/pdf` ผ่าน `/preview`
  และไฟล์เกือบ 200 MB ผ่านเข้ามาได้แม้ UI จะจำกัดที่ 100 MB การ validate ฝั่ง client ยกเลิกได้ด้วย
  การยิง API ตรง จึงต้องมีสำเนาฝั่ง server เสมอ
- **Suggested fix:**

```python
MAX_PDF_SIZE = 100 * 1024 * 1024

if len(file_bytes) > MAX_PDF_SIZE:
    return jsonify({'error': 'ไฟล์ใหญ่เกิน 100 MB'}), 413
if not file_bytes.startswith(b'%PDF-'):
    return jsonify({'error': 'ไฟล์ไม่ใช่ PDF ที่ถูกต้อง'}), 400
```

### 11. GET route 7 ตัวไม่มี try/finally connection รั่วเมื่อ query พัง — **แก้แล้ว 26/10/2026 (เหตุผลในข้อนี้ผิดบางส่วน ดูท้ายไฟล์)**

- **Severity:** Important
- **Location:** `app.py:448` (`get_all`), `:465` (`get_one`), `:652` (`list_attachments`), `:768` (`download_attachment`),
  `:794` (`preview_attachment`), `:889` (`stats`), `:915` (`export_csv_server`) และ `:330` (`init_db`)
- **Issue:** route ฝั่งเขียนใช้ `try/finally: con.close()` ครบทุกตัว แต่ route ฝั่งอ่านเรียก `con.close()`
  เป็นบรรทัดตรง ถ้า `execute()` โยน exception connection จะไม่ถูกปิด
- **Why it matters:** SQLite โหมด WAL ที่มี connection ค้างจะ hold shared lock ทำให้ checkpoint ทำไม่ได้
  และไฟล์ `-wal` โตไม่หยุด desktop app ที่เปิดค้างหลายวันจะสะสมปัญหานี้ขึ้นเรื่อย ๆ
- **Suggested fix:** ใช้ `contextlib.closing` ให้ทุก route ปิด connection แน่นอน

```python
from contextlib import closing

@app.route('/api/records', methods=['GET'])
def get_all():
    with closing(get_db()) as con:
        rows = con.execute('SELECT * FROM records ORDER BY id DESC').fetchall()
    return jsonify([row_to_dict(r) for r in rows])
```

### 12. Upload ผ่าน base64-in-JSON กิน memory หลายเท่าตัว ทั้งที่ server รองรับ multipart อยู่แล้ว

- **สถานะ:** แก้แล้ว 22/10/2026 frontend เปลี่ยนไปใช้ multipart ที่ server รองรับอยู่แล้ว
  - `apiFetch` (`:570-575`) รับ `FormData` ได้ และไม่เซ็ต `Content-Type` ทับเมื่อเป็น FormData
    เพราะ browser ต้องเติม boundary ที่สุ่มใหม่ทุก request เอง
  - `_readAndCache` → `_cacheFile` (`:917`) เก็บ `File` object ตรง ๆ ไม่เรียก `FileReader` อีกเลย
  - `_uploadToFlask` (`:960`) และ `_uploadToFlaskAnn` ส่ง `FormData` แทน JSON
  - `_openLocal` (`:939`) ใหม่ ใช้ blob URL สำหรับดูหรือดาวน์โหลดไฟล์ที่ยังไม่ได้อัปโหลด
    แทน data URL และ `revokeObjectURL` ทิ้งหลังใช้
  - ลบ `pendingReads` กับ guard ของมันในหน้า submit ออกหมด เพราะไม่มีการอ่านไฟล์แบบ async อีกแล้ว
  - `MAX_CONTENT_LENGTH` ลดจาก 200 MB เหลือ 110 MB และข้อความ 413 แก้จาก 150 MB เป็น 100 MB
    ให้ตรงกับเพดานที่ UI บังคับจริง

  วัดขนาด request body จริงของทั้งสองเส้นทางด้วยไฟล์ 3,145,728 bytes เดียวกัน

  | เส้นทาง | request body | เทียบขนาดไฟล์ |
  |---|---|---|
  | multipart (ที่ใช้ตอนนี้) | 3,146,001 bytes | 1.000x (overhead 273 bytes) |
  | base64 ใน JSON (ของเดิม) | 4,194,405 bytes | 1.333x |

  ส่วนต่าง 1,048,404 bytes ต่อไฟล์หนึ่งตัว ทั้งสองเส้นทางยังตอบ 201 และไฟล์ที่ได้
  ตรงกับต้นฉบับทุกไบต์ ยืนยันในเบราว์เซอร์จริงว่า request ที่ยิงไป `/attachments`
  มี `body` เป็น `FormData` และไม่มี `Content-Type` ที่เราเซ็ตเอง ทดสอบครบทั้งเส้นทาง
  "เลือกไฟล์ตอนสร้าง record ใหม่แล้วอัปโหลดหลังบันทึก" และ "เปิดแก้ไข record เดิมแล้วอัปโหลดทันที"
  รวมถึงช่องประกาศ (`ann`) และ preview ไฟล์ที่ยังไม่อัปโหลดซึ่งเปิดด้วย `blob:` ไม่ใช่ `data:`

  **อัปเดต 22/10/2026** ตอนทดสอบรอบนั้นไฟล์ชื่อ `ผลงานเล่มที่1.pdf` ยังถูกเก็บลง DB เป็น `1.pdf`
  ตามข้อ 6 ซึ่งตอนนี้แก้แล้ว ชื่อไทยเก็บครบทั้งชื่อ
- **Severity:** Important
- **Location:** `app.py:688-702` (JSON branch), `:703-707` (multipart branch ที่ไม่มีใครเรียก),
  `templates/index.html:905-934` (`_readAndCache`), `:942-963` (`_uploadToFlask`)
- **Issue:** ไฟล์ 100 MB ถูก `FileReader.readAsDataURL` อ่านเป็น data URL ขนาดประมาณ 133 MB
  แล้ว `JSON.stringify` ก๊อปอีกชุด แล้ว server parse JSON 133 MB แล้ว `b64decode` ได้ bytes อีก 100 MB
  ค้างใน RAM พร้อมกัน ที่ย้อนแย้งคือ `upload_attachment` มี branch multipart รองรับอยู่แล้วแต่ frontend ไม่ใช้
- **Why it matters:** เป็นสาเหตุที่ต้องตั้ง `MAX_CONTENT_LENGTH` สูงถึง 200 MB และเป็นจุดที่ทำให้ UI
  ค้างหรือ tab ตายกับไฟล์ใหญ่ ถ้าเปลี่ยนมาใช้ `FormData` browser จะ stream ไฟล์ให้โดยไม่ต้องอ่านเข้า memory ทั้งก้อน
  และเพดาน 200 MB ก็ลดลงมาเท่าขนาดไฟล์จริงได้
- **Suggested fix:** เก็บ `File` object ไว้ใน `fileCache` แทน base64 แล้วส่งเป็น `FormData`

```javascript
async function _uploadToFlask(n, rid){
  const fd = fileCache[n]; if(!fd) return true;
  const form = new FormData();
  form.append('book_no', n);
  form.append('file', fd.file, fd.name);
  const r = await fetch(`/api/records/${rid}/attachments`, {method:'POST', body:form});
  if(!r.ok) { /* ...เดิม... */ }
}
```

หมายเหตุ การเปลี่ยนนี้ทำให้ `pendingReads` (`:534`) ไม่จำเป็นอีก เพราะไม่ต้องรอ FileReader อ่านไฟล์ก่อนบันทึก
ลบ guard ที่ `:1279` ออกได้ด้วย

### 13. `fix_db.py` ไม่เช็กว่าไฟล์ DB มีอยู่จริง และใช้ regex แก้ schema แบบเหวี่ยง

- **สถานะ:** แก้แล้ว 22/10/2026 เขียน [fix_db.py](fix_db.py) ใหม่ทั้งไฟล์ (119 บรรทัด)

  | ปัญหา | การแก้ | บรรทัด |
  |---|---|---|
  | `connect` สร้าง DB เปล่าถ้าไม่มีไฟล์ | เช็ก `os.path.exists` ก่อน connect แล้ว exit 1 | 22 |
  | regex ใส่ `DEFAULT ''` ให้ทุกชนิดคอลัมน์ | ตาราง `TYPE_DEFAULTS` เลือก default ตามชนิด และผูก pattern กับชื่อคอลัมน์ทีละตัว | 28-46 |
  | ไม่มีไฟล์สำรอง | `shutil.copy2` เป็น `.bak-<timestamp>` ไม่ทับสำเนารอบก่อน | 74 |
  | ไม่มีทรานแซกชัน | `isolation_level=None` + `BEGIN`/`COMMIT`/`ROLLBACK` ครอบทั้งหมด | 49, 92-107 |
  | `PRAGMA foreign_keys=ON` ไม่มีผล | ย้ายออกมานอกทรานแซกชัน + `PRAGMA foreign_key_check` ตรวจแถวกำพร้า | 109-110 |
  | ไม่รู้จักชนิดคอลัมน์ก็ยังแก้ | หยุดทันทีโดยไม่แก้อะไร | 67-71 |
  | ตาราง `records_new` ค้างทำให้รันไม่ได้ | `DROP TABLE IF EXISTS records_new` ในทรานแซกชัน แบบเดียวกับ `attachments_v2` | 93-95 |

  ยืนยันด้วยการรันสคริปต์จริง 6 สถานการณ์ ไม่มี DB → exit 1 ไม่สร้างไฟล์ขยะ,
  ซ่อมจริงแล้ว TEXT ได้ `''` และ INTEGER ได้ `0` (`typeof` เป็น `integer` ไม่ใช่ `text`) ข้อมูล 2 แถวครบ
  ตาราง `attachments` รอด, รันซ้ำบน DB ที่ซ่อมแล้วเป็น no-op ไม่สร้างสำเนาเปล่า ๆ,
  redirect output ลงไฟล์ได้ไม่พัง, มี `records_new` ค้างแล้วกู้ได้, และเมื่อบังคับให้ error กลางทาง
  ข้อมูลเดิมยังครบ schema ไม่ถูกแก้ครึ่ง ๆ กลาง ๆ พร้อมสำเนาให้กู้
- **แก้ข้อความในรีวิวนี้:** ย่อหน้า Issue ด้านล่างเขียนว่าสคริปต์เดิม "พิมพ์ ✓ ไม่พบ column ปัญหา แล้ว exit 0"
  รันจริงแล้วไม่ใช่ มันตายด้วย `UnicodeEncodeError: 'charmap' codec can't encode character '✓'`
  แล้ว exit 1 เมื่อ stdout ไม่ใช่คอนโซล (เช่น redirect ลงไฟล์) บน Windows codepage ไทย
  ส่วนที่ตรงคือมันสร้างไฟล์ DB เปล่า 0 ไบต์ทิ้งไว้จริง และนี่คือข้อบกพร่องตัวที่ห้าที่รีวิวรอบแรกไม่เห็น
  ([check_db.py](check_db.py) ก็มีอักขระ `✓` แบบเดียวกันและน่าจะพังเหมือนกัน **ยังไม่ได้แก้** อยู่นอกขอบเขตข้อนี้)
- **Severity:** Important (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `fix_db.py:9` (connect โดยไม่เช็ก), `:35-39` (regex), `:50` (`PRAGMA foreign_keys=ON` ในทรานแซกชัน)
- **Issue:** สามเรื่องในสคริปต์เดียว
  เรื่องแรก `check_db.py:9-11` เช็ก `os.path.exists(DB)` ก่อนแล้ว exit ถ้าไม่มี แต่ `fix_db.py` ซึ่งเป็นสคริปต์คู่กัน
  ข้ามขั้นนี้ `sqlite3.connect` จึง**สร้างไฟล์ DB เปล่าขึ้นมาใหม่** แล้ว `PRAGMA table_info(records)` คืน list ว่าง
  สคริปต์พิมพ์ "✓ ไม่พบ column ปัญหา ไม่ต้องแก้ไข" แล้ว exit 0 ทั้งที่เพิ่งสร้างไฟล์ขยะทิ้งไว้
  เรื่องที่สอง regex `(NOT NULL)(?!\s+DEFAULT)` เติม `DEFAULT ''` ให้ทุก `NOT NULL` ที่เจอโดยไม่ดูชนิดคอลัมน์
  ตาราง `records` ตอนนี้เป็น TEXT ทั้งหมดจึงรอด แต่ถ้าวันหน้าเพิ่มคอลัมน์ `INTEGER NOT NULL`
  สคริปต์จะใส่ `DEFAULT ''` ให้คอลัมน์ตัวเลข
  เรื่องที่สาม `con.execute("PRAGMA foreign_keys=ON")` ที่บรรทัด 50 ไม่มีผล เพราะ SQLite เพิกเฉย pragma นี้
  เมื่ออยู่ในทรานแซกชัน และบรรทัดถัดไปคือ `con.commit()`
- **Why it matters:** สคริปต์นี้ `DROP TABLE records` โดยไม่สำรองไฟล์และไม่มี transaction ครอบ
  เป็นเครื่องมือที่ผู้ใช้จะหยิบมารันตอนระบบมีปัญหาอยู่แล้ว ซึ่งเป็นจังหวะที่แพงที่สุดถ้ามันทำข้อมูลเสียเพิ่ม
  ข้อความใน `check_db.py:31` ที่แนะว่า "แนวทางแก้ไข: รัน fix_db.py" ทำให้ผู้ใช้เดินเข้าหาเครื่องมือนี้โดยตรง
- **Suggested fix:** เติม guard ให้ตรงกับ `check_db.py` และสำรองไฟล์ก่อนแตะ schema

```python
import shutil
if not os.path.exists(DB):
    print('ไม่พบไฟล์ hr_promotion.db'); sys.exit(1)
shutil.copy2(DB, DB + '.bak')
print(f'สำรองไว้ที่ {DB}.bak')
```

---

## Minor issues และข้อเสนอแนะ

> **อัปเดต 22/10/2026** แก้แล้ว 2 ข้อจาก 12 ข้อ (ข้อ 19, 24)
> ข้อ 24 (pin เวอร์ชัน + ชุด pytest) ทำก่อนเพราะมันกันข้อที่เหลือไม่ให้ย้อนกลับมาเงียบ ๆ

### 14. Content-Type ของ CSV export ผิดรูปแบบ มี `charset` ซ้อนสองตัว — **แก้แล้ว 26/10/2026 (วิธีแก้ที่เสนอไว้ใช้ไม่ได้ ดูท้ายไฟล์)**

- **Severity:** Minor (เป็นประเด็นใหม่ ยืนยันด้วยการรันจริง)
- **Location:** `app.py:923`
- **Issue:** ยิง `GET /api/export/csv` จริงแล้วได้ header `text/csv; charset=utf-8-sig; charset=utf-8`
  สาเหตุคือโค้ดส่ง `mimetype='text/csv; charset=utf-8-sig'` ซึ่งใส่ parameter มาใน mimetype
  แล้ว Werkzeug เติม `charset=utf-8` ของตัวเองต่อท้ายอีกชั้น นอกจากนี้ `utf-8-sig` เป็นชื่อ codec ของ Python
  ไม่ใช่ charset ที่จดทะเบียนกับ IANA จึงไม่มีความหมายในบริบท HTTP
- **Why it matters:** client ที่อ่าน charset ตัวแรกจะเจอค่าที่ไม่รู้จักแล้ว fallback ไป encoding default
  ซึ่งทำให้ภาษาไทยเพี้ยน ส่วน BOM ที่ใส่ไว้ (`:923`) คือกลไกที่แก้ปัญหา Excel ได้จริงอยู่แล้ว
  ไม่ต้องพึ่ง charset ใน header เลย
- **Suggested fix:** `mimetype='text/csv; charset=utf-8'`

### 15. CSV formula injection ทั้ง export ฝั่ง server และ client — **แก้แล้ว 26/10/2026**

- **Severity:** Minor
- **Location:** `app.py:918-923`, `templates/index.html:1486`
- **Issue:** ค่าที่ขึ้นต้นด้วย `=`, `+`, `-`, `@` จะถูก Excel ตีความเป็นสูตร การครอบด้วย `"` ที่บรรทัด 1486
  ไม่ช่วย เพราะ Excel ถอดเครื่องหมายคำพูดของ CSV ออกก่อนแล้วจึงประเมินเนื้อหา
- **Why it matters:** เปิดไฟล์ export ใน Excel แล้วสูตรทำงานทันที ใช้ดึงข้อมูลออกหรือหลอกให้คลิกลิงก์ได้
- **Suggested fix:** นำหน้าค่าเสี่ยงด้วย single quote ทั้งสองฝั่ง เช่น `if v and v[0] in '=+-@': v = "'" + v`

### 16. ตาราง `attachments` ไม่มี index บน `record_id` — **แก้แล้ว 26/10/2026 (รวมกับข้อ 18 เป็น index ตัวเดียว)**

- **Severity:** Minor
- **Location:** `app.py:289-300`
- **Issue:** query หลัก (`WHERE record_id=?` ที่ `:650`, `:729`) และ `ON DELETE CASCADE` ต้อง full scan
- **Why it matters:** FK ที่ไม่มี index ทำให้ CASCADE delete ช้าเป็น O(n) เมื่อไฟล์สะสมมากขึ้น
- **Suggested fix:** เพิ่มใน `init_db()` ว่า
  `con.execute('CREATE INDEX IF NOT EXISTS idx_att_record ON attachments(record_id)')`

### 17. badge บอกโหมดแสดงจุดเขียวทั้งสองโหมด และ class `.dy` ไม่มีใน CSS — **แก้แล้ว 26/10/2026 (ส่วน `.dy` เป็นข้อมูลผิด ดูท้ายไฟล์)**

- **Severity:** Minor (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `templates/index.html:100` (`class="dot dy"`), `:591` และ `:593` (ทั้งคู่ใช้ `dot dg`), CSS ที่ `:23-24`
- **Issue:** CSS นิยามแค่ `.dot` และ `.dg` (เขียว) ส่วน `.dy` ที่ใช้ตอน "กำลังเชื่อมต่อ..." ไม่มีนิยาม
  จุดนั้นจึงเรนเดอร์เป็นวงกลม 9px โปร่งใส และเมื่อ probe ล้มเหลวจนเข้าโหมด LocalStorage
  บรรทัด 593 ก็ยังใช้ `dot dg` ซึ่งเป็นจุดเขียวเหมือนโหมดปกติ
- **Why it matters:** badge นี้มีหน้าที่เดียวคือบอกผู้ใช้ว่ากำลังทำงานโหมดไหน การใช้สีเขียวกับโหมด offline
  สื่อว่า "ปกติดี" ทั้งที่เป็นโหมดที่ไฟล์แนบใช้ไม่ได้และข้อมูลอยู่แค่ใน browser (ดูข้อ 23)
  ผู้ใช้ที่เหลือบเห็นแค่สีจะไม่รู้ว่าต้องระวัง
- **Suggested fix:** เพิ่ม `.dy{background:#f59e0b}` และ `.dr{background:#dc3545}` ใน CSS
  แล้วเปลี่ยนบรรทัด 593 เป็น `'<span class="dot dr"></span>LocalStorage (Offline)'`

### 18. ไม่มี UNIQUE constraint บน `(record_id, book_no)` — **แก้แล้ว 26/10/2026 (เหตุผลเรื่อง race ผิด ดูท้ายไฟล์)**

- **Severity:** Minor (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `app.py:289-300` (CREATE TABLE), `:733-738` (DELETE แล้ว INSERT)
- **Issue:** กฎ "หนึ่ง record มีไฟล์ได้ช่องละหนึ่งไฟล์" ถูกบังคับด้วยลำดับ `DELETE` แล้ว `INSERT` ในโค้ด
  ไม่ได้บังคับที่ schema
- **Why it matters:** `book_no` มาจาก client โดยตรง (`str(body.get('book_no', '1'))` ที่ `:691`) ไม่มี whitelist
  client จึงสร้างช่องชื่ออะไรก็ได้ไม่จำกัดจำนวน และถ้ามีการยิง upload ช่องเดียวกันสองครั้งพร้อมกัน
  ทั้งสองอาจ INSERT สำเร็จ ทำให้มีสองแถวในช่องเดียว แล้ว `loadAttachments` (`:1106`) จะเขียนทับกันเอง
  ไฟล์หนึ่งกลายเป็นกำพร้าเงียบ ๆ
- **Suggested fix:** ย้ายกฎไปอยู่ที่ schema ให้ DB บังคับแทนโค้ด

```sql
UNIQUE (record_id, book_no)
```

แล้วเปลี่ยน INSERT เป็น `INSERT INTO attachments (...) VALUES (...)
ON CONFLICT(record_id, book_no) DO UPDATE SET ...` และตรวจ `book_no` ให้อยู่ใน `{'1','2','3','ann'}`

### 19. upload ไปยัง record ที่ไม่มีอยู่ตอบ 500 แทน 404

- **สถานะ:** แก้แล้ว 22/10/2026 เช็ก `SELECT 1 FROM records WHERE id=?` ด้วย `closing(get_db())`
  ก่อนเขียนไฟล์ลงดิสก์ ([app.py](app.py):803-809) ไม่พบ → 404 โดยไม่มีไฟล์ขยะให้ตามลบเลย
  และเพิ่ม `except sqlite3.IntegrityError` ([app.py](app.py):838-843) ดักช่องว่างระหว่าง
  "เช็กแล้วมี" กับ "INSERT" (ผู้ใช้อีกหน้าต่างลบ record ทิ้งตรงนั้น) ซึ่ง FK constraint จะยิง
  IntegrityError — เคสนี้ก็ตอบ 404 เหมือนกันพร้อม rollback และลบไฟล์ที่เพิ่งเขียนทิ้ง
  ส่วน DB error สาเหตุอื่นยังเป็น 500 ตามเดิม (มี test กันการแก้เกินเลยข้อนี้ไว้ด้วย)

  ยืนยันด้วย `curl` จริง `POST /api/records/9999/attachments` ได้ `404 {"error":"ไม่พบข้อมูล id=9999"}`
  และโฟลเดอร์ `uploads/` ว่างเปล่าหลังจากนั้น ส่วนเคส race จำลองด้วย fault injection
  ให้ `INSERT` โยน `IntegrityError` แล้วตรวจว่าได้ 404 และไฟล์ที่เพิ่งเขียนถูกเก็บกวาด
  (test 3 ตัวใน `tests/test_api.py` — `-k item19`)
- **Severity:** Minor
- **Location:** `app.py:722-746`
- **Issue:** ถ้า `rid` ไม่มีจริง INSERT จะชน FK constraint แล้วตอบ 500 พร้อมข้อความ IntegrityError
- **Why it matters:** 500 สื่อว่า server พัง ทั้งที่จริงคือ client อ้างถึงของที่ไม่มี ทำให้ debug ฝั่ง frontend หลงทาง
  และที่สำคัญคือไฟล์ถูกเขียนลงดิสก์ไปแล้วที่บรรทัด 716 ก่อนจะรู้ว่า record ไม่มี จึงต้องพึ่ง rollback ลบทิ้ง
- **Suggested fix:** เช็กก่อนเขียนไฟล์

```python
with closing(get_db()) as con:
    if not con.execute('SELECT 1 FROM records WHERE id=?', [rid]).fetchone():
        return jsonify({'error': f'ไม่พบข้อมูล id={rid}'}), 404
```

### 20. `delete_record` ตอบ 200 สำหรับ id ที่ไม่มีอยู่ ต่างจาก `update_record` ที่ตอบ 404

- **Severity:** Minor (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `app.py:567-598` เทียบกับ `:549-551`
- **Issue:** `update_record` เช็ก `SELECT id` ก่อนแล้วตอบ 404 ถ้าไม่พบ แต่ `delete_record` ยิง
  `DELETE FROM records WHERE id=?` ตรง ๆ แล้วตอบ `{'ok': True}` เสมอ ไม่ว่าจะลบจริงหรือไม่
- **Why it matters:** `delete_attachment` (`:809`) อธิบายไว้ชัดว่าเลือกตอบ 200 เพราะ DELETE เป็น idempotent
  ซึ่งเป็นเหตุผลที่ฟังขึ้น แต่ `delete_record` ไม่มีคอมเมนต์อธิบายเลย จึงอ่านเหมือนลืมเช็ก
  ไม่เหมือนการตัดสินใจ ถ้าเจตนาเหมือนกันให้เขียนคอมเมนต์เหมือนกัน ถ้าไม่ใช่ให้เพิ่มเช็กให้ตรงกับ `update_record`

### 21. โค้ดชุด `*Ann` ซ้ำกับชุดเล่ม 1-3 เกือบทั้งหมด

- **Severity:** Minor
- **Location:** `templates/index.html:1010-1098` (`handleFileSelectAnn` ถึง `previewFileAnn`)
- **Issue:** ฟังก์ชัน 8 ตัวถูกก๊อปมาแก้ suffix ต่างกันแค่ element id (`-ann`) กับ `book_no`
- **Why it matters:** ทุก bugfix ต้องแก้สองที่ และตอนนี้เริ่มไม่ sync กันแล้ว
- **Suggested fix:** รวมเป็นชุดเดียวโดยให้พารามิเตอร์ `n` รับได้ทั้งเลขและ `'ann'` โครงสร้าง element id
  รองรับอยู่แล้ว (`uzone-1` กับ `uzone-ann`) แล้วลบชุด `Ann` ทิ้ง ลดได้ประมาณ 120 บรรทัด

### 22. `time.sleep(0.8)` เดาว่า server พร้อมแล้ว

- **Severity:** Minor
- **Location:** `app.py:968`
- **Issue:** เครื่องช้าหรือ antivirus scan อาจทำให้ 0.8 วินาทีไม่พอ webview จะเปิดมาเจอ connection refused
- **Why it matters:** ผู้ใช้ปลายทางเห็นหน้าขาวโดยไม่รู้ว่าแค่ต้องรอ เป็น first impression ของ `.exe` ที่เสียฟรี
- **Suggested fix:** รอจน port เปิดจริงแทนการเดาเวลา

```python
import socket
for _ in range(50):                       # รอสูงสุดประมาณ 5 วินาที
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=0.1):
            break
    except OSError:
        time.sleep(0.1)
```

### 23. โหมด LocalStorage ทิ้งไฟล์แนบที่ผู้ใช้เลือกไว้แบบเงียบ ๆ

- **Severity:** Minor
- **Location:** `templates/index.html:1303` (`if(USE_FLASK&&savedId)`), `:920`, `:532-534` (`fileCache` อยู่แค่ใน memory)
- **Issue:** เมื่อ `USE_FLASK` เป็น false ทั้ง block อัปโหลดถูกข้าม ไฟล์ค้างสถานะ "⏳ รอบันทึกข้อมูล" ตลอดไป
  `fileCache` ไม่เคยถูกเขียนลง LocalStorage และไม่มีการแจ้งผู้ใช้ว่าโหมดนี้เก็บไฟล์ไม่ได้
- **Why it matters:** ผู้ใช้เข้าใจว่าแนบไฟล์สำเร็จแล้ว แต่เปิดโปรแกรมใหม่ไฟล์หายหมด
  data loss แบบเงียบคือ UX bug ที่แย่ที่สุดประเภทหนึ่ง และทำงานร่วมกับข้อ 17 ที่ badge แสดงจุดเขียวให้ดูปกติ
- **Suggested fix:** ถ้า `!USE_FLASK` ให้ซ่อน panel อัปโหลดไปเลย หรือ toast เตือนตอนเลือกไฟล์ว่า
  "โหมด Offline ไม่รองรับไฟล์แนบ"

### 24. `requirements.txt` ไม่ pin เวอร์ชัน และยังไม่มี test อัตโนมัติเลย

- **สถานะ:** แก้แล้ว 22/10/2026 ทั้งสองครึ่ง
  - [requirements.txt](requirements.txt) pin เป็น `flask==3.1.3` และ `pywebview==6.2.1`
    ตามเวอร์ชันที่ทดสอบจริงบน Python 3.14.7 / Windows 11 พร้อมคอมเมนต์บอกว่าทำไมต้อง `==`
    (อ้างข้อ 5 เป็นตัวอย่างที่เกิดขึ้นแล้ว)
  - [requirements-dev.txt](requirements-dev.txt) ใหม่ แยก `pytest==9.1.1` ออกจาก dependency
    ของ runtime เพื่อไม่ให้หลุดเข้า `.exe`
  - [pytest.ini](pytest.ini) ตั้ง `testpaths = tests` และ `--strict-markers`
  - [tests/conftest.py](tests/conftest.py) + [tests/test_api.py](tests/test_api.py) — 29 test
    (27 passed, 2 xfailed, 0.6 วินาที)

  ขอบเขตที่เลือกทดสอบคือเส้นทางที่ "พังเงียบ" ได้ คือพังแล้วไม่มีสัญญาณจนผู้ใช้เจอเอง

  | กลุ่ม | จำนวน | ครอบอะไร |
  |---|---|---|
  | ข้อ 5 | 4 | `app.json.ensure_ascii`, ไทยอ่านออกใน response ดิบทั้ง list/single/eval rows, ขนาด payload |
  | ข้อ 6 | 11 | `sanitize_display_name` 8 เคส (ไทย, ตัวคั่น path, อักขระควบคุม, ว่าง, `None`), clamp 255, end-to-end ชื่อไทยใน DB + header RFC 5987, `list_attachments` ไม่ปล่อย `stored_name` |
  | ข้อ 7 | 6 | fault injection ให้ `INSERT`/`DELETE` ล้มกลางทาง แล้วตรวจว่าไฟล์เก่ายังอยู่ ดาวน์โหลดได้ และไฟล์ใหม่ถูกเก็บกวาด ครบทั้ง upload / delete_record / delete_attachment / delete_all |
  | เส้นทางพื้นฐาน | 5 | required fields 422 พร้อมป้ายไทย, update 404, body ไม่ใช่ JSON 400, `/api/stats`, BOM ใน CSV |
  | ข้อที่ยังไม่แก้ | 2 | ข้อ 10 และ 19 ทำเป็น `xfail(strict=True)` |

  `xfail(strict=True)` ตั้งใจเลือกมากกว่า `skip` — วันที่มีคนแก้ข้อ 10 หรือ 19 test จะ XPASS
  แล้ว strict ทำให้ run แดงทันที เป็นสัญญาณให้มาลบ marker ไม่ใช่ test ที่ลืมค้างไว้แบบ skip

  **ยืนยันว่า test จับของจริงได้ ไม่ใช่ test ที่เขียนให้ผ่าน** ลอง mutate โค้ดย้อนกลับไปสถานะก่อนแก้
  แล้วดูผล — ย้อนข้อ 5 (`ensure_ascii = True`) ทำให้ test กลุ่มข้อ 5 แดงทั้ง 4 ตัว
  และย้อนข้อ 6 (สลับกลับไปใช้ `secure_filename`) ทำให้ test กลุ่มข้อ 6 แดง 7 ตัว

  test ทุกตัว monkeypatch `DB_PATH` กับ `UPLOAD_FOLDER` ไปที่ `tmp_path` ของ pytest
  ตรวจแล้วหลังรันว่า `hr_promotion.db` ยังมี 4 records / 2 attachments และ `static/uploads/`
  ยังมี 2 ไฟล์เท่าเดิม (fixture `fail_sql` จงใจไม่ใช้ `monkeypatch` เพราะ `monkeypatch.undo()`
  จะปลด path ชั่วคราวทิ้งไปด้วย แล้ว test จะไปเขียน DB จริง)
- **Severity:** Minor (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `requirements.txt`
- **Issue:** ไฟล์มีสองบรรทัดคือ `flask>=3.1` และ `pywebview>=6.2` ซึ่งเปิดช่วงบนไว้ไม่จำกัด
  และไม่มีไฟล์ test ใดในโปรเจกต์
- **Why it matters:** ข้อ 5 เป็นตัวอย่างที่เกิดขึ้นจริงแล้วว่าการอัปเกรด Flask ทำให้ config หนึ่งตายเงียบ ๆ
  โดยไม่มีอะไรจับได้ ถ้า build `.exe` ใหม่วันหน้าแล้ว pip ดึง Flask 4 มา จะไม่มีสัญญาณเตือนใดเลย
  จนผู้ใช้เจอปัญหาเอง การ pin เวอร์ชันแก้ครึ่งแรก การมี test แก้ครึ่งหลัง
- **Suggested fix:** pin เป็น `flask==3.1.3` และ `pywebview==6.2.x` ตามที่ทดสอบแล้วจริง
  แล้วเพิ่ม pytest ชุดเล็กที่ครอบ 4 เส้นทางซึ่งคุ้มค่าที่สุดก่อน

```python
def test_thai_json_not_escaped(client):
    """กันข้อ 5 กลับมา — ภาษาไทยต้องอ่านออกใน response ดิบ"""
    client.post('/api/records', json={'p1_fname': 'สมชาย', 'p1_lname': 'ใจดี',
                                      'p2_pos_current': 'นักวิชาการ', 'p2_empid': '123'})
    assert 'สมชาย' in client.get('/api/records').get_data(as_text=True)

def test_thai_filename_preserved(client):   # กันข้อ 6
def test_upload_rejects_non_pdf(client):    # กันข้อ 10
def test_upload_to_missing_record_404(client):  # กันข้อ 19
```

### 25. ฟิลด์บังคับฝั่ง client 13 ตัว ฝั่ง server 4 ตัว

- **Severity:** Minor (เป็นประเด็นใหม่ ไม่อยู่ในรีวิวเดิม)
- **Location:** `templates/index.html:1281-1293` เทียบกับ `app.py:352-357` (`REQUIRED_FIELDS`)
- **Issue:** submit handler เช็ก 13 ฟิลด์ ส่วน server เช็ก 4 ฟิลด์ คือ `p1_fname`, `p1_lname`,
  `p2_pos_current`, `p2_empid` คอมเมนต์ที่ `app.py:350` รู้เรื่องนี้และเขียนว่า
  "ขั้นต่ำกว่าฝั่ง frontend ซึ่งบังคับมากกว่า"
- **Why it matters:** ตอนนี้ไม่กระทบอะไรเพราะมี client เดียว แต่กฎทางธุรกิจถูกเขียนไว้สองที่ด้วยค่าไม่เท่ากัน
  ซึ่งเป็นรูปแบบที่เพี้ยนเพิ่มขึ้นทุกครั้งที่มีคนแก้ฝั่งเดียว ถ้า 13 ฟิลด์คือกฎจริงให้ย้ายไปอยู่ที่ server
  แล้วให้ client อ่านรายการเดียวกันนั้นมาใช้ ถ้า 4 ฟิลด์คือกฎจริงให้ลดฝั่ง client ลงมาให้ตรงกัน

---

## ข้อแก้ไขจากรีวิว 16/07/2026

รีวิวรอบก่อนระบุขอบเขตของ Stored XSS (ข้อ 2 ในไฟล์นั้น) ไว้เกินจริงสองจุด ตรวจซ้ำแล้วไม่เป็นช่องโหว่

- **Dashboard stat cards (`templates/index.html:1572`) ไม่ใช่จุด XSS** ค่าที่ฉีดเข้า `innerHTML`
  มีแค่ `total`, `passed`, `avg` และผลคำนวณเปอร์เซ็นต์ ทั้งหมดเป็นตัวเลขที่ได้จากการนับและหาค่าเฉลี่ย
  ไม่มีสตริงจากผู้ใช้ผ่านเข้ามาเลย
- **label ของกราฟไม่ใช่จุด XSS** ชื่อคนที่ `:1581` ถูกส่งเข้า Chart.js ซึ่งวาดข้อความลง `<canvas>`
  ด้วย 2D context ไม่ได้ parse HTML จึงฉีดสคริปต์ไม่ได้

จุด XSS จริงมีสองจุดคือ `renderTable` (`:1363`) กับ `printReport` (`:1144`) ตามข้อ 3 ข้างต้น
ความรุนแรงยังเป็น Critical เท่าเดิม เพราะ `printReport` ครอบฟิลด์มากกว่าที่รีวิวเดิมระบุ
รวมถึงค่าในแถวประเมิน 5.1 และ 5.2 ที่ server ไม่ได้ตรวจเนื้อหาภายใน list

---

## ข้อแก้ไขจากรีวิวนี้ (26/10/2026) — ข้อ 9 และ ข้อ 11

ทั้งสองข้อแก้แล้ว รายละเอียดการแก้อยู่ใน [spec.md](spec.md) ส่วนนี้บันทึกเฉพาะจุดที่
**การตรวจจริงไม่ตรงกับที่รีวิวเขียนไว้** เพื่อไม่ให้รอบหน้าเชื่อข้อสรุปที่พิสูจน์แล้วว่าไม่จริง

- **ข้อ 11 ส่วน "Why it matters" ผิด** รีวิวเขียนว่า connection ที่ค้างในโหมด WAL จะ hold shared
  lock ทำให้ checkpoint ทำไม่ได้และไฟล์ `-wal` โตไม่หยุด วัดตรงด้วย `PRAGMA wal_checkpoint(TRUNCATE)`
  ผ่าน HTTP จริงทั้งก่อนและหลังแก้ ได้ `busy=0` ทั้งสองกรณี checkpoint ไม่ถูกบล็อกเลย
  เพราะ connection ที่เปิดค้างอยู่เฉย ๆ ไม่ได้ถือ read transaction ไว้ SQLite จึงไม่ติด lock
  ผลกระทบที่วัดได้จริงคือ **handle ของไฟล์ DB ค้างเปิดค้างอยู่หนึ่งตัวต่อ GET ที่ล้มหนึ่งครั้ง**
  จนกว่า cycle collector จะเก็บ (traceback ถือ frame ของ route ไว้ ซึ่งถือ local `con` ต่อ)
  ตัวข้อบกพร่องยังเป็นของจริงและยังควรแก้ แต่เหตุผลคือ handle รั่ว ไม่ใช่ `-wal` บวม
- **ข้อ 9 ขอบเขตกว้างกว่า 8 จุดที่ระบุ** `@app.errorhandler(400/404/500)` ก็ต่อ `str(e)` แบบเดียวกัน
  ของพวกนี้ไม่ได้เปิดเผยรายละเอียดภายใน (เป็น boilerplate ของ Werkzeug) แต่ยาวเกิน 80 ตัวอักษร
  จึงเปิด `alert()` ด้วยข้อความอังกฤษที่ผู้ใช้ทำอะไรต่อไม่ได้ แก้รวมไปในรอบนี้แล้ว

---

## ข้อแก้ไขจากรีวิวนี้ (26/10/2026) — ข้อ 14 ถึง 18

ทั้งห้าข้อแก้แล้ว รายละเอียดอยู่ใน [spec.md](spec.md) ส่วนนี้บันทึกเฉพาะจุดที่
**การตรวจจริงไม่ตรงกับที่รีวิวเขียนไว้**

- **ข้อ 14 วิธีแก้ที่เสนอไว้ใช้ไม่ได้** รีวิวเสนอ `mimetype='text/csv; charset=utf-8'`
  วัดบน flask 3.1.3 / werkzeug 3.1.9 แล้วได้ `text/csv; charset=utf-8; charset=utf-8`
  ยังซ้อนสองตัวเหมือนเดิม เพราะ Werkzeug เติม charset ต่อท้ายไม่ว่า mimetype จะมีอยู่แล้วหรือไม่
  ที่ถูกคือ `mimetype='text/csv'` เฉย ๆ หรือกำหนด `content_type=` ไปทั้งก้อน
- **ข้อ 17 ครึ่งหนึ่งเป็นข้อมูลผิด** รีวิวบอกว่า CSS นิยามแค่ `.dot` กับ `.dg` ส่วน `.dy` ไม่มี
  จุดนั้นจึงเรนเดอร์เป็นวงกลม 9px โปร่งใส ตรวจแล้ว `.dy` อยู่ในไฟล์ตั้งแต่ commit แรก
  (`git log -S` ชี้ไป `14d4f69`) และ browser รายงาน computed background เป็น `rgb(245, 158, 11)`
  ส่วนที่จริงคือ `.dr` ไม่มี และ branch โหมด offline เลือก class ผิดเป็น `dot dg`
- **ข้อ 18 เหตุผลเรื่อง race ผิด** รีวิวบอกว่า upload ช่องเดียวกันพร้อมกันอาจ INSERT สำเร็จทั้งคู่
  ได้สองแถวในช่องเดียว ยิงพร้อมกัน 2, 4 และ 8 เส้นแล้วไม่เคยเกิดสักครั้ง เพราะ sqlite
  serialise ตัวเขียนอยู่แล้ว ผลเสียที่เกิดจริงคือ **ไฟล์กำพร้า** ซึ่งตรงกับที่รีวิวกังวลไว้ตอนท้าย
  แต่คนละกลไก: แต่ละ request อ่าน `stored_name` เก่าใน snapshot ของตัวเองก่อนได้ write lock
  หลาย request จึงเห็นค่าเดียวกัน แล้วไฟล์ที่ผู้แพ้แทนที่ไปไม่มีใครลบ
  (อัปพร้อมกัน 4 ครั้งเหลือขยะ 2 ไฟล์ 8 ครั้งเหลือ 5 ไฟล์)
- **ข้อ 18 ส่วนที่รีวิวประเมินต่ำไป** `book_no` ไม่ได้แค่ "สร้างช่องได้ไม่จำกัด"
  ยิงตรงด้วย `book_no='../../etc/passwd'` ก็ได้ 201 (ชื่อนี้ไม่เคยถูกใช้ประกอบ path บนดิสก์
  จึงไม่ใช่ช่องโหว่ path traversal แต่เป็นแถวที่ UI แสดงไม่ได้และผู้ใช้ลบไม่ได้)

---

## Positive highlights

- **การป้องกัน SQL injection ทำถูกหลัก** dynamic SQL ที่ `:503`, `:552` ประกอบชื่อคอลัมน์จาก whitelist
  `RECORD_COLS` เท่านั้น และ `sanitize_record` วน loop ตาม whitelist ไม่ใช่ตาม key ที่ client ส่งมา
  ซึ่งเป็นทิศทางที่ถูกต้อง ค่าของผู้ใช้ผ่าน placeholder `?` ทุกจุดโดยไม่มีข้อยกเว้น
- **เปิด `PRAGMA foreign_keys=ON` ทุก connection** (`:62`) เป็นจุดที่คนใช้ SQLite พลาดบ่อยที่สุด ที่นี่ทำถูก
  และคอมเมนต์อธิบายด้วยว่าทำไมต้องเปิดเองทุกครั้ง
- **`list_attachments` จงใจไม่ SELECT `stored_name`** (`:649-651`) ไม่ปล่อยชื่อไฟล์บนดิสก์ออกไปให้ client
  เป็นการคิดเรื่องขอบเขตข้อมูลที่ละเอียดกว่าที่โปรเจกต์ขนาดนี้มักทำ
- **BOM ใน CSV export** แก้ปัญหาภาษาไทยใน Excel ได้ตรงจุดทั้งฝั่ง server (`:923`) และ client (`:1487`)
- **`kc()` destroy Chart.js instance เดิมก่อนวาดใหม่** (`:1512`) กัน memory leak ที่ dashboard ซึ่ง re-render บ่อย
- **คอมเมนต์อธิบายเหตุผลไม่ใช่อธิบายโค้ด** เช่น `use_reloader=False` (`:938`) ที่บอกว่าทำไม reloader
  ทำงานใน thread ลูกไม่ได้ ทำให้ผู้ดูแลคนถัดไปไม่ต้องขุด git history
  ข้อสังเกตเดียวคือคอมเมนต์หลายจุดอ้างคุณสมบัติที่โค้ดจริงไม่มี (ข้อ 4, 7, 8) ซึ่งอันตรายกว่าไม่มีคอมเมนต์
  เพราะคนอ่านจะเชื่อแล้วไม่ตรวจ — ข้อ 4 กับ 7 แก้แล้ว 22/10/2026 (โค้ดทำตามที่คอมเมนต์อ้างจริงแล้ว)
  เหลือข้อ 8 ที่ docstring ยังอ้าง idempotent อยู่

---

## ลำดับความสำคัญที่แนะนำ

| ลำดับ | ข้อ | เหตุผล | ประมาณงาน |
|---|---|---|---|
| ~~1~~ | ~~2, 3~~ | **แก้แล้ว 22/10/2026** ดูสถานะในหัวข้อของแต่ละข้อ | เสร็จ |
| ~~2~~ | ~~1~~ | **แก้แล้ว 22/10/2026** self-host ไลบรารีทั้งหมดใน `static/vendor/` | เสร็จ |
| ~~3~~ | ~~4, 13~~ | **แก้แล้ว 22/10/2026** migration เป็น all-or-nothing และ `fix_db.py` เขียนใหม่ | เสร็จ |
| ~~4~~ | ~~6, 7~~ | **แก้แล้ว 22/10/2026** ชื่อไฟล์ไทยเก็บครบ และลบไฟล์หลัง commit ครบทั้ง 4 จุด | เสร็จ |
| ~~5~~ | ~~5, 24~~ | **แก้แล้ว 22/10/2026** `app.json.ensure_ascii`, pin เวอร์ชัน และชุด pytest 29 test | เสร็จ |
| 6 | ~~10~~, 9, 11 | **ข้อ 10 แก้แล้ว 22/10/2026** (validate PDF + เพดานขนาดฝั่ง server) เหลือ information disclosure (ข้อ 9) และ connection leak (ข้อ 11) — ข้อ 8 หมดพิษแล้วจากการแก้ข้อ 4 แต่ SQL ยังขาด `WHERE` | กลาง |
| ~~7~~ | ~~12~~ | **แก้แล้ว 22/10/2026** multipart แทน base64 payload ลดจาก 1.333x เหลือ 1.000x | เสร็จ |
| 8 | 14-18, ~~19~~, 20-23, 25 | **ข้อ 19 แก้แล้ว 22/10/2026** (404 + ไม่ทิ้งไฟล์ขยะ) ที่เหลือปรับปรุงคุณภาพระยะยาว ทำตามสะดวก | เล็ก |

ลำดับ 1-5 และ 7 แก้ครบแล้ว ลำดับ 6 เหลือข้อ 9 กับ 11 และลำดับ 8 เหลือ Minor อีก 11 ข้อ
สรุปสถานะทั้งรีวิว: แก้แล้ว 12 ข้อจาก 25 ข้อ (Critical 4/4, Important 6/9, Minor 2/12)
ความเสี่ยงที่หนักปิดหมดแล้ว ที่เหลือคือ information disclosure (ข้อ 9), connection leak (ข้อ 11),
migration ที่ docstring ยังอ้าง idempotent เกินจริง (ข้อ 8) และงานคุณภาพระยะยาว
ชุด pytest (ข้อ 24) ตอนนี้มี 38 test ไม่มี xfail ค้างแล้ว การแก้ข้อถัดไปควรเพิ่ม test คู่กันไปด้วย
