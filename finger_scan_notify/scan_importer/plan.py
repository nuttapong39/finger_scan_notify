"""แกนตรรกะล้วนของ Scan importer: ตัดสินว่าแถวต้นทางแต่ละแถว "เขียน / กัก / ซ้ำ" — ไม่ทำ I/O ไม่เรียกนาฬิกาเอง (รับ now)

แถวต้นทาง = รูปแบบเดียวกับ tigersoft._normalise: {'seq','machine','person','time','device_time'}
"""
from datetime import timedelta

HOLD_AFTER = timedelta(hours=24)        # แถวเก่ากว่านี้ไม่เขียนเอง
FUTURE_SKEW = timedelta(hours=1)        # เวลาล้ำอนาคตเกินนี้ = นาฬิกาเครื่องเพี้ยน ไม่เขียนเอง
BATCH_CAP = 300                         # รอบเดียวเขียนเกินนี้ = กักทั้งรอบ (กันท่วมระบบที่แจ้งตามแถวใหม่)
RELEASE_WRITER = 'SCAN-IMPORTER-RELEASED'    # ป้าย CREATE_BY ของแถวที่ปล่อยจากที่กัก — ระบบอื่นแยกออกได้ว่าไม่ใช่สแกนสด


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
