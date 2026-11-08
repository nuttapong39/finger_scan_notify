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
TIME_COL = 'TimeInout'
CHUNK = 500
_SELECT_ONLY = re.compile(r'^\s*SELECT\b', re.I)


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
                           database=t['database'], login_timeout=10, timeout=30, as_dict=True, charset='UTF-8')


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
    if not _SELECT_ONLY.match(sql) or ';' in sql:
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
    return {'seq': int(raw[SEQ_COL]), 'time': raw.get(TIME_COL),
            'person': raw.get(person_col) if person_col else None,
            'machine': raw.get(machine_col) if machine_col else None}


def get_rows(seqs):
    """{seq: {'seq','time','person','machine'}} ของเลขที่เครื่องมีอยู่จริง (เลขที่เครื่องไม่เคยออกให้ไม่อยู่ในผล)"""
    seqs = sorted({int(s) for s in seqs})
    out = {}
    if not seqs:
        return out
    with _open() as conn:
        for i in range(0, len(seqs), CHUNK):
            part = seqs[i:i + CHUNK]
            marks = ','.join(['%s'] * len(part))
            for raw in _run(conn, f'SELECT * FROM {TABLE} WHERE {SEQ_COL} IN ({marks})', part):
                row = _normalise(raw)
                out[row['seq']] = row
    return out
