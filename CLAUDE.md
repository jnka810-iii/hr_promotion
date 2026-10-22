# hr_promotion — CLAUDE.md

ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง  
Flask + SQLite3 + pywebview → Desktop `.exe`

## Run

```bash
python app.py        # pywebview window (or browser fallback on http://127.0.0.1:5000)
```

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest     # 38 tests, ~0.7s
```

Tests monkeypatch `DB_PATH` + `UPLOAD_FOLDER` to pytest's `tmp_path` — they must never touch
`hr_promotion.db` or `static/uploads/`. The `fail_sql` fixture deliberately avoids `monkeypatch`
(its `undo()` would restore the real paths mid-test). No `xfail` is currently parked; when you
write a test ahead of its fix, mark it `xfail(strict=True)` so the run goes red the moment the
fix lands and the marker has to be removed, instead of a `skip` that rots silently.

Runtime deps are pinned `==` in [requirements.txt](requirements.txt) — a floating Flask is what
silently killed `JSON_AS_ASCII`. Keep pytest out of it; it belongs in `requirements-dev.txt`.

## Architecture

Single-file backend: [app.py](app.py)

| Concern | Detail |
|---|---|
| DB | `hr_promotion.db` — WAL mode, foreign_keys ON, fresh connection per request |
| File storage | `static/uploads/<uuid>.pdf` — no BLOBs in DB. **Invariant:** every disk delete happens *after* `con.commit()` (read `stored_name` first, delete later). Deleting before commit means a rollback leaves a row pointing at a missing file — worse than an orphan file |
| Frontend libs | `static/vendor/` — Bootstrap, Chart.js, Font Awesome, Sarabun are **self-hosted**. Never move them back to a CDN: this ships as an offline `.exe`. PyInstaller must `--add-data "static/vendor;static/vendor"` |
| Upload method | Frontend sends `multipart/form-data` (field `file` + `book_no`); the route also still accepts base64-in-JSON for curl (`MAX_CONTENT_LENGTH` = 110 MB) |
| Upload validation | Server-side, **before touching disk**: non-empty, `<= MAX_PDF_SIZE` (100 MB, must match what the UI promises), `%PDF-` within the first 1024 bytes, and the record must exist. Both input paths share it. The client only checks extension + size, so never drop these |
| Thai JSON | `app.json.ensure_ascii = False` — required everywhere. **Not** `app.config['JSON_AS_ASCII']`: Flask ≥ 2.3 ignores that key silently, which escapes Thai to `\uXXXX` and grows the payload ~23% |
| BASE_DIR | Resolves for both `python app.py` and PyInstaller `.exe` via `sys.executable` check |
| Port | 5000 (hardcoded) |

## DB Schema

### `records`

Personnel data for one promotion candidate per row.

- `p1_*` — personal info (title, fname, lname, edu, edu_major)
- `p2_*` — current position (pos_current, pos_level, empid, dept, sec, org)
- `p3_*` — target position + announcement (same fields as p2 + p3_announcement)
- `p4_*` — work history: 3 work entries (title/year) + concept_title + work_award
- `p5_*` — evaluation results (doc_received, work_result, work_score, work_eval_date)
- `eval51_rows`, `eval52_rows` — JSON arrays (evaluation grid rows), stored as TEXT
- `created`, `updated` — datetime strings `dd/mm/YYYY HH:MM`

Required fields (validated in `validate_required()`): `p1_fname`, `p1_lname`, `p2_pos_current`, `p2_empid`

### `attachments`

PDF file metadata only. Physical file is in `static/uploads/`.

```
id, record_id (→ records.id ON DELETE CASCADE),
book_no (TEXT), filename (original name), stored_name (UUID .pdf),
mime_type, file_size, uploaded_at
```

Upload replaces existing `(record_id, book_no)` pair — old file deleted from disk.

## Key Functions

| Function | Purpose |
|---|---|
| `get_db()` | Open SQLite connection with WAL + FK |
| `sanitize_record(d)` | Whitelist RECORD_COLS; serialize eval*_rows → JSON string |
| `validate_required(allowed)` | Return list of missing Thai field labels |
| `sanitize_display_name(name, fallback)` | Clean the original filename for DB + `download_name` — strips path separators and control chars only, **keeps Thai**. Never use `secure_filename()` here: it drops all non-ASCII (`'ประกาศ.pdf'` → `'pdf'`) |
| `_safe_remove(path)` | Delete file, swallow errors |
| `_stored_path(stored_name)` | `UPLOAD_FOLDER / stored_name` |
| `init_db()` | Create tables + additive ALTER TABLE migrations |
| `_migrate_blobs_to_files(con)` | One-time: move old BLOB schema → file-based schema |

## API Routes

| Method | URL | Description |
|---|---|---|
| GET | `/api/records` | All records (ORDER BY id DESC) |
| GET | `/api/records/<id>` | Single record |
| POST | `/api/records` | Create record |
| PUT | `/api/records/<id>` | Update record |
| DELETE | `/api/records/<id>` | Delete record + cascade attachments + disk files |
| DELETE | `/api/records` | Delete ALL records |
| GET | `/api/records/<id>/attachments` | List attachments for record |
| POST | `/api/records/<id>/attachments` | Upload PDF (base64 JSON or multipart) — 400 not a PDF/empty, 404 no such record, 413 over 100 MB |
| GET | `/api/attachments/<att_id>` | Download PDF |
| GET | `/api/attachments/<att_id>/preview` | Inline preview PDF |
| DELETE | `/api/attachments/<att_id>` | Delete attachment + disk file |
| GET | `/api/stats` | Totals by result and level |
| GET | `/api/export/csv` | CSV export (UTF-8 BOM for Excel) |

## Utilities

- [check_db.py](check_db.py) — inspect DB state
- [fix_db.py](fix_db.py) — manual DB repair
- [tests/](tests/) — pytest suite (`conftest.py` holds the temp-DB fixtures)

## Adding Columns

Use `ALTER TABLE` in `init_db()` via the `new_cols` dict — backward-compatible, no downtime.  
Never `DROP` and recreate tables unless doing a full migration like `_migrate_blobs_to_files`.
