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


def get_scans_in_id_range(lo, hi, person_ids):
    """Scan ที่ lo < ID <= hi ของคนที่ระบุ — Monitor อ่านซ้ำแถวที่ commit ช้ากว่าแถวที่ ID สูงกว่า (raise ถ้า DB มีปัญหา)"""
    pids = [int(p) for p in person_ids]
    if not pids or int(hi) <= int(lo):
        return []
    return _query(f"SELECT {SCAN_COLS} FROM `{_tbl()}` WHERE ID > %s AND ID <= %s AND PERSON_ID IN ({_in(pids)}) "
                  f"ORDER BY ID LIMIT 500", (int(lo), int(hi), *pids))


def get_device_window(n=2000, days=14):
    """Device sequence (MACHIN_SCAN_ID) ของ Scan จากเครื่อง n เลขล่าสุด → [{'seq','machine','time'}] (raise ถ้า DB มีปัญหา)
    ใช้ดัชนีเวลาสแกน (ไม่มีดัชนีบน MACHIN_SCAN_ID) แล้วตัดช่วงเลขในโค้ด ; แถว MANUAL/SIGN ไม่มีเลขจึงไม่ถูกนับ"""
    rows = _query(f"SELECT MACHIN_SCAN_ID AS seq, MACHIN_ID AS machine, SCAN_DATE_TIME AS time FROM `{_tbl()}` "
                  f"WHERE SCAN_DATE_TIME >= DATE_SUB(NOW(), INTERVAL %s DAY) AND MACHIN_SCAN_ID IS NOT NULL "
                  f"ORDER BY MACHIN_SCAN_ID", (int(days),))
    rows = [r for r in rows if r['time'] is not None]
    if not rows:
        return []
    floor = max(r['seq'] for r in rows) - int(n)
    return [r for r in rows if r['seq'] >= floor]


def get_old_device_rows_after(last_id, older_than_hours, upto_id):
    """สรุป (ไม่คืนข้อมูลบุคคล) ของแถวใหม่ที่ ID > last_id, มาจากเครื่องสแกน (มี Device sequence) แต่เวลาสแกนเก่ากว่า older_than_hours
    → {'count','first_id','last_id','oldest','newest','max_id'} ; ใช้จับการนำเข้าย้อนหลังเป็นชุด (แถว MANUAL/SIGN ไม่มีเลขเครื่อง จึงไม่นับ)"""
    rows = _query(f"SELECT COUNT(*) AS n, MIN(ID) AS first_id, MAX(ID) AS last_id, MIN(SCAN_DATE_TIME) AS oldest, "
                  f"MAX(SCAN_DATE_TIME) AS newest FROM `{_tbl()}` WHERE ID > %s AND ID <= %s AND MACHIN_SCAN_ID IS NOT NULL "
                  f"AND SCAN_DATE_TIME < DATE_SUB(NOW(), INTERVAL %s HOUR)", (int(last_id), int(upto_id), int(older_than_hours)))
    r = rows[0] if rows else {}
    return {'count': int(r.get('n') or 0), 'first_id': r.get('first_id'), 'last_id': r.get('last_id'),
            'oldest': r.get('oldest'), 'newest': r.get('newest')}


def get_fill_sample(max_rows=400):
    """แถวล่าสุดที่ตัวนำเข้าเดิมเขียน (มีเลขเครื่อง ไม่มีป้าย CREATE_BY) เป็น dict ทุกคอลัมน์ — ใช้เฉพาะสรุปว่าคอลัมน์ไหนถูกเติม
    (ผู้เรียกต้องไม่แสดงค่า) ; ใช้ช่วง ID ที่มีดัชนี ไม่สแกนทั้งตาราง"""
    top = get_max_scan_id()
    if top is None:
        raise HosofficeUnavailable('อ่านเลข ID ล่าสุดไม่ได้')
    return _query(f"SELECT * FROM `{_tbl()}` WHERE ID > %s AND ID <= %s AND MACHIN_SCAN_ID IS NOT NULL AND CREATE_BY IS NULL "
                  f"ORDER BY ID DESC LIMIT %s", (max(0, top - 30000), top, int(max_rows)))


def count_new_rows_by_writer(last_id, writer, upto_id):
    """จำนวน/ช่วง ID ของแถวใหม่ (last_id < ID <= upto_id) ที่ผู้เขียนนี้สร้าง (CREATE_BY = writer) — ใช้จับผู้เขียนที่ปิดไปแล้วแต่กลับมาเขียน
    ช่วง ID ใช้ดัชนี PK ; ห้ามเปลี่ยนเป็น join/EXISTS ซ้อนกับคอลัมน์ที่ไม่มีดัชนี (MACHIN_SCAN_ID) บนฐานข้อมูลจริง"""
    rows = _query(f"SELECT COUNT(*) AS n, MIN(ID) AS first_id, MAX(ID) AS last_id FROM `{_tbl()}` "
                  f"WHERE ID > %s AND ID <= %s AND CREATE_BY = %s", (int(last_id), int(upto_id), str(writer)))
    r = rows[0] if rows else {}
    return {'count': int(r.get('n') or 0), 'first_id': r.get('first_id'), 'last_id': r.get('last_id')}


def get_duplicate_device_sequences(days=60):
    """แถวที่ใช้ Device sequence + เครื่องเดียวกันมากกว่า 1 แถว (นำเข้าซ้ำ) เรียงตามเลข/เวลา — ให้ HR ยืนยันก่อนลบ"""
    return _query(f"SELECT f.ID, f.MACHIN_SCAN_ID AS seq, f.MACHIN_ID AS machine, f.PERSON_ID, f.HR_FULLNAME, f.SCAN_DATE_TIME "
                  f"FROM `{_tbl()}` f JOIN (SELECT MACHIN_ID, MACHIN_SCAN_ID FROM `{_tbl()}` "
                  f"WHERE MACHIN_SCAN_ID IS NOT NULL AND SCAN_DATE_TIME >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                  f"GROUP BY MACHIN_ID, MACHIN_SCAN_ID HAVING COUNT(*) > 1) d "
                  f"ON d.MACHIN_SCAN_ID = f.MACHIN_SCAN_ID AND d.MACHIN_ID <=> f.MACHIN_ID "
                  f"ORDER BY f.MACHIN_SCAN_ID, f.SCAN_DATE_TIME LIMIT 3000", (int(days),))


def get_names_by_fingle_ids(fingle_ids):
    """{str(FINGLE_ID): 'ชื่อ นามสกุล'} จาก hr_person — ใช้ระบุเจ้าของ Lost scan (ไม่มี/หาไม่เจอ = ไม่อยู่ในผล)"""
    ids = sorted({str(i).strip() for i in fingle_ids if str(i).strip()})
    if not ids:
        return {}
    rows = _query(f"SELECT FINGLE_ID, CONCAT_WS(' ', HR_FNAME, HR_LNAME) AS name FROM hr_person "
                  f"WHERE FINGLE_ID IN ({_in(ids)})", tuple(ids))
    return {str(r['FINGLE_ID']).strip(): (r['name'] or '').strip() for r in rows}


class HosofficeWriteError(RuntimeError):
    """เขียน hosoffice ไม่สำเร็จ (ทั้งชุดถูกย้อนกลับ ไม่มีแถวค้างครึ่งทาง)"""


# คอลัมน์ที่ Scan importer เขียนได้ — ชื่อคอลัมน์ไม่เคยมาจากข้อมูลภายนอก (กัน SQL injection ทางชื่อคอลัมน์)
INSERT_COLS = ('PERSON_ID', 'FINGLE_ID', 'SCAN_DATE_TIME', 'MACHIN_ID', 'MACHIN_SCAN_ID', 'SCAN_TYPE', 'DAY_TYPE_ID', 'DAY_TYPE_NAME',
               'COM_NAME', 'APP_TYPE_SAVE', 'ISCONFIRM', 'ISINPUT', 'HR_FULLNAME', 'HR_POSITION_NAME', 'HR_LEAVEL_NAME',
               'HR_DEP_NAME', 'HR_DEP_SUB_NAME', 'HR_PERSON_TYPE_NAME', 'CREATE_BY')


def insert_scan_rows(rows, writer):
    """เขียนแถวใหม่ลง fingle_scan ในหนึ่ง transaction ด้วยบัญชีเขียน (INSERT เท่านั้น) — คืนจำนวนที่เขียน
    ทุกแถวติดป้าย CREATE_BY = writer ; ผิดพลาดแม้แถวเดียว = ย้อนกลับทั้งชุด ; นี่คือที่เดียวในแอปที่เขียน hosoffice"""
    if not rows:
        return 0
    w = settings.current.importer_write
    base = connection_params()
    if not w or not base:
        raise HosofficeWriteError('ยังไม่ได้ตั้งบัญชีเขียน (SCAN_IMPORTER_MODE=write + HOSOFFICE_WRITE_*)')
    conn = None
    try:
        conn = mysql.connector.connect(host=base['host'], port=int(base['port']), user=w['user'], password=w['password'],
                                       database=base['database'], connection_timeout=10, charset='utf8mb4', autocommit=False)
        cur = conn.cursor()
        marks = ','.join(['%s'] * len(INSERT_COLS))
        sql = f"INSERT INTO `{_tbl()}` ({','.join(INSERT_COLS)}) VALUES ({marks})"
        cur.executemany(sql, [tuple(dict(r, CREATE_BY=writer).get(c) for c in INSERT_COLS) for r in rows])
        conn.commit()
        cur.close()
        return len(rows)
    except Error as e:
        if conn is not None:
            try:
                conn.rollback()
            except Error:
                pass
        raise HosofficeWriteError(f'เขียน hosoffice ล้มเหลว (ย้อนกลับทั้งชุดแล้ว): {e}') from e
    finally:
        if conn is not None:
            conn.close()


def get_existing_keys(since):
    """{(MACHIN_ID, MACHIN_SCAN_ID)} ของแถวที่มีเลขเครื่องและเวลาสแกน >= since — ใช้ดัชนีเวลา (ห้ามค้นตามเลขตรง ๆ : ไม่มีดัชนี)"""
    rows = _query(f"SELECT MACHIN_ID AS m, MACHIN_SCAN_ID AS s FROM `{_tbl()}` WHERE SCAN_DATE_TIME >= %s AND MACHIN_SCAN_ID IS NOT NULL",
                  (since,))
    return {(r['m'], int(r['s'])) for r in rows}


def get_person_ids(fingle_ids):
    """{str(FINGLE_ID): hr_person.ID} — ใช้เติม PERSON_ID ให้แถวใหม่ (ไม่เจอ = ไม่อยู่ในผล ผู้เรียกต้องกักแถว)"""
    ids = sorted({str(i).strip() for i in fingle_ids if str(i).strip()})
    if not ids:
        return {}
    rows = _query(f"SELECT FINGLE_ID, ID FROM hr_person WHERE FINGLE_ID IN ({_in(ids)})", tuple(ids))
    return {str(r['FINGLE_ID']).strip(): r['ID'] for r in rows}


_TEMPLATE_COLS = ('HR_FULLNAME, HR_POSITION_NAME, HR_LEAVEL_NAME, HR_DEP_NAME, HR_DEP_SUB_NAME, HR_PERSON_TYPE_NAME')


def get_latest_rows_by_person(person_ids):
    """{PERSON_ID: แถวล่าสุดที่ตัวนำเข้าเดิมเขียน (เฉพาะคอลัมน์ HR_*)} ใช้เป็นต้นแบบคัดลอกให้แถวใหม่ ; คนที่ไม่มีแถวเดิมไม่อยู่ในผล
    หนึ่ง query ต่อคน ใช้ดัชนี (PERSON_ID, SCAN_DATE_TIME) — ห้ามรวมเป็น subquery/EXISTS ซ้อน"""
    out = {}
    for pid in sorted({int(p) for p in person_ids if p is not None}):
        rows = _query(f"SELECT {_TEMPLATE_COLS} FROM `{_tbl()}` WHERE PERSON_ID = %s AND CREATE_BY IS NULL "
                      f"AND MACHIN_SCAN_ID IS NOT NULL ORDER BY SCAN_DATE_TIME DESC LIMIT 1", (pid,))
        if rows:
            out[pid] = rows[0]
    return out


HR_NAME_SEPARATOR = '   '        # ตัวเดิมคั่นชื่อกับนามสกุลด้วยช่องว่าง 3 ตัว (ยืนยันแล้วกับ 400/400 แถวล่าสุด)
_NO_ACCESS_ERRNOS = (1044, 1142, 1143, 1054, 1146)       # ไม่มีสิทธิ์ตาราง/คอลัมน์ หรือไม่พบคอลัมน์/ตาราง


def get_hr_profiles(person_ids):
    """{PERSON_ID: {HR_FULLNAME, HR_POSITION_NAME, HR_LEAVEL_NAME, HR_DEP_NAME, HR_DEP_SUB_NAME, HR_PERSON_TYPE_NAME}} สร้างจากข้อมูล HR ตรง ๆ
    (เหมือนที่ตัวเดิมทำ) ใช้กับคนที่ยังไม่เคยมีแถวใน hosoffice (พนักงานใหม่) ; ข้อมูลไม่ครบ (ไม่มีชื่อ/ตำแหน่ง/กลุ่มงาน/งาน/ประเภท) = ไม่อยู่ในผล
    คืน None ถ้าบัญชีอ่านยังไม่มีสิทธิ์ (หรือไม่พบตาราง/คอลัมน์) — ผู้เรียกใช้ต้นแบบจากแถวเก่าอย่างเดียวต่อไป ; ต่อฐานข้อมูลไม่ได้ = raise"""
    pids = sorted({int(p) for p in person_ids if p is not None})
    if not pids:
        return {}
    try:
        rows = _query(
            "SELECT p.ID AS pid, pf.HR_PREFIX_NAME AS prefix, p.HR_FNAME AS fname, p.HR_LNAME AS lname, "
            "pos.HR_POSITION_NAME AS position, lv.HR_LEVEL_NAME AS level, d.HR_DEPARTMENT_NAME AS dep, "
            "s.HR_DEPARTMENT_SUB_NAME AS sub, t.HR_PERSON_TYPE_NAME AS ptype "
            "FROM hr_person p "
            "LEFT JOIN hr_prefix pf ON pf.HR_PREFIX_ID = p.HR_PREFIX_ID "
            "LEFT JOIN hr_position pos ON pos.HR_POSITION_ID = p.HR_POSITION_ID "
            "LEFT JOIN hr_level lv ON lv.HR_LEVEL_ID = p.HR_LEVEL_ID "
            "LEFT JOIN hr_department d ON d.HR_DEPARTMENT_ID = p.HR_DEPARTMENT_ID "
            # จับคู่งานย่อยด้วยรหัสงานย่อยอย่างเดียว (รหัสไม่ซ้ำข้ามกลุ่มงาน — ตรวจแล้ว) : บางคนสังกัดงานย่อยต่างกลุ่มงานกับตัวเอง และตัวเดิมก็จับคู่แบบนี้
            "LEFT JOIN hr_department_sub s ON s.HR_DEPARTMENT_SUB_ID = p.HR_DEPARTMENT_SUB_ID "
            "LEFT JOIN hr_person_type t ON t.HR_PERSON_TYPE_ID = p.HR_PERSON_TYPE_ID "
            f"WHERE p.ID IN ({_in(pids)})", tuple(pids))
    except HosofficeUnavailable as e:
        if getattr(e.__cause__, 'errno', None) in _NO_ACCESS_ERRNOS:
            return None
        raise
    out = {}
    for r in rows:
        fname, lname = r['fname'] or '', r['lname'] or ''           # ไม่ตัดช่องว่าง : ตัวเดิมต่อสตริงตามที่เก็บใน hr_person
        profile = {'HR_FULLNAME': f"{r['prefix'] or ''}{fname}{HR_NAME_SEPARATOR}{lname}", 'HR_POSITION_NAME': r['position'],
                   'HR_LEAVEL_NAME': r['level'] or '', 'HR_DEP_NAME': r['dep'], 'HR_DEP_SUB_NAME': r['sub'], 'HR_PERSON_TYPE_NAME': r['ptype']}
        if fname.strip() and lname.strip() and all(profile[c] for c in ('HR_POSITION_NAME', 'HR_DEP_NAME', 'HR_DEP_SUB_NAME', 'HR_PERSON_TYPE_NAME')):
            out[r['pid']] = profile
    return out


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
