"""ไอคอนของหน้าเว็บ — Lucide (ไอคอนทั่วไป) และ Simple Icons (โลโก้แบรนด์ ชื่อขึ้นต้น brand-)

ทุกไอคอนอยู่ใน static/icons.svg (สร้างด้วย tools/build_icons.py จากชื่อที่ template เรียก `icon('ชื่อ')`)
จึงใช้ได้แม้ Server ไม่มีอินเทอร์เน็ต
"""
import os
import re

from flask import url_for
from markupsafe import Markup

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
_NAME = re.compile(r'^[a-z0-9-]+$')
_THAI_LEADING_VOWELS = 'เแโใไ'


def static_url(filename):
    """URL ของไฟล์ใน static/ พร้อมเลขเวอร์ชัน (เปลี่ยนไฟล์แล้ว browser โหลดใหม่เอง ไม่ค้างของเก่าหลัง deploy)"""
    try:
        version = int(os.stat(os.path.join(STATIC_DIR, filename)).st_mtime)
    except OSError:
        version = 0
    return url_for('static', filename=filename, v=version)


def sprite_url():
    return static_url('icons.svg')


def icon(name, cls=''):
    """<svg> ของไอคอนหนึ่งตัว — สีตามตัวอักษรรอบข้าง (currentColor)"""
    if not _NAME.match(name):
        raise ValueError(f'ชื่อไอคอนไม่ถูกต้อง: {name!r}')
    classes = 'ic' + (' ic-brand' if name.startswith('brand-') else '') + (f' {cls}' if cls else '')
    return Markup(f'<svg class="{classes}" aria-hidden="true" focusable="false">'
                  f'<use href="{sprite_url()}#{name}"/></svg>')


def initial(name):
    """ตัวอักษรแรกของชื่อสำหรับวงกลมแทนรูป — ข้ามสระนำภาษาไทย (เ แ โ ใ ไ) ที่ไม่ใช่พยัญชนะต้น"""
    for ch in (name or '').strip():
        if ch in _THAI_LEADING_VOWELS or not ch.isalnum():
            continue
        return ch.upper()
    return '?'
