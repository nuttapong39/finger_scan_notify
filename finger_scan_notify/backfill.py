"""Delivery status (Recheck) และ Backfill (ส่งย้อนหลัง)

- delivery_status : สถานะของแต่ละ Scan จาก notify_logs + scan_skips (สำเร็จ / ล้มเหลว / ถูกข้าม / ไม่เคยส่ง)
- preview         : สรุปก่อนส่ง (ไม่ส่งอะไร)
- start_job       : ส่งจริงแบบ background ทีละรายการ, ไม่เกิน MAX_PER_JOB, job เดียวพร้อมกัน, ยกเลิกได้
Scan ทุกแถวอ่านใหม่จาก hosoffice ด้วย ID เสมอ — ไม่เชื่อข้อมูลจาก browser
"""
import threading
import time
from datetime import date, datetime, timedelta

import database
import hosoffice
import scan_types
from delivery import deliver_scan, scan_skip_reason

MAX_PER_JOB = 200
CONFIRM_DAYS = 7
CONFIRM_TEXT = 'ยืนยันส่งย้อนหลัง'
THROTTLE_SECONDS = 0.5

SKIP_REASON_TH = {
    'stale': 'scan เก่าเกินเวลาที่กำหนด', 'duplicate': 'เคยส่งแล้ว (ซ้ำ)', 'quota': 'เกินโควตาต่อชั่วโมง',
    'type_off': 'ปิดแจ้งเตือนประเภทนี้', 'muted': 'ขอปิดแจ้งเตือนใน hosoffice', 'no_keys': 'token ไม่ครบ',
    'unknown_type': 'ไม่ทราบประเภทการสแกน', 'no_token': 'ไม่มี token', 'already_sent': 'ส่งสำเร็จแล้ว',
}

_lock = threading.Lock()
_cancelled = set()


class BackfillError(ValueError):
    """คำขอส่งย้อนหลังไม่ถูกต้อง (ข้อความภาษาไทยแสดงให้ผู้ใช้ได้)"""


# ── Delivery status ───────────────────────────────────────────
def delivery_status(scan_ids):
    """{scan_id: {'status', 'detail', 'at', 'source'}} — สำเร็จครั้งใดก็ถือว่าสำเร็จ"""
    logs, skips = database.get_delivery_records(scan_ids)
    out = {int(i): {'status': 'never', 'detail': '', 'at': None, 'source': None} for i in scan_ids}
    for row in skips:
        out[row['scan_id']] = {'status': 'skipped', 'detail': row['reason'], 'at': row['created_at'],
                               'source': row['source']}
    for row in logs:
        cur = out[row['scan_id']]
        if cur['status'] == 'success' and row['status'] != 'success':
            continue
        out[row['scan_id']] = {'status': 'success' if row['status'] == 'success' else 'error',
                               'detail': f"HTTP {row['http_code']} {row['response'] or ''}".strip(),
                               'at': row['created_at'], 'source': row['source'] or 'live'}
    return out


def build_rows(scans, statuses, token_pids):
    rows = []
    for s in scans:
        sid, pid = int(s['ID']), int(s['PERSON_ID'] or 0)
        st = statuses.get(sid, {'status': 'never', 'detail': '', 'at': None, 'source': None})
        way = scan_types.direction(s.get('SCAN_TYPE'))
        manual = (s.get('ISINPUT') or '').upper() == 'MANUAL'
        has_token = pid in token_pids
        dt = s.get('SCAN_DATE_TIME')
        rows.append({
            'id': sid, 'person_id': pid, 'name': (s.get('HR_FULLNAME') or '').strip(),
            'department': (s.get('HR_DEP_SUB_NAME') or '').strip(),
            'datetime': dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(dt, 'strftime') else str(dt or ''),
            'direction': way, 'label': scan_types.label(s.get('SCAN_TYPE')) or str(s.get('SCAN_TYPE')),
            'isinput': s.get('ISINPUT') or '', 'manual': manual, 'has_token': has_token,
            'status': st['status'], 'detail': SKIP_REASON_TH.get(st['detail'], st['detail']),
            'at': st['at'], 'source': st['source'],
            'default_checked': bool(has_token and way and not manual and st['status'] in ('never', 'error')),
        })
    return rows


# ── ช่วงวันที่ ────────────────────────────────────────────────
def _parse_date(v):
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        raise BackfillError('รูปแบบวันที่ต้องเป็น YYYY-MM-DD') from None


def parse_range(date_from, date_to, today):
    d1, d2 = _parse_date(date_from), _parse_date(date_to)
    if d1 > d2:
        raise BackfillError('วันเริ่มต้องไม่หลังวันสิ้นสุด')
    if d2 > today:
        raise BackfillError('เลือกวันในอนาคตไม่ได้')
    return d1, d2


def needs_confirm(date_from, today):
    return date_from < today - timedelta(days=CONFIRM_DAYS)


# ── แผนการส่ง ─────────────────────────────────────────────────
def _plan(scan_ids, resend_ids):
    """คืน (items=[(scan, token_row, is_resend)], excluded={reason: n}) — raise HosofficeUnavailable ถ้าต่อ DB ไม่ได้"""
    scans = hosoffice.get_scans_by_ids(scan_ids)
    muted = hosoffice.get_muted_person_ids()
    tokens = {t['person_id']: t for t in database.get_all_tokens() if t.get('active')}
    statuses = delivery_status([s['ID'] for s in scans])
    resend = {int(i) for i in resend_ids}
    items, excluded = [], {}

    def skip(reason):
        excluded[reason] = excluded.get(reason, 0) + 1

    for s in scans:
        sid, pid = int(s['ID']), int(s['PERSON_ID'] or 0)
        token = tokens.get(pid)
        if scan_types.direction(s.get('SCAN_TYPE')) is None:
            skip('unknown_type')
        elif not token:
            skip('no_token')
        elif pid in muted:
            skip('muted')
        elif statuses[sid]['status'] == 'success' and sid not in resend:
            skip('already_sent')
        elif (reason := scan_skip_reason(s, token)):
            skip(reason)
        else:
            items.append((s, token, sid in resend))
    return items, excluded


def preview(scan_ids, resend_ids, date_from, date_to, today):
    items, excluded = _plan(scan_ids, resend_ids)
    return {
        'messages': len(items),
        'persons': len({s['PERSON_ID'] for s, _, _ in items}),
        'resend': sum(1 for _, _, r in items if r),
        'excluded': excluded,
        'excluded_th': {SKIP_REASON_TH.get(k, k): v for k, v in excluded.items()},
        'max': MAX_PER_JOB, 'over_cap': len(items) > MAX_PER_JOB,
        'needs_confirm': needs_confirm(date_from, today), 'confirm_text': CONFIRM_TEXT,
    }


# ── Job ───────────────────────────────────────────────────────
def start_job(scan_ids, resend_ids, date_from, date_to, confirm_text, today, run_async=True):
    """เริ่มส่งย้อนหลัง — คืน job_id ; raise BackfillError ถ้าคำขอไม่ผ่านตัวกัน"""
    with _lock:
        if database.get_running_backfill_job():
            raise BackfillError('มีงานส่งย้อนหลังกำลังทำงานอยู่ รอให้เสร็จหรือกดหยุดก่อน')
        if needs_confirm(date_from, today) and (confirm_text or '').strip() != CONFIRM_TEXT:
            raise BackfillError(f'ช่วงวันที่เกิน {CONFIRM_DAYS} วัน ต้องพิมพ์ "{CONFIRM_TEXT}" เพื่อยืนยัน')
        items, _ = _plan(scan_ids, resend_ids)
        if not items:
            raise BackfillError('ไม่มีรายการที่ส่งได้')
        if len(items) > MAX_PER_JOB:
            raise BackfillError(f'{len(items)} ข้อความ เกินเพดาน {MAX_PER_JOB} ต่อครั้ง — แบ่งส่งเป็นหลายรอบ')
        job_id = database.create_backfill_job(str(date_from), str(date_to), len(items))
    if run_async:
        threading.Thread(target=run_job, args=(job_id, items), daemon=True).start()
    else:
        run_job(job_id, items)
    return job_id


def cancel_job(job_id):
    _cancelled.add(int(job_id))


def run_job(job_id, items):
    sent = failed = skipped = 0
    status, message = 'done', ''
    try:
        for i, (scan, token, is_resend) in enumerate(items):
            if job_id in _cancelled:
                status = 'cancelled'
                break
            # ตรวจซ้ำก่อนส่ง: ระหว่างรอคิว Monitor อาจส่งรายการนี้ไปแล้ว
            if not is_resend and delivery_status([scan['ID']])[int(scan['ID'])]['status'] == 'success':
                skipped += 1
            else:
                ok, _, _ = deliver_scan(scan, token, source='backfill', backfill=True, job_id=job_id)
                sent, failed = sent + ok, failed + (not ok)
            database.update_backfill_job(job_id, sent=sent, failed=failed, skipped=skipped)
            if THROTTLE_SECONDS and i < len(items) - 1:
                time.sleep(THROTTLE_SECONDS)
        else:
            status = 'cancelled' if job_id in _cancelled else 'done'
    except Exception as e:   # noqa: BLE001 — job ต้องจบสถานะเสมอ ไม่ค้าง running
        status, message = 'error', str(e)[:300]
    finally:
        _cancelled.discard(job_id)
        database.update_backfill_job(job_id, sent=sent, failed=failed, skipped=skipped, status=status,
                                     message=message, finished=True)
    return status
