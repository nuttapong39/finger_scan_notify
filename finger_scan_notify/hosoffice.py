"""การอ่านฐานข้อมูล hosoffice (MySQL) ทั้งหมด — อ่านอย่างเดียว และเป็นจุด mock ใน test

ฟังก์ชันที่ใช้ตัดสินใจส่ง/เตือน 'raise' เมื่อต่อ DB ไม่ได้ (fail closed):
ห้ามคืนค่าว่างเงียบ ๆ เพราะ "ไม่พบ Scan" จะถูกตีความว่า "ยังไม่สแกน"
"""
from datetime import date, timedelta

import mysql.connector
from mysql.connector import Error

import scan_types
import settings
from database import get_db_config

SCAN_COLS = ("ID,PERSON_ID,SCAN_DATE_TIME,HR_FULLNAME,HR_POSITION_NAME,HR_DEP_NAME,HR_DEP_SUB_NAME,"
             "HR_PERSON_TYPE_NAME,SCAN_TYPE,DAY_TYPE_NAME,ISINPUT,ISCONFIRM")
LEAVE_STATUSES = ('Z', 'A')          # Z = ผอ.อนุญาต, A = รออนุมัติ (Q29)
MAX_ROWS = 5000


class HosofficeUnavailable(RuntimeError):
    """ต่อ hosoffice ไม่ได้ / query ล้มเหลว"""


READ_ONLY_SQL = 'SET SESSION TRANSACTION READ ONLY'


def connection_params():
    """ค่าเชื่อมต่อ: .env (HOSOFFICE_*) ก่อน — ไม่มีค่อยใช้หน้าตั้งค่าใน SQLite (เครื่องพัฒนา)"""
    if settings.current.hosoffice:
        return dict(settings.current.hosoffice)
    cfg = get_db_config()
    if not cfg:
        return None
    return {'host': cfg['host'], 'port': int(cfg['port']), 'user': cfg['username'],
            'password': cfg['password'], 'database': cfg['database_name']}


def connect_readonly(params, raise_errors=False):
    """เปิด connection แล้วบังคับ READ ONLY ก่อนคำสั่งแรก — ตั้งไม่ได้ = ไม่ใช้ connection นั้น (fail closed)
    ด่านที่สองต่อจากสิทธิ์ของบัญชี: บัญชีปัจจุบันยังเป็น root (ดู docs/design/phase3-deploy-almalinux.md)"""
    if not params:
        return None
    conn = None
    try:
        conn = mysql.connector.connect(
            host=params['host'], port=int(params['port']), user=params['user'],
            password=params['password'], database=params['database'],
            connection_timeout=10, charset='utf8mb4')
        cur = conn.cursor()
        cur.execute(READ_ONLY_SQL)
        cur.close()
        return conn
    except Error:
        if conn is not None:
            conn.close()
        if raise_errors:
            raise
        return None


def get_mysql_connection():
    return connect_readonly(connection_params())


def check_connection(params=None):
    """ปุ่มทดสอบในหน้าตั้งค่า — params None = ค่าที่ระบบใช้อยู่"""
    try:
        c = connect_readonly(params or connection_params(), raise_errors=True)
        if c is None:
            return False, 'ยังไม่ได้ตั้งค่าการเชื่อมต่อ'
        c.close()
        return True, "เชื่อมต่อสำเร็จ ✅ (READ ONLY)"
    except Error as e:
        return False, str(e)


def _tbl():
    return get_db_config().get('table_name', 'fingle_scan')


def _query(sql, params=()):
    """คืน list ของ dict — raise HosofficeUnavailable เมื่อมีปัญหา"""
    conn = get_mysql_connection()
    if not conn:
        raise HosofficeUnavailable('เชื่อมต่อ MySQL (hosoffice) ไม่ได้')
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    except Error as e:
        raise HosofficeUnavailable(f'query hosoffice ล้มเหลว: {e}') from e
    finally:
        conn.close()


def _in(values):
    return ','.join(['%s'] * len(values))


# ── Monitor / Dashboard (ของเดิม) ─────────────────────────────
def get_recent_scans_from_mysql(limit=100):
    try:
        return _query(f"SELECT {SCAN_COLS} FROM `{_tbl()}` ORDER BY ID DESC LIMIT %s", (int(limit),))
    except HosofficeUnavailable:
        return []


def get_new_scans_from_mysql(last_id, limit=500):
    """ดึง scan ที่ ID > last_id (เรียง ID ASC) — raise ถ้า DB มีปัญหา"""
    return _query(f"SELECT {SCAN_COLS} FROM `{_tbl()}` WHERE ID > %s ORDER BY ID ASC LIMIT %s",
                  (int(last_id), int(limit)))


def get_max_scan_id():
    """MAX(ID) หรือ None ถ้าต่อ DB ไม่ได้ (None ≠ 0: ห้ามใช้ 0 เป็นจุดเริ่มต้น)"""
    try:
        rows = _query(f"SELECT COALESCE(MAX(ID),0) AS m FROM `{_tbl()}`")
        return int(rows[0]['m']) if rows else None
    except HosofficeUnavailable:
        return None


def get_today_stats():
    tin, tout = scan_types.sql_list(scan_types.CHECKIN), scan_types.sql_list(scan_types.CHECKOUT)
    t = _tbl()
    try:
        stats = _query(f"""SELECT COUNT(DISTINCT PERSON_ID) AS total_person, COUNT(*) AS total_scan,
                SUM(CASE WHEN SCAN_TYPE IN ({tin}) THEN 1 ELSE 0 END) AS checkin,
                SUM(CASE WHEN SCAN_TYPE IN ({tout}) THEN 1 ELSE 0 END) AS checkout
                FROM `{t}` WHERE DATE(SCAN_DATE_TIME)=CURDATE()""")
        hourly = _query(f"""SELECT HOUR(SCAN_DATE_TIME) AS hr, COUNT(*) AS cnt FROM `{t}`
                WHERE DATE(SCAN_DATE_TIME)=CURDATE() GROUP BY HOUR(SCAN_DATE_TIME) ORDER BY hr""")
        dept = _query(f"""SELECT HR_DEP_NAME, COUNT(*) AS cnt FROM `{t}` WHERE DATE(SCAN_DATE_TIME)=CURDATE()
                GROUP BY HR_DEP_NAME ORDER BY cnt DESC LIMIT 10""")
        trend = _query(f"""SELECT DATE(SCAN_DATE_TIME) AS d, COUNT(*) AS cnt FROM `{t}`
                WHERE SCAN_DATE_TIME >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                GROUP BY DATE(SCAN_DATE_TIME) ORDER BY d""")
    except HosofficeUnavailable:
        return {}
    for row in trend:
        if hasattr(row.get('d'), 'strftime'):
            row['d'] = row['d'].strftime('%Y-%m-%d')
    return {'stats': stats[0] if stats else {}, 'hourly': hourly, 'dept': dept, 'trend': trend}


# ── ส่งย้อนหลัง / ตรวจสถานะ ──────────────────────────────────
def get_scans_by_ids(ids):
    ids = [int(i) for i in ids]
    if not ids:
        return []
    return _query(f"SELECT {SCAN_COLS} FROM `{_tbl()}` WHERE ID IN ({_in(ids)}) ORDER BY SCAN_DATE_TIME, ID",
                  tuple(ids))


def get_scans_between(date_from, date_to, person_ids, limit=MAX_ROWS):
    """Scan ของคนที่เลือก ระหว่าง date_from..date_to (รวมทั้งสองวัน)"""
    pids = [int(p) for p in person_ids]
    if not pids:
        return []
    return _query(f"""SELECT {SCAN_COLS} FROM `{_tbl()}`
                      WHERE SCAN_DATE_TIME >= %s AND SCAN_DATE_TIME < %s AND PERSON_ID IN ({_in(pids)})
                      ORDER BY SCAN_DATE_TIME, ID LIMIT %s""",
                  (date_from, date_to + timedelta(days=1), *pids, int(limit)))


# ── เตือนยังไม่สแกน ──────────────────────────────────────────
def get_scans_on(day, person_ids):
    return get_scans_between(day, day, person_ids)


def is_holiday(day: date):
    rows = _query("SELECT 1 AS x FROM leave_holiday WHERE DATE_HOLIDAY=%s AND ACTIVE='True' LIMIT 1", (day,))
    return bool(rows)


def get_persons_on_leave(day: date, person_ids):
    """คนที่มีใบลาครอบคลุมวันนี้ (Z/A และไม่ถูกยกเลิก) — ครึ่งวันนับเป็นลาทั้งวันใน phase นี้"""
    pids = [int(p) for p in person_ids]
    if not pids:
        return set()
    rows = _query(f"""SELECT DISTINCT LEAVE_PERSON_ID AS pid FROM leave_register
                      WHERE LEAVE_STATUS_CODE IN ({_in(LEAVE_STATUSES)})
                        AND (LEAVE_CANCEL_STATUS IS NULL OR LEAVE_CANCEL_STATUS <> 'True')
                        AND %s BETWEEN LEAVE_DATE_BEGIN AND LEAVE_DATE_END
                        AND LEAVE_PERSON_ID IN ({_in(pids)})""",
                  (*LEAVE_STATUSES, day, *pids))
    return {int(r['pid']) for r in rows}


def get_muted_person_ids():
    """รายชื่อที่ขอปิดแจ้งเตือนใน hosoffice (fingle_scan_notify_mute) — เคารพทุก Send source"""
    return {int(r['pid']) for r in _query("SELECT PERSON_ID AS pid FROM fingle_scan_notify_mute")}
