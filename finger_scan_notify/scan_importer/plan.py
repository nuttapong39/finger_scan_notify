"""แกนตรรกะล้วนของ Scan importer: ตัดสินว่าแถวต้นทางแต่ละแถว "เขียน / กัก / ซ้ำ" — ไม่ทำ I/O ไม่เรียกนาฬิกาเอง (รับ now)

แถวต้นทาง = รูปแบบเดียวกับ tigersoft._normalise: {'seq','machine','person','time','device_time'}
"""
from datetime import datetime, timedelta

HOLD_AFTER = timedelta(hours=24)        # แถวเก่ากว่านี้ไม่เขียนเอง
FUTURE_SKEW = timedelta(hours=1)        # เวลาล้ำอนาคตเกินนี้ = นาฬิกาเครื่องเพี้ยน ไม่เขียนเอง
BATCH_CAP = 300                         # รอบเดียวเขียนเกินนี้ = กักทั้งรอบ (กันท่วมระบบที่แจ้งตามแถวใหม่)
RELEASE_WRITER = 'SCAN-IMPORTER-RELEASED'    # ป้าย CREATE_BY ของแถวที่ปล่อยจากที่กัก — ระบบอื่นแยกออกได้ว่าไม่ใช่สแกนสด
MANUAL_WRITER = 'SCAN-IMPORTER-MANUAL'          # แถวที่คนสั่งนำเข้าจากเครื่องสแกนตามเลขที่ระบุเอง (หน้า Watch)


def row_key(src):
    return (src.get('machine'), src['seq'])


def row_time(src):
    """เวลาสแกนที่จะเก็บ: CommitTime (ถึงวินาที) สำรอง TimeInout ระดับนาที"""
    return src.get('time') or src.get('device_time')


def _judgeable(t, now):
    return t is not None and -FUTURE_SKEW <= (now - t) <= HOLD_AFTER


def plan_batch(source_rows, existing_keys, now, *, checkpoint):
    """คืน {'insert','held','duplicate','needs_baseline','flood','next_checkpoint'}

    ไม่มีจุดล่าสุด (checkpoint=None) = ไม่เขียนอะไรเลย รอคนตั้งจุดเริ่ม ; ไม่เดาว่าต้องเริ่มจากต้นตาราง (เหตุการณ์ 8 พ.ย. 2569)
    """
    if checkpoint is None:
        return {'insert': [], 'held': [], 'duplicate': [], 'needs_baseline': True, 'flood': False, 'next_checkpoint': None}
    insert, held, duplicate, seen = [], [], [], set(existing_keys)
    for r in sorted(source_rows, key=lambda r: r['seq']):
        k = row_key(r)
        if k in seen:
            duplicate.append(r)
            continue
        seen.add(k)
        (insert if _judgeable(row_time(r), now) else held).append(r)
    flood = len(insert) > BATCH_CAP
    if flood:                                   # ทั้งรอบเป็นสิ่งผิดปกติ: ไม่เขียนสักแถว ให้คนดูก่อน
        held = sorted(held + insert, key=lambda r: r['seq'])
        insert = []
    handled = insert + held + duplicate
    return {'insert': insert, 'held': held, 'duplicate': duplicate, 'needs_baseline': False, 'flood': flood,
            'next_checkpoint': max((r['seq'] for r in handled), default=checkpoint)}


def plan_release(held_rows, existing_keys, seq_from, seq_to):
    """แผนปล่อยแถวที่กัก (ระบุช่วง Device sequence) + สรุปสำหรับ dry-run : จำนวน, วันเก่าสุด/ใหม่สุด, จำนวนต่อวัน
    ไม่เขียนแถวที่มีกุญแจอยู่แล้ว (สั่งซ้ำได้ปลอดภัย) และไม่ปล่อยแถวที่ไม่มีเวลาสแกน (`unreleasable` — ให้คนแก้ที่ต้นทางก่อน)
    การแยก dry-run กับ --confirm เป็นหน้าที่ของคำสั่งในช่วง B ; ที่นี่คืนแผนล้วน ๆ ให้ทั้งสองโหมดใช้"""
    seen = set(existing_keys)
    insert, duplicate, unreleasable = [], [], []
    for r in sorted(held_rows, key=lambda r: r['seq']):
        if not seq_from <= r['seq'] <= seq_to:
            continue
        k = row_key(r)
        if k in seen:
            duplicate.append(r)
        elif row_time(r) is None:
            unreleasable.append(r)
        else:
            insert.append(r)
            seen.add(k)
    times = sorted(row_time(r) for r in insert)
    per_day = {}
    for t in times:
        per_day[t.date().isoformat()] = per_day.get(t.date().isoformat(), 0) + 1
    return {'insert': insert, 'duplicate': duplicate, 'unreleasable': unreleasable, 'writer': RELEASE_WRITER,
            'summary': {'count': len(insert), 'oldest': times[0] if times else None, 'newest': times[-1] if times else None,
                        'per_day': per_day}}


WEEKDAYS_TH = ('จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์')     # ตาม datetime.weekday()
OLD_IMPORTER_CONSTANTS = {'APP_TYPE_SAVE': 'WIN', 'ISCONFIRM': 'False', 'ISINPUT': 'FACESCAN'}
HR_COPIED = ('HR_FULLNAME', 'HR_POSITION_NAME', 'HR_LEAVEL_NAME', 'HR_DEP_NAME', 'HR_DEP_SUB_NAME', 'HR_PERSON_TYPE_NAME')


def build_row(src, person_id, template, consts):
    """แถว fingle_scan ที่จะเขียนสำหรับแถวต้นทางนี้ (dict คอลัมน์→ค่า) หรือ None ถ้าสร้างไม่ได้ → ผู้เรียกกักแถวไว้ ไม่เดา
    template = แถวล่าสุดของคนเดียวกันที่ตัวเดิมเขียน (ใช้คัดลอก HR_*) ; consts ต้องมี COM_NAME ; ล้วน ไม่มี I/O"""
    t = row_time(src)
    if t is None or person_id is None or template is None:
        return None
    row = {'PERSON_ID': person_id, 'FINGLE_ID': None if src.get('person') is None else str(src['person']).strip(), 'SCAN_DATE_TIME': t,
           'MACHIN_ID': src.get('machine'), 'MACHIN_SCAN_ID': src['seq'], 'SCAN_TYPE': src.get('mode'),
           'DAY_TYPE_ID': (t.weekday() + 1) % 7 + 1, 'DAY_TYPE_NAME': WEEKDAYS_TH[t.weekday()],
           'COM_NAME': consts['COM_NAME'], **OLD_IMPORTER_CONSTANTS}
    row.update({c: template.get(c) for c in HR_COPIED})
    return row


TIME_TOLERANCE_S = 2     # ตัวเดิมเก็บ CommitTime คลาดได้ ~1–2 วินาที (ยืนยันกับของจริง) — ไม่นับเป็นความต่าง


def _same(a, b):
    if isinstance(a, datetime) and isinstance(b, datetime):
        return abs((a - b).total_seconds()) <= TIME_TOLERANCE_S
    return a == b


def differing_columns(built, real):
    """ชื่อคอลัมน์ที่ค่าไม่ตรงกัน (เฉพาะคอลัมน์ที่ built มี ; เวลาคลาดได้ TIME_TOLERANCE_S) — คืนแต่ชื่อ ไม่คืนค่า เพื่อไม่ให้รายงานมีข้อมูลบุคคล"""
    return [c for c, v in built.items() if not _same(v, real.get(c))]
