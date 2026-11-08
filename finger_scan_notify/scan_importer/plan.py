"""แกนตรรกะล้วนของ Scan importer: ตัดสินว่าแถวต้นทางแต่ละแถว "เขียน / กัก / ซ้ำ" — ไม่ทำ I/O ไม่เรียกนาฬิกาเอง (รับ now)

แถวต้นทาง = รูปแบบเดียวกับ tigersoft._normalise: {'seq','machine','person','time','device_time'}
"""
from datetime import timedelta

HOLD_AFTER = timedelta(hours=24)        # แถวเก่ากว่านี้ไม่เขียนเอง
BATCH_CAP = 300                         # รอบเดียวเขียนเกินนี้ = กักทั้งรอบ (กันท่วมระบบที่แจ้งตามแถวใหม่)


def row_key(src):
    return (src.get('machine'), src['seq'])


def row_time(src):
    """เวลาสแกนที่จะเก็บ: CommitTime (ถึงวินาที) สำรอง TimeInout ระดับนาที"""
    return src.get('time') or src.get('device_time')


def plan_batch(source_rows, existing_keys, now, *, checkpoint, max_age=HOLD_AFTER, batch_cap=BATCH_CAP):
    """คืน {'insert','held','duplicate','needs_baseline','flood','next_checkpoint'}

    ไม่มีจุดล่าสุด (checkpoint=None) = ไม่เขียนอะไรเลย รอคนตั้งจุดเริ่ม ; ไม่เดาว่าต้องเริ่มจากต้นตาราง (เหตุการณ์ 8 พ.ย. 2569)
    """
    if checkpoint is None:
        return {'insert': [], 'held': [], 'duplicate': [], 'needs_baseline': True, 'flood': False, 'next_checkpoint': None}
    insert, held, duplicate, seen = [], [], [], set(existing_keys)
    for r in sorted(source_rows, key=lambda r: r['seq']):
        k = row_key(r)
        t = row_time(r)
        if k in seen:
            duplicate.append(r)
        elif t is None or now - t > max_age:
            held.append(r)
            seen.add(k)
        else:
            insert.append(r)
            seen.add(k)
    flood = len(insert) > batch_cap
    if flood:                                   # ทั้งรอบเป็นสิ่งผิดปกติ: ไม่เขียนสักแถว ให้คนดูก่อน
        held = sorted(held + insert, key=lambda r: r['seq'])
        insert = []
    handled = insert + held + duplicate
    return {'insert': insert, 'held': held, 'duplicate': duplicate, 'needs_baseline': False, 'flood': flood,
            'next_checkpoint': max((r['seq'] for r in handled), default=checkpoint)}


WRITER = 'SCAN-IMPORTER'                      # ป้าย CREATE_BY ของแถวปกติ
BACKFILL_WRITER = 'SCAN-IMPORTER-BACKFILL'    # แถวที่ปล่อยจากที่กัก — ระบบอื่นแยกออกได้ว่าเป็นของย้อนหลัง


def plan_release(held_rows, existing_keys, seq_from, seq_to):
    """แผนปล่อยแถวที่กัก (ระบุช่วง Device sequence) + สรุปสำหรับ dry-run : จำนวน, วันเก่าสุด/ใหม่สุด, จำนวนต่อวัน
    ไม่เขียนแถวที่มีกุญแจอยู่แล้ว จึงสั่งซ้ำได้ปลอดภัย"""
    seen = set(existing_keys)
    insert, duplicate = [], []
    for r in sorted(held_rows, key=lambda r: r['seq']):
        if not seq_from <= r['seq'] <= seq_to:
            continue
        k = row_key(r)
        if k in seen:
            duplicate.append(r)
        else:
            insert.append(r)
            seen.add(k)
    times = [row_time(r) for r in insert if row_time(r) is not None]
    per_day = {}
    for t in sorted(times):
        per_day[t.date().isoformat()] = per_day.get(t.date().isoformat(), 0) + 1
    return {'insert': insert, 'duplicate': duplicate, 'writer': BACKFILL_WRITER,
            'summary': {'count': len(insert), 'oldest': min(times, default=None), 'newest': max(times, default=None),
                        'per_day': per_day}}
