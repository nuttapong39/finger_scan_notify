"""Flex card ทุกแบบ (ฟังก์ชันล้วน — ไม่แตะ DB/เครือข่าย)

- build_flex_message   : การ์ด Scan ปกติ (แจ้งเตือนสด / ทดสอบ)
- build_backfill_card  : การ์ด Scan ที่ส่งย้อนหลัง (header เทา + "📨 ส่งย้อนหลัง · ส่งเมื่อ HH:MM" สีแดง)
- build_reminder_card  : การ์ดเตือนยังไม่สแกน (เข้า = ส้ม, ออก = amber)
- build_line_message   : ข้อความ text สำหรับ LINE Notify (ผู้ให้บริการเดิม)
"""
from datetime import datetime

import scan_types
from flex_theme import merge_theme

DEFAULT_LOGO_URL = 'https://www.ckhospital.net/home/PDF/Logo_ck.png'
DEFAULT_HOSPITAL = 'โรงพยาบาลเชียงกลาง'

# สีส่วนที่เป็นสีขาว (คงที่ — ที่ปรับได้คือ Theme)
_INK   = '#0F172A'
_MUTED = '#64748B'
_FAINT = '#94A3B8'
_RULE  = '#E2E8F0'
_PILL  = '#F1F5F9'

THAI_DAYS = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']

EMPLOYEE_TYPE_COLOR = {
    'ข้าราชการ':                '#1A56E8',
    'พนักงานราชการ':             '#7C3AED',
    'พนักงานกระทรวงสาธารณสุข':  '#0891B2',
    'ลูกจ้างรายวัน':             '#D97706',
    'ลูกจ้างประจำ':              '#059669',
}


def _emp_color(emp_type):
    for k, v in EMPLOYEE_TYPE_COLOR.items():
        if k in (emp_type or ''):
            return v
    return '#475569'


def _dt(scan_row):
    dt = scan_row.get('SCAN_DATE_TIME')
    if dt and hasattr(dt, 'strftime'):
        return dt.strftime('%d/%m/%Y'), dt.strftime('%H:%M:%S')
    s = str(dt) if dt else ''
    parts = s.split(' ')
    return (parts[0] or '-') if parts else '-', (parts[1][:8] if len(parts) > 1 else '-')


def thai_date(d):
    """'ศุกร์ 6/11/2569'"""
    return f"{THAI_DAYS[d.weekday()]} {d.day}/{d.month}/{d.year + 543}"


def _hospital(name, logo):
    hosp = (name or '').strip()
    if not hosp or '�' in hosp:
        hosp = DEFAULT_HOSPITAL
    return hosp, ((logo or '').strip() or DEFAULT_LOGO_URL)


def card_label(scan_code, theme):
    """ป้ายหัวการ์ด: เข้า/ออกธรรมดาใช้คำจาก Theme, แบบพิเศษคงชื่อเดิม"""
    lbl = scan_types.label(scan_code)
    if lbl == 'เข้างาน':
        return theme['checkin_label']
    if lbl == 'ออกงาน':
        return theme['checkout_label']
    return lbl


# ── ชิ้นส่วนร่วม ──────────────────────────────────────────────
def _text(t, size='xs', color=_INK, **kw):
    return {"type": "text", "text": (t or '').strip() or '-', "size": size, "color": color, **kw}


def _section(heading, *children):
    return {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "xs",
            "contents": [_text(heading, 'xxs', _FAINT, weight="bold"), *children]}


def _row(lbl, value):
    return {"type": "box", "layout": "horizontal", "margin": "sm",
            "contents": [_text(lbl, 'xs', _MUTED, flex=3),
                         _text(value, 'xs', _INK, flex=7, align="end", weight="bold", wrap=True)]}


def _pill(t, fg, bg):
    return {"type": "box", "layout": "vertical", "flex": 0, "cornerRadius": "12px",
            "backgroundColor": bg, "justifyContent": "center",
            "paddingTop": "3px", "paddingBottom": "3px", "paddingStart": "8px", "paddingEnd": "8px",
            "contents": [_text(t, 'xxs', fg, weight="bold")]}


def _header(title, subtitle, logo_url, start, end, angle, pill=None):
    contents = [
        {"type": "box", "layout": "vertical", "flex": 0,
         "width": "40px", "height": "40px", "cornerRadius": "20px", "backgroundColor": "#FFFFFF",
         "contents": [{"type": "image", "url": logo_url, "size": "full",
                       "aspectMode": "cover", "aspectRatio": "1:1"}]},
        {"type": "box", "layout": "vertical", "flex": 1, "spacing": "xs",
         "contents": [_text(title, 'lg', '#FFFFFF', weight="bold", wrap=True),
                      _text(subtitle, 'xxs', '#F1F5F9', wrap=True)]},
    ]
    if pill:
        contents.append(pill)
    return {
        "type": "box", "layout": "horizontal", "alignItems": "center", "spacing": "md",
        "paddingAll": "14px", "cornerRadius": "16px",
        "backgroundColor": start,     # fallback สำหรับ LINE รุ่นเก่าที่ไม่รู้จัก gradient
        "background": {"type": "linearGradient", "angle": f"{angle}deg",
                       "startColor": start, "endColor": end},
        "contents": contents,
    }


def _footer(footer_text, right_text):
    return [
        {"type": "separator", "margin": "lg", "color": _RULE},
        {"type": "box", "layout": "horizontal", "margin": "md",
         "contents": [_text(footer_text, 'xxs', _FAINT, flex=1, wrap=True),
                      _text(right_text, 'xxs', _FAINT, flex=0, align="end")]},
    ]


def _bubble(alt_text, body_contents):
    body = {"type": "box", "layout": "vertical", "paddingAll": "12px",
            "backgroundColor": "#FFFFFF", "contents": body_contents}
    return [{"type": "flex", "altText": alt_text[:400],
             "contents": {"type": "bubble", "size": "mega", "body": body}}]


# ── การ์ด Scan (ปกติ / ส่งย้อนหลัง) ─────────────────────────────
def _scan_card(scan_row, hospital_name, hospital_logo, theme, sent_at=None):
    theme = merge_theme(theme)
    code = scan_row.get('SCAN_TYPE')
    way = scan_types.direction(code)
    if way is None:
        raise ValueError(f'ไม่ทราบทิศทางของ SCAN_TYPE={code!r}')
    is_checkin = way == 'in'
    backfill = sent_at is not None
    pre = 'checkin' if is_checkin else 'checkout'
    accent = theme[pre + '_accent']
    if backfill:
        start, end = theme['backfill_start'], theme['backfill_end']
    else:
        start, end = theme[pre + '_start'], theme[pre + '_end']
    title = card_label(code, theme)
    icon = '🟢' if is_checkin else '🔴'

    def val(key):
        return (scan_row.get(key) or '').strip()

    name       = val('HR_FULLNAME') or 'ไม่ระบุ'
    position   = val('HR_POSITION_NAME')
    emp_type   = val('HR_PERSON_TYPE_NAME')
    input_type = val('ISINPUT') or 'FACESCAN'
    day_name   = val('DAY_TYPE_NAME')
    date_str, time_str = _dt(scan_row)
    hosp, logo_url = _hospital(hospital_name, hospital_logo)

    person = [_text(name, 'md', _INK, weight="bold", wrap=True)]
    if position:
        person.append(_text(position, 'xs', _MUTED, wrap=True))
    if emp_type:
        person.append({"type": "box", "layout": "horizontal", "margin": "sm",
                       "contents": [_pill(emp_type, _emp_color(emp_type), _PILL), {"type": "filler"}]})

    when = {"type": "box", "layout": "horizontal", "margin": "xs", "alignItems": "center",
            "contents": [
                {"type": "box", "layout": "vertical", "flex": 1,
                 "contents": [_text(date_str, 'sm', _INK, weight="bold")] +
                             ([_text(day_name, 'xs', _MUTED)] if day_name else [])},
                _text(time_str, 'xl', accent, flex=1, align="end", weight="bold"),
            ]}

    contents = [_header(title, hosp, logo_url, start, end, theme['angle'],
                        pill=_pill(input_type, accent, '#FFFFFF'))]
    if backfill:
        contents.append(_text(f"📨 ส่งย้อนหลัง · ส่งเมื่อ {sent_at.strftime('%H:%M')}",
                              'sm', theme['backfill_text'], weight="bold", margin="md", align="center"))
    contents += [
        _section('บุคลากร', *person),
        _section('สังกัด', _row('กลุ่มงาน', val('HR_DEP_NAME') or '-'),
                 _row('งาน', val('HR_DEP_SUB_NAME') or '-')),
        _section('เวลาสแกน', when),
        *_footer(theme['footer_text'], date_str),
    ]
    prefix = '📨 ส่งย้อนหลัง · ' if backfill else ''
    return _bubble(f"{prefix}{icon} {title} - {name} {time_str}", contents)


def build_flex_message(scan_row, hospital_name, hospital_logo, theme=None):
    """การ์ด Scan ปกติ — theme=None ใช้ค่า default ; raise ValueError ถ้า SCAN_TYPE ไม่ทราบทิศทาง"""
    return _scan_card(scan_row, hospital_name, hospital_logo, theme)


def build_backfill_card(scan_row, hospital_name, hospital_logo, theme=None, sent_at=None):
    """การ์ด Scan ที่ส่งย้อนหลัง"""
    return _scan_card(scan_row, hospital_name, hospital_logo, theme, sent_at or datetime.now())


# ── การ์ดเตือนยังไม่สแกน ──────────────────────────────────────
_REMINDER_TEXT = {
    'checkin': {'title': 'ยังไม่พบการสแกนเข้างาน', 'main': 'กรุณาสแกนเข้างานทันที',
                'soft': 'หากวันนี้เป็นวันหยุดหรือวันลาของคุณ ไม่ต้องดำเนินการ', 'pre': 'reminder_in'},
    'checkout': {'title': 'ยังไม่พบการสแกนออกงาน', 'main': 'อย่าลืมสแกนออกก่อนกลับ',
                 'soft': 'หากยังปฏิบัติงานอยู่ หรือวันนี้เป็นวันหยุดของคุณ ไม่ต้องดำเนินการ', 'pre': 'reminder_out'},
}


def build_reminder_card(kind, person, hospital_name, hospital_logo, theme=None,
                        checked_at=None, button_url='', button_label=''):
    """kind: 'checkin' | 'checkout' ; person: {'name', 'department'}"""
    if kind not in _REMINDER_TEXT:
        raise ValueError(f'kind ไม่ถูกต้อง: {kind!r}')
    theme = merge_theme(theme)
    txt = _REMINDER_TEXT[kind]
    start, end, accent = (theme[txt['pre'] + s] for s in ('_start', '_end', '_accent'))
    checked_at = checked_at or datetime.now()
    hosp, logo_url = _hospital(hospital_name, hospital_logo)
    name = (person.get('name') or '').strip() or 'ไม่ระบุ'
    dept = (person.get('department') or '').strip()

    who = [_text(name, 'md', _INK, weight="bold", wrap=True)]
    if dept:
        who.append(_text(dept, 'xs', _MUTED, wrap=True))

    contents = [
        _header('⚠️ ' + txt['title'], hosp, logo_url, start, end, theme['angle']),
        {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "xs", "contents": who},
        _text(f"ตรวจเมื่อ {checked_at.strftime('%H:%M')} · {thai_date(checked_at)}", 'xs', _MUTED,
              margin="sm", wrap=True),
        {"type": "box", "layout": "vertical", "margin": "lg", "paddingAll": "12px",
         "cornerRadius": "12px", "backgroundColor": "#FFF7ED",
         "contents": [_text(txt['main'], 'md', accent, weight="bold", align="center", wrap=True)]},
        _text(txt['soft'], 'xxs', _FAINT, margin="md", align="center", wrap=True),
    ]
    url = (button_url or '').strip()
    if url:
        contents.append({"type": "button", "style": "primary", "height": "sm", "margin": "md",
                         "color": accent,
                         "action": {"type": "uri", "label": (button_label or '').strip()[:20] or 'เปิด',
                                    "uri": url}})
    contents += _footer(theme['footer_text'], checked_at.strftime('%d/%m/%Y'))
    return _bubble(f"⚠️ {txt['title']} - {name}", contents)


# ── การ์ดแจ้งเตือนระบบ (ถึง IT alert recipient) ───────────────
_SYSTEM_ALERT = {
    'problem':   {'pill': 'ปัญหา', 'start': '#B91C1C', 'end': '#7F1D1D', 'pill_bg': '#FEE2E2', 'pill_fg': '#991B1B'},
    'recovered': {'pill': 'กลับมาปกติ', 'start': '#15803D', 'end': '#14532D', 'pill_bg': '#DCFCE7', 'pill_fg': '#166534'},
    'summary':   {'pill': 'สรุป', 'start': '#475569', 'end': '#1E293B', 'pill_bg': '#E2E8F0', 'pill_fg': '#334155'},
}


def build_system_alert_card(kind, title, lines, hospital_name, hospital_logo, now=None):
    """การ์ดปัญหาของระบบ (Lost scan, การนำเข้าหยุด ฯลฯ) — สีคงที่แยกจากการ์ดเข้า-ออกงานและไม่ขึ้นกับ Theme"""
    if kind not in _SYSTEM_ALERT:
        raise ValueError(f'kind ไม่ถูกต้อง: {kind!r}')
    style = _SYSTEM_ALERT[kind]
    now = now or datetime.now()
    hosp, logo_url = _hospital(hospital_name, hospital_logo)
    contents = [
        _header('แจ้งเตือนระบบ', hosp, logo_url, style['start'], style['end'], 135,
                pill=_pill(style['pill'], style['pill_fg'], style['pill_bg'])),
        {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "sm",
         "contents": [_text(title, 'md', _INK, weight="bold", wrap=True),
                      *[_text(ln, 'xs', _MUTED, wrap=True) for ln in (lines or [])]]},
        _text(f"{now.strftime('%H:%M')} · {thai_date(now)}", 'xxs', _FAINT, margin="md"),
        *_footer('ระบบแจ้งเตือนการสแกน · ดูรายละเอียดที่หน้า "Scan ที่อาจตกหล่น"', now.strftime('%d/%m/%Y')),
    ]
    return _bubble(f'แจ้งเตือนระบบ: {title}', contents)


# ── LINE Notify (text) ─────────────────────────────────────────
def build_line_message(scan_row, hospital_name, backfill=False):
    way = scan_types.direction(scan_row.get('SCAN_TYPE'))
    if way is None:
        raise ValueError('ไม่ทราบทิศทางของ Scan')
    icon = '🟢' if way == 'in' else '🔴'
    lbl = scan_types.label(scan_row.get('SCAN_TYPE'))

    def val(key, default=''):
        return (scan_row.get(key) or default).strip()

    date_str, time_str = _dt(scan_row)
    hosp, _ = _hospital(hospital_name, '')
    head = "\n📨 ส่งย้อนหลัง" if backfill else ''
    return (
        f"{head}\n{icon} {lbl}"
        f"\n{'─'*25}"
        f"\n🏥 {hosp}"
        f"\n👤 {val('HR_FULLNAME', 'ไม่ระบุ')}"
        f"\n💼 {val('HR_POSITION_NAME')}"
        f"\n📂 {val('HR_DEP_SUB_NAME')}"
        f"\n🏷  {val('HR_PERSON_TYPE_NAME')}"
        f"\n{'─'*25}"
        f"\n📅 {date_str} ({val('DAY_TYPE_NAME')})"
        f"\n🕐 {time_str}"
        f"\n📱 {val('ISINPUT', 'FACESCAN')}"
    )
