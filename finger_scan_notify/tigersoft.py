"""อ่านฐานข้อมูลเครื่องสแกน (Tigersoft XFace, SQL Server `TigerWebServerPlus`) — อ่านอย่างเดียว (ช่วง C)

ใช้ระบุว่า Lost scan เป็นของใคร/เวลาไหน และเทียบเลขล่าสุดสองฝั่งเพื่อตรวจ Import stall ตลอดวัน
กฎความปลอดภัย: ส่งได้เฉพาะ SELECT คำสั่งเดียว (ตรวจก่อนส่งทุกครั้ง) และค่าทั้งหมดส่งเป็นพารามิเตอร์ ไม่ต่อสตริง
SQL Server ไม่มีโหมด session READ ONLY เหมือน MySQL — ความปลอดภัยจึงพึ่งโค้ดนี้ + สิทธิ์ของบัญชี (ควรเป็น db_datareader)
ต่อไม่ได้/ผิดพลาด = TigerUnavailable (ผู้เรียกข้ามไปเฉย ๆ ไม่ให้แอปล้ม) และข้อความ error ไม่มีรหัสผ่าน
"""
import re
from contextlib import contextmanager

import settings

TABLE = 'ZFP_TimeInOut'
SEQ_COL = 'ID_TimeInout'          # = fingle_scan.MACHIN_SCAN_ID (Device sequence) — ยืนยันด้วย manage.py tiger-check
TIME_COL = 'TimeInout'            # เวลาที่เครื่องบันทึก — ระดับนาทีเท่านั้น (วินาที = 00)
MODE_COL = 'InOutMode'            # = fingle_scan.SCAN_TYPE (0 เข้า, 1 ออก, 4 เข้าพิเศษ, 5 ออกพิเศษ) — ยืนยันเทียบ 397 แถว
COMMIT_COL = 'CommitTime'         # เวลาถึงวินาที — ค่านี้คือ SCAN_DATE_TIME ที่ตัวนำเข้าเดิมเก็บใน hosoffice (±1 วินาที)
CHUNK = 500
_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_SELECT_ONLY = re.compile(r'^\s*SELECT\b', re.I)
# T-SQL ไม่ต้องมี ; คั่นคำสั่ง จึงห้ามคำสั่งเขียน/รันและคอมเมนต์ทั้งหมด (ด่านที่สองรองจากสิทธิ์ db_datareader ของบัญชี)
_FORBIDDEN = re.compile(r'\b(INTO|EXEC|EXECUTE|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|'
                        r'BULK|OPENROWSET|OPENQUERY|OPENDATASOURCE|SHUTDOWN|BACKUP|RESTORE)\b|--|/\*', re.I)


class TigerUnavailable(RuntimeError):
    """ยังไม่ได้ตั้งค่า / ต่อฐานข้อมูลเครื่องสแกนไม่ได้ / query ล้มเหลว"""


def configured():
    return settings.current.tiger is not None


def _connect():
    try:
        import pymssql
    except ImportError:
        raise TigerUnavailable('ยังไม่ได้ติดตั้ง pymssql (pip install -r requirements.txt)') from None
    t = settings.current.tiger
    return pymssql.connect(server=t['host'], port=str(t['port']), user=t['user'], password=t['password'],
                           database=t['database'], login_timeout=5, timeout=15, as_dict=True, charset='UTF-8')


def _clean(prefix, err):
    """ข้อความ error ที่ปลอดภัยต่อการแสดง/บันทึก — ตัดรหัสผ่านออกเสมอ"""
    msg = str(err)
    t = settings.current.tiger
    if t and t['password']:
        msg = msg.replace(t['password'], '****')
    return f'{prefix}: {msg[:200]}'


@contextmanager
def _open():
    if not configured():
        raise TigerUnavailable('ยังไม่ได้ตั้งค่า TIGER_* ใน .env')
    try:
        conn = _connect()
    except TigerUnavailable:
        raise
    except Exception as e:   # noqa: BLE001 — driver ใด ๆ
        raise TigerUnavailable(_clean('ต่อฐานข้อมูลเครื่องสแกนไม่ได้', e)) from None
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:   # noqa: BLE001
            pass


def _check_select(sql):
    if not _SELECT_ONLY.match(sql) or ';' in sql or _FORBIDDEN.search(sql):
        raise ValueError('ฐานข้อมูลเครื่องสแกนอนุญาตเฉพาะ SELECT คำสั่งเดียว')


def _run(conn, sql, params=()):
    _check_select(sql)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(params))
            return cur.fetchall()
        finally:
            cur.close()
    except Exception as e:   # noqa: BLE001
        raise TigerUnavailable(_clean('อ่านฐานข้อมูลเครื่องสแกนไม่สำเร็จ', e)) from None


def _select(sql, params=()):
    _check_select(sql)
    with _open() as conn:
        return _run(conn, sql, params)


def get_latest_seq():
    rows = _select(f'SELECT MAX({SEQ_COL}) AS m FROM {TABLE}')
    return int((rows[0]['m'] if rows else 0) or 0)


def get_columns():
    """[(ชื่อคอลัมน์, ชนิด)] ของตาราง log การสแกน — ใช้หาชื่อคอลัมน์ที่จะตั้ง TIGER_PERSON_COLUMN / TIGER_MACHINE_COLUMN"""
    rows = _select('SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = %s '
                   'ORDER BY ORDINAL_POSITION', (TABLE,))
    return [(r['COLUMN_NAME'], r['DATA_TYPE']) for r in rows]


def _normalise(raw):
    t = settings.current.tiger
    person_col, machine_col = t['person_column'], t['machine_column']
    try:
        seq = int(raw[SEQ_COL])
    except (KeyError, TypeError, ValueError):
        raise TigerUnavailable(f'ผลที่ได้ไม่มีคอลัมน์ {SEQ_COL} (ตรวจชื่อตารางด้วย manage.py tiger-check)') from None
    return {'seq': seq, 'time': raw.get(COMMIT_COL) or raw.get(TIME_COL), 'device_time': raw.get(TIME_COL),
            'mode': None if raw.get(MODE_COL) is None else str(raw[MODE_COL]).strip(),
            'person': _whole(raw.get(person_col)) if person_col else None,
            'machine': _whole(raw.get(machine_col)) if machine_col else None}


def _whole(v):
    """คอลัมน์ numeric ของ SQL Server มาเป็น Decimal — แปลงเป็นจำนวนเต็มเมื่อไม่มีเศษ (ใช้เป็นกุญแจจับคู่ hr_person ได้ตรง ๆ)"""
    try:
        return int(v) if v is not None and int(v) == v else v
    except (TypeError, ValueError):
        return v


def same_scan_time(hos_time, src):
    """เวลาใน hosoffice ตรงกับแถวต้นทางไหม: ตัวนำเข้าเก็บ CommitTime (ถึงวินาที, คลาดได้ ~2 วินาที) หรือ TimeInout (ระดับนาที) อย่างใดอย่างหนึ่ง"""
    if not src or not hos_time:
        return False
    t = src.get('time')
    if t and abs((hos_time - t).total_seconds()) <= 2:
        return True
    return bool(src.get('device_time') and hos_time == src['device_time'])


def get_rows_after(seq, limit=3000):
    """แถวที่ ID_TimeInout > seq เรียงตามเลข (สูงสุด limit แถว) — ใช้ในโหมดเงา/Scan importer ; รูปแบบเดียวกับ get_rows"""
    t = settings.current.tiger
    wanted = [SEQ_COL, TIME_COL, COMMIT_COL, MODE_COL, t['person_column'], t['machine_column']]
    for name in wanted:
        if name and not _IDENT.match(name):
            raise TigerUnavailable(f'ชื่อคอลัมน์ไม่ถูกต้อง: {name!r}')
    cols = ', '.join(dict.fromkeys(n for n in wanted if n))
    rows = _select(f'SELECT TOP {int(limit)} {cols} FROM {TABLE} WHERE {SEQ_COL} > %s ORDER BY {SEQ_COL}', (int(seq),))
    return [_normalise(r) for r in rows]


def get_rows(seqs):
    """{seq: {'seq','time','person','machine'}} ของเลขที่เครื่องมีอยู่จริง (เลขที่เครื่องไม่เคยออกให้ไม่อยู่ในผล)"""
    seqs = sorted({int(s) for s in seqs})
    out = {}
    if not seqs:
        return out
    t = settings.current.tiger
    # เลือกเฉพาะคอลัมน์ที่ใช้ (ตารางของเครื่องสแกนอาจมีรูป/ข้อมูลใบหน้าขนาดใหญ่) — ชื่อคอลัมน์จาก .env ตรวจรูปแบบก่อนต่อเข้า SQL
    wanted = [SEQ_COL, TIME_COL, COMMIT_COL, MODE_COL, t['person_column'], t['machine_column']]
    for name in wanted:
        if name and not _IDENT.match(name):
            raise TigerUnavailable(f'ชื่อคอลัมน์ไม่ถูกต้อง: {name!r}')
    cols = ', '.join(dict.fromkeys(n for n in wanted if n))
    with _open() as conn:
        for i in range(0, len(seqs), CHUNK):
            part = seqs[i:i + CHUNK]
            marks = ','.join(['%s'] * len(part))
            for raw in _run(conn, f'SELECT {cols} FROM {TABLE} WHERE {SEQ_COL} IN ({marks})', part):
                row = _normalise(raw)
                out[row['seq']] = row
    return out
