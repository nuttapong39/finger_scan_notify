"""รอบทำงานของ Scan importer ตัวใหม่ (off | shadow | write)
อ่านเครื่องสแกน → plan_batch → เก็บสถานะ/แถวที่กักลง SQLite ของแอปนี้ให้หน้า Watch แสดง แล้วขยับจุดล่าสุด
shadow = ไม่เขียน hosoffice เลย ; write = เขียนแถวที่ "จะเขียน" ในหนึ่ง transaction แล้วค่อยขยับจุด
ข้อผิดพลาดของฐานข้อมูล = บันทึกแล้วจบรอบ ไม่ขยับจุด (รอบหน้าอ่านช่วงเดิมซ้ำ ; แถวที่เขียนไปแล้วถูกกันซ้ำด้วยกุญแจ)"""
import threading
import time
from datetime import datetime, timedelta

import database
import hosoffice
import scan_types
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


class ManualInputError(ValueError):
    """อินพุตของการนำเข้า/ค้นหาตามเลขไม่ถูกต้อง (ช่วงใหญ่เกิน, สแกนเก่าเกิน, จำนวนวันนอกช่วง) — ข้อความแสดงให้ผู้ใช้ได้"""


def _consts():
    return {'COM_NAME': (settings.current.importer_write or {}).get('com_name')}


def build_rows(src_rows):
    """(แถวที่สร้างได้, [(แถวต้นทาง, เหตุผล)] ที่สร้างไม่ได้) — ต้องหา PERSON_ID และต้นแบบ HR_* ของคนนั้นเจอ ไม่เดา ;
    ฐานข้อมูลล่ม = raise (ผู้เรียกจบรอบโดยไม่ขยับจุด)"""
    pids = hosoffice.get_person_ids([r.get('person') for r in src_rows if r.get('person') is not None])
    pid_of = {r['seq']: pids.get(str(r.get('person')).strip()) for r in src_rows}
    known = sorted({v for v in pid_of.values() if v is not None})
    # HR_* มาจากข้อมูล HR ปัจจุบัน เหมือนตัวเดิมที่อ่าน HR สดทุกครั้ง (ตรง 399/400 ; แถวเดียวที่ต่างคือ HR เพิ่งเปลี่ยน) —
    # คัดลอกจากแถวเก่าก่อนไม่ได้ เพราะหลังตัวเดิมหยุด แถวล่าสุดของมันค้างตายตัว คนที่ HR เปลี่ยนตำแหน่ง/แผนกจะได้ค่าเก่าไปเรื่อย ๆ
    # แถวเก่าของคนเดิมเป็นตัวสำรองเมื่อ HR ไม่ครบหรือบัญชีอ่านตาราง HR ไม่ได้ (get_hr_profiles คืน None)
    profiles = (hosoffice.get_hr_profiles(known) or {}) if known else {}
    templates = hosoffice.get_latest_rows_by_person([p for p in known if p not in profiles])
    built, unbuildable = [], []
    for r in src_rows:
        pid = pid_of[r['seq']]
        row = plan.build_row(r, pid, profiles.get(pid) or templates.get(pid), _consts())
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


# ── นำเข้าตามเลขที่ระบุเอง (อ่านจากเครื่องสแกนโดยตรง ไม่ผ่านจุดล่าสุด) ─────────────────
MANUAL_MAX = 200                         # ช่วงเลขต่อครั้ง
MANUAL_MAX_AGE = timedelta(days=60)      # เก่ากว่านี้ตรวจแถวซ้ำด้วยดัชนีเวลาไม่ได้ผลน่าเชื่อถือ → ไม่ทำ


PREVIEW_ROWS = 50                        # แถวที่แสดงรายละเอียดบนหน้าจอต่อครั้ง


def describe_rows(src_rows, status_of):
    """รายละเอียดต่อแถวให้ผู้ดูแลตรวจก่อนยืนยัน: เลข เครื่อง เวลาถึงวินาที ทิศทาง เจ้าของ (PERSON_ID + ชื่อจาก hr_person) สถานะ
    status_of = {seq: (status, reason)} · ชื่ออ่านจาก hr_person แสดงบนหน้าที่ล็อกอินเท่านั้น ไม่บันทึกลง log/SQLite"""
    rows = sorted(src_rows, key=lambda r: r['seq'])
    shown = rows[:PREVIEW_ROWS]
    cards = [r['person'] for r in shown if r.get('person') is not None]
    pids, names = hosoffice.get_person_ids(cards), hosoffice.get_names_by_fingle_ids(cards)
    out = []
    for r in shown:
        card = None if r.get('person') is None else str(r['person']).strip()
        status, reason = status_of.get(r['seq'], ('unwritable', 'no_time'))
        t = plan.row_time(r)
        item = {'seq': r['seq'], 'machine': r.get('machine'), 'time': t.strftime('%Y-%m-%d %H:%M:%S') if t else None,
                'direction': scan_types.label(r.get('mode')) or 'ไม่ทราบ', 'person_id': pids.get(card), 'name': names.get(card),
                'status': status}
        if reason:
            item['reason'] = reason
        out.append(item)
    return out, len(rows) > PREVIEW_ROWS


def manual_plan(lo=None, hi=None, now=None, seqs=None):
    """แผนนำเข้าจากเครื่องสแกน (อ่านอย่างเดียว) — ระบุเป็นช่วงเลข lo–hi หรือรายการ seqs ; ใช้กับสแกนที่ตกหล่นก่อนจุดเริ่มต้น ข้ามแถวที่ hosoffice มีกุญแจนั้นแล้ว
    ValueError = เลขไม่ถูกต้อง/มากเกิน/เก่าเกิน ; TigerUnavailable / HosofficeUnavailable = ต่อฐานข้อมูลไม่ได้"""
    now = now or datetime.now()
    if seqs is not None:
        wanted = sorted({int(x) for x in seqs})
        if not wanted or len(wanted) > MANUAL_MAX:
            raise ManualInputError(f'ระบุเลขได้ 1–{MANUAL_MAX} เลขต่อครั้ง')
        lo, hi = wanted[0], wanted[-1]
    else:
        if hi < lo or hi - lo + 1 > MANUAL_MAX:
            raise ManualInputError(f'ช่วงเลขต้องไม่เกิน {MANUAL_MAX} เลขต่อครั้ง และ from ต้องไม่มากกว่า to')
        wanted = range(lo, hi + 1)
    rows = list(tigersoft.get_rows(wanted).values())
    times = [t for t in (plan.row_time(r) for r in rows) if t]
    existing = set()
    if times:
        if min(times) < now - MANUAL_MAX_AGE:
            raise ManualInputError(f'มีสแกนเก่ากว่า {MANUAL_MAX_AGE.days} วัน — ตรวจแถวซ้ำแบบอัตโนมัติไม่ได้ ให้ผู้ดูแลตรวจเอง')
        existing = hosoffice.get_existing_keys(min(times) - EXISTING_LOOKBACK)
    return _assess(rows, existing, len(wanted), lo, hi)


def _assess(rows, existing, wanted_n, lo, hi):
    """ประเมินแถวจากเครื่องสแกนว่าเขียนได้/มีแล้ว/เขียนไม่ได้ (อ่านอย่างเดียว) + รายละเอียดต่อแถว — ใช้ร่วมกันระหว่างนำเข้าตามเลขกับค้นหาที่ขาด"""
    p = plan.plan_release(rows, existing, lo, hi)
    built, unbuildable = build_rows(p['insert']) if p['insert'] else ([], [])
    status_of = {r['seq']: ('present', None) for r in p['duplicate']}
    status_of.update({b['MACHIN_SCAN_ID']: ('will_write', None) for b in built})
    status_of.update({r['seq']: ('unwritable', why) for r, why in unbuildable})
    details, truncated = describe_rows(rows, status_of)
    return {'plan': p, 'built': built, 'unbuildable': unbuildable, 'on_scanner': len(rows), 'not_on_scanner': wanted_n - len(rows),
            'rows': details, 'rows_truncated': truncated}


FIND_MAX_DAYS = 7                        # ค้นหาที่ขาดย้อนหลังได้ไม่เกินนี้
FIND_LIMIT = 5000                        # แถวจากเครื่องสแกนต่อครั้ง
RECENT = timedelta(minutes=15)           # สแกนที่ใหม่กว่านี้ยังไม่นับว่าขาด: ตัวนำเข้าเดิม/SCAN-WATCH เขียนช้ากว่าเครื่องสแกนเล็กน้อย


def find_missing(days, now=None):
    """ค้นหาสแกนที่เครื่องสแกนมีแต่ hosoffice ไม่มีกุญแจ (เครื่อง, เลข) ย้อนหลัง days วัน (อ่านอย่างเดียว ใช้ได้ทุกโหมด)
    สแกนใหม่กว่า 15 นาทีถูกกันออก (too_recent) ; คืนรายละเอียดต่อแถว + seqs ที่เขียนได้ (สูงสุด 200 เลข ส่งต่อให้นำเข้าได้)
    ValueError = days นอกช่วง ; TigerUnavailable / HosofficeUnavailable = ต่อฐานข้อมูลไม่ได้"""
    now = now or datetime.now()
    if not 1 <= days <= FIND_MAX_DAYS:
        raise ManualInputError(f'ค้นหาย้อนหลังได้ 1–{FIND_MAX_DAYS} วัน')
    start = now - timedelta(days=days)
    rows = tigersoft.get_rows_between(start, now + timedelta(minutes=1), FIND_LIMIT)
    existing = hosoffice.get_existing_keys(start - EXISTING_LOOKBACK) if rows else set()
    absent = [r for r in rows if plan.row_key(r) not in existing]
    recent = {r['seq'] for r in absent if (plan.row_time(r) or start) > now - RECENT}
    missing = [r for r in absent if r['seq'] not in recent]
    out = {'scanned': len(rows), 'scan_capped': len(rows) >= FIND_LIMIT, 'too_recent': len(recent), 'missing': len(missing),
           'will_write': 0, 'unwritable': 0, 'seqs': [], 'seqs_capped': False, 'rows': [], 'rows_truncated': False}
    if missing:
        a = _assess(missing, set(), len(missing), min(r['seq'] for r in missing), max(r['seq'] for r in missing))
        writable = sorted(b['MACHIN_SCAN_ID'] for b in a['built'])
        out.update(will_write=len(a['built']), unwritable=len(missing) - len(a['built']), seqs=writable[:MANUAL_MAX],
                   seqs_capped=len(writable) > MANUAL_MAX, rows=a['rows'], rows_truncated=a['rows_truncated'])
    return out


def manual_import(lo, hi, expect, who, now=None, seqs=None):
    """เขียนสแกนเลข lo–hi จากเครื่องสแกนลง hosoffice (ป้าย SCAN-IMPORTER-MANUAL) — เฉพาะโหมด write และเมื่อจำนวนตรงกับตัวอย่างที่ผู้ใช้เห็น ;
    คืนจำนวนที่เขียน ; บันทึก audit ว่าใครสั่ง ; แถวที่เคยกักไว้ในช่วงนี้ถูกทำเครื่องหมายว่าจัดการแล้ว"""
    now = now or datetime.now()
    if settings.current.importer_mode != 'write':
        raise ReleaseRefused('นำเข้าได้เฉพาะโหมด write (SCAN_IMPORTER_MODE=write)')
    mp = manual_plan(lo, hi, now, seqs=seqs)
    if len(mp['built']) != int(expect):
        raise ReleaseRefused(f'จำนวนที่จะเขียนเปลี่ยนไปแล้ว (ตัวอย่างเห็น {expect}, ตอนนี้ {len(mp["built"])}) — ดูตัวอย่างใหม่อีกครั้ง')
    n = hosoffice.insert_scan_rows(mp['built'], plan.MANUAL_WRITER)
    p = mp['plan']
    database.mark_importer_released([(r['machine'], r['seq']) for r in p['insert'] + p['duplicate']], now.timestamp())
    database.add_importer_audit(who, 'manual-import', {'from': lo if seqs is None else min(seqs), 'to': hi if seqs is None else max(seqs),
                                                       **({'seqs': sorted(seqs)} if seqs is not None else {}), 'written': n, 'already_present': len(p['duplicate']),
                                                       'unreleasable': len(p['unreleasable']) + len(mp['unbuildable'])}, now.timestamp())
    return n
