"""จุดเดียวของการส่งข้อความ: เลือกการ์ดตามผู้ให้บริการ → ส่ง → บันทึก log พร้อม Send source

Send source: 'live' (Monitor) | 'backfill' (ส่งย้อนหลัง) | 'test' (ปุ่มทดสอบ) ; การเตือนยังไม่สแกนบันทึกใน reminder_log
"""
from datetime import datetime

import cards
import notifier
import scan_types
from database import add_guard_event, add_notify_log, get_api_config, get_flex_theme

SOURCES = ('live', 'backfill', 'test')


def _provider(token_row):
    return (token_row.get('provider') or 'LINE_NOTIFY').upper()


def _keys(token_row):
    return ((token_row.get('client_key') or '').strip(), (token_row.get('secret_key') or '').strip(),
            (token_row.get('line_token') or token_row.get('token') or '').strip())


def scan_skip_reason(scan, token_row, check_type_flags=True):
    """เหตุผลที่ส่ง Scan นี้ให้ token นี้ไม่ได้ หรือ None ถ้าส่งได้"""
    way = scan_types.direction(scan.get('SCAN_TYPE'))
    if way is None:
        return 'unknown_type'
    if check_type_flags:
        if way == 'in' and not token_row.get('notify_checkin', 1):
            return 'type_off'
        if way == 'out' and not token_row.get('notify_checkout', 1):
            return 'type_off'
    ck, sk, line = _keys(token_row)
    provider = _provider(token_row)
    if provider == 'MOPH_ALERT' and ck and sk:
        return None
    if provider == 'LINE_NOTIFY' and line:
        return None
    return 'no_keys'


def scan_datetime_str(scan):
    dt = scan.get('SCAN_DATE_TIME')
    return dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(dt, 'strftime') else str(dt or '')[:19]


def deliver_scan(scan, token_row, *, source, theme=None, job_id=None, backfill=False,
                 now=None, check_type_flags=True):
    """ส่ง Flex card (หรือ text สำหรับ LINE Notify) ของ Scan หนึ่งรายการ แล้วบันทึก notify_logs
    คืน (success, http_code, response) ; ถ้าส่งไม่ได้ตามกฎ คืน (False, 0, <skip reason>) และไม่บันทึก log"""
    if source not in SOURCES:
        raise ValueError(f'source ไม่ถูกต้อง: {source!r}')
    reason = scan_skip_reason(scan, token_row, check_type_flags)
    if reason:
        return False, 0, reason
    api = get_api_config()
    hosp, logo = api.get('hospital_name', ''), api.get('hospital_logo', '')
    ck, sk, line = _keys(token_row)
    provider = _provider(token_row)
    if provider == 'MOPH_ALERT':
        theme = theme if theme is not None else get_flex_theme()
        msgs = (cards.build_backfill_card(scan, hosp, logo, theme, sent_at=now or datetime.now()) if backfill
                else cards.build_flex_message(scan, hosp, logo, theme))
        success, code, resp = notifier.post_moph(api.get('moph_api_url'), ck, sk, msgs)
    else:
        success, code, resp = notifier.post_line_notify(line, cards.build_line_message(scan, hosp, backfill))
    add_notify_log(scan.get('ID', 0), scan.get('PERSON_ID', 0), (scan.get('HR_FULLNAME') or '').strip(),
                   scan_types.label(scan.get('SCAN_TYPE')), scan_datetime_str(scan), provider,
                   'success' if success else 'error', code, resp, source=source, job_id=job_id)
    return success, code, resp


def deliver_reminder(kind, token_row, cfg, *, theme=None, now=None):
    """ส่งการ์ดเตือนยังไม่สแกนหนึ่งใบ — cfg คือแถว reminder_config ของ kind นี้ ; ไม่บันทึก log (ผู้เรียกบันทึก reminder_log)"""
    api = get_api_config()
    hosp, logo = api.get('hospital_name', ''), api.get('hospital_logo', '')
    person = {'name': token_row.get('person_name') or '', 'department': token_row.get('department') or ''}
    now = now or datetime.now()
    ck, sk, line = _keys(token_row)
    if _provider(token_row) == 'MOPH_ALERT':
        if not (ck and sk):
            return False, 0, 'no_keys'
        msgs = cards.build_reminder_card(kind, person, hosp, logo,
                                         theme if theme is not None else get_flex_theme(), checked_at=now,
                                         button_url=cfg.get('button_url', ''), button_label=cfg.get('button_label', ''))
        return notifier.post_moph(api.get('moph_api_url'), ck, sk, msgs)
    if not line:
        return False, 0, 'no_keys'
    what = 'เข้างาน' if kind == 'checkin' else 'ออกงาน'
    url = (cfg.get('button_url') or '').strip()
    return notifier.post_line_notify(line, f"\n⚠️ ยังไม่พบการสแกน{what}\n👤 {person['name']}\n"
                                           f"ตรวจเมื่อ {now.strftime('%H:%M')}" + (f"\n{url}" if url else ''))


def deliver_system_alert(kind, title, lines, token_row, *, now=None):
    """ส่งการ์ดแจ้งเตือนระบบ (Lost scan / การนำเข้าหยุด) ถึง IT alert recipient
    ไม่บันทึก notify_logs และไม่นับโควตา (ไม่ใช่ Scan ของบุคลากร) — บันทึกผลเป็น guard event 'it_alert'"""
    api = get_api_config()
    hosp, logo = api.get('hospital_name', ''), api.get('hospital_logo', '')
    now = now or datetime.now()
    ck, sk, line = _keys(token_row)
    if _provider(token_row) == 'MOPH_ALERT':
        if not (ck and sk):
            return False, 0, 'no_keys'
        result = notifier.post_moph(api.get('moph_api_url'), ck, sk,
                                    cards.build_system_alert_card(kind, title, lines, hosp, logo, now))
    else:
        if not line:
            return False, 0, 'no_keys'
        result = notifier.post_line_notify(line, "\n[แจ้งเตือนระบบ] " + title + "".join("\n" + ln for ln in lines))
    add_guard_event('it_alert', f'{kind}: {title} -> HTTP {result[1]}')
    return result
