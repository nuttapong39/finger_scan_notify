"""กฎเดียวของทิศทาง Scan (Check-in / Check-out) — ใช้ทุกที่: การ์ด, Dashboard, Monitor, ส่งย้อนหลัง, เตือนยังไม่สแกน

อ้างอิงตาราง hosoffice.fingle_scan_type:
  0 เข้า · 1 ออก · 4 เข้าเวลาพิเศษ · 5 ออกเวลาพิเศษ · IN เข้า · OUT ออก
รหัสอื่น = ไม่ทราบทิศทาง → ไม่ส่งแจ้งเตือน
"""

CHECKIN = frozenset({'0', '4', 'IN'})
CHECKOUT = frozenset({'1', '5', 'OUT'})

# label ต้องตรงกับที่เคยบันทึกใน notify_logs.scan_type (ใช้กันส่งซ้ำ)
_LABELS = {
    '0': 'เข้างาน', 'IN': 'เข้างาน', '4': 'เข้างานพิเศษ',
    '1': 'ออกงาน', 'OUT': 'ออกงาน', '5': 'ออกงานพิเศษ',
}


def _norm(code):
    return '' if code is None else str(code).strip().upper()


def direction(code):
    """'in' | 'out' | None"""
    c = _norm(code)
    if c in CHECKIN:
        return 'in'
    if c in CHECKOUT:
        return 'out'
    return None


def label(code):
    """ป้ายภาษาไทยของรหัส หรือ None ถ้าไม่รู้จัก"""
    return _LABELS.get(_norm(code))


def sql_list(codes):
    """รหัสเป็น literal สำหรับ IN (...) — ค่าคงที่ของเราเท่านั้น ไม่ใช่ input ผู้ใช้"""
    return ','.join(f"'{c}'" for c in sorted(codes, key=lambda x: (len(x), x)))
