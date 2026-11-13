# hr_promotion — CLAUDE.md

ระบบบันทึกข้อมูลบุคคลและประเมินผลงานเพื่อเลื่อนระดับตำแหน่ง  
Flask + SQLite3 + pywebview → Desktop `.exe`

## Run

```bash
python app.py        # pywebview window (or browser fallback on http://127.0.0.1:5000)
```

Every route needs a login, so a fresh install needs an account before it opens at all:

```bash
python manage_users.py add <username> --role admin     # prompts for the password
```

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest                      # 183 tests, ~6s    — backend
node --test tests/client.test.mjs     # 19 tests, ~0.4s   — frontend
```

Two commands, both must pass. `node --test tests/` does **not** work on this platform (node reads
the directory as a module) — name the file. No `package.json` and no JS dependency: the frontend
suite runs on `node --test` built into node 22 plus a hand-rolled DOM in
[tests/client_harness.mjs](tests/client_harness.mjs). Do not add jest/vitest/jsdom for it.

The harness reads the live `<script>` out of [templates/index.html](templates/index.html) and
imports it as a module, so it tests the shipped code, not a copy. Two things it has to keep doing:
it asserts every name it pulls out still exists (rename one and you get a named error, not a suite
that silently tests nothing), and it appends a unique comment per load to bust node's module cache
— data: URL imports are cached by specifier, so without it every test shares one module instance
and `allRecords` / `loadState` leak across tests while the suite still reports green.

Tests monkeypatch `DB_PATH` + `UPLOAD_FOLDER` to pytest's `tmp_path` — they must never touch
`hr_promotion.db` or `static/uploads/`. A test that calls `main()` must also patch `_backup_db`
(or `BACKUP_DIR`, as the `backup_app` fixture in [tests/test_backup.py](tests/test_backup.py)
does): `main()` backs the DB up, so an unpatched one snapshots the **real** database into the
repo's `backup/`. Same rule, one more path. The `fail_sql` fixture deliberately avoids `monkeypatch`
(its `undo()` would restore the real paths mid-test). That injector also records `close()`, so
`fail_sql.unclosed()` reports connections a route opened and never closed. That is what pins the
connection invariant under Architecture across all 14 routes, instead of eyeballing each site.
That injector skips the auth gate's own `SELECT` (matched on `AUTH_SQL_MARK`): failing it would
kill every request before its route ran and quietly turn the whole connection suite into a test of
the gate, still green. The gate has its own test that forces `resolve_session` to raise.

The `client` fixture is **logged in as admin** — every route is closed otherwise, and the 124
pre-login tests pass unchanged because `make_record` / `upload` / `download` all hang off it.
Use `anon_client` for "not logged in" and `staff_client` for role limits. `hash_password` is
monkeypatched to one pbkdf2 round in the fixtures: scrypt is deliberately slow, and paying it
three times per test turns a 3-second suite into a minute. One test pins the real KDF.
**There is no flag that disables auth for tests and there must not be** — the day someone sets it
in production, every test would still be green.
`test_every_route_is_closed_without_a_session` walks Flask's own `url_map`, not a hand-written
list, so a route added without auth goes red with nobody having to remember this file.
No `xfail` is currently parked; when you
write a test ahead of its fix, mark it `xfail(strict=True)` so the run goes red the moment the
fix lands and the marker has to be removed, instead of a `skip` that rots silently.

Runtime deps are pinned `==` in [requirements.txt](requirements.txt) — a floating Flask is what
silently killed `JSON_AS_ASCII`. Keep pytest out of it; it belongs in `requirements-dev.txt`.

## Health check

```bash
python check_health.py    # exit 0 = all clear, 1 = something to fix
```

Read-only (every connection uses `mode=ro`), so it runs with the app open. It checks the **real**
installation, which pytest cannot: pytest points `DB_PATH` at `tmp_path` and must never touch the
live files. Distinct from [check_db.py](check_db.py), which inspects schema and writes
`PRAGMA journal_mode` to the live DB.

It verifies live-DB integrity, that no ref's history carries the DB file, that `.gitignore` covers
it, and that backups are readable and not in WAL mode. The load-bearing number is the orphan count:
uploaded PDFs with no `attachments` row pointing at them. That is how far the DB has drifted from
disk, and it is the number that exposed the git-tracking bug in the first place. The 54 orphans
(617 MB) left over from that bug are gone; as of 10/11/2026 the script exits 0 on this
installation, with the live DB holding 0 records.

## Architecture

Single-file backend: [app.py](app.py)

| Concern | Detail |
|---|---|
| DB file | `hr_promotion.db` next to the program is the **only** real store, and it is **not tracked by git**. It used to be: every `git checkout` / branch switch / `git stash` then overwrote real data with the committed version — only two commits ever held it, both April 2026, both carrying the same 4 trial records. That is what "the app only ever shows test data, and it keeps coming back" was. `.gitignore` already listed `*.db`, which does nothing for an already-tracked file. **Never `git add -f` it** — `test_db_file_is_not_tracked_by_git` is the guard. The giveaway was asymmetry: `static/uploads/*.pdf` is ignored so files survived every checkout while the `attachments` rows pointing at them did not, leaving 54 orphan PDFs (617 MB) against 0 rows. Removing it from the index does **not** close the hole — every old commit and every branch still carries the file, so checking one out clobbers the live data again. Rationale: [docs/adr/0002](docs/adr/0002-live-database-stays-out-of-version-control.md) |
| Backup | `backup/hr_promotion-YYYYMMDD.db`, one per day, `BACKUP_KEEP` = 14, written by `_backup_db()` from `main()` **before** `init_db()` — `init_db()` runs migrations that change data and `_dedupe_attachment_slots` deletes displaced files, so the snapshot has to predate it. Uses `sqlite3.Connection.backup()`, never `shutil.copy`: copying a live WAL database file gives an inconsistent snapshot. Sets `journal_mode=DELETE` on the copy so each backup is one self-contained file rather than three that must travel together. First snapshot of the day wins — re-running never overwrites, otherwise launching the app after data was wiped would overwrite a good snapshot with an empty one. Backup failure never blocks startup. `backup/` is gitignored: git is the reason this exists |
| DB | `hr_promotion.db` — WAL mode, foreign_keys ON, fresh connection per request. **Invariant:** every `get_db()` call site closes deterministically. Read paths use `with closing(get_db()) as con:`; write paths use `try/except/finally: con.close()` because they also need rollback. A bare `con.close()` after `execute()` is skipped when the query raises, which leaks an OS handle on the DB file until a GC pass |
| File storage | `static/uploads/<uuid>.pdf` — no BLOBs in DB. **Invariant:** every disk delete happens *after* `con.commit()` (read `stored_name` first, delete later). Deleting before commit means a rollback leaves a row pointing at a missing file — worse than an orphan file |
| Data load state | `allRecords` alone cannot say whether `0` means empty or unknown, so `loadState` carries it: `loading` / `loaded` / `stale` / `failed`. **Invariant:** `0` reaches the screen only in `loaded`; `loading` and `failed` show `UNKNOWN` at all three counters (`badge-count`, `rec-shown`, `rec-total`). The three used to read `0` for every one of those states, which is how "the app shows 0 records" was indistinguishable from "there are no records". `loadAll()` **never throws** — it catches once, toasts, and returns `loadState`; its 7 call sites previously had zero catches between them, so the server's Thai error message died in an unhandled rejection. See [GLOSSARY.md](GLOSSARY.md) |
| Offline fallback | LocalStorage mode is entered **only when there is no backend** (`fetch` rejects, so the error carries no `httpStatus`). A `5xx` means the backend exists and its data is intact: stay in Flask mode, amber light, report the error. Switching to an empty LocalStorage on a `500` answers from the wrong store and reports `0 รายการ` while SQLite holds the rows. `apiFetch` tags `e.httpStatus` purely so these two can be told apart — do not collapse `detectMode`'s two `catch` branches back into one. Rationale: [docs/adr/0001](docs/adr/0001-fallback-only-when-there-is-no-backend.md) |
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
| Bind host | `_server_bind()` reads `HR_HOST` (default `127.0.0.1`) and `HR_DEBUG` for the no-pywebview path only; the desktop window always binds loopback. `run_server.bat` sets `HR_HOST=0.0.0.0` so the LAN can reach it. **Invariant:** debug is forced off whenever the host is not loopback — the Werkzeug debugger runs arbitrary Python, so debug + LAN is remote code execution. `run.bat` / `run_browser.bat` never set `HR_HOST` |
| Authentication | `require_login` is a `before_request` hook, **not** a decorator per route: a decorator you forget leaves the new route open and silent, a hook leaves it closed and makes opening one a visible line in `PUBLIC_ENDPOINTS` (`login`, `static` — nothing else). **Invariant:** no exemption for loopback (behind a reverse proxy `remote_addr` becomes `127.0.0.1` for everyone at once) and none for an empty `users` table (that would reopen the door on every restore of the wrong backup — it is a message on the login page instead). The gate also 404s `static/uploads/*`: the PDFs sit under `static/` by accident of layout, and the app only ever links to `/api/attachments/<id>`. Rationale: [docs/adr/0004](docs/adr/0004-the-security-boundary-is-the-office-lan.md) |
| Session | Row in `sessions` + a `secrets.token_urlsafe(32)` cookie, **not** `flask.session`. No `SECRET_KEY` exists anywhere — `app.secret_key` must stay `None` — and logout/disable/password-change revoke for real. 12 h, or 30 days with "remember me"; expiry slides only when under half the window is left, so one session is written at most once per 6 h instead of taking the write lock on every request. Cookie is `HttpOnly` + `SameSite=Lax` + **not** `Secure` (plain HTTP on the LAN). `SameSite=Lax` is the only CSRF defence in the system — there is no CSRF token, so never change it to `None`. Rationale: [docs/adr/0003](docs/adr/0003-sessions-live-on-the-server-not-in-a-signed-cookie.md) |
| Roles | `admin` / `staff`, one TEXT column with a `CHECK` constraint so a typo like `Admin` cannot sit in the DB passing every "not an admin" test (SQLite cannot add a CHECK later). `staff` does all day-to-day work; `admin` additionally gets `DELETE /api/records`. **Invariant:** wiping all records must never touch `users` or `sessions` — whoever pressed it would lock themselves out permanently. Hiding the button in the template is tidiness; `require_admin` is the enforcement |
| Accounts | `manage_users.py` on the server console only — `add` / `list` / `passwd` / `role` / `disable` / `enable` / `logout-all`. **No delete command:** a reused username makes two people indistinguishable in the log, so retired accounts are disabled, which also cuts their sessions immediately. Password policy is length only (≥ 8); scrypt via `werkzeug.security`, which is why `werkzeug==3.1.9` is now pinned explicitly instead of riding along with Flask |
| Audit | One `log.info` line per mutation carrying the username, into the existing `hr_promotion.log`. No `audit` table: a table with no screen is a table nobody reads, and it would drag in questions (who may read it, may it be deleted, how is it backed up) that are not worth answering yet. Accepted limit: it rotates away at 1 MB × 3 and is only readable on the server machine |
| Comparable time | `ts_now()` / `ts_after()` return `YYYY-MM-DD HH:MM:SS` and are **not** interchangeable with `now_str()`. `dd/mm/YYYY` sorts `02/01/2026` before `31/12/2025`, so any column answering "has this expired" (`sessions.expires_at`, `users.locked_until`) must use the former |

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

Required fields live in **one** place, `REQUIRED_FIELDS` in [app.py](app.py) — 13 columns, each
mapped to the Thai label the user sees. `index()` hands the list to the page, which derives the
asterisks, the `required` attributes and the pre-submit check from it; the markup carries none of
those itself. **Invariant:** it travels as a list of pairs, not a dict — Jinja `tojson` sorts keys,
and the order of that list is the order the "you missed these" message names fields on both sides.
Labels must stay unique: `ตำแหน่งเลขที่` exists in two sections, so each carries its section.

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

### `users` / `sessions`

```
users:    id, username (UNIQUE, lowercased), password_hash, role (CHECK admin|staff),
          is_active, failed_count, locked_until, created, updated
sessions: token (PK), user_id (→ users.id ON DELETE CASCADE), expires_at, remember, created
          + INDEX idx_sessions_user ON sessions(user_id)
```

**Invariant:** no foreign key from `users` to `records`. The person who logs in and the person
being evaluated are different things even when they are the same human — deleting a record must
never touch an account, and disabling an account must never touch a record.

`idx_sessions_user` is not an index "just in case": revoking every session of one user happens on
`logout-all`, password change and disable, and all three look up by `user_id`, which the primary
key on `token` cannot serve. Usernames are ASCII-only (`a-z 0-9 . _ -`, 3–32) because they land in
the audit log and are reserved forever; allowing non-ASCII would admit a Cyrillic `аdmin` that no
one can tell from the Latin one by eye.

Both tables are created by `init_db()`, so an upgraded DB gets them empty and **nobody can open
the app until `manage_users.py add` runs** — intended, not a bug.

## Key Functions

| Function | Purpose |
|---|---|
| `get_db()` | Open SQLite connection with WAL + FK |
| `sanitize_record(d)` | Whitelist RECORD_COLS; serialize eval*_rows → JSON string |
| `validate_required(allowed)` | Return list of missing Thai field labels |
| `required_error(missing)` | Build the 422 for missing fields. `error` is what the client shows, so it names the field only when exactly one is missing and counts them otherwise — all 13 labels run to 303 characters, well past the 80 that turns a toast into a blocking `alert()`. The full list goes in `missing` for callers that read it |
| `sanitize_attachment_name(name, fallback)` | Clean the original filename for DB + `download_name` — strips path separators and control chars only, **keeps Thai**. Never use `secure_filename()` here: it drops all non-ASCII (`'ประกาศ.pdf'` → `'pdf'`). Named for the filename it cleans, not for a person's name: a people-name sanitizer lives elsewhere |
| `csv_cell(value)` | Prefix a cell with `'` when it starts with `=`, `+`, `-`, `@`, tab or CR, so Excel shows it as text instead of running it as a formula. CSV quoting does not help: Excel strips the quotes first. Lives at the export, not at input validation, because the export reads the DB directly and `fix_db.py` edits it by hand |
| `_dedupe_attachment_slots(con)` | Keep the highest `id` per `(record_id, book_no)` and return the displaced files for deletion after commit. Runs before `CREATE UNIQUE INDEX`, because an older DB can already hold duplicates and the index would otherwise raise inside `init_db()`, which stops the app from opening at all |
| `fail(context, message, status)` | Log the live exception with `log.exception`, return a fixed Thai message. **Call only from inside an `except` block** — `log.exception` reads `sys.exc_info()`, so outside one it records `NoneType: None`. Statuses stay per-route: 422 for an integrity violation in `add_record`, 404 for a record that vanished mid-upload |
| `_setup_logging()` | Attach the rotating file handler. Called from `main()`, never at import |
| `authenticate(con, user, pw)` | Return `(user_row, reason)`. The reason is for the **log only** — the client sees one message for every failure, or the login page becomes a way to ask which accounts exist. An unknown username is still checked against a throwaway hash so the reply does not come back visibly faster. Counts failures and locks the account for 5 minutes after 5, never permanently: a permanent lock hands anyone a way to keep a colleague out all day by typing a wrong password five times |
| `resolve_session(con, token)` | Token → user row, `None` for every unusable case (absent, expired, disabled). Deletes the expired row and revokes a disabled user's sessions on the way. Slides the expiry only when under half the window is left |
| `set_password` / `set_active` / `revoke_sessions` | All take `con` and **do not commit** — the caller owns the transaction so "new password + old sessions gone" is one atomic step. `set_password` deliberately does not revoke: the caller decides, because a user changing their own password must not be kicked off the machine they are typing on, while an admin reset must clear every machine |
| `audit(action, detail)` | One `log.info` line naming the logged-in user |
| `_safe_next(target)` | Keep `?next=` to a local path. `//host` borrows the current scheme and would turn our login page into a redirector to someone else's site |
| `_safe_remove(path)` | Delete file, swallow errors |
| `_stored_path(stored_name)` | `UPLOAD_FOLDER / stored_name` |
| `init_db()` | Create tables + additive ALTER TABLE migrations |
| `_migrate_blobs_to_files(con)` | One-time: move old BLOB schema → file-based schema. **Invariant:** a failure at any step leaves no file on disk that no row points at. Every step after the `.db` backup is one transaction, so the DB rolls itself back, but files have no transaction — one `except` rolls back, removes what it wrote and re-raises. Filtering already-migrated rows with a `WHERE` does not give you this: a rollback resets `stored_name` on every row, so there is no intermediate state to filter |

## API Routes

Every route below needs a session. `/api/*` answers **401 + a Thai message** when there is none
(never an empty list — that would report "0 records" while SQLite holds them); page routes
redirect to `/login?next=…`.

| Method | URL | Description |
|---|---|---|
| GET/POST | `/login` | Login form / check credentials. Public. Plain form post, no JS |
| POST | `/logout` | Delete this session row. POST so a stray link cannot sign people out |
| GET/POST | `/change-password` | Own password, requires the current one, revokes every other session |
| GET | `/api/records` | All records (ORDER BY id DESC) |
| GET | `/api/records/<id>` | Single record |
| POST | `/api/records` | Create record |
| PUT | `/api/records/<id>` | Update record |
| DELETE | `/api/records/<id>` | Delete record + cascade attachments + disk files |
| DELETE | `/api/records` | Delete ALL records — **`admin` only**, 403 for `staff`. Never touches `users` / `sessions` |
| GET | `/api/records/<id>/attachments` | List attachments for record |
| POST | `/api/records/<id>/attachments` | Upload PDF (base64 JSON or multipart) — 400 not a PDF/empty/`book_no` outside `BOOK_SLOTS`, 404 no such record, 413 over 100 MB |
| GET | `/api/attachments/<att_id>` | Download PDF |
| GET | `/api/attachments/<att_id>/preview` | Inline preview PDF |
| DELETE | `/api/attachments/<att_id>` | Delete attachment + disk file |
| GET | `/api/stats` | Totals by result and level |
| GET | `/api/export/csv` | CSV export (UTF-8 BOM for Excel) |

## Utilities

- [manage_users.py](manage_users.py) — the only way to create or change an account. Backs the DB
  up before `init_db()`, same order as `main()`, because it may be the first thing run after an
  upgrade. Passwords come from a prompt, never from `argv` (shell history, process list)
- [check_db.py](check_db.py) — inspect DB state
- [fix_db.py](fix_db.py) — manual DB repair
- [tests/](tests/) — pytest suite (`conftest.py` holds the temp-DB fixtures)

## Adding Columns

Use `ALTER TABLE` in `init_db()` via the `new_cols` dict — backward-compatible, no downtime.
A constraint needs an index instead, created at the end of `init_db()`. Anything that can fail on
existing data must clean that data first in the same transaction, the way
`_dedupe_attachment_slots` does; a migration that raises here means the app never opens.  
Never `DROP` and recreate tables unless doing a full migration like `_migrate_blobs_to_files`.
