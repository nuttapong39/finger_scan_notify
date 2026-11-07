"""การ์ดใหม่: ส่งย้อนหลัง (Backfill) และเตือนยังไม่สแกน (Missing-scan reminder)"""
import json
import re
from datetime import datetime

import pytest

from cards import build_backfill_card, build_flex_message, build_reminder_card, build_line_message
from flex_theme import DEFAULT_THEME

HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
SIZES = {'xxs', 'xs', 'sm', 'md', 'lg', 'xl', 'xxl', '3xl', '4xl', '5xl'}
LOGO = 'https://example.org/logo.png'


def scan(scan_type='0', **kw):
    base = {'ID': 1, 'PERSON_ID': 7, 'HR_FULLNAME': 'สมชาย ใจดี', 'HR_POSITION_NAME': 'พยาบาล',
            'HR_DEP_NAME': 'กลุ่มงานการพยาบาล', 'HR_DEP_SUB_NAME': 'งานผู้ป่วยใน',
            'HR_PERSON_TYPE_NAME': 'ข้าราชการ', 'SCAN_TYPE': scan_type,
            'SCAN_DATE_TIME': datetime(2026, 11, 6, 8, 3, 12), 'DAY_TYPE_NAME': 'ศุกร์', 'ISINPUT': 'FACESCAN'}
    base.update(kw)
    return base


PERSON = {'name': 'สมชาย ใจดี', 'department': 'งานผู้ป่วยใน'}
CHECKED = datetime(2026, 11, 6, 9, 30)


def walk(node):
    yield node
    kids = node.get('contents')
    if isinstance(kids, list):
        for k in kids:
            yield from walk(k)


def all_nodes(msgs):
    assert len(msgs) == 1 and msgs[0]['type'] == 'flex'
    return list(walk(msgs[0]['contents']['body']))


def texts(msgs):
    return [n['text'] for n in all_nodes(msgs) if n.get('type') == 'text']


def header(msgs):
    return msgs[0]['contents']['body']['contents'][0]


def assert_line_valid(msgs):
    c = msgs[0]
    assert c['contents']['size'] == 'mega'
    assert 0 < len(c['altText']) <= 400
    json.dumps(msgs, ensure_ascii=False)
    for n in all_nodes(msgs):
        for key in ('color', 'backgroundColor'):
            if key in n:
                assert HEX.match(n[key]), (key, n[key])
        if 'background' in n:
            assert HEX.match(n['background']['startColor']) and HEX.match(n['background']['endColor'])
        if n.get('type') == 'text':
            assert n['text'].strip()
            assert n.get('size', 'md') in SIZES
        if n.get('type') == 'button':
            assert n['action']['type'] == 'uri' and n['action']['uri'].startswith('https://')
            assert 0 < len(n['action']['label']) <= 20


# ── scan card: OUT/IN ใช้กฎ scan_types ─────────────────────────
def test_out_scan_is_rendered_as_checkout_not_checkin():
    msgs = build_flex_message(scan('OUT'), 'รพ.', LOGO, DEFAULT_THEME)
    assert header(msgs)['background']['startColor'] == DEFAULT_THEME['checkout_start']
    assert DEFAULT_THEME['checkout_label'] in texts(msgs)


def test_unknown_scan_type_raises():
    with pytest.raises(ValueError):
        build_flex_message(scan('9'), 'รพ.', LOGO, DEFAULT_THEME)


# ── backfill card ──────────────────────────────────────────────
def test_backfill_card_has_grey_header_and_red_notice():
    t = {**DEFAULT_THEME, 'backfill_start': '#AAAAAA', 'backfill_end': '#555555', 'backfill_text': '#DD0000'}
    msgs = build_backfill_card(scan('0'), 'รพ.', LOGO, t, sent_at=datetime(2026, 11, 6, 14, 5))
    g = header(msgs)['background']
    assert (g['startColor'], g['endColor']) == ('#AAAAAA', '#555555')
    notice = [n for n in all_nodes(msgs) if n.get('type') == 'text' and 'ส่งย้อนหลัง' in n['text']]
    assert len(notice) == 1
    assert notice[0]['text'] == '📨 ส่งย้อนหลัง · ส่งเมื่อ 14:05'
    assert notice[0]['color'] == '#DD0000' and notice[0]['weight'] == 'bold'
    assert 'เข้างาน' in texts(msgs) and '08:03:12' in texts(msgs)
    assert 'ส่งย้อนหลัง' in msgs[0]['altText']
    assert_line_valid(msgs)


def test_backfill_card_keeps_direction_label_for_checkout():
    msgs = build_backfill_card(scan('1'), 'รพ.', LOGO, DEFAULT_THEME, sent_at=CHECKED)
    assert 'ออกงาน' in texts(msgs)


# ── reminder cards ─────────────────────────────────────────────
def test_checkin_reminder_card_content():
    msgs = build_reminder_card('checkin', PERSON, 'โรงพยาบาลเชียงกลาง', LOGO, DEFAULT_THEME, checked_at=CHECKED)
    t = texts(msgs)
    assert '⚠️ ยังไม่พบการสแกนเข้างาน' in t
    assert 'กรุณาสแกนเข้างานทันที' in t
    assert 'สมชาย ใจดี' in t and 'งานผู้ป่วยใน' in t
    assert any('ตรวจเมื่อ 09:30' in x and 'ศุกร์' in x and '6/11/2569' in x for x in t)
    assert any('วันหยุดหรือวันลา' in x for x in t)
    assert header(msgs)['background']['startColor'] == DEFAULT_THEME['reminder_in_start']
    assert not [n for n in all_nodes(msgs) if n.get('type') == 'button']
    assert_line_valid(msgs)


def test_checkout_reminder_card_content():
    msgs = build_reminder_card('checkout', PERSON, 'รพ.', LOGO, DEFAULT_THEME, checked_at=datetime(2026, 11, 6, 17, 0))
    t = texts(msgs)
    assert '⚠️ ยังไม่พบการสแกนออกงาน' in t
    assert 'อย่าลืมสแกนออกก่อนกลับ' in t
    assert any('ยังปฏิบัติงานอยู่' in x for x in t)
    assert header(msgs)['background']['startColor'] == DEFAULT_THEME['reminder_out_start']
    assert_line_valid(msgs)


def test_reminder_button_only_when_url_given():
    msgs = build_reminder_card('checkin', PERSON, 'รพ.', LOGO, DEFAULT_THEME, checked_at=CHECKED,
                               button_url='https://hos.example/scan', button_label='สแกนเข้างานตอนนี้')
    buttons = [n for n in all_nodes(msgs) if n.get('type') == 'button']
    assert len(buttons) == 1
    assert buttons[0]['action'] == {'type': 'uri', 'label': 'สแกนเข้างานตอนนี้', 'uri': 'https://hos.example/scan'}
    assert_line_valid(msgs)


def test_reminder_without_department_has_no_empty_text():
    msgs = build_reminder_card('checkin', {'name': 'ก', 'department': ''}, 'รพ.', LOGO, DEFAULT_THEME, checked_at=CHECKED)
    assert_line_valid(msgs)


def test_reminder_rejects_unknown_kind():
    with pytest.raises(ValueError):
        build_reminder_card('lunch', PERSON, 'รพ.', LOGO, DEFAULT_THEME, checked_at=CHECKED)


# ── LINE Notify text (ผู้ให้บริการเดิม) ─────────────────────────
def test_line_text_message_marks_backfill():
    assert 'ส่งย้อนหลัง' in build_line_message(scan('0'), 'รพ.', backfill=True)
    assert 'ส่งย้อนหลัง' not in build_line_message(scan('0'), 'รพ.')
