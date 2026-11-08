"""ตรวจ Lost scan และ Import stall (ช่วง A — ดู docs/design/lost-scan-and-importer.md)

ทุกนาทีอ่าน Device sequence (fingle_scan.MACHIN_SCAN_ID) ช่วงล่าสุดจาก hosoffice (อ่านอย่างเดียว):
  - เลขที่ขาดระหว่างเลขที่มี = Lost scan (เครื่องบันทึกแล้วแต่ไม่ถึง hosoffice) — ขาดเกิน 15 นาที → แจ้ง IT ครั้งเดียว
  - ไม่มีเลขใหม่นาน ≥ 20 นาที ในช่วงที่ควรมีคนสแกน (และปกติเวลานี้มีสแกน) = Import stall
    → เตือนยังไม่สแกนงดรอบนั้น (reminders ถาม import_stalled) + แจ้ง IT ครั้งเดียว
  - เหตุการณ์ปิดเมื่อเลขถูกเติม/มีเลขใหม่ → แจ้ง "กลับมาปกติ" ; 07:30 สรุปถ้ายังมีเลขค้าง
Fail closed: ต่อ hosoffice ไม่ได้ = ไม่เปิด/ปิดเหตุการณ์ใด ๆ (ไม่ตีความว่าข้อมูลหาย)
"""
import threading
import time
from datetime import datetime, timedelta

import database
import delivery
import hosoffice
import tigersoft

POLL_SECONDS = 60
WINDOW_SEQ = 2000                  # ย้อนดูกี่เลข (~9 วันทำงาน)
LOST_AFTER_S = 15 * 60             # เลขขาดเกินนี้ถึงแจ้ง (กันกรณีแค่มาช้า)
STALL_AFTER_S = 20 * 60            # ไม่มีเลขใหม่เกินนี้ในช่วงเฝ้า = Import stall
STALL_LEAD_MIN = 30                # ช่วงเฝ้าเริ่ม 30 นาทีก่อน "เวลาเตือน"
MIN_EXPECTED = 3                   # ปกติช่วงเวลาเดียวกันต้องมีสแกนเฉลี่ยอย่างน้อยเท่านี้ ถึงนับว่า "เงียบผิดปกติ"
FRESH_S = 5 * 60                   # ผลตรวจเก่ากว่านี้ = ไม่รู้สถานะ (ไม่ตีความว่าหยุด)
SUMMARY_AT = '07:30'
MAX_GAP = 100                      # ช่องว่างใหญ่กว่านี้ถือว่าเลขกระโดด/เริ่มนับใหม่ ไม่ใช่ scan หาย (กันเหตุการณ์ท่วม)
RETRY_ALERT_S = 180                # แจ้ง IT ไม่สำเร็จ: รออย่างน้อยเท่านี้ก่อนลองใหม่
SUMMARY_UNTIL = '10:30'            # เลยเวลานี้ (เช่น restart ตอนบ่าย) ไม่ส่งสรุปเช้าของวันนั้นแล้ว
MAX_LISTED = 8
OLD_AFTER_H = 24                   # แถวใหม่ที่เวลาสแกนเก่ากว่านี้ = สแกนย้อนหลัง
OLD_MIN_ROWS = 10                  # ในหนึ่งรอบตรวจมีแถวย้อนหลังตั้งแต่เท่านี้ = นำเข้าย้อนหลังเป็นชุด (ไม่ใช่สแกนที่มาช้าสองสามแถว)
OLD_QUIET_S = 10 * 60              # ไม่มีแถวย้อนหลังเพิ่มนานเท่านี้ = ชุดนำเข้าจบ
EXPECT_DAYS = 5

status = {'running': False, 'last_run': None, 'last_error': None}


# ── ฟังก์ชันล้วน ──────────────────────────────────────────────
def find_gaps(rows, max_gap=MAX_GAP):
    """rows: [{'seq','time',...}] → เลขที่ขาดระหว่างเลขต่ำสุดกับสูงสุดที่มี พร้อมเวลาของแถวก่อน/หลัง
    ช่องว่างที่กว้างเกิน max_gap เลขข้ามไป (เลขกระโดด ไม่ใช่การสแกนที่หาย)"""
    first_time = {}
    for r in sorted(rows, key=lambda r: (r['seq'], r['time'])):
        first_time.setdefault(r['seq'], r['time'])
    seqs = sorted(first_time)
    gaps = []
    for prev, nxt in zip(seqs, seqs[1:]):
        if nxt - prev - 1 > max_gap:
            continue
        for missing in range(prev + 1, nxt):
            gaps.append({'seq': missing, 'prev_time': first_time[prev], 'next_time': first_time[nxt]})
    return gaps


def expected_scans(rows, since, now, days=EXPECT_DAYS):
    """สแกนเฉลี่ยที่ "ปกติควรมา" ในช่วง since→now: นับจากวันทำงานก่อนหน้าที่มีข้อมูล ในช่วงนาฬิกาเดียวกัน
    (ข้ามเที่ยงคืนได้) — เช้า 09:30 ที่ไม่มีเลขใหม่ตั้งแต่ 08:05 จะได้ค่าสูง (ช่วงนั้นปกติคนสแกนเยอะ)
    แต่ช่วงบ่ายที่เงียบอยู่แล้วได้ค่าต่ำ จึงไม่ตีความความเงียบปกติว่าการนำเข้าหยุด"""
    if since.date() != now.date():
        # เงียบข้ามวัน: ดูแค่ช่วง 20 นาทีล่าสุดของวันนี้ (ไม่รวมหางของเมื่อวาน) — "ปกติเวลานี้ต้องมีคนสแกน แต่ไม่มีมาตั้งแต่เมื่อวาน"
        since = now - timedelta(minutes=STALL_AFTER_S // 60)
    start, end = since.time(), now.time()
    if start > end:
        return 0.0
    workdays = sorted({r['time'].date() for r in rows
                       if r['time'].date() < now.date() and r['time'].weekday() < 5}, reverse=True)[:days]
    if not workdays:
        return 0.0
    chosen = set(workdays)
    n = sum(1 for r in rows if r['time'].date() in chosen and start <= r['time'].time() <= end)
    return n / len(workdays)


def _minutes(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def in_watch_window(cfgs, now):
    """ช่วงที่ควรมีคนสแกน: 30 นาทีก่อน "เวลาเตือน" ถึง "เตือนได้ถึง" ของเข้า/ออกงาน ในวันทำงาน"""
    if now.weekday() >= 5:
        return False
    try:
        if hosoffice.is_holiday(now.date()):
            return False
    except hosoffice.HosofficeUnavailable:
        pass                                  # ไม่รู้ว่าวันหยุดไหม → ถือว่าเป็นวันทำงาน (การเตือนจริงยัง fail closed เอง)
    cur = now.hour * 60 + now.minute
    return any(_minutes(c['remind_at']) - STALL_LEAD_MIN <= cur < _minutes(c['remind_until']) for c in cfgs.values())


def import_stalled(now):
    """เตือนยังไม่สแกนเรียกก่อนส่ง: True = การนำเข้าหยุดอยู่ (ผลตรวจต้องสดภายใน 5 นาที)"""
    st = database.get_watch_state()
    return bool(st['stalled'] and st['last_poll_at'] and 0 <= now.timestamp() - st['last_poll_at'] <= FRESH_S)


# ── การแจ้ง IT ───────────────────────────────────────────────
def _notify(kind, title, lines, now, ts):
    st = database.get_watch_state()
    if st['alert_fail_at'] and 0 <= ts - st['alert_fail_at'] < RETRY_ALERT_S:
        return False
    token = database.get_token_by_person_id(st['it_person_id'])
    if token:
        ok, _, why = delivery.deliver_system_alert(kind, title, lines, token, now=now)
        if ok:
            return True
    else:
        why = 'no_token'
    if not st['alert_log_at'] or ts - st['alert_log_at'] > 3600:       # ส่งไม่ได้: พัก 3 นาทีแล้วลองใหม่ แต่บันทึกชั่วโมงละครั้ง
        database.add_guard_event('it_alert_undeliverable', f'ส่งแจ้ง IT (person {st["it_person_id"]}) ไม่ได้: {why}')
        database.save_watch_state(alert_log_at=ts)
    database.save_watch_state(alert_fail_at=ts)
    return False


def _fmt(d):
    return d.strftime('%d/%m %H:%M:%S') if isinstance(d, datetime) else str(d or '-')


def _gap_lines(events):
    lines = []
    for e in events[:MAX_LISTED]:
        d = e['detail']
        if d.get('scan_time'):          # ระบุตัวตนจากเครื่องสแกนได้ (ช่วง C)
            who = ' · '.join(str(x) for x in (d.get('name'), f"สแกน {d['scan_time'][-8:]}",
                                              f"เครื่อง {d['machine']}" if d.get('machine') is not None else None) if x)
            lines.append(f"เลข {e['seq']} · {who}")
        else:
            lines.append(f"เลข {e['seq']} · ระหว่างสแกน {d.get('prev_time', '-')[-8:]}–{d.get('next_time', '-')[-8:]}")
    if len(events) > MAX_LISTED:
        lines.append(f'…และอีก {len(events) - MAX_LISTED} รายการ (ดูหน้า Scan ที่อาจตกหล่น)')
    return lines


def _send_alerts(now, ts):
    lost = database.get_watch_events('lost_scan')
    stall = database.get_watch_events('stall')

    due = [e for e in lost if e['open'] and not e['alerted_at'] and ts - e['first_seen'] >= LOST_AFTER_S]
    if due and _notify('problem', f'Lost scan {len(due)} รายการ',
                       ['สแกนที่เครื่องบันทึกแล้วแต่ไม่มาถึง hosoffice (เลข Device sequence ขาด)', *_gap_lines(due)], now, ts):
        database.mark_watch_alerted([e['id'] for e in due], ts)

    for e in (x for x in stall if x['open'] and not x['alerted_at']):
        if _notify('problem', 'การนำเข้าสแกนหยุด',
                   [f"ไม่มีสแกนใหม่ถึง hosoffice ตั้งแต่ {e['detail'].get('last_new', '-')}"
                    + (f" (เครื่องสแกนมีเลข {e['detail']['src_max']} แต่ hosoffice ล่าสุด {e['detail']['hos_max']})"
                       if e['detail'].get('src_max') is not None else ' ทั้งที่ปกติช่วงนี้มีคนสแกน'),
                    'การเตือนยังไม่สแกนถูกงดไว้ชั่วคราวจนกว่าข้อมูลจะกลับมา'], now, ts):
            database.mark_watch_alerted([e['id']], ts)

    for e in (x for x in database.get_watch_events('old_rows') if x['open'] and not x['alerted_at']):
        d = e['detail']
        if _notify('problem', 'พบสแกนย้อนหลังเข้า hosoffice เป็นชุด',
                   [f"แถวใหม่ {d['count']} แถว (ID {d['first_id']}–{d['last_id']}) เป็นสแกนเก่าตั้งแต่ {d['oldest']} ถึง {d['newest']}",
                    'ระบบอื่นที่แจ้งเตือนตามแถวใหม่โดยไม่ดูอายุสแกน (เช่นตัวเฝ้าของ HOSOffice ERP) อาจส่งการ์ดของเก่าถึงบุคลากร — ตรวจและปิดตัวนั้นก่อน',
                    'แอปนี้ข้ามสแกนเก่ากว่า 180 นาทีเอง จึงไม่ส่งการ์ดย้อนหลัง'], now, ts):
            database.mark_watch_alerted([e['id']], ts)
    for e in (x for x in database.get_watch_events('old_rows') if not x['open'] and x['alerted_at'] and not x['recovery_notified']):
        d = e['detail']
        if _notify('recovered', 'สแกนย้อนหลังหยุดเข้าแล้ว',
                   [f"รวม {d['count']} แถว (ID {d['first_id']}–{d['last_id']}) ให้ HR ตัดสินว่าจะเก็บไว้หรือไม่"], now, ts):
            database.mark_watch_recovery_notified([e['id']])

    pending_open = [e for e in lost if e['open'] and e['alerted_at']]
    recovered = [e for e in lost if not e['open'] and e['alerted_at'] and not e['recovery_notified']]
    if recovered and not pending_open and _notify(
            'recovered', f'กลับมาปกติ: เติม Lost scan ครบ {len(recovered)} รายการ',
            ['เลขที่เคยขาดมีใน hosoffice แล้ว: ' + ', '.join(str(e['seq']) for e in recovered[:MAX_LISTED * 2])], now, ts):
        database.mark_watch_recovery_notified([e['id'] for e in recovered])
    for e in (x for x in stall if not x['open'] and x['alerted_at'] and not x['recovery_notified']):
        if _notify('recovered', 'กลับมาปกติ: การนำเข้าสแกนทำงานแล้ว',
                   ['มีสแกนใหม่ถึง hosoffice แล้ว การเตือนยังไม่สแกนกลับมาทำงานตามปกติ'], now, ts):
            database.mark_watch_recovery_notified([e['id']])

    _send_summary(now, ts, pending_open, [x for x in stall if x['open'] and x['alerted_at']])


def _send_summary(now, ts, open_lost, open_stall):
    today = now.date().isoformat()
    hm = now.strftime('%H:%M')
    if hm < SUMMARY_AT or database.get_watch_state()['summary_date'] == today:
        return
    if hm > SUMMARY_UNTIL:
        database.save_watch_state(summary_date=today)
        return
    if not open_lost and not open_stall:
        database.save_watch_state(summary_date=today)
        return
    lines = _gap_lines(open_lost) if open_lost else []
    if open_stall:
        lines.append('การนำเข้าสแกนยังหยุดอยู่')
    if _notify('summary', f'สรุปเช้า: ยังมีเรื่องค้าง {len(open_lost) + len(open_stall)} รายการ', lines, now, ts):
        database.save_watch_state(summary_date=today)


# ── แถวย้อนหลังเข้ามาเป็นชุด (เหตุการณ์ 8 พ.ย. 2569) ─────────────────
def _fmt_day(d):
    return d.strftime('%d/%m/%Y') if hasattr(d, 'strftime') else str(d or '-')


def check_old_rows(ts):
    """แถวใหม่ (ID เกินจุดที่ตรวจแล้ว) จากเครื่องสแกนที่เวลาสแกนเก่ากว่า 24 ชม. เป็นชุดใหญ่ = มีการนำเข้าย้อนหลัง
    ระบบอื่นที่แจ้งเตือนตาม "แถวใหม่" โดยไม่ดูอายุสแกน (เช่นตัวเฝ้าของ HOSOffice ERP) จะส่งการ์ดของเก่าตามเป็นร้อยใบ → แจ้ง IT ทันที
    อ่านอย่างเดียว ไม่ตัดสินว่าแถวถูกหรือผิด รอบแรก = ตั้งจุดเริ่มที่ ID ล่าสุด (ไม่ตีความประวัติเดิมว่าเป็นเหตุการณ์)"""
    max_id = hosoffice.get_max_scan_id()
    if max_id is None:
        return
    last = database.get_watch_state()['old_check_id']
    if last is None or last > max_id:                  # รอบแรก / ตารางถูกกู้คืนจนเลขลดลง → เริ่มนับใหม่ที่ปัจจุบัน
        database.save_watch_state(old_check_id=max_id)
        return
    r = hosoffice.get_old_device_rows_after(last, OLD_AFTER_H)
    database.save_watch_state(old_check_id=max_id)
    open_ev = database.get_watch_events('old_rows', open_only=True)
    if open_ev and r['count']:                         # ชุดเดิมยังไหลอยู่: รวมเข้าเหตุการณ์เดิม (แม้รอบนี้น้อยกว่าเกณฑ์)
        d = dict(open_ev[0]['detail'], last_id=r['last_id'], last_at=ts)
        d['count'] = d.get('count', 0) + r['count']
        database.update_watch_event_detail(open_ev[0]['id'], d)
    elif not open_ev and r['count'] >= OLD_MIN_ROWS:
        database.open_watch_event('old_rows', r['first_id'],
                                  {'count': r['count'], 'first_id': r['first_id'], 'last_id': r['last_id'],
                                   'oldest': _fmt_day(r['oldest']), 'newest': _fmt_day(r['newest']), 'last_at': ts}, ts)
    elif open_ev and ts - open_ev[0]['detail'].get('last_at', ts) >= OLD_QUIET_S:
        database.close_watch_events([open_ev[0]['id']], ts)


# ── ฐานข้อมูลเครื่องสแกน (ช่วง C) ──────────────────────────────
def _identify(ts, present_seqs):
    """เติมชื่อ/เวลา/เครื่องให้ Lost scan ที่เปิดอยู่จากเครื่องสแกน — เลขที่เครื่องไม่เคยออกให้ = รูในการนับ ไม่ใช่สแกนที่หาย → ปิดเงียบ ๆ
    ก่อนเชื่อว่า "ไม่มีที่เครื่อง" ต้องพิสูจน์ก่อนว่าเลขสองฝั่งเป็นเลขชุดเดียวกัน: เลขล่าสุดของ hosoffice ต้องไปเจอที่เครื่องอย่างน้อยหนึ่งแถว
    (กันฐานข้อมูล/ตารางผิดที่ตอบว่างโดยไม่ error แล้วล้างเหตุการณ์ทิ้งทั้งหมด)"""
    events = [e for e in database.get_watch_events('lost_scan', open_only=True) if not e['detail'].get('looked_up')][:100]
    if not events:
        return
    sample = sorted(present_seqs)[-20:]
    found = tigersoft.get_rows([e['seq'] for e in events] + sample)
    if sample and not any(s in found for s in sample):
        raise tigersoft.TigerUnavailable('เลขล่าสุดของ hosoffice ไม่พบที่เครื่องสแกนเลย — เลข MACHIN_SCAN_ID กับ ID_TimeInout อาจไม่ใช่ชุดเดียวกัน '
                                         '(ตรวจด้วย manage.py tiger-check) จึงยังไม่ระบุตัวตน/ไม่ปิดเหตุการณ์')
    people = {r['person'] for s, r in found.items() if r['person'] is not None and s not in sample}
    names, lookup_ok = {}, True
    if people:
        try:
            names = hosoffice.get_names_by_fingle_ids(people)
        except hosoffice.HosofficeUnavailable:
            lookup_ok = False                 # ลองหาชื่อใหม่รอบหน้า (เก็บเวลา/เครื่องไว้ก่อน)
    for e in events:
        d = dict(e['detail'], looked_up=lookup_ok)
        src = found.get(e['seq'])
        if src is None:
            if ts - e['first_seen'] < LOST_AFTER_S:
                continue          # แถวของเครื่องอาจ commit ช้ากว่าแถวถัดไป — ยังไม่สรุปว่า "ไม่มีที่เครื่อง" จนกว่าจะเลยเวลาแจ้ง
            d['not_in_source'] = True
            database.update_watch_event_detail(e['id'], d)
            database.close_watch_events([e['id']], ts)
            database.mark_watch_recovery_notified([e['id']])
            continue
        if src['time'] is not None:
            d['scan_time'] = src['time'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(src['time'], 'strftime') else str(src['time'])
        if src['machine'] is not None:
            d['machine'] = src['machine']
        name = names.get(src['person'], names.get(str(src['person']))) if src['person'] is not None else None
        if name:
            d['name'] = name
        database.update_watch_event_detail(e['id'], d)


# ── หนึ่งรอบ ──────────────────────────────────────────────────
def run_cycle(now=None):
    now = now or datetime.now()
    ts = now.timestamp()
    try:
        rows = hosoffice.get_device_window(WINDOW_SEQ)
    except hosoffice.HosofficeUnavailable as e:
        database.save_watch_state(last_error=str(e)[:300])
        return {'status': 'error', 'error': str(e)}

    state = database.get_watch_state()
    max_seq = max((r['seq'] for r in rows), default=state['max_seq'])
    new_data = max_seq is not None and (state['max_seq'] is None or max_seq > state['max_seq'])
    first_poll = state['max_seq'] is None
    if new_data and state['max_seq'] is not None:
        last_new_at = ts
    elif state['last_new_at']:
        last_new_at = state['last_new_at']
    else:       # เริ่มตรวจครั้งแรก: ใช้เวลาของสแกนล่าสุดจริง (ไม่ใช่เวลาที่เปิดแอป) — เปิดแอปหลังข้อมูลหยุดไปแล้วก็จับได้ทันที
        latest = max((r['time'] for r in rows if r['seq'] == max_seq), default=now)
        last_new_at = min(ts, latest.timestamp())

    present = {r['seq'] for r in rows}
    floor = min(present) if present else None
    for g in find_gaps(rows):
        database.open_watch_event('lost_scan', g['seq'],
                                  {'prev_time': _fmt(g['prev_time']), 'next_time': _fmt(g['next_time'])}, ts)
    open_lost = database.get_watch_events('lost_scan', open_only=True)
    database.close_watch_events([e['id'] for e in open_lost if e['seq'] in present], ts)
    expired = [e['id'] for e in open_lost if floor is not None and e['seq'] < floor]       # เลื่อนพ้นหน้าต่างแล้ว ตรวจไม่ได้อีก
    database.close_watch_events(expired, ts)
    database.mark_watch_recovery_notified(expired)

    src_max, tiger_error = None, None
    if tigersoft.configured():
        try:
            src_max = tigersoft.get_latest_seq()
            _identify(ts, present)
        except Exception as e:   # noqa: BLE001 — ส่วนเสริม: ไม่ว่าพังแบบไหนห้ามทำให้รอบตรวจหลักล้ม
            tiger_error = str(e)[:300]

    try:
        check_old_rows(ts)
    except hosoffice.HosofficeUnavailable:
        pass                                  # ตรวจรอบหน้า (จุดที่ตรวจแล้วไม่ขยับ)

    cfgs = database.get_reminder_config()
    if src_max is not None:
        # เทียบเลขล่าสุดสองฝั่ง: เครื่องมีเลขใหม่กว่าที่ hosoffice และ hosoffice ไม่ขยับ — นับ 20 นาทีจากครั้งแรกที่เห็น "ตามหลัง"
        # (ไม่นับจากเวลาสแกนล่าสุด: สแกนแรกหลังช่วงเงียบจะไม่ถูกตีเป็นหยุดทั้งที่ตัวนำเข้ากำลังจะนำเข้า)
        behind = max_seq is not None and src_max > max_seq and not new_data
        behind_since = (state['behind_since'] or ts) if behind else None
        stalled = bool(behind_since and ts - behind_since >= STALL_AFTER_S)
    else:
        behind_since = None
        stalled = ((not new_data or first_poll) and in_watch_window(cfgs, now) and ts - last_new_at >= STALL_AFTER_S
                   and expected_scans(rows, datetime.fromtimestamp(last_new_at), now) >= MIN_EXPECTED)
    open_stall = database.get_watch_events('stall', open_only=True)
    if stalled and not open_stall:
        database.open_watch_event('stall', None, {'last_new': datetime.fromtimestamp(last_new_at).strftime('%H:%M'),
                                                  'src_max': src_max, 'hos_max': max_seq}, ts)
    if new_data and open_stall:
        database.close_watch_events([e['id'] for e in open_stall], ts)

    database.save_watch_state(max_seq=max_seq, last_new_at=last_new_at, last_poll_at=ts, last_error=None,
                              stalled=int(stalled), tiger_error=tiger_error, tiger_max_seq=src_max,
                              behind_since=behind_since)
    _send_alerts(now, ts)
    return {'status': 'ok', 'stalled': stalled, 'gaps': len(database.get_watch_events('lost_scan', open_only=True))}


def loop():
    while status['running']:
        try:
            report = run_cycle()
            status.update(last_run=datetime.now().strftime('%H:%M:%S'),
                          last_error=report.get('error'))
        except Exception as e:   # noqa: BLE001 — ตัวเฝ้าต้องไม่ตาย
            status.update(last_error=str(e)[:300])
        for _ in range(POLL_SECONDS * 2):
            if not status['running']:
                break
            time.sleep(0.5)


def start():
    if status['running']:
        return
    status['running'] = True
    threading.Thread(target=loop, daemon=True).start()
