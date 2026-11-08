"""คำสั่งดูแลระบบบน Server — รันในนามผู้ใช้ของแอป:

    sudo -u scannotify .venv/bin/python manage.py <คำสั่ง>

  init-admin            สร้างบัญชี admin ครั้งแรก (แสดงรหัสชั่วคราวครั้งเดียว ต้องเปลี่ยนเมื่อล็อกอิน)
  reset-admin           ลืมรหัส: ออกรหัสชั่วคราวใหม่ + บังคับเปลี่ยน + ทุก session หลุด
  backup [--keep=30] [--dir=data/backups]
                        สำรอง SQLite ด้วย VACUUM INTO (ทำได้ขณะระบบทำงาน) สิทธิ์ 600 เก็บ N ไฟล์ล่าสุด
  can-restart           exit 0 ถ้า restart ได้ / 1 ถ้ามี job ส่งย้อนหลังกำลังรัน (deploy.sh ใช้)
  reset-checkpoint      หลังกู้ไฟล์สำรอง: Monitor เริ่มนับจาก scan ล่าสุด ไม่ส่ง scan เก่าซ้ำ (หยุดบริการก่อน)
  tiger-check           ตรวจการเชื่อมต่อฐานข้อมูลเครื่องสแกน (SELECT อย่างเดียว) แสดงชื่อคอลัมน์ และเทียบเลขกับ hosoffice
  check                 ตรวจ .env, hosoffice (READ ONLY + อ่านได้ครบ 4 ตาราง — SELECT อย่างเดียว),
                        ต่อ MOPH Alert ได้ (เปิด TCP เท่านั้น ไม่ส่งข้อความ), มีบัญชี admin
"""
import os
import socket
import sys
from datetime import datetime
from urllib.parse import urlsplit

import auth
import database
import hosoffice
import settings
import tigersoft

DEFAULT_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'backups')
EXTRA_TABLES = ('leave_holiday', 'leave_register', 'fingle_scan_notify_mute')


def _opts(args):
    out = {}
    for a in args:
        if a.startswith('--') and '=' in a:
            k, v = a[2:].split('=', 1)
            out[k] = v
    return out


# ── admin ─────────────────────────────────────────────────────
def cmd_init_admin(args):
    try:
        temp = auth.create_admin()
    except auth.AuthError as e:
        print(f'✗ {e}')
        return 1
    _print_temp(temp)
    return 0


def cmd_reset_admin(args):
    _print_temp(auth.create_admin(reset=True))
    print('ทุก session ที่ล็อกอินอยู่ถูกออกจากระบบแล้ว')
    return 0


def _print_temp(temp):
    print('บัญชี: admin')
    print(f'รหัสชั่วคราว: {temp}')
    print('แสดงครั้งนี้ครั้งเดียว — ล็อกอินแล้วระบบจะให้เปลี่ยนรหัสทันที')


# ── backup ────────────────────────────────────────────────────
def cmd_backup(args):
    o = _opts(args)
    keep = max(1, int(o.get('keep', 30)))
    out_dir = o.get('dir', DEFAULT_BACKUP_DIR)
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    path = os.path.join(out_dir, f'app_config-{datetime.now():%Y%m%d-%H%M%S}.db')
    old_umask = os.umask(0o077)
    try:
        conn = database.get_sqlite_conn()
        conn.execute('VACUUM INTO ?', (path,))
        conn.close()
    finally:
        os.umask(old_umask)
    os.chmod(path, 0o600)
    print(f'สำรองแล้ว: {path} ({os.path.getsize(path):,} ไบต์)')
    backups = sorted(f for f in os.listdir(out_dir) if f.startswith('app_config-') and f.endswith('.db'))
    for f in backups[:-keep]:
        os.remove(os.path.join(out_dir, f))
    return 0


# ── can-restart ───────────────────────────────────────────────
def cmd_can_restart(args):
    job = database.get_running_backfill_job()
    if job:
        print(f'✗ มีงานส่งย้อนหลังกำลังทำงาน (job #{job["id"]}) — รอให้เสร็จหรือกดหยุดที่หน้า 🔁 ก่อน restart')
        return 1
    print('✓ restart ได้ (ไม่มีงานส่งย้อนหลังค้าง)')
    return 0


# ── reset-checkpoint ──────────────────────────────────────────
def cmd_reset_checkpoint(args):
    database.update_last_scan_id(0)
    print('✓ ล้างตำแหน่ง scan แล้ว — Monitor รอบถัดไปจะเริ่มนับจาก scan ล่าสุดของ hosoffice (ไม่ส่งย้อนหลัง)')
    print('  scan ที่ตกหล่นช่วงนี้ ส่งด้วยหน้า 🔁 ตรวจสอบและส่งย้อนหลัง')
    return 0


# ── check ─────────────────────────────────────────────────────
def tcp_reachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=8):
            return True
    except OSError:
        return False


def _scalar(conn, sql):
    cur = conn.cursor()
    try:
        cur.execute(sql)            # ทุกคำสั่งที่ส่งมาเป็น SELECT ... (LIMIT 1 / ค่าเดียว) จึงไม่มีแถวค้าง
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def _session_read_only(conn):
    for var in ('@@session.transaction_read_only', '@@session.tx_read_only'):   # MySQL 8 / MariaDB
        try:
            return str(_scalar(conn, f'SELECT {var}')) == '1'
        except hosoffice.Error:
            continue
    return False


def cmd_check(args):
    bad = 0

    def ok(msg):
        print(f'  ok   {msg}')

    def fail(msg):
        nonlocal bad
        bad = 1
        print(f'  FAIL {msg}')

    s = settings.current
    print('== .env')
    (ok if s.is_production else fail)(f'APP_ENV={s.app_env}')
    (ok if s.hosoffice else fail)('ค่าเชื่อมต่อ hosoffice อยู่ใน .env' if s.hosoffice
                                  else 'ยังไม่ได้ตั้ง HOSOFFICE_* ใน .env')
    if s.monitor_autostart:
        ok('MONITOR_AUTOSTART=1 (เครื่องหลัก — Monitor และการเตือนเริ่มเอง)')
    else:
        print('  WARN MONITOR_AUTOSTART ไม่ได้ตั้ง — Monitor จะไม่เริ่มเอง (ถูกต้องระหว่างติดตั้ง / เครื่องพัฒนา)')

    print('== hosoffice (SELECT อย่างเดียว)')
    params = hosoffice.connection_params()
    try:
        conn = hosoffice.connect_readonly(params, raise_errors=True)
        if conn is None:
            fail('ต่อไม่ได้ (ยังไม่ได้ตั้งค่าการเชื่อมต่อ?)')
    except hosoffice.Error as e:
        conn = None
        fail(f'ต่อไม่ได้: {e}')
    if conn is not None:
        try:
            ok(f'เชื่อมต่อ {params["host"]}:{params["port"]} ในนาม {params["user"]}')
            (ok if _session_read_only(conn) else fail)('session เป็น READ ONLY')
            for table in (database.get_db_config().get('table_name') or 'fingle_scan', *EXTRA_TABLES):
                try:
                    _scalar(conn, f'SELECT 1 FROM `{table}` LIMIT 1')
                    ok(f'อ่าน {table} ได้')
                except hosoffice.Error as e:
                    fail(f'อ่าน {table} ไม่ได้: {e}')
        finally:
            conn.close()

    print('== MOPH Alert')
    url = database.get_api_config().get('moph_api_url') or ''
    parts = urlsplit(url)
    host, port = parts.hostname or '', parts.port or (443 if parts.scheme == 'https' else 80)
    if host and tcp_reachable(host, port):
        ok(f'ต่อ {host}:{port} ได้ (ไม่ได้ส่งข้อความ)')
    else:
        fail(f'ต่อ MOPH Alert {host or "(ยังไม่ได้ตั้ง URL)"}:{port} ไม่ได้ — ตรวจ firewall/proxy ขาออก')

    print('== บัญชี admin')
    acc = auth.get_account()
    if not acc:
        fail('ยังไม่มีบัญชี admin (manage.py init-admin)')
    else:
        ok('มีบัญชี admin' + (' — ยังใช้รหัสชั่วคราว' if acc['must_change'] else ''))

    print('\n✓ ผ่านทุกข้อ' if not bad else '\n✗ มีข้อที่ต้องแก้')
    return bad


def cmd_tiger_check(args):
    t = settings.current.tiger
    if not t:
        print('✗ ยังไม่ได้ตั้งค่าฐานข้อมูลเครื่องสแกน — ใส่ TIGER_HOST, TIGER_DATABASE, TIGER_USER, TIGER_PASSWORD ใน .env')
        return 1
    print(f"เครื่อง {t['host']}:{t['port']} · ฐานข้อมูล {t['database']} · บัญชี {t['user']}")
    try:
        cols = tigersoft.get_columns()
        latest = tigersoft.get_latest_seq()
    except tigersoft.TigerUnavailable as e:
        print(f'  FAIL ต่อ/อ่านไม่ได้: {e}')
        return 1
    names = [c for c, _ in cols]
    print(f'  ok   ตาราง {tigersoft.TABLE}: {len(cols)} คอลัมน์')
    print('       ' + ', '.join(f'{c} ({k})' for c, k in cols))
    bad = 0
    for needed in (tigersoft.SEQ_COL, tigersoft.TIME_COL):
        if needed not in names:
            print(f'  FAIL ไม่พบคอลัมน์ {needed}')
            bad = 1
    print(f'  ok   เลขล่าสุดที่เครื่อง ({tigersoft.SEQ_COL}): {latest}')
    for key, env in (('person_column', 'TIGER_PERSON_COLUMN'), ('machine_column', 'TIGER_MACHINE_COLUMN')):
        v = t[key]
        if not v:
            print(f'  --   {env} ยังไม่ตั้ง (ไม่จำเป็น แต่ถ้าตั้ง จะระบุ{"ชื่อ" if key == "person_column" else "เครื่อง"}ของ Lost scan ได้)')
        elif v not in names:
            print(f'  FAIL {env}={v} ไม่มีในตาราง')
            bad = 1
        else:
            print(f'  ok   {env}={v}')
    try:
        window = hosoffice.get_device_window(200)
    except hosoffice.HosofficeUnavailable as e:
        print(f'  WARN เทียบกับ hosoffice ไม่ได้: {e}')
        return bad
    seqs = [r['seq'] for r in window]
    try:
        src = tigersoft.get_rows(seqs)
    except tigersoft.TigerUnavailable as e:
        print(f'  FAIL อ่านแถวเพื่อเทียบไม่ได้: {e}')
        return 1
    same_time = sum(1 for r in window if r['seq'] in src and src[r['seq']]['time'] == r['time'])
    print(f"  {'ok  ' if len(src) == len(seqs) else 'WARN'} เลขของ hosoffice ล่าสุด {len(seqs)} แถว: พบที่เครื่อง {len(src)}/{len(seqs)} · เวลาตรงกัน {same_time}/{len(seqs)}")
    print('\n✓ พร้อมใช้' if not bad else '\n✗ มีข้อที่ต้องแก้')
    return bad


COMMANDS = {'tiger-check': cmd_tiger_check, 'init-admin': cmd_init_admin, 'reset-admin': cmd_reset_admin, 'backup': cmd_backup,
            'can-restart': cmd_can_restart, 'reset-checkpoint': cmd_reset_checkpoint, 'check': cmd_check}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    os.umask(0o077)              # ไฟล์ที่สร้าง (SQLite, ไฟล์สำรอง) มี key ของ token — เจ้าของอ่านได้คนเดียว
    database.init_db()
    return COMMANDS[argv[0]](argv[1:])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
