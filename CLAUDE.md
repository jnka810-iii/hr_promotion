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
python -m pytest     # 83 tests, ~1.7s
```

Tests monkeypatch `DB_PATH` + `UPLOAD_FOLDER` to pytest's `tmp_path` — they must never touch
`hr_promotion.db` or `static/uploads/`. The `fail_sql` fixture deliberately avoids `monkeypatch`
(its `undo()` would restore the real paths mid-test). That injector also records `close()`, so
`fail_sql.unclosed()` reports connections a route opened and never closed. That is what pins the
connection invariant under Architecture across all 14 routes, instead of eyeballing each site.
No `xfail` is currently parked; when you
write a test ahead of its fix, mark it `xfail(strict=True)` so the run goes red the moment the
fix lands and the marker has to be removed, instead of a `skip` that rots silently.

Runtime deps are pinned `==` in [requirements.txt](requirements.txt) — a floating Flask is what
silently killed `JSON_AS_ASCII`. Keep pytest out of it; it belongs in `requirements-dev.txt`.

## Architecture

Single-file backend: [app.py](app.py)

| Concern | Detail |
|---|---|
| DB | `hr_promotion.db` — WAL mode, foreign_keys ON, fresh connection per request. **Invariant:** every `get_db()` call site closes deterministically. Read paths use `with closing(get_db()) as con:`; write paths use `try/except/finally: con.close()` because they also need rollback. A bare `con.close()` after `execute()` is skipped when the query raises, which leaks an OS handle on the DB file until a GC pass |
| File storage | `static/uploads/<uuid>.pdf` — no BLOBs in DB. **Invariant:** every disk delete happens *after* `con.commit()` (read `stored_name` first, delete later). Deleting before commit means a rollback leaves a row pointing at a missing file — worse than an orphan file |
| Frontend libs | `static/vendor/` — Bootstrap, Chart.js, Font Awesome, Sarabun are **self-hosted**. Never move them back to a CDN: this ships as an offline `.exe`. PyInstaller must `--add-data "static/vendor;static/vendor"` |
| Upload method | Frontend sends `multipart/form-data` (field `file` + `book_no`); the route also still accepts base64-in-JSON for curl (`MAX_CONTENT_LENGTH` = 110 MB) |
| CSV export | `mimetype` must carry **no** parameter. Werkzeug appends its own `charset`, so `mimetype='text/csv; charset=X'` ships two of them and the client reads the first. The UTF-8 BOM, not the header, is what makes Excel read Thai. Every cell goes through `csv_cell()`; `exportAllCSV` in the template holds the twin rule and the two must change together |
| Upload validation | Server-side, **before touching disk**: non-empty, `<= MAX_PDF_SIZE` (100 MB, must match what the UI promises), `%PDF-` within the first 1024 bytes, and the record must exist. Both input paths share it. The client only checks extension + size, so never drop these |
| Error responses | Routes **never** put `str(e)` in a response: it leaked absolute paths and SQL text, and `toastError` escalates anything over 80 characters to a blocking `alert()`. `fail()` is the one place that decides what a client sees; detail goes to the log. Keep every user-facing message under 80 characters |
| Logging | `BASE_DIR/hr_promotion.log`, rotating at 1 MB × 3, wired in `_setup_logging()` from `main()` **only**. Not at import, or every test run would drop a log file in the repo. It is attached to both `log` and `app.logger`, so caught and unhandled tracebacks both land there. This file is the only remaining copy of the detail the client no longer gets |
| Attachment slots | `book_no` must be one of `BOOK_SLOTS` = `{'1','2','3','ann'}`, the four slots the UI has. It arrives straight from the client, so anything else becomes a file no screen shows and no user can delete |
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

`UNIQUE INDEX idx_att_record_book ON attachments(record_id, book_no)` holds the one-file-per-slot
rule. **Invariant:** the rule lives in the schema, not in the route's statement order. The same
index serves `WHERE record_id=?` and the `ON DELETE CASCADE` as a leftmost prefix, so no separate
index on `record_id` is needed. SQLite cannot `ALTER TABLE ADD CONSTRAINT`, which is why this is an
index and not a table-level `UNIQUE`.

**Invariant:** the replace step reads the displaced `stored_name` from `DELETE ... RETURNING`, never
from a `SELECT` before it. A separate `SELECT` runs in the request's own snapshot, taken before the
write lock, so concurrent uploads to one slot all read the same old name and the files the losers
displaced are never deleted. Measured before the fix: 4 concurrent uploads left 2 orphans, 8 left 5.

## Key Functions

| Function | Purpose |
|---|---|
| `get_db()` | Open SQLite connection with WAL + FK |
| `sanitize_record(d)` | Whitelist RECORD_COLS; serialize eval*_rows → JSON string |
| `validate_required(allowed)` | Return list of missing Thai field labels |
| `sanitize_display_name(name, fallback)` | Clean the original filename for DB + `download_name` — strips path separators and control chars only, **keeps Thai**. Never use `secure_filename()` here: it drops all non-ASCII (`'ประกาศ.pdf'` → `'pdf'`) |
| `csv_cell(value)` | Prefix a cell with `'` when it starts with `=`, `+`, `-`, `@`, tab or CR, so Excel shows it as text instead of running it as a formula. CSV quoting does not help: Excel strips the quotes first. Lives at the export, not at input validation, because the export reads the DB directly and `fix_db.py` edits it by hand |
| `_dedupe_attachment_slots(con)` | Keep the highest `id` per `(record_id, book_no)` and return the displaced files for deletion after commit. Runs before `CREATE UNIQUE INDEX`, because an older DB can already hold duplicates and the index would otherwise raise inside `init_db()`, which stops the app from opening at all |
| `fail(context, message, status)` | Log the live exception with `log.exception`, return a fixed Thai message. **Call only from inside an `except` block** — `log.exception` reads `sys.exc_info()`, so outside one it records `NoneType: None`. Statuses stay per-route: 422 for an integrity violation in `add_record`, 404 for a record that vanished mid-upload |
| `_setup_logging()` | Attach the rotating file handler. Called from `main()`, never at import |
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
| POST | `/api/records/<id>/attachments` | Upload PDF (base64 JSON or multipart) — 400 not a PDF/empty/`book_no` outside `BOOK_SLOTS`, 404 no such record, 413 over 100 MB |
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
A constraint needs an index instead, created at the end of `init_db()`. Anything that can fail on
existing data must clean that data first in the same transaction, the way
`_dedupe_attachment_slots` does; a migration that raises here means the app never opens.  
Never `DROP` and recreate tables unless doing a full migration like `_migrate_blobs_to_files`.
