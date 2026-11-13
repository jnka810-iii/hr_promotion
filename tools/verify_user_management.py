# -*- coding: utf-8 -*-
"""
พิสูจน์ขอบเขตสิทธิ์ของแท็บจัดการผู้ใช้งาน กับเซิร์ฟเวอร์ที่รันจริง

    python tools/verify_user_management.py        # exit 0 = ผ่านทุกข้อ

สคริปต์นี้ยกเซิร์ฟเวอร์ของตัวเองขึ้นมาบนพอร์ต 5099 ชี้ DB, โฟลเดอร์อัปโหลด และ
โฟลเดอร์สำรองไปที่ temp dir ที่สร้างใหม่ทุกครั้ง **ไม่แตะ `hr_promotion.db` ของจริง**
แล้วลบทิ้งเมื่อจบ รันพร้อมกับที่เปิดโปรแกรมอยู่ได้

มีสองอย่างที่ pytest ทำไม่ได้ จึงต้องมีไฟล์นี้:

1. **การแข่งกันของสอง request** admin สองคนสั่งปิดบัญชีของกันและกันพร้อมกัน ต่างคน
   ต่างผ่านด่าน "ไม่ใช่ตัวเอง" ของตัวเอง ถ้าด่านสุดท้ายนับ admin *ก่อน* เขียน ทั้งคู่
   จะอ่าน snapshot ที่ถ่ายไว้ก่อนได้ write lock เห็นเลขเดียวกัน แล้ว commit ทั้งคู่
   จนไม่เหลือ admin ที่ใช้งานได้เลย ซึ่งกู้คืนได้ทางเดียวคือไปพิมพ์ที่เครื่องเซิร์ฟเวอร์
   test client ของ Flask เรียงลำดับ request ให้เสมอ จังหวะนี้จึงสร้างในนั้นไม่ได้
2. **เส้นทางจริงทั้งเส้น** cookie, ด่าน before_request, และการที่เซสชันถูกตัดกลางคัน
   ซึ่งของพวกนี้เดินผ่าน HTTP จริง ไม่ใช่ผ่าน test client

ชุดเทสต์ปกติยังต้องผ่านด้วย: `python -m pytest` และ `node --test tests/client.test.mjs`
"""
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PORT = 5099
BASE = f'http://127.0.0.1:{PORT}'

ADMIN_A = ('somchai', 'verify-password-1')
ADMIN_B = ('wichai.t', 'verify-password-4')
STAFF_A = ('malee.s', 'verify-password-2')
STAFF_B = ('nipaporn.k', 'verify-password-3')

_ok = _fail = 0


def check(label, got, want):
    global _ok, _fail
    if got == want:
        _ok += 1
        print(f'  ok    {label}  -> {got}')
    else:
        _fail += 1
        print(f'  ล้มเหลว {label}  -> {got}, ต้องการ {want}')


def session(user=None, password=None):
    """opener ที่ถือ cookie ของตัวเอง = หนึ่งเครื่องของผู้ใช้หนึ่งคน"""
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if user:
        body = urllib.parse.urlencode({'username': user, 'password': password}).encode()
        try:
            op.open(BASE + '/login', body)
        except urllib.error.HTTPError:
            return None
    return op


def call(op, method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    if body is not None:
        req.add_header('Content-Type', 'application/json')
        req.data = json.dumps(body).encode()
    try:
        with op.open(req) as r:
            return r.status, json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or '{}')
        except json.JSONDecodeError:
            return e.code, {'error': raw[:80]}


def boot(workdir):
    """เขียนสคริปต์เซิร์ฟเวอร์ลง workdir แล้วรันเป็น process ลูก"""
    script = os.path.join(workdir, 'serve.py')
    with open(script, 'w', encoding='utf-8') as f:
        f.write(f'''# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, {REPO!r})
import app as hr
hr.DB_PATH       = os.path.join({workdir!r}, 'verify.db')
hr.BACKUP_DIR    = os.path.join({workdir!r}, 'backup')
hr.UPLOAD_FOLDER = os.path.join({workdir!r}, 'uploads')
os.makedirs(hr.BACKUP_DIR, exist_ok=True)
os.makedirs(hr.UPLOAD_FOLDER, exist_ok=True)
hr.init_db()
from contextlib import closing
with closing(hr.get_db()) as con:
    hr.create_user(con, {ADMIN_A[0]!r}, {ADMIN_A[1]!r}, hr.ROLE_ADMIN, 'สมชาย ใจดี')
    hr.create_user(con, {ADMIN_B[0]!r}, {ADMIN_B[1]!r}, hr.ROLE_ADMIN, 'วิชัย ธรรมดี')
    hr.create_user(con, {STAFF_A[0]!r}, {STAFF_A[1]!r}, hr.ROLE_STAFF, 'มาลี ศรีสุข')
    hr.create_user(con, {STAFF_B[0]!r}, {STAFF_B[1]!r}, hr.ROLE_STAFF, 'นิภาพร คงดี')
    con.commit()
hr.app.run(host='127.0.0.1', port={PORT}, debug=False, use_reloader=False)
''')
    proc = subprocess.Popen([sys.executable, script],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', PORT)) == 0:
                return proc
        time.sleep(0.1)
    proc.terminate()
    sys.exit(f'เซิร์ฟเวอร์ไม่ขึ้นภายใน 10 วินาที (พอร์ต {PORT} ถูกใช้อยู่หรือเปล่า)')


def permission_checks(ids):
    admin = session(*ADMIN_A)
    staff = session(*STAFF_A)

    print('\nเจ้าหน้าที่ยกระดับตัวเองไม่ได้')
    check('สร้างบัญชีผู้ดูแลระบบ',
          call(staff, 'POST', '/api/users',
               {'username': 'hacker.x', 'display_name': 'ทดสอบ',
                'password': 'verify-password-9', 'role': 'admin'})[0], 403)
    check('เลื่อนบทบาทคนอื่นเป็นผู้ดูแลระบบ',
          call(staff, 'PATCH', f'/api/users/{ids[STAFF_B[0]]}', {'role': 'admin'})[0], 403)
    check('เลื่อนบทบาทตัวเอง',
          call(staff, 'PATCH', f'/api/users/{ids[STAFF_A[0]]}', {'role': 'admin'})[0], 403)
    check('ตั้งรหัสผ่านใหม่ให้ผู้ดูแลระบบ',
          call(staff, 'PUT', f'/api/users/{ids[ADMIN_A[0]]}/password',
               {'password': 'verify-password-9'})[0], 403)
    check('แก้ชื่อของผู้ดูแลระบบ',
          call(staff, 'PATCH', f'/api/users/{ids[ADMIN_A[0]]}',
               {'display_name': 'โดนแก้'})[0], 403)
    check('ปิดบัญชีคนอื่น',
          call(staff, 'PATCH', f'/api/users/{ids[STAFF_B[0]]}', {'is_active': False})[0], 403)

    print('\nเจ้าหน้าที่ทำงานที่ได้รับอนุญาตได้จริง')
    check('เห็นรายชื่อ', call(staff, 'GET', '/api/users')[0], 200)
    check('แก้ชื่อเจ้าหน้าที่คนอื่น',
          call(staff, 'PATCH', f'/api/users/{ids[STAFF_B[0]]}',
               {'display_name': 'นิภาพร คงดี'})[0], 200)
    st, body = call(staff, 'POST', '/api/users',
                    {'username': 'somsri.p', 'display_name': 'สมศรี พากเพียร',
                     'password': 'verify-password-5'})
    check('สร้างบัญชีเจ้าหน้าที่', st, 201)
    new_id = body.get('id')

    print('\nบัญชีที่เพิ่งสร้างใช้หน้าล็อกอินได้ทันที')
    fresh = session('somsri.p', 'verify-password-5')
    check('ล็อกอินด้วยบัญชีใหม่', fresh is not None, True)
    check('บัญชีใหม่ใช้ระบบได้', call(fresh, 'GET', '/api/users')[0], 200)

    print('\nปิดบัญชีแล้วถูกไล่ออกจากทุกเครื่องทันที')
    check('ผู้ดูแลระบบปิดบัญชี',
          call(admin, 'PATCH', f'/api/users/{new_id}', {'is_active': False})[0], 200)
    check('เซสชันที่ค้างอยู่ของบัญชีที่ถูกปิด', call(fresh, 'GET', '/api/users')[0], 401)

    print('\nตั้งรหัสผ่านใหม่แล้วถูกไล่ออกจากทุกเครื่อง')
    victim = session(*STAFF_B)
    check('เซสชันก่อนถูกรีเซ็ต', call(victim, 'GET', '/api/users')[0], 200)
    st, body = call(admin, 'PUT', f'/api/users/{ids[STAFF_B[0]]}/password',
                    {'password': 'verify-password-7'})
    check('ผู้ดูแลระบบตั้งรหัสผ่านใหม่', st, 200)
    check('ไล่ออกอย่างน้อยหนึ่งเครื่อง', body.get('revoked', 0) >= 1, True)
    check('เซสชันเดิมหลังถูกรีเซ็ต', call(victim, 'GET', '/api/users')[0], 401)

    print('\nทำกับบัญชีตัวเองไม่ได้ ยกเว้นแก้ชื่อ')
    me = ids[ADMIN_A[0]]
    check('ปิดบัญชีตัวเอง', call(admin, 'PATCH', f'/api/users/{me}', {'is_active': False})[0], 403)
    check('ลดบทบาทตัวเอง', call(admin, 'PATCH', f'/api/users/{me}', {'role': 'staff'})[0], 403)
    check('รีเซ็ตรหัสผ่านตัวเอง',
          call(admin, 'PUT', f'/api/users/{me}/password',
               {'password': 'verify-password-9'})[0], 403)
    check('แก้ชื่อตัวเอง', call(admin, 'PATCH', f'/api/users/{me}',
                                {'display_name': 'สมชาย ใจดี'})[0], 200)

    print('\nส่งค่าเดิมซ้ำไม่นับเป็นการแก้ไข')
    st, body = call(admin, 'PATCH', f'/api/users/{ids[STAFF_A[0]]}',
                    {'display_name': 'มาลี ศรีสุข'})
    check('ตอบ 200', st, 200)
    check('ไม่มีฟิลด์ไหนเปลี่ยน', body.get('changed'), [])

    print('\nข้อความถึงผู้ใช้สั้นพอที่จะไม่กลายเป็นกล่องเตือน')
    longest = ''
    for method, path, body in [
            ('PATCH', f'/api/users/{ids[ADMIN_A[0]]}', {'is_active': False}),
            ('POST', '/api/users', {'username': 'x', 'display_name': 'ก', 'password': '1'}),
            ('PATCH', '/api/users/99999', {'display_name': 'ก'}),
            ('PATCH', f'/api/users/{ids[STAFF_B[0]]}', {'ไม่รู้จัก': 1}),
    ]:
        msg = call(staff, method, path, body)[1].get('error', '')
        longest = max(longest, msg, key=len)
    check(f'ยาวสุด {len(longest)} ตัวอักษร', len(longest) <= 80, True)


def last_admin_race(workdir, ids, rounds=15):
    """
    สอง admin สั่งปิดบัญชีของกันและกันพร้อมกัน ต้องเหลือ admin ที่ใช้งานได้เสมอ

    รีเซ็ตให้ทั้งคู่เป็น admin ที่ใช้งานได้ก่อนทุกรอบ แล้วยิงสองเธรดพร้อมกัน
    ผลที่ยอมรับได้มีสามแบบ: คนชนะได้ 200, คนแพ้ได้ 409 (ด่านนับ admin หลังเขียน
    ทำงาน) หรือ 401 (เซสชันถูกตัดไปแล้วโดยคนที่ชนะ) ที่ยอมไม่ได้คือเหลือศูนย์
    """
    import sqlite3
    db = os.path.join(workdir, 'verify.db')
    print(f'\nแข่งกันปิดบัญชีของกันและกัน {rounds} รอบ')
    for n in range(1, rounds + 1):
        con = sqlite3.connect(db)
        con.execute("UPDATE users SET is_active=1, role='admin' WHERE username IN (?,?)",
                    (ADMIN_A[0], ADMIN_B[0]))
        con.commit()
        con.close()
        a, b = session(*ADMIN_A), session(*ADMIN_B)
        out = {}

        def hit(key, op, target):
            out[key] = call(op, 'PATCH', f'/api/users/{target}', {'is_active': False})[0]

        t1 = threading.Thread(target=hit, args=('a->b', a, ids[ADMIN_B[0]]))
        t2 = threading.Thread(target=hit, args=('b->a', b, ids[ADMIN_A[0]]))
        t1.start(); t2.start(); t1.join(); t2.join()

        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        left = con.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1").fetchone()[0]
        con.close()
        check(f'รอบ {n:2d} {out} เหลือผู้ดูแลระบบ {left}', left >= 1, True)


def main():
    workdir = tempfile.mkdtemp(prefix='hr-verify-')
    proc = boot(workdir)
    try:
        admin = session(*ADMIN_A)
        if admin is None:
            sys.exit('ล็อกอินไม่ผ่าน — เซิร์ฟเวอร์ตรวจไม่ขึ้นถูกต้องหรือเปล่า')
        rows = call(admin, 'GET', '/api/users')[1]['users']
        ids = {u['username']: u['id'] for u in rows}
        permission_checks(ids)
        last_admin_race(workdir, ids)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(workdir, ignore_errors=True)

    print(f'\nผ่าน {_ok} ข้อ ไม่ผ่าน {_fail} ข้อ')
    return 1 if _fail else 0


if __name__ == '__main__':
    sys.exit(main())
