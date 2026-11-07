"""Theme ของ Flex card — ค่าหน้าตาที่ปรับได้จากหน้า Dashboard (โมดูลล้วน ไม่แตะ DB)

Theme ชุดเดียวใช้กับบุคลากรทุกคน เก็บเป็น dict แบน (flat) เพื่อให้ฟอร์มและ JSON ตรงกัน
"""
import re

DEFAULT_THEME = {
    'checkin_start':  '#22C55E',
    'checkin_end':    '#15803D',
    'checkin_accent': '#15803D',
    'checkout_start':  '#F97316',
    'checkout_end':    '#DC2626',
    'checkout_accent': '#DC2626',
    # เตือนยังไม่สแกน: เข้า = ส้ม, ออก = amber
    'reminder_in_start':   '#FB923C',
    'reminder_in_end':     '#EA580C',
    'reminder_in_accent':  '#EA580C',
    'reminder_out_start':  '#FBBF24',
    'reminder_out_end':    '#D97706',
    'reminder_out_accent': '#B45309',
    # ส่งย้อนหลัง: header เทา + ข้อความเตือนสีแดง
    'backfill_start': '#9CA3AF',
    'backfill_end':   '#4B5563',
    'backfill_text':  '#DC2626',
    'angle': 135,
    'checkin_label':  'เข้างาน',
    'checkout_label': 'ออกงาน',
    'footer_text': 'ระบบแจ้งเตือนการสแกน · รพ.เชียงกลาง',
}

COLOR_KEYS = ('checkin_start', 'checkin_end', 'checkin_accent',
              'checkout_start', 'checkout_end', 'checkout_accent',
              'reminder_in_start', 'reminder_in_end', 'reminder_in_accent',
              'reminder_out_start', 'reminder_out_end', 'reminder_out_accent',
              'backfill_start', 'backfill_end', 'backfill_text')
# key → (ความยาวต่ำสุด, สูงสุด)
TEXT_LIMITS = {'checkin_label': (1, 20), 'checkout_label': (1, 20), 'footer_text': (1, 60)}

_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')


def _check(key, value):
    """คืน (ค่าที่ล้างแล้ว, ข้อความ error หรือ None) สำหรับ 1 ฟิลด์"""
    if key in COLOR_KEYS:
        if isinstance(value, str) and _HEX.match(value):
            return value.upper(), None
        return None, 'ต้องเป็นสี hex แบบ #RRGGBB'
    if key == 'angle':
        if isinstance(value, bool):
            return None, 'มุมต้องเป็นจำนวนเต็ม 0-360'
        if isinstance(value, int):
            n = value
        elif isinstance(value, str) and re.fullmatch(r'\d{1,3}', value.strip()):
            n = int(value)
        else:
            return None, 'มุมต้องเป็นจำนวนเต็ม 0-360'
        return (n, None) if 0 <= n <= 360 else (None, 'มุมต้องอยู่ระหว่าง 0-360')
    lo, hi = TEXT_LIMITS[key]
    if not isinstance(value, str):
        return None, 'ต้องเป็นข้อความ'
    text = value.strip()
    if not lo <= len(text) <= hi:
        return None, f'ความยาวต้อง {lo}-{hi} ตัวอักษร'
    return text, None


def validate_theme(data):
    """ตรวจ theme ที่รับจากผู้ใช้ → (theme ที่ล้างแล้ว, {ฟิลด์: เหตุผล})
    ต้องมีครบทุกฟิลด์ ฟิลด์แปลกปลอมถูกทิ้ง"""
    if not isinstance(data, dict):
        return {}, {'_': 'theme ต้องเป็น object'}
    clean, errors = {}, {}
    for key in DEFAULT_THEME:
        if key not in data:
            errors[key] = 'ไม่มีค่า'
            continue
        value, err = _check(key, data[key])
        if err:
            errors[key] = err
        else:
            clean[key] = value
    return clean, errors


def merge_theme(stored):
    """ใช้ตอนอ่านจากที่เก็บ: ฟิลด์ที่หาย/ไม่ถูกต้องย้อนกลับเป็นค่า default รายฟิลด์"""
    merged = dict(DEFAULT_THEME)
    if isinstance(stored, dict):
        for key in DEFAULT_THEME:
            if key in stored:
                value, err = _check(key, stored[key])
                if not err:
                    merged[key] = value
    return merged
