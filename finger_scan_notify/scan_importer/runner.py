"""รอบทำงานของ Scan importer ตัวใหม่ (off | shadow | write)
อ่านเครื่องสแกน → plan_batch → เก็บสถานะ/แถวที่กักลง SQLite ของแอปนี้ให้หน้า Watch แสดง แล้วขยับจุดล่าสุด
shadow = ไม่เขียน hosoffice เลย ; write = เขียนแถวที่ "จะเขียน" ในหนึ่ง transaction แล้วค่อยขยับจุด
ข้อผิดพลาดของฐานข้อมูล = บันทึกแล้วจบรอบ ไม่ขยับจุด (รอบหน้าอ่านช่วงเดิมซ้ำ ; แถวที่เขียนไปแล้วถูกกันซ้ำด้วยกุญแจ)"""
import threading
import time
from datetime import datetime, timedelta

import database
import hosoffice
import settings
import tigersoft

from scan_importer import plan

ROUND_SECONDS = 15
EXISTING_LOOKBACK = timedelta(days=1)          # ตรวจแถวที่มีอยู่แล้วย้อนจากแถวต้นทางเก่าสุดเท่านี้
EXISTING_MAX_BACK = timedelta(days=30)         # ไม่ย้อนเกินนี้ (query ตามดัชนีเวลา) — แถวเก่ากว่านั้นถูกกักอยู่แล้ว
status = {'running': False}
WRITER = 'SCAN-IMPORTER'


class ReleaseRefused(RuntimeError):
    """ปล่อยแถวที่กักไม่ได้ (โหมดไม่ใช่ write / ตัวอย่างล้าสมัย)"""


def _consts():
    return {'COM_NAME': (settings.current.importer_write or {}).get('com_name')}


def build_rows(src_rows):
    """(แถวที่สร้างได้, [(แถวต้นทาง, เหตุผล)] ที่สร้างไม่ได้) — ต้องหา PERSON_ID และต้นแบบ HR_* ของคนนั้นเจอ ไม่เดา ;
    ฐานข้อมูลล่ม = raise (ผู้เรียกจบรอบโดยไม่ขยับจุด)"""
    pids = hosoffice.get_person_ids([r.get('person') for r in src_rows if r.get('person') is not None])
    pid_of = {r['seq']: pids.get(str(r.get('person')).strip()) for r in src_rows}
    templates = hosoffice.get_latest_rows_by_person([v for v in pid_of.values() if v is not None])
    built, unbuildable = [], []
    for r in src_rows:
        pid = pid_of[r['seq']]
        row = plan.build_row(r, pid, templates.get(pid), _consts())
        if row is not None:
            built.append(row)
        else:
            unbuildable.append((r, 'no_time' if plan.row_time(r) is None else 'no_person' if pid is None else 'no_template'))
    return built, unbuildable


def hold_reason(src, now, flood):
    if flood:
        return 'flood'
    t = plan.row_time(src)
    if t is None:
        return 'no_time'
    return 'future' if t > now else 'old'


def run_round(now=None):
    """หนึ่งรอบ → {'status': 'off'|'needs_baseline'|'idle'|'ok'|'error', ...} ; บันทึกสถานะลง database ทุกครั้งที่ทำงานจริง"""
    now = now or datetime.now()
    ts = now.timestamp()
    if settings.current.importer_mode == 'off':
        return {'status': 'off'}
    st = database.get_importer_state()
    if st['checkpoint'] is None:
        database.save_importer_state(last_round_at=ts, last_error=None, last_counts={'needs_baseline': True})
        return {'status': 'needs_baseline'}
    try:
        rows = tigersoft.get_rows_after(st['checkpoint'])
        existing = set()
        if rows:
            times = [t for t in (plan.row_time(r) for r in rows) if t]
            since = max(min(times) - EXISTING_LOOKBACK, now - EXISTING_MAX_BACK) if times else now - EXISTING_LOOKBACK
            existing = hosoffice.get_existing_keys(since)
    except (tigersoft.TigerUnavailable, hosoffice.HosofficeUnavailable) as e:
        database.save_importer_state(last_round_at=ts, last_error=str(e)[:300])
        return {'status': 'error', 'error': str(e)}
    p = plan.plan_batch(rows, existing, now, checkpoint=st['checkpoint'])
    held = [{'src': r, 'reason': hold_reason(r, now, p['flood'])} for r in p['held']]
    inserted, would_n = None, 0
    if p['insert']:
        try:
            # โหมดเงาก็สร้างแถวด้วย (อ่านอย่างเดียว) เพื่อให้เห็นก่อนเปิดเขียนว่ามีแถว/คนที่สร้างไม่ได้ (ไม่มี PERSON_ID หรือต้นแบบ HR_*) เท่าไร
            built, unbuildable = build_rows(p['insert'])
            held += [{'src': r, 'reason': why} for r, why in unbuildable]
            database.add_importer_held(held, ts)
            would_n = len(built)
            if settings.current.importer_mode == 'write':
                inserted = hosoffice.insert_scan_rows(built, WRITER)
        except (hosoffice.HosofficeUnavailable, hosoffice.HosofficeWriteError) as e:
            database.save_importer_state(last_round_at=ts, last_error=str(e)[:300])
            return {'status': 'error', 'error': str(e)}
    else:
        database.add_importer_held(held, ts)
    counts = {'would_insert': would_n, 'held': len(held), 'duplicate': len(p['duplicate']),
              'flood': p['flood'], 'source_rows': len(rows)}
    if inserted is not None:
        counts['inserted'] = inserted
    database.save_importer_state(checkpoint=p['next_checkpoint'], last_round_at=ts, last_error=None, last_counts=counts,
                                 totals=add_totals(st['totals'], rows, p, would_n, len(held), inserted, ts))
    return {'status': 'ok' if rows else 'idle', **counts}


def add_totals(totals, rows, p, would_n, held_n, inserted, ts):
    """ตัวเลขสะสมตั้งแต่ตั้งจุดเริ่มต้น (หน้า Watch โชว์ — รอบล่าสุดที่เงียบจะไม่ลบสิ่งที่เห็นไปแล้ว) ; มีแต่จำนวนและเลข ไม่มีข้อมูลบุคคล"""
    t = dict(totals)
    for k, n in (('read', len(rows)), ('existing', len(p['duplicate'])), ('would_insert', would_n),
                 ('inserted', inserted or 0), ('held', held_n)):
        t[k] = t.get(k, 0) + n
    if rows:
        t['last_new_at'] = ts
        t['last_new_seq'] = max(r['seq'] for r in rows)
    return t


def loop():
    while status['running']:
        try:
            run_round()
        except Exception as e:   # noqa: BLE001 — ต้องไม่ตาย
            database.save_importer_state(last_error=str(e)[:300])
        for _ in range(ROUND_SECONDS * 2):
            if not status['running']:
                break
            time.sleep(0.5)


def start():
    if status['running'] or settings.current.importer_mode == 'off':
        return
    status['running'] = True
    threading.Thread(target=loop, daemon=True).start()


# ── ปล่อยแถวที่กัก ─────────────────────────────────────────────
def release_plan(lo, hi):
    """แผนปล่อยช่วงเลข lo–hi ของแถวที่กัก (อ่านอย่างเดียว) → {'plan', 'built', 'unbuildable', 'held_in_range'} ; raise ถ้าต่อ hosoffice ไม่ได้"""
    held = database.get_importer_held(lo, hi)
    existing = set()
    if held:
        oldest = min((plan.row_time(r) for r in held if plan.row_time(r)), default=datetime.now())
        existing = hosoffice.get_existing_keys(oldest)
    p = plan.plan_release(held, existing, lo, hi)
    built, unbuildable = build_rows(p['insert']) if p['insert'] else ([], [])
    return {'plan': p, 'built': built, 'unbuildable': unbuildable, 'held_in_range': len(held)}


def release(lo, hi, expect, who, now=None):
    """ปล่อยแถวที่กักในช่วง lo–hi ลง hosoffice (ป้าย SCAN-IMPORTER-RELEASED) — เฉพาะโหมด write และเมื่อจำนวนตรงกับที่ผู้ใช้เห็นในตัวอย่าง (expect)
    คืนจำนวนที่เขียน ; บันทึก audit ว่าใครสั่ง"""
    now = now or datetime.now()
    if settings.current.importer_mode != 'write':
        raise ReleaseRefused('ปล่อยได้เฉพาะโหมด write (SCAN_IMPORTER_MODE=write)')
    rp = release_plan(lo, hi)
    if len(rp['built']) != int(expect):
        raise ReleaseRefused(f'จำนวนที่จะเขียนเปลี่ยนไปแล้ว (ตัวอย่างเห็น {expect}, ตอนนี้ {len(rp["built"])}) — ดูตัวอย่างใหม่อีกครั้ง')
    n = hosoffice.insert_scan_rows(rp['built'], plan.RELEASE_WRITER)
    p = rp['plan']
    database.mark_importer_released([(r['machine'], r['seq']) for r in p['insert'] + p['duplicate']], now.timestamp())
    database.add_importer_audit(who, 'release', {'from': lo, 'to': hi, 'written': n, 'already_present': len(p['duplicate']),
                                                 'unreleasable': len(p['unreleasable']) + len(rp['unbuildable'])}, now.timestamp())
    return n
