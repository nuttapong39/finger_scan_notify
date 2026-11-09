import json
import sqlite3
import os

import settings
from flex_theme import merge_theme

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'app_config.db')

# kind → (เวลาเตือน, เตือนได้ถึง, ป้ายปุ่ม)  (Q14, Q24)
REMINDER_DEFAULTS = {
    'checkin':  ('09:30', '12:00', 'สแกนเข้างานตอนนี้'),
    'checkout': ('17:00', '22:00', 'สแกนออกงานตอนนี้'),
}

# ตรา รพ. = ไฟล์ logo_ck.png ในโปรเจกต์ (LINE โหลดรูปจาก HTTPS สาธารณะเท่านั้น จึงใช้ไฟล์เดียวกันบนเว็บ รพ.)
HOSPITAL_LOGO_URL = 'https://www.ckhospital.net/home/PDF/Logo_ck.png'
OLD_PLACEHOLDER_LOGO = 'https://morpromt2c.moph.go.th/image/image_3771a3e8-57d0-4fe0-b0f8-3c97427eb201.png'

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ── db_config ─────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS db_config (
        id INTEGER PRIMARY KEY,
        host TEXT DEFAULT '192.168.1.250',
        port INTEGER DEFAULT 3306,
        username TEXT DEFAULT 'root',
        password TEXT DEFAULT '',
        database_name TEXT DEFAULT 'hosoffice',
        table_name TEXT DEFAULT 'fingle_scan',
        poll_interval INTEGER DEFAULT 10,
        max_scan_age_minutes INTEGER DEFAULT 180,
        flood_limit INTEGER DEFAULT 300,
        max_per_person_hour INTEGER DEFAULT 6,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── api_config (MOPH Alert settings) ─────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS api_config (
        id INTEGER PRIMARY KEY,
        moph_api_url TEXT DEFAULT 'https://morpromt2f.moph.go.th/api/notify/send?messages=yes',
        hospital_name TEXT DEFAULT 'โรงพยาบาลเชียงกลาง',
        hospital_logo TEXT DEFAULT 'https://www.ckhospital.net/home/PDF/Logo_ck.png',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── person_tokens — รองรับทั้ง MOPH Alert + Line Notify ──
    # provider: 'MOPH_ALERT' | 'LINE_NOTIFY'
    c.execute('''CREATE TABLE IF NOT EXISTS person_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER NOT NULL UNIQUE,
        person_name TEXT,
        provider TEXT DEFAULT 'LINE_NOTIFY',
        line_token TEXT,
        client_key TEXT,
        secret_key TEXT,
        department TEXT,
        notify_checkin INTEGER DEFAULT 1,
        notify_checkout INTEGER DEFAULT 1,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── notify_logs ───────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS notify_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER,
        person_id INTEGER,
        person_name TEXT,
        scan_type TEXT,
        scan_datetime TEXT,
        provider TEXT,
        status TEXT,
        http_code INTEGER,
        response TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── monitor_state ─────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS monitor_state (
        id INTEGER PRIMARY KEY,
        last_scan_id INTEGER DEFAULT 0,
        last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── defaults ──────────────────────────────────────────────
    c.execute("INSERT OR IGNORE INTO db_config (id) VALUES (1)")
    c.execute("INSERT OR IGNORE INTO api_config (id) VALUES (1)")
    c.execute("INSERT OR IGNORE INTO monitor_state (id, last_scan_id) VALUES (1, 0)")

    # ── Migration: ถ้า person_tokens เดิมมี 'token' column ──
    existing_cols = [row[1] for row in c.execute("PRAGMA table_info(person_tokens)").fetchall()]
    if 'token' in existing_cols and 'line_token' not in existing_cols:
        try:
            c.execute("ALTER TABLE person_tokens ADD COLUMN line_token TEXT")
            c.execute("ALTER TABLE person_tokens ADD COLUMN provider TEXT DEFAULT 'LINE_NOTIFY'")
            c.execute("ALTER TABLE person_tokens ADD COLUMN client_key TEXT")
            c.execute("ALTER TABLE person_tokens ADD COLUMN secret_key TEXT")
            # copy old token → line_token
            c.execute("UPDATE person_tokens SET line_token = token, provider = 'LINE_NOTIFY'")
        except Exception:
            pass

    # Migration: add provider if missing
    if 'provider' not in existing_cols:
        try:
            c.execute("ALTER TABLE person_tokens ADD COLUMN provider TEXT DEFAULT 'LINE_NOTIFY'")
            c.execute("ALTER TABLE person_tokens ADD COLUMN line_token TEXT")
            c.execute("ALTER TABLE person_tokens ADD COLUMN client_key TEXT")
            c.execute("ALTER TABLE person_tokens ADD COLUMN secret_key TEXT")
        except Exception:
            pass

    # Migration: api_config rename column
    api_cols = [row[1] for row in c.execute("PRAGMA table_info(api_config)").fetchall()]
    if 'api_url' in api_cols and 'moph_api_url' not in api_cols:
        try:
            c.execute("ALTER TABLE api_config ADD COLUMN moph_api_url TEXT")
            c.execute("UPDATE api_config SET moph_api_url = api_url")
        except Exception:
            pass

    # Migration: notify_logs add provider column
    log_cols = [row[1] for row in c.execute("PRAGMA table_info(notify_logs)").fetchall()]
    if 'provider' not in log_cols:
        try:
            c.execute("ALTER TABLE notify_logs ADD COLUMN provider TEXT")
        except Exception:
            pass

    # Migration: guard settings (ป้องกันแจ้งเตือนท่วม)
    cfg_cols = [row[1] for row in c.execute("PRAGMA table_info(db_config)").fetchall()]
    for col, default in (('max_scan_age_minutes', 180), ('flood_limit', 300), ('max_per_person_hour', 6)):
        if col not in cfg_cols:
            c.execute(f"ALTER TABLE db_config ADD COLUMN {col} INTEGER DEFAULT {default}")

    # guard_events: บันทึกเหตุที่ระบบกันไม่ให้ส่ง (แยกจาก notify_logs เพื่อไม่ปนกับสถิติการส่ง)
    c.execute('''CREATE TABLE IF NOT EXISTS guard_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT,
        detail TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # flex_theme: Theme ของ Flex card (แถวเดียว เก็บเป็น JSON — อ่านผ่าน merge_theme เสมอ)
    c.execute('''CREATE TABLE IF NOT EXISTS flex_theme (
        id INTEGER PRIMARY KEY,
        data TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_notify_person_scan ON notify_logs(person_id, scan_datetime, scan_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_notify_person_created ON notify_logs(person_id, created_at)")

    # ── Phase 2: Send source / ส่งย้อนหลัง / เตือนยังไม่สแกน ──
    log_cols = [row[1] for row in c.execute("PRAGMA table_info(notify_logs)").fetchall()]
    if 'source' not in log_cols:
        c.execute("ALTER TABLE notify_logs ADD COLUMN source TEXT DEFAULT 'live'")
    if 'job_id' not in log_cols:
        c.execute("ALTER TABLE notify_logs ADD COLUMN job_id INTEGER")
    c.execute("CREATE INDEX IF NOT EXISTS idx_notify_scan ON notify_logs(scan_id)")

    tok_cols = [row[1] for row in c.execute("PRAGMA table_info(person_tokens)").fetchall()]
    if 'remind_enabled' not in tok_cols:
        c.execute("ALTER TABLE person_tokens ADD COLUMN remind_enabled INTEGER DEFAULT 0")

    # เหตุผลที่ไม่ได้ส่ง (สถานะ "ถูกข้าม") — แยกจาก notify_logs ไม่ให้ปนสถิติการส่ง
    c.execute("""CREATE TABLE IF NOT EXISTS scan_skips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER, person_id INTEGER, source TEXT, reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_skips_scan ON scan_skips(scan_id)")

    c.execute("""CREATE TABLE IF NOT EXISTS reminder_config (
        kind TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 0,
        remind_at TEXT, remind_until TEXT,
        button_url TEXT DEFAULT '', button_label TEXT DEFAULT '',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    for kind, (at, until, lbl) in REMINDER_DEFAULTS.items():
        c.execute("INSERT OR IGNORE INTO reminder_config (kind, enabled, remind_at, remind_until, button_label) "
                  "VALUES (?,0,?,?,?)", (kind, at, until, lbl))

    # UNIQUE(คน, วัน, แบบ) = เตือนวันละครั้ง และ claim ก่อนส่ง (กันซ้ำข้าม restart)
    c.execute("""CREATE TABLE IF NOT EXISTS reminder_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER, work_date TEXT, kind TEXT,
        status TEXT, http_code INTEGER, response TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(person_id, work_date, kind)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS backfill_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date_from TEXT, date_to TEXT,
        total INTEGER DEFAULT 0, sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0,
        status TEXT, message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, finished_at TIMESTAMP
    )""")
    # logo เดิมเป็นรูปตัวอย่างของหมอพร้อม → เปลี่ยนเป็นตรา รพ. (logo_ck.png บนเว็บ รพ.) ; URL ที่ admin ตั้งเองไม่แตะ
    c.execute("UPDATE api_config SET hospital_logo=? WHERE hospital_logo=? OR TRIM(COALESCE(hospital_logo,''))=''",
              (HOSPITAL_LOGO_URL, OLD_PLACEHOLDER_LOGO))
    # ── ล็อกอิน Admin (บัญชีเดียว, hash เท่านั้น) และตัวนับรหัสผิดต่อ IP ──
    c.execute("""CREATE TABLE IF NOT EXISTS admin_account (
        id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL, password_hash TEXT NOT NULL,
        must_change INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS login_attempts (
        ip TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0
    )""")
    # ── Lost scan / Import stall (scanwatch.py) ──
    c.execute("""CREATE TABLE IF NOT EXISTS scan_watch_state (
        id INTEGER PRIMARY KEY CHECK (id = 1), it_person_id INTEGER NOT NULL DEFAULT 156,
        max_seq INTEGER, last_new_at REAL, last_poll_at REAL, last_error TEXT,
        stalled INTEGER NOT NULL DEFAULT 0, summary_date TEXT, alert_fail_at REAL, alert_log_at REAL
    )""")
    have = [r[1] for r in c.execute("PRAGMA table_info(scan_watch_state)").fetchall()]
    for col, kind in (('alert_log_at', 'REAL'), ('tiger_error', 'TEXT'), ('tiger_max_seq', 'INTEGER'), ('behind_since', 'REAL'),
                      ('old_check_id', 'INTEGER')):
        if col not in have:
            c.execute(f"ALTER TABLE scan_watch_state ADD COLUMN {col} {kind}")
    c.execute("INSERT OR IGNORE INTO scan_watch_state (id) VALUES (1)")
    c.execute("""CREATE TABLE IF NOT EXISTS watch_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, seq INTEGER, detail TEXT,
        first_seen REAL NOT NULL, closed_at REAL, alerted_at REAL, recovery_notified INTEGER NOT NULL DEFAULT 0,
        UNIQUE(kind, seq)
    )""")
    # ── Scan importer ตัวใหม่ (scan_importer/runner.py) : สถานะรอบล่าสุด + แถวที่กัก ──
    c.execute("""CREATE TABLE IF NOT EXISTS importer_state (
        id INTEGER PRIMARY KEY CHECK (id = 1), checkpoint INTEGER, last_round_at REAL, last_error TEXT, last_counts TEXT
    )""")
    if 'totals' not in [r[1] for r in c.execute("PRAGMA table_info(importer_state)").fetchall()]:
        c.execute("ALTER TABLE importer_state ADD COLUMN totals TEXT")      # ตัวเลขสะสมตั้งแต่ตั้งจุดเริ่มต้น (JSON)
    c.execute("INSERT OR IGNORE INTO importer_state (id) VALUES (1)")
    c.execute("""CREATE TABLE IF NOT EXISTS importer_held (
        machine INTEGER NOT NULL, seq INTEGER NOT NULL, row_time TEXT, device_time TEXT, person TEXT, mode TEXT,
        reason TEXT NOT NULL, held_at REAL NOT NULL, released_at REAL, PRIMARY KEY (machine, seq)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS importer_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, who TEXT, action TEXT NOT NULL, detail TEXT
    )""")
    # มีค่าเชื่อมต่อ hosoffice ใน .env แล้ว → ไม่เก็บรหัส MySQL ซ้ำใน SQLite (ไฟล์นี้ถูกคัดลอก/สำรองไปที่อื่น)
    if settings.current.hosoffice:
        c.execute("UPDATE db_config SET password='' WHERE COALESCE(password,'') <> ''")
    conn.commit()
    conn.close()


def close_stuck_backfill_jobs():
    """เรียกตอนแอป (process หลัก) เริ่มเท่านั้น — job ที่ค้าง 'running' จากการปิดแอปกลางคันจะบล็อก
    การส่งย้อนหลังครั้งถัดไป ห้ามเรียกจาก init_db หรือ manage.py เพราะจะไปปิด job ที่กำลังรันจริงในแอป"""
    conn = get_sqlite_conn()
    n = conn.execute("UPDATE backfill_jobs SET status='error', "
                     "message='แอปถูกปิดระหว่างส่ง (ตรวจสถานะแล้วส่งรายการที่เหลือใหม่)', "
                     "finished_at=CURRENT_TIMESTAMP WHERE status='running'").rowcount
    conn.commit(); conn.close()
    return n

# ── SQLite helpers ────────────────────────────────────────────
def get_sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── DB Config ─────────────────────────────────────────────────
def get_db_config():
    conn = get_sqlite_conn()
    row = conn.execute("SELECT * FROM db_config WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}

def save_db_config(host, port, username, password, database_name, table_name, poll_interval):
    conn = get_sqlite_conn()
    conn.execute("""UPDATE db_config SET host=?,port=?,username=?,password=?,
                    database_name=?,table_name=?,poll_interval=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=1""",
                 (host, int(port), username, password, database_name, table_name, int(poll_interval)))
    conn.commit(); conn.close()

def save_db_settings(table_name, poll_interval):
    """เฉพาะค่าที่ไม่ใช่ความลับ — ใช้เมื่อค่าการเชื่อมต่ออยู่ใน .env"""
    conn = get_sqlite_conn()
    conn.execute("UPDATE db_config SET table_name=?, poll_interval=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
                 (table_name, int(poll_interval)))
    conn.commit(); conn.close()


# ── API Config ────────────────────────────────────────────────
def get_api_config():
    conn = get_sqlite_conn()
    row = conn.execute("SELECT * FROM api_config WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}

def save_api_config(moph_api_url, hospital_name, hospital_logo):
    conn = get_sqlite_conn()
    conn.execute("""UPDATE api_config SET moph_api_url=?,hospital_name=?,hospital_logo=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=1""",
                 (moph_api_url, hospital_name, hospital_logo))
    conn.commit(); conn.close()

# ── Flex theme ────────────────────────────────────────────────
def get_flex_theme():
    """theme ที่บันทึกไว้ ผสานกับค่า default — JSON เสีย/ไม่มีแถว → default (การ์ดต้องส่งได้เสมอ)"""
    try:
        conn = get_sqlite_conn()
        row = conn.execute("SELECT data FROM flex_theme WHERE id=1").fetchone()
        conn.close()
        stored = json.loads(row['data']) if row and row['data'] else None
    except (sqlite3.Error, ValueError, TypeError):
        stored = None
    return merge_theme(stored)

def save_flex_theme(theme):
    """บันทึก theme ที่ผ่าน validate_theme แล้ว (ทับแถวเดียว)"""
    conn = get_sqlite_conn()
    conn.execute("""INSERT INTO flex_theme (id, data) VALUES (1, ?)
                    ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP""",
                 (json.dumps(theme, ensure_ascii=False),))
    conn.commit(); conn.close()

# ── Token CRUD ────────────────────────────────────────────────
def get_all_tokens():
    conn = get_sqlite_conn()
    rows = conn.execute("SELECT * FROM person_tokens ORDER BY person_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_token_by_person_id(person_id):
    conn = get_sqlite_conn()
    row = conn.execute("SELECT * FROM person_tokens WHERE person_id=? AND active=1", (person_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def save_token(person_id, person_name, provider, line_token, client_key, secret_key,
               department, notify_checkin, notify_checkout):
    conn = get_sqlite_conn()
    conn.execute("""INSERT INTO person_tokens
                    (person_id,person_name,provider,line_token,client_key,secret_key,
                     department,notify_checkin,notify_checkout)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        person_name=excluded.person_name,
                        provider=excluded.provider,
                        line_token=excluded.line_token,
                        client_key=excluded.client_key,
                        secret_key=excluded.secret_key,
                        department=excluded.department,
                        notify_checkin=excluded.notify_checkin,
                        notify_checkout=excluded.notify_checkout,
                        updated_at=CURRENT_TIMESTAMP""",
                 (int(person_id), person_name, provider, line_token or '', client_key or '',
                  secret_key or '', department, int(notify_checkin), int(notify_checkout)))
    conn.commit(); conn.close()

def delete_token(token_id):
    conn = get_sqlite_conn()
    conn.execute("DELETE FROM person_tokens WHERE id=?", (token_id,))
    conn.commit(); conn.close()

def toggle_token(token_id, active):
    conn = get_sqlite_conn()
    conn.execute("UPDATE person_tokens SET active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (active, token_id))
    conn.commit(); conn.close()

# ── Monitor state ─────────────────────────────────────────────
def get_last_scan_id():
    conn = get_sqlite_conn()
    row = conn.execute("SELECT last_scan_id FROM monitor_state WHERE id=1").fetchone()
    conn.close()
    return row['last_scan_id'] if row else 0

def update_last_scan_id(scan_id):
    conn = get_sqlite_conn()
    conn.execute("UPDATE monitor_state SET last_scan_id=?,last_checked=CURRENT_TIMESTAMP WHERE id=1", (scan_id,))
    conn.commit(); conn.close()

# ── Guard (ป้องกันแจ้งเตือนท่วม) ──────────────────────────────
def advance_last_scan_id(scan_id):
    """เลื่อน checkpoint แบบ compare-and-set: สำเร็จเฉพาะเมื่อ scan_id > ค่าปัจจุบัน
    True  = เราเป็นผู้ 'รับ' แถวนี้ไปส่ง
    False = มี process/instance อื่นรับไปแล้ว (กันส่งซ้ำเมื่อรันหลายตัวพร้อมกัน)"""
    conn = get_sqlite_conn()
    cur = conn.execute("UPDATE monitor_state SET last_scan_id=?,last_checked=CURRENT_TIMESTAMP "
                       "WHERE id=1 AND last_scan_id<?", (int(scan_id), int(scan_id)))
    conn.commit()
    ok = cur.rowcount == 1
    conn.close()
    return ok

def was_notified(person_id, scan_datetime, scan_type_label):
    """เคยส่งสำเร็จสำหรับ (คน, เวลาสแกน, ประเภท) นี้แล้วหรือยัง — กัน record เดิมที่ถูกใส่ซ้ำด้วย ID ใหม่"""
    conn = get_sqlite_conn()
    row = conn.execute("""SELECT 1 FROM notify_logs
                          WHERE person_id=? AND scan_datetime=? AND scan_type=? AND status='success'
                          LIMIT 1""", (person_id, scan_datetime, scan_type_label)).fetchone()
    conn.close()
    return row is not None

def count_recent_notified(person_id, minutes=60):
    """จำนวนการแจ้งเตือนสด (source=live) ที่ส่งสำเร็จให้คนนี้ใน N นาทีล่าสุด (created_at เก็บเป็น UTC)
    — ส่งย้อนหลัง / เตือน / ทดสอบ ไม่กินโควตา"""
    conn = get_sqlite_conn()
    row = conn.execute("""SELECT COUNT(*) FROM notify_logs
                          WHERE person_id=? AND status='success' AND COALESCE(source,'live')='live'
                            AND created_at >= datetime('now', ?)""",
                       (person_id, f'-{int(minutes)} minutes')).fetchone()
    conn.close()
    return row[0] if row else 0

def add_guard_event(kind, detail):
    conn = get_sqlite_conn()
    conn.execute("INSERT INTO guard_events (kind, detail) VALUES (?,?)", (kind, str(detail)[:1000]))
    conn.commit(); conn.close()

def get_guard_events(limit=100):
    conn = get_sqlite_conn()
    rows = conn.execute("SELECT * FROM guard_events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_guard_settings(max_scan_age_minutes, flood_limit, max_per_person_hour):
    conn = get_sqlite_conn()
    conn.execute("""UPDATE db_config SET max_scan_age_minutes=?,flood_limit=?,max_per_person_hour=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=1""",
                 (max(1, int(max_scan_age_minutes)), max(1, int(flood_limit)), max(1, int(max_per_person_hour))))
    conn.commit(); conn.close()

# ── Notify logs ───────────────────────────────────────────────
def add_notify_log(scan_id, person_id, person_name, scan_type, scan_datetime,
                   provider, status, http_code, response, source='live', job_id=None):
    conn = get_sqlite_conn()
    conn.execute("""INSERT INTO notify_logs
                    (scan_id,person_id,person_name,scan_type,scan_datetime,provider,status,http_code,response,
                     source,job_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (scan_id, person_id, person_name, scan_type, scan_datetime,
                  provider, status, http_code, response, source, job_id))
    conn.commit(); conn.close()

def get_notify_logs(limit=300):
    conn = get_sqlite_conn()
    rows = conn.execute("SELECT * FROM notify_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Scan skips (สถานะ "ถูกข้าม") ──────────────────────────────
def add_scan_skips(rows):
    """rows: [(scan_id, person_id, source, reason), ...]"""
    if not rows:
        return
    conn = get_sqlite_conn()
    conn.executemany("INSERT INTO scan_skips (scan_id, person_id, source, reason) VALUES (?,?,?,?)", rows)
    conn.commit(); conn.close()

def get_delivery_records(scan_ids):
    """log การส่ง + การข้าม ของ scan ที่ระบุ (เรียงเก่า→ใหม่) สำหรับคำนวณ Delivery status"""
    ids = [int(i) for i in scan_ids]
    if not ids:
        return [], []
    q = ','.join('?' * len(ids))
    conn = get_sqlite_conn()
    logs = [dict(r) for r in conn.execute(
        f"SELECT scan_id, status, http_code, response, source, datetime(created_at, 'localtime') AS created_at "
        f"FROM notify_logs "
        f"WHERE scan_id IN ({q}) ORDER BY id", ids).fetchall()]
    skips = [dict(r) for r in conn.execute(
        f"SELECT scan_id, reason, source, datetime(created_at, 'localtime') AS created_at FROM scan_skips "
        f"WHERE scan_id IN ({q}) ORDER BY id",
        ids).fetchall()]
    conn.close()
    return logs, skips

# ── Lost scan / Import stall ──────────────────────────────────
_WATCH_FIELDS = ('it_person_id', 'max_seq', 'last_new_at', 'last_poll_at', 'last_error', 'stalled',
                 'summary_date', 'alert_fail_at', 'alert_log_at', 'tiger_error', 'tiger_max_seq', 'behind_since', 'old_check_id')


def get_watch_state():
    conn = get_sqlite_conn()
    row = conn.execute("SELECT * FROM scan_watch_state WHERE id=1").fetchone()
    conn.close()
    return dict(row)


def save_watch_state(**fields):
    bad = set(fields) - set(_WATCH_FIELDS)
    if bad:
        raise ValueError(f'ไม่มีฟิลด์: {sorted(bad)}')
    if not fields:
        return
    conn = get_sqlite_conn()
    conn.execute(f"UPDATE scan_watch_state SET {', '.join(k + '=?' for k in fields)} WHERE id=1", tuple(fields.values()))
    conn.commit(); conn.close()


def open_watch_event(kind, seq, detail, now_ts):
    """เปิดเหตุการณ์ (kind, seq) — เปิดซ้ำเลขเดิมไม่สร้างใหม่ ; stall (seq=None) เปิดได้หลายครั้งต่างเวลา"""
    conn = get_sqlite_conn()
    cur = conn.execute("INSERT OR IGNORE INTO watch_events (kind, seq, detail, first_seen) VALUES (?,?,?,?)",
                       (kind, seq, json.dumps(detail or {}, ensure_ascii=False), now_ts))
    conn.commit(); conn.close()
    return cur.rowcount == 1


def get_watch_events(kind=None, open_only=False, limit=500):
    where, args = [], []
    if kind:
        where.append('kind=?'); args.append(kind)
    if open_only:
        where.append('closed_at IS NULL')
    conn = get_sqlite_conn()
    rows = conn.execute("SELECT * FROM watch_events" + (' WHERE ' + ' AND '.join(where) if where else '') +
                        " ORDER BY (closed_at IS NOT NULL), id DESC LIMIT ?", (*args, int(limit))).fetchall()
    conn.close()
    out = []
    for r in rows:
        e = dict(r)
        e['detail'] = json.loads(e['detail'] or '{}')
        e['open'] = e['closed_at'] is None
        out.append(e)
    return out


def _mark_events(column, ids, value):
    if not ids:
        return
    conn = get_sqlite_conn()
    conn.executemany(f"UPDATE watch_events SET {column}=? WHERE id=?", [(value, int(i)) for i in ids])
    conn.commit(); conn.close()


def update_watch_event_detail(event_id, detail):
    conn = get_sqlite_conn()
    conn.execute("UPDATE watch_events SET detail=? WHERE id=?", (json.dumps(detail, ensure_ascii=False), int(event_id)))
    conn.commit(); conn.close()


def close_watch_events(ids, now_ts):
    _mark_events('closed_at', ids, now_ts)


def mark_watch_alerted(ids, now_ts):
    _mark_events('alerted_at', ids, now_ts)


def mark_watch_recovery_notified(ids):
    _mark_events('recovery_notified', ids, 1)


def get_handled_scan_ids(scan_ids):
    """scan ที่แอปจัดการแล้ว (มี log การส่ง หรือบันทึกการข้าม) — Monitor ใช้กันอ่านซ้ำ/ส่งซ้ำ"""
    ids = [int(i) for i in scan_ids]
    if not ids:
        return set()
    q = ','.join('?' * len(ids))
    conn = get_sqlite_conn()
    done = {r[0] for r in conn.execute(f"SELECT scan_id FROM notify_logs WHERE scan_id IN ({q})", ids)}
    done |= {r[0] for r in conn.execute(f"SELECT scan_id FROM scan_skips WHERE scan_id IN ({q})", ids)}
    conn.close()
    return done


# ── Reminder config / recipients / log ────────────────────────
def get_reminder_config():
    conn = get_sqlite_conn()
    rows = {r['kind']: dict(r) for r in conn.execute("SELECT * FROM reminder_config").fetchall()}
    conn.close()
    return rows

def save_reminder_config(kind, enabled, remind_at, remind_until, button_url, button_label):
    conn = get_sqlite_conn()
    conn.execute("""UPDATE reminder_config SET enabled=?, remind_at=?, remind_until=?, button_url=?,
                    button_label=?, updated_at=CURRENT_TIMESTAMP WHERE kind=?""",
                 (int(bool(enabled)), remind_at, remind_until, button_url, button_label, kind))
    conn.commit(); conn.close()

def set_remind_enabled(person_ids, enabled):
    ids = [int(i) for i in person_ids]
    if not ids:
        return 0
    conn = get_sqlite_conn()
    cur = conn.execute(f"UPDATE person_tokens SET remind_enabled=? WHERE person_id IN ({','.join('?' * len(ids))})",
                       (int(bool(enabled)), *ids))
    conn.commit(); n = cur.rowcount; conn.close()
    return n

def get_reminded_person_ids(work_date, kind):
    conn = get_sqlite_conn()
    rows = conn.execute("SELECT person_id FROM reminder_log WHERE work_date=? AND kind=?",
                        (work_date, kind)).fetchall()
    conn.close()
    return {r[0] for r in rows}

def claim_reminder(person_id, work_date, kind):
    """จองการเตือน (คน, วัน, แบบ) ก่อนส่ง — True = เราได้สิทธิ์ส่ง, False = มีการจอง/ส่งไปแล้ว"""
    conn = get_sqlite_conn()
    cur = conn.execute("INSERT OR IGNORE INTO reminder_log (person_id, work_date, kind, status) "
                       "VALUES (?,?,?,'claimed')", (int(person_id), work_date, kind))
    conn.commit(); ok = cur.rowcount == 1; conn.close()
    return ok

def finish_reminder(person_id, work_date, kind, status, http_code=None, response=None):
    conn = get_sqlite_conn()
    conn.execute("UPDATE reminder_log SET status=?, http_code=?, response=? "
                 "WHERE person_id=? AND work_date=? AND kind=?",
                 (status, http_code, (response or '')[:500], int(person_id), work_date, kind))
    conn.commit(); conn.close()

def get_reminder_log(work_date):
    conn = get_sqlite_conn()
    rows = conn.execute("""SELECT r.*, datetime(r.created_at, 'localtime') AS created_local, t.person_name FROM reminder_log r
                           LEFT JOIN person_tokens t ON t.person_id = r.person_id
                           WHERE r.work_date=? ORDER BY r.id DESC""", (work_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Backfill jobs ─────────────────────────────────────────────
def create_backfill_job(date_from, date_to, total):
    conn = get_sqlite_conn()
    cur = conn.execute("INSERT INTO backfill_jobs (date_from, date_to, total, status) VALUES (?,?,?,'running')",
                       (date_from, date_to, int(total)))
    conn.commit(); job_id = cur.lastrowid; conn.close()
    return job_id

def update_backfill_job(job_id, finished=False, **fields):
    """finished=True → finished_at = CURRENT_TIMESTAMP (UTC เหมือน created_at)"""
    allowed = ('sent', 'failed', 'skipped', 'status', 'message')
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets and not finished:
        return
    cols = [k + '=?' for k in sets] + (['finished_at=CURRENT_TIMESTAMP'] if finished else [])
    conn = get_sqlite_conn()
    conn.execute(f"UPDATE backfill_jobs SET {', '.join(cols)} WHERE id=?", (*sets.values(), int(job_id)))
    conn.commit(); conn.close()

def get_backfill_job(job_id):
    conn = get_sqlite_conn()
    row = conn.execute("SELECT *, datetime(created_at, 'localtime') AS created_local, "
                       "datetime(finished_at, 'localtime') AS finished_local FROM backfill_jobs WHERE id=?",
                       (int(job_id),)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_running_backfill_job():
    conn = get_sqlite_conn()
    row = conn.execute("SELECT * FROM backfill_jobs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


# ── Scan importer ตัวใหม่ ─────────────────────────────────────
def get_importer_state():
    conn = get_sqlite_conn()
    row = dict(conn.execute("SELECT * FROM importer_state WHERE id=1").fetchone())
    conn.close()
    row['last_counts'] = json.loads(row['last_counts']) if row.get('last_counts') else {}
    row['totals'] = json.loads(row['totals']) if row.get('totals') else {}
    return row


def save_importer_state(**fields):
    bad = set(fields) - {'checkpoint', 'last_round_at', 'last_error', 'last_counts', 'totals'}
    if bad:
        raise ValueError(f'ไม่มีฟิลด์: {sorted(bad)}')
    for k in ('last_counts', 'totals'):
        if k in fields:
            fields[k] = json.dumps(fields[k])
    if not fields:
        return
    conn = get_sqlite_conn()
    conn.execute(f"UPDATE importer_state SET {', '.join(k + '=?' for k in fields)} WHERE id=1", tuple(fields.values()))
    conn.commit(); conn.close()


def add_importer_held(rows, now_ts):
    """rows = [{'src': แถวต้นทาง, 'reason': ...}] — แถวที่กักแล้ว (เครื่อง, เลข) เดิมไม่ถูกเขียนทับ"""
    conn = get_sqlite_conn()
    for r in rows:
        s = r['src']
        conn.execute("INSERT OR IGNORE INTO importer_held (machine, seq, row_time, device_time, person, mode, reason, held_at) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (s.get('machine'), s['seq'], s['time'].isoformat() if s.get('time') else None,
                      s['device_time'].isoformat() if s.get('device_time') else None,
                      None if s.get('person') is None else str(s['person']), s.get('mode'), r['reason'], now_ts))
    conn.commit(); conn.close()


def get_importer_held(seq_from=None, seq_to=None, open_only=True):
    """แถวที่กัก (รูปแบบเดียวกับแถวต้นทางของ plan) เรียงตามเลข"""
    where, args = [], []
    if open_only:
        where.append('released_at IS NULL')
    if seq_from is not None:
        where.append('seq >= ?'); args.append(int(seq_from))
    if seq_to is not None:
        where.append('seq <= ?'); args.append(int(seq_to))
    conn = get_sqlite_conn()
    rows = conn.execute(f"SELECT * FROM importer_held {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY seq", tuple(args)).fetchall()
    conn.close()
    from datetime import datetime
    iso = lambda v: datetime.fromisoformat(v) if v else None   # noqa: E731
    return [{'seq': r['seq'], 'machine': r['machine'], 'person': r['person'], 'mode': r['mode'], 'reason': r['reason'],
             'time': iso(r['row_time']), 'device_time': iso(r['device_time'])} for r in rows]


def mark_importer_released(keys, now_ts):
    """keys = [(machine, seq)] — ปล่อยแล้ว (หรือมีอยู่ใน hosoffice แล้ว) ไม่นับเป็นแถวกักค้าง"""
    conn = get_sqlite_conn()
    conn.executemany("UPDATE importer_held SET released_at=? WHERE machine IS ? AND seq=? AND released_at IS NULL",
                     [(now_ts, m, q) for m, q in keys])
    conn.commit(); conn.close()


def add_importer_audit(who, action, detail, now_ts):
    conn = get_sqlite_conn()
    conn.execute("INSERT INTO importer_audit (at, who, action, detail) VALUES (?,?,?,?)", (now_ts, who, action, json.dumps(detail)))
    conn.commit(); conn.close()


def get_importer_audit(limit=5):
    conn = get_sqlite_conn()
    rows = conn.execute("SELECT * FROM importer_audit ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [{'at': r['at'], 'who': r['who'], 'action': r['action'], 'detail': json.loads(r['detail'] or '{}')} for r in rows]
