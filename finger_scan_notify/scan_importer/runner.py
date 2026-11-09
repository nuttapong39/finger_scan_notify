"""รอบทำงานของ Scan importer ตัวใหม่ — ตอนนี้มีแค่โหมดเงา (off | shadow) : ไม่เขียน hosoffice เลย
อ่านเครื่องสแกน → plan_batch → เก็บสถานะ/แถวที่กักลง SQLite ของแอปนี้ให้หน้า Watch แสดง แล้วขยับจุดล่าสุด
ข้อผิดพลาดของฐานข้อมูล = บันทึกแล้วจบรอบ ไม่ขยับจุด (รอบหน้าอ่านช่วงเดิมซ้ำ)"""
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
    database.add_importer_held([{'src': r, 'reason': hold_reason(r, now, p['flood'])} for r in p['held']], ts)
    counts = {'would_insert': len(p['insert']), 'held': len(p['held']), 'duplicate': len(p['duplicate']),
              'flood': p['flood'], 'source_rows': len(rows)}
    database.save_importer_state(checkpoint=p['next_checkpoint'], last_round_at=ts, last_error=None, last_counts=counts)
    return {'status': 'ok' if rows else 'idle', **counts}


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
