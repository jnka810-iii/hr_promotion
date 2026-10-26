# hr_promotion — spec.md

ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง (Flask + SQLite3 + pywebview → .exe)

## สถานะปัจจุบัน (26/10/2026)

- ระบบทำงานครบ: บันทึก/แก้ไข/ลบ record, อัปโหลด PDF (เล่ม 1-3 + ประกาศ), dashboard, export CSV/JSON
- ไฟล์แนบเก็บแบบ file-based ใน `static/uploads/` (migrate จาก BLOB แล้ว)
- Frontend มีโหมด offline fallback ผ่าน LocalStorage

## งานล่าสุด

- **แก้ Minor ข้อ 14-18 (26/10/2026)** — CSV export, index/constraint ของไฟล์แนบ และ badge บอกโหมด
  - **ข้อ 14** `mimetype='text/csv; charset=utf-8-sig'` ใส่ parameter มาใน mimetype
    Werkzeug จึงเติม charset ของตัวเองต่อท้ายอีกชั้น ได้ `text/csv; charset=utf-8-sig; charset=utf-8`
    **วิธีแก้ที่รีวิวเสนอก็ผิด** วัดบน flask 3.1.3 / werkzeug 3.1.9 แล้ว
    `mimetype='text/csv; charset=utf-8'` ยังได้ charset ซ้อนสองตัวเหมือนเดิม
    ที่ถูกคือไม่ใส่ parameter ใน mimetype เลย ปล่อยให้ Werkzeug เติมเอง → `text/csv; charset=utf-8`
    BOM ซึ่งเป็นกลไกจริงที่ทำให้ Excel อ่านไทยได้ ยังอยู่ครบและมี test คุมไว้
  - **ข้อ 15** เพิ่ม `csv_cell()` ([app.py](app.py)) และ `csvCell()` ([templates/index.html](templates/index.html))
    นำหน้าค่าที่ขึ้นต้นด้วย `=` `+` `-` `@` tab หรือ CR ด้วย single quote
    ด่านอยู่ตรงจุด export ไม่ใช่ตอนรับ input เพราะ export อ่านจาก DB ตรง ๆ และ `fix_db.py`
    แก้ DB ด้วยมือได้ สองฝั่งอ้างถึงกันใน comment เพราะถ้าแก้ข้างเดียวไฟล์จากสองปุ่มจะไม่เหมือนกัน
  - **ข้อ 16 + 18 ใช้ index ตัวเดียวกัน** `CREATE UNIQUE INDEX idx_att_record_book ON attachments(record_id, book_no)`
    บังคับกฎ 1 ช่อง 1 ไฟล์ที่ schema และคอลัมน์ซ้ายสุดก็รับงาน `WHERE record_id=?` กับ CASCADE
    ที่ข้อ 16 ขอ index แยกไว้ด้วย query plan เปลี่ยนจาก `SCAN attachments` เป็น
    `SEARCH attachments USING COVERING INDEX`
    sqlite ทำ `ALTER TABLE ADD CONSTRAINT` ไม่ได้ และ CLAUDE.md ห้าม DROP แล้วสร้างใหม่
    นอกงาน migration เต็มรูปแบบ จึงใช้ index แทน UNIQUE ระดับตารางที่รีวิวเสนอ
  - `_dedupe_attachment_slots()` รวบแถวซ้ำให้เหลือ id สูงสุดของแต่ละช่องก่อนสร้าง index
    ถ้าไม่ทำ DB เก่าที่มีแถวซ้ำจะทำให้ `CREATE UNIQUE INDEX` โยน exception กลาง `init_db()`
    = แอปเปิดไม่ขึ้นทั้งตัว ไม่ใช่แค่ฟีเจอร์เดียวพัง ไฟล์ของแถวที่ทิ้งลบหลัง commit ตาม invariant เดิม
    (ตรวจ `hr_promotion.db` ตัวจริงแบบ read-only แล้ว: 4 record, 2 ไฟล์แนบ, ไม่มีคู่ซ้ำ
    migration จึงไม่ลบอะไรเลยบนเครื่องนี้ และ book_no ที่มีอยู่ `['1','ann']` อยู่ใน whitelist ครบ)
  - **ข้อ 18** `book_no` ต้องอยู่ใน `BOOK_SLOTS = {'1','2','3','ann'}` ไม่อย่างนั้นตอบ 400
    ก่อนแตะดิสก์ ของเดิมรับอะไรก็ได้ ยิงตรงด้วย `book_no='../../etc/passwd'` ก็ได้ 201
  - **ผลกระทบจริงของ race ไม่ตรงกับที่รีวิวอ้าง** รีวิวบอกว่า upload พร้อมกันจะได้สองแถวในช่องเดียว
    ทดสอบจริงแล้วไม่เคยเกิด เพราะ sqlite serialise ตัวเขียนอยู่แล้ว ของจริงที่เกิดคือ **ไฟล์กำพร้า**
    แต่ละ request อ่าน `stored_name` เก่าใน snapshot ของตัวเองก่อนได้ write lock หลายตัวจึงเห็นค่าเดียวกัน
    แล้วไฟล์ที่ผู้แพ้แทนที่ไปไม่มีใครลบ วัดได้: อัปพร้อมกัน 4 ครั้งเหลือขยะ 2 ไฟล์ 8 ครั้งเหลือ 5 ไฟล์
    แก้โดยอ่านชื่อไฟล์เก่าจาก `DELETE ... RETURNING stored_name` ซึ่งทำงานใต้ write lock
    หลังแก้ทั้ง 2/4/8 ครั้งเหลือ 0 ไฟล์กำพร้า
  - **ข้อ 17** badge โหมด LocalStorage ใช้จุดเขียวเหมือนโหมดปกติ เปลี่ยนเป็นจุดแดงและเพิ่ม `.dr` ใน CSS
    **ครึ่งหนึ่งของข้อนี้ผิด** รีวิวบอกว่า `.dy` ไม่มีใน CSS และเรนเดอร์เป็นวงกลมโปร่งใส
    แต่ `.dy` อยู่ในไฟล์มาตั้งแต่ commit แรก และ browser รายงาน computed background
    เป็น `rgb(245, 158, 11)` จริง ไม่มีอะไรโปร่งใสและไม่ต้องเพิ่มอะไรให้มัน
  - ยืนยันข้อ 17 ด้วย browser จริงกับแอปสองตัว ตัวหนึ่งปกติ อีกตัว drop ตาราง records ให้ probe ล้ม:
    offline ได้ `rgb(220, 53, 69)` ส่วนปกติยังเป็น `rgb(34, 197, 94)`
  - test เพิ่มเป็น 83 ตัว (จาก 59) รวม test ที่ยิง upload พร้อมกัน 6 เส้นแล้วเช็ก invariant
    "ไฟล์บนดิสก์ = แถวใน DB" (รันซ้ำ 5 รอบไม่ flaky)

- **แก้ Important ข้อ 9 และ ข้อ 11 (26/10/2026)** — ไม่ส่ง `str(e)` ให้ client และปิด connection ทุกเส้นทาง
  - ข้อ 9: ทุก route เคยต่อ `str(e)` เข้าข้อความ error วัดจริงผ่าน HTTP ก่อนแก้ — upload ที่เขียนไฟล์ไม่ได้
    ตอบ `ไม่สามารถบันทึกไฟล์ได้: [WinError 183] ... 'C:\\Users\\...\\uploads'` ยาว 161 ตัวอักษร
    ซึ่งเกินเพดาน 80 ตัวอักษรของ `toastError` → เปิด `alert()` โชว์ path ในเครื่องผู้ใช้
    และ DB ล้มตอบ `DB error: no such table: records` บอกชื่อตารางให้
  - รวมการตัดสินใจว่า "client เห็นอะไรได้" ไว้ที่ `fail(context, message, status)` ที่เดียว ([app.py](app.py):77)
    status code ของแต่ละ route ยังเดิม เพราะ 422 (integrity ใน `add_record`) และ 404
    (record หายระหว่าง upload) เป็นสัญญากับ frontend ไม่ใช่เรื่องบังเอิญ ข้อความไทยทุกตัวสั้นกว่า 80 ตัวอักษร
  - `@app.errorhandler` ทั้งสามตัวมีอาการเดียวกันจึงแก้ด้วย (แก้ที่ pattern ไม่ใช่แค่ 8 จุดที่รีวิวระบุ)
    ของเดิมคือ boilerplate อังกฤษของ Werkzeug ยาวพอที่จะเปิด `alert()` เหมือนกัน
  - `_setup_logging()` เขียน `BASE_DIR/hr_promotion.log` หมุนที่ 1 MB เก็บ 3 รุ่น เรียกจาก `main()`
    เท่านั้นไม่ใช่ตอน import (ไม่อย่างนั้น test จะทิ้งไฟล์ log ไว้ในโปรเจกต์) ต่อ handler เข้าทั้ง
    `log` และ `app.logger` ยืนยันแล้วว่า exception ที่จับได้และที่ไม่มีใครจับ ลง log ครบพร้อม traceback
    ขณะที่ client เห็นแค่ข้อความไทย — ถ้าไม่ทำส่วนนี้ การแก้ข้อ 9 จะเท่ากับลบช่องทาง debug ช่องเดียวที่มี
  - ข้อ 11: route ฝั่งอ่าน 7 ตัว + `init_db()` เรียก `con.close()` เป็นบรรทัดตรง ๆ หลัง `execute()`
    เปลี่ยนเป็น `with closing(get_db()) as con:` ทั้งหมด (idiom ที่ `upload_attachment` ใช้อยู่แล้ว)
    route ฝั่งเขียนคง `try/except/finally` ไว้ เพราะต้อง rollback ด้วย
  - กลไกจริงวัดด้วย `gc.get_referrers`: connection ที่รั่วถูกยึดโดย frame ของ `get_all` ผ่าน local `con`
    ซึ่ง traceback ของ exception ถือไว้ จึงไม่ถูกปล่อยจนกว่า cycle collector จะทำงาน
  - **ผลกระทบจริงไม่ตรงกับที่รีวิวอ้าง** รีวิวบอกว่า connection ค้างจะ hold shared lock ทำให้
    checkpoint ไม่ได้และ `-wal` โตไม่หยุด ทดสอบตรงแล้วไม่เป็นอย่างนั้น: `PRAGMA wal_checkpoint(TRUNCATE)`
    ทำงานสำเร็จ (busy=0) ทั้งก่อนและหลังแก้ เพราะ connection ที่เปิดค้างแบบ idle ไม่ได้ถือ read
    transaction อยู่ ผลกระทบที่วัดได้จริงคือ **handle ของไฟล์ DB ค้างเปิด**: ก่อนแก้ handle ยังเปิดอยู่
    หลัง GET ที่ล้ม (และหลังล้ม 1, 5, 25 ครั้ง) หลังแก้ไม่ค้างเลย
  - test เพิ่มเป็น 59 ตัว (จาก 38) — `fail_sql` จดการเรียก `close()` แล้ว `unclosed()` รายงาน
    connection ที่เปิดแล้วไม่ปิด จึงคุม route ทั้ง 14 ตัวด้วย test ชุดเดียวแทนการไล่ดูทีละจุด

- **แก้ Important ข้อ 10 และ Minor ข้อ 19 (22/10/2026)** — validate ไฟล์อัปโหลดฝั่ง server
  - เพิ่มค่าคงที่ `MAX_PDF_SIZE = 100 MB` ([app.py](app.py):34) ให้ตรงกับที่ UI บอกผู้ใช้
    (`MAX_CONTENT_LENGTH` 110 MB เป็นเพดานของ request ทั้งก้อน คนละตัวกัน)
  - ด่านตรวจใน `upload_attachment` ทำงานก่อนเขียนไฟล์ลงดิสก์ ครอบทั้ง multipart และ base64-in-JSON:
    ไฟล์ว่าง → 400, เกิน `MAX_PDF_SIZE` → 413, ไม่พบ header `%PDF-` ใน 1024 ไบต์แรก → 400
    (สแกน 1024 ไบต์แรกไม่บังคับไบต์ที่ 0 เพราะไฟล์จากเครื่องสแกนมี BOM นำหน้าได้ และ reader ก็ผ่อนปรนแบบเดียวกัน)
  - เช็ก `SELECT 1 FROM records WHERE id=?` ก่อนเขียนไฟล์ → ไม่พบตอบ 404 (เดิมปล่อยให้ INSERT
    ชน FK แล้วตอบ 500) และเพิ่ม `except sqlite3.IntegrityError` → 404 ดักกรณี record
    ถูกลบระหว่างเช็กกับ INSERT ส่วน DB error สาเหตุอื่นยังเป็น 500
  - frontend: `_uploadToFlask`/`_uploadToFlaskAnn` เดิมกลืนเหตุผลจาก server แล้วโชว์แค่
    "✗ อัปโหลดล้มเหลว" ตอนนี้ `toast()` ข้อความจริงจาก server เพราะด่านฝั่ง client ดูแค่
    นามสกุลกับขนาด ไฟล์ที่ตั้งชื่อ `.pdf` แต่ข้างในไม่ใช่จะผ่านมาถึง server ได้
  - ยืนยันด้วย HTTP จริง (Flask บน port 5055 ชี้ DB/uploads ไป temp dir ยิงด้วย `curl`):
    PDF จริง 201 และดาวน์โหลดกลับมาไบต์ตรงกัน / `MZ...` ตั้งชื่อ `.pdf` → 400 /
    ไฟล์ 0 ไบต์ → 400 / base64 ของไฟล์ที่ไม่ใช่ PDF → 400 / PDF 101 MB → 413 /
    PDF 120 MB → 413 ทุกเคสที่ถูกปฏิเสธไม่มีไฟล์เหลือใน `uploads/` และไม่มี row ใน DB
  - test เพิ่มเป็น 38 ตัว (จาก 29) ไม่มี `xfail` ค้างแล้ว

- **แก้ Important ข้อ 5 และ Minor ข้อ 24 (22/10/2026)** — Thai JSON, pin เวอร์ชัน และ test ชุดแรก
  - `app.config['JSON_AS_ASCII'] = False` → `app.json.ensure_ascii = False` ([app.py](app.py):21-28)
    Flask ย้ายกลไก JSON ไปที่ provider ตั้งแต่ 2.3 config key เดิมถูกเพิกเฉยแบบไม่ error
    ไม่เก็บ key เดิมไว้ควบคู่ เพราะ pin `flask==3.1.3` แล้ว การทิ้งโค้ดที่ไม่ทำอะไรไว้คือต้นเหตุของข้อนี้เอง
    วัดจริงกับ DB ปัจจุบัน (อ่านอย่างเดียว) `GET /api/records` ลดจาก 13,668 → 10,557 bytes (−22.8%)
  - [requirements.txt](requirements.txt) pin `flask==3.1.3`, `pywebview==6.2.1` (ทดสอบบน Python 3.14.7)
    และแยก [requirements-dev.txt](requirements-dev.txt) ไว้ใส่ `pytest==9.1.1` ไม่ให้ปนกับ runtime
  - เพิ่มชุด test ชุดแรกของโปรเจกต์: [pytest.ini](pytest.ini), [tests/conftest.py](tests/conftest.py),
    [tests/test_api.py](tests/test_api.py) — **29 test (27 passed, 2 xfailed) ใน 0.6 วินาที**
    ครอบข้อ 5 (4 ตัว), ข้อ 6 (11 ตัว), ข้อ 7 (6 ตัว, ใช้ fault injection), เส้นทางพื้นฐาน (5 ตัว)
    และข้อที่ยังไม่แก้ (10, 19) ทำเป็น `xfail(strict=True)` เพื่อให้ run แดงเตือนตอนมีคนแก้สำเร็จ
  - พิสูจน์ว่า test จับของจริง: mutate โค้ดย้อนกลับสถานะก่อนแก้ ข้อ 5 แดง 4 ตัว ข้อ 6 แดง 7 ตัว
  - test ไม่แตะข้อมูลจริง ทุกตัวชี้ `DB_PATH`/`UPLOAD_FOLDER` ไป `tmp_path` ตรวจหลังรันแล้วว่า
    `hr_promotion.db` ยังมี 4 records / 2 attachments และ `static/uploads/` ยังมี 2 ไฟล์เท่าเดิม

- **แก้ Important ข้อ 6 และ 7 (22/10/2026)** — ชื่อไฟล์ไทย และลำดับลบไฟล์เทียบกับ commit
  - เพิ่ม `sanitize_display_name()` ([app.py](app.py):116-136) แทน `secure_filename()` ที่ตัด
    อักขระ non-ASCII ทิ้งทั้งหมด (`'ประกาศ.pdf'` เคยเหลือ `'pdf'` ซึ่งไม่มีนามสกุลให้ Windows รู้จัก)
    ตัวใหม่ตัดเฉพาะ `\` `/` อักขระควบคุม `\x00-\x1f` และ `\x7f` แล้ว clamp ที่ 255 ตัวอักษร
    ไม่มีความเสี่ยง path traversal เพราะชื่อนี้เป็นแค่ display name — path จริงคือ UUID
    ลบ import `secure_filename` ออกแล้ว ทั้งโปรเจกต์ไม่เรียกอีก
  - เลื่อนการลบไฟล์ไปหลัง `commit()` ครบทั้ง 4 จุด: `upload_attachment` (จำ `old_path` ไว้
    แล้วลบหลัง commit), `delete_record`, `delete_all`, `delete_attachment`
    เดิมลบไฟล์ก่อน ถ้า DB ล้ม `rollback()` จะคืน row ที่ชี้ไปยังไฟล์ที่ถูกลบไปแล้ว
    ผู้ใช้เห็นว่ามีไฟล์แนบแต่กดดาวน์โหลดได้ 404 ซึ่งแย่กว่าไฟล์กำพร้าที่เสียแค่พื้นที่ดิสก์
  - ยืนยันด้วย fault injection จริง ครอบ connection ด้วย proxy ที่โยน `OperationalError`
    ตาม SQL ที่กำหนด ทดสอบ 6 สถานการณ์ (อัปโหลดทับสำเร็จ, INSERT ล้ม, `DELETE FROM records` ล้ม,
    ลบ record สำเร็จ, `DELETE FROM attachments` ล้มแล้วลองใหม่, `delete_all` ล้มและสำเร็จ)
    ไม่มีเคสใดเหลือ row ที่ชี้ไฟล์หาย และ end-to-end อัปโหลด `ผลงานเล่มที่1.pdf` ได้ชื่อไทยครบทั้งใน DB
    และใน header `filename*=UTF-8''...` ตอนดาวน์โหลด
  - ทดสอบบน DB ชั่วคราวใน temp dir ไม่แตะ `hr_promotion.db` (ยังมี 2 attachments ชื่อ ASCII เหมือนเดิม)

- **แก้ Important ข้อ 12 (22/10/2026)** — เปลี่ยน upload จาก base64-in-JSON เป็น multipart
  - `apiFetch` รับ `FormData` ได้ และปล่อยให้ browser ตั้ง `Content-Type` พร้อม boundary เอง
  - `_readAndCache` → `_cacheFile` เก็บ `File` object ตรง ๆ ไม่ใช้ `FileReader` อีกแล้ว
    `_uploadToFlask` / `_uploadToFlaskAnn` ส่ง `FormData` ให้ browser stream ไฟล์
  - `_openLocal` ใหม่ ใช้ blob URL แทน data URL สำหรับ preview/download ไฟล์ที่ยังไม่อัปโหลด
  - ลบ `pendingReads` และ guard ของมันออก เพราะไม่มีการอ่านไฟล์แบบ async แล้ว
  - `MAX_CONTENT_LENGTH` 200 MB → 110 MB และข้อความ 413 แก้เป็น 100 MB ให้ตรงกับที่ UI บังคับ
  - วัดจริงด้วยไฟล์ 3,145,728 bytes: multipart ส่ง 3,146,001 bytes (1.000x)
    ส่วน base64 ส่ง 4,194,405 bytes (1.333x) ต่างกัน 1,048,404 bytes ต่อไฟล์
  - route ฝั่ง server ยังรับ base64-in-JSON ไว้สำหรับ curl แต่เส้นทางนั้นอัปไฟล์ได้จริงราว 82 MB

- **แก้ Critical ข้อ 4 และ Important ข้อ 13 (22/10/2026)** — ปิดความเสี่ยงข้อมูลหายถาวรทั้งสองทาง
  - `_migrate_blobs_to_files` ([app.py](app.py):170-212) สำรอง `.db` เป็น `<db>.pre-migration.bak` ก่อนแตะข้อมูล
    และเปลี่ยนเป็น all-or-nothing ถ้าเขียนไฟล์แถวใดพลาดจะ rollback ลบไฟล์ที่เขียนไปแล้ว แล้ว raise
    แทนที่จะตั้ง `stored_name=''` แล้วเดินหน้าไป DROP คอลัมน์ `file_data` ทิ้ง
  - [fix_db.py](fix_db.py) เขียนใหม่ทั้งไฟล์ เช็กไฟล์ DB ก่อน connect, สำรองแบบติด timestamp,
    ครอบด้วยทรานแซกชัน, เลือก DEFAULT ตามชนิดคอลัมน์ (TEXT ได้ `''` / INTEGER ได้ `0`),
    ย้าย `PRAGMA foreign_keys=ON` ออกนอกทรานแซกชันให้มีผลจริง และบังคับ stdout เป็น UTF-8
  - ยืนยันด้วย repro จริง fault-inject ให้เขียนไฟล์ล้มแบบดิสก์เต็ม และรัน `fix_db.py` 6 สถานการณ์
    รวมทั้ง happy path ที่เทียบไฟล์บนดิสก์กับ BLOB ต้นฉบับทีละไบต์

- **แก้ Critical ข้อ 1 (22/10/2026)** — ย้ายไลบรารีทั้งหมดจาก CDN มา self-host
  - เพิ่ม `static/vendor/` 25 ไฟล์ รวม 1.1 MB ได้แก่ Bootstrap 5.3.2 (CSS + JS bundle),
    Chart.js 4.4.1, Font Awesome 6.4.2 (CSS + woff2 4 ไฟล์), Sarabun (CSS + woff2 16 ไฟล์
    ครบ 4 น้ำหนัก × 4 subset รวม thai)
  - `templates/index.html` ไม่มี URL ภายนอกเหลือเลย อ้างผ่าน `url_for('static', ...)` ทุกจุด
    ยกเว้น `printReport` ที่ใช้ `${location.origin}/static/vendor/sarabun.css` เพราะ popup เป็น `about:blank`
  - ยืนยันในเบราว์เซอร์ว่า resource ทั้ง 14 รายการมาจาก `127.0.0.1` ทั้งหมด ไม่มี host ภายนอก
    Chart.js, Bootstrap, Sarabun และ Font Awesome โหลดครบ กราฟวาดได้ 4 ตัว

- **แก้ Critical ข้อ 2 และ 3 (22/10/2026)** — เป็นการเปลี่ยน behavior ครั้งแรกนับจากรีวิว
  - `app.py:980` fallback mode ผูก `127.0.0.1` แทน `0.0.0.0` และ debug ปิดเป็นค่าเริ่มต้น
    เปิดได้ด้วย env `HR_DEBUG=1` เท่านั้น
  - `templates/index.html` เพิ่ม `esc()` (บรรทัด 627-630) แล้วครอบค่าจากผู้ใช้ทั้งหมดใน
    `renderTable` (10 จุด) และ `printReport` (25 จุด รวมแถวประเมิน 5.1/5.2) ปิด Stored XSS
  - ยืนยันด้วย repro จริงทั้งสองข้อ ไม่ได้ใช้ DB จริงในการทดสอบ (`hr_promotion.db` 4 records เท่าเดิม)

- **Code review รอบที่ 2 (22/10/2026)** — ฉบับปัจจุบันอยู่ที่ [CODE_REVIEW_2026-10-22.md](CODE_REVIEW_2026-10-22.md)
  พบ 25 ประเด็น: Critical 4 ข้อ, Important 9 ข้อ, Minor 12 ข้อ
  ประเด็นใหม่ที่รอบ 16/07 ไม่พบ 10 ข้อ ที่หนักสุดคือ frontend ดึง Bootstrap/Chart.js/Font Awesome/Sarabun
  จาก CDN ทั้งหมด แอปจึงใช้งานออฟไลน์ไม่ได้จริงทั้งที่แพ็กเป็น `.exe` (ข้อ 1) และ `_migrate_blobs_to_files`
  ทำลาย BLOB ที่เขียนไฟล์ไม่สำเร็จแล้ว commit ต่อ (ข้อ 4)
  ยืนยันด้วยการรันโค้ดจริงบน Flask 3.1.3 ว่า `JSON_AS_ASCII = False` ไม่มีผลแล้ว (`app.json.ensure_ascii` เป็น `True`)
  และ `secure_filename('ประกาศ.pdf')` คืน `'pdf'` โดยนามสกุลหายทั้งหมด

- **Code review รอบแรก (16/07/2026)** — [CODE_REVIEW.md](CODE_REVIEW.md) เก็บไว้เป็นประวัติ
  ทุกข้อในไฟล์นั้นถูกตรวจซ้ำในรอบที่ 2 แล้ว และมี 2 จุดที่ระบุเกินจริง (stat cards กับ label กราฟ
  ไม่ใช่ช่องทาง XSS) แก้ไว้ในหัวข้อ "ข้อแก้ไขจากรีวิว 16/07/2026" ของไฟล์ใหม่

- **เพิ่มคอมเมนต์ภาษาไทยทั่วทั้ง [app.py](app.py) และ [templates/index.html](templates/index.html)**
  (docstring/JSDoc อธิบาย logic, อัลกอริทึม, เหตุผลการออกแบบ, parameters และ return values
  ของทุกฟังก์ชัน) — ไม่มีการเปลี่ยน behavior ใด ๆ ตรวจ syntax ผ่านแล้วทั้ง
  `python -m py_compile` และ JS parse check

## ข้อควรระวังตอน build .exe

หลังแก้ข้อ 1 แล้ว PyInstaller **ต้อง** แพ็ก `static/vendor` ไปด้วย ไม่งั้น `.exe` จะหาไลบรารีไม่เจอ
แล้วกลับไปพังแบบเดิมทุกอย่าง (layout เพี้ยน, dashboard ตาย, toast แจ้ง error ไม่ได้)

```bash
pyinstaller --onefile --noconsole ^
  --add-data "templates;templates" ^
  --add-data "static/vendor;static/vendor" ^
  app.py
```

Flask serve `static/` จากตำแหน่งของโมดูล (ใต้ `sys._MEIPASS` เมื่อ frozen) ส่วนไฟล์ที่ผู้ใช้อัปโหลด
เขียนลง `BASE_DIR/static/uploads` ซึ่งอยู่ข้างตัว `.exe` คนละที่กัน จุดนี้ไม่กระทบกันเพราะ
`send_file` ใช้ absolute path อยู่แล้ว

## API / Data contracts

endpoint และ schema ไม่เปลี่ยน — ดูรายละเอียดครบใน [CLAUDE.md](CLAUDE.md)

สิ่งเดียวที่เปลี่ยนคือ `POST /api/records/<id>/attachments` ซึ่ง frontend ส่งเป็น
`multipart/form-data` (field `file` + `book_no`) แทน base64 ใน JSON ตั้งแต่ 22/10/2026
route ยังรับ JSON แบบเดิมอยู่ จึงไม่กระทบ client อื่นที่เรียกอยู่ก่อน

## สิ่งที่ควรทำต่อ (ถ้ามีโอกาส)

แก้แล้ว 12 ข้อจาก 25 ข้อ — Critical 4/4, Important 6/9 (5, 6, 7, 10, 12, 13), Minor 2/12 (19, 24)
ลำดับที่แนะนำอยู่ในตารางท้าย [CODE_REVIEW_2026-10-22.md](CODE_REVIEW_2026-10-22.md)

- ข้อ 8 — `_migrate_blobs_to_files` ยังไม่ idempotent จริง (SQL ขาด `WHERE stored_name IS NULL OR = ''`)
- ข้อ 9 — ส่ง `str(e)` ดิบกลับให้ client 8 จุด (information disclosure)
- ข้อ 11 — GET route 7 ตัวไม่มี try/finally connection รั่วเมื่อ query พัง
  (ตอนนี้มี `from contextlib import closing` ใน app.py แล้วจากการแก้ข้อ 19 ใช้ pattern เดียวกันได้เลย)
- ข้อ Minor ที่เหลือ (14-18, 20-23, 25) ทำตามสะดวก
- การแก้ข้อถัดไปควรเพิ่ม test คู่กันไปด้วย ชุดปัจจุบันมี 38 ตัว รันใน 0.7 วินาที
