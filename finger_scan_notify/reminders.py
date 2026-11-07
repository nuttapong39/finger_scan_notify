"""Missing-scan reminder (เตือนยังไม่สแกน)

ทุกนาที สำหรับแต่ละ kind ('checkin' / 'checkout'):
  เปิดใช้ + อยู่ใน Reminder window + ไม่ใช่เสาร์-อาทิตย์/วันหยุดราชการ
  → ผู้มีสิทธิ์ = token active + เปิดรับเตือน − ขอปิดแจ้งเตือน − มีใบลาวันนี้ − เตือนแล้ววันนี้
  → decide() จาก Scan วันนี้ → claim → ตรวจ Scan สดซ้ำ → ส่ง
Fail closed: ต่อ hosoffice ไม่ได้ = ไม่เตือน (ไม่ตีความ "ไม่พบ Scan" ว่าลืมสแกน)
"""
import re
import threading
import time
from datetime import datetime

import database
import hosoffice
import scan_types
from delivery import deliver_reminder

KINDS = ('checkin', 'checkout')
THROTTLE_SECONDS = 0.5
LOOP_SECONDS = 60
_HHMM = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')

status = {'running': False, 'last_run': None, 'last_error': None, 'last_report': None}


# ── ตั้งค่า ────────────────────────────────────────────────────
def validate_config(data):
    """→ (clean, errors) ; เวลา HH:MM, เวลาเตือน < เตือนได้ถึง, URL ต้อง https:// (หรือว่าง)"""
    if not isinstance(data, dict):
        return {}, {'_': 'ต้องเป็น object'}
    clean, errors = {'enabled': bool(data.get('enabled'))}, {}
    for key in ('remind_at', 'remind_until'):
        v = str(data.get(key) or '').strip()
        if _HHMM.match(v):
            clean[key] = v
        else:
            errors[key] = 'รูปแบบเวลาต้องเป็น HH:MM (เช่น 09:30)'
    if not errors and clean['remind_at'] >= clean['remind_until']:
        errors['remind_until'] = 'ต้องหลังเวลาเตือน'
    url = str(data.get('button_url') or '').strip()
    if url and not (url.startswith('https://') and len(url) <= 1000 and ' ' not in url):
        errors['button_url'] = 'ต้องเป็นลิงก์ https:// (หรือเว้นว่าง = ไม่มีปุ่ม)'
    clean['button_url'] = url
    label = str(data.get('button_label') or '').strip()
    if len(label) > 20:
        errors['button_label'] = 'ไม่เกิน 20 ตัวอักษร'
    elif url and not label:
        errors['button_label'] = 'ต้องมีข้อความปุ่มเมื่อใส่ลิงก์'
    clean['button_label'] = label
    return clean, errors


def in_window(cfg, now):
    hm = now.strftime('%H:%M')
    return cfg['remind_at'] <= hm < cfg['remind_until']


# ── ตัดสินใจ (ล้วน) ─────────────────────────────────────────────
def decide(kind, person_ids, scans):
    """person_id ที่ต้องเตือน (เรียงตาม person_ids)
    checkin : ยังไม่มี Scan เข้า
    checkout: มี Scan เข้าแล้ว แต่ยังไม่มี Scan ออก (ไม่มีเข้าเลย → ไม่เตือนออก)"""
    has = {}
    for s in scans:
        way = scan_types.direction(s.get('SCAN_TYPE'))
        if way:
            has.setdefault(int(s['PERSON_ID']), set()).add(way)
    if kind == 'checkin':
        return [p for p in person_ids if 'in' not in has.get(p, set())]
    return [p for p in person_ids if 'in' in has.get(p, set()) and 'out' not in has.get(p, set())]


# ── รอบการทำงาน ───────────────────────────────────────────────
def _run_kind(kind, cfg, now, dry_run, ignore_window):
    if not dry_run and not cfg.get('enabled'):
        return {'status': 'disabled'}
    if not ignore_window and not in_window(cfg, now):
        return {'status': 'outside_window'}
    today = now.date()
    if today.weekday() >= 5:
        return {'status': 'weekend'}
    if hosoffice.is_holiday(today):
        return {'status': 'holiday'}

    day = today.isoformat()
    tokens = {t['person_id']: t for t in database.get_all_tokens() if t.get('active') and t.get('remind_enabled')}
    already = set() if dry_run else database.get_reminded_person_ids(day, kind)
    pids = sorted(set(tokens) - already)
    excluded = {'muted': sorted(hosoffice.get_muted_person_ids() & set(pids))}
    pids = [p for p in pids if p not in excluded['muted']]
    excluded['leave'] = sorted(hosoffice.get_persons_on_leave(today, pids))
    pids = [p for p in pids if p not in excluded['leave']]
    targets = decide(kind, pids, hosoffice.get_scans_on(today, pids))
    if dry_run:
        return {'status': 'ok', 'would_remind': targets, 'excluded': excluded,
                'names': {p: tokens[p].get('person_name') for p in targets}}

    report = {'status': 'ok', 'reminded': [], 'cancelled': [], 'errors': [], 'excluded': excluded}
    for i, pid in enumerate(targets):
        if not database.claim_reminder(pid, day, kind):
            continue
        try:
            # ตรวจสดซ้ำหลัง claim: คนอาจเพิ่งสแกนระหว่างรอบ
            if not decide(kind, [pid], hosoffice.get_scans_on(today, [pid])):
                database.finish_reminder(pid, day, kind, 'cancelled')
                report['cancelled'].append(pid)
                continue
            ok, code, resp = deliver_reminder(kind, tokens[pid], cfg, now=now)
        except Exception as e:   # noqa: BLE001 — ปิดสถานะการจองเสมอ ไม่ค้าง 'claimed'
            database.finish_reminder(pid, day, kind, 'error', 0, str(e))
            report['errors'].append(pid)
            raise
        database.finish_reminder(pid, day, kind, 'success' if ok else 'error', code, resp)
        (report['reminded'] if ok else report['errors']).append(pid)
        if THROTTLE_SECONDS and i < len(targets) - 1:
            time.sleep(THROTTLE_SECONDS)
    return report


def run_cycle(now=None, dry_run=False, ignore_window=False):
    """หนึ่งรอบของทุก kind — คืน report ต่อ kind ; ข้อผิดพลาดของ kind หนึ่งไม่กระทบอีก kind"""
    now = now or datetime.now()
    cfgs = database.get_reminder_config()
    report = {}
    for kind in KINDS:
        try:
            report[kind] = _run_kind(kind, cfgs[kind], now, dry_run, ignore_window)
        except Exception as e:   # noqa: BLE001 — fail closed
            report[kind] = {'status': 'error', 'error': str(e)[:300]}
    return report


def loop():
    while status['running']:
        report = run_cycle()
        errors = [r.get('error') for r in report.values() if r.get('status') == 'error']
        status.update(last_run=datetime.now().strftime('%H:%M:%S'), last_report=report,
                      last_error=errors[0] if errors else None)
        for _ in range(LOOP_SECONDS * 2):
            if not status['running']:
                break
            time.sleep(0.5)


def start():
    if status['running']:
        return
    status['running'] = True
    threading.Thread(target=loop, daemon=True).start()
