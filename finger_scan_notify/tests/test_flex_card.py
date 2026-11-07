import json
import re
from datetime import datetime

from flex_theme import DEFAULT_THEME
from cards import build_flex_message

HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
SIZES = {'xxs', 'xs', 'sm', 'md', 'lg', 'xl', 'xxl', '3xl', '4xl', '5xl'}
LOGO = 'https://example.org/logo.png'


def scan(scan_type='0', **kw):
    base = {
        'ID': 1, 'PERSON_ID': 7, 'HR_FULLNAME': 'สมชาย ใจดี',
        'HR_POSITION_NAME': 'พยาบาลวิชาชีพ', 'HR_DEP_NAME': 'กลุ่มงานการพยาบาล',
        'HR_DEP_SUB_NAME': 'งานผู้ป่วยใน', 'HR_PERSON_TYPE_NAME': 'ข้าราชการ',
        'SCAN_TYPE': scan_type, 'SCAN_DATE_TIME': datetime(2026, 11, 6, 8, 3, 12),
        'DAY_TYPE_NAME': 'ศุกร์', 'ISINPUT': 'FACESCAN',
    }
    base.update(kw)
    return base


def card(scan_row=None, theme=None):
    msgs = build_flex_message(scan_row or scan(), 'โรงพยาบาลเชียงกลาง', LOGO, theme)
    assert len(msgs) == 1 and msgs[0]['type'] == 'flex'
    return msgs[0]


def walk(node):
    yield node
    children = node.get('contents')
    if isinstance(children, list):
        for child in children:
            yield from walk(child)


def nodes(msg):
    return list(walk(msg['contents']['body']))


def header(msg):
    return msg['contents']['body']['contents'][0]


def test_bubble_is_mega_and_white():
    c = card()
    assert c['contents']['type'] == 'bubble'
    assert c['contents']['size'] == 'mega'
    assert c['contents']['body']['backgroundColor'].upper() == '#FFFFFF'


def test_header_gradient_follows_scan_direction_and_theme():
    t = {**DEFAULT_THEME, 'checkin_start': '#111111', 'checkin_end': '#222222',
         'checkout_start': '#333333', 'checkout_end': '#444444', 'angle': 90}
    g_in = header(card(scan('0'), t))['background']
    g_out = header(card(scan('1'), t))['background']
    assert g_in == {'type': 'linearGradient', 'angle': '90deg',
                    'startColor': '#111111', 'endColor': '#222222'}
    assert (g_out['startColor'], g_out['endColor']) == ('#333333', '#444444')


def test_header_has_solid_fallback_color():
    assert HEX.match(header(card())['backgroundColor'])


def test_theme_labels_replace_plain_checkin_checkout_only():
    t = {**DEFAULT_THEME, 'checkin_label': 'เข้าปฏิบัติงาน', 'checkout_label': 'เลิกงาน'}
    texts_in = [n.get('text') for n in nodes(card(scan('0'), t))]
    texts_out = [n.get('text') for n in nodes(card(scan('1'), t))]
    texts_special = [n.get('text') for n in nodes(card(scan('4'), t))]
    assert 'เข้าปฏิบัติงาน' in texts_in
    assert 'เลิกงาน' in texts_out
    assert 'เข้างานพิเศษ' in texts_special          # แบบพิเศษคงชื่อเดิม


def test_footer_text_and_logo_appears_once_in_header():
    t = {**DEFAULT_THEME, 'footer_text': 'ฟุตเตอร์ทดสอบ'}
    c = card(theme=t)
    texts = [n.get('text') for n in nodes(c)]
    assert 'ฟุตเตอร์ทดสอบ' in texts
    images = [n for n in nodes(c) if n.get('type') == 'image']
    assert len(images) == 1 and images[0]['url'] == LOGO
    assert any(n.get('type') == 'image' for n in walk(header(c)))   # logo อยู่ใน header


def test_card_carries_all_scan_fields():
    texts = ' | '.join(str(n.get('text')) for n in nodes(card()))
    for expected in ('สมชาย ใจดี', 'พยาบาลวิชาชีพ', 'กลุ่มงานการพยาบาล', 'งานผู้ป่วยใน',
                     'ข้าราชการ', '08:03:12', '06/11/2026', 'ศุกร์', 'FACESCAN', 'โรงพยาบาลเชียงกลาง'):
        assert expected in texts, expected


def test_empty_fields_do_not_produce_empty_text_nodes():
    c = card(scan(HR_POSITION_NAME=None, HR_DEP_NAME='', HR_DEP_SUB_NAME=None,
                  HR_PERSON_TYPE_NAME=None, DAY_TYPE_NAME=None, ISINPUT=None))
    for n in nodes(c):
        if n.get('type') == 'text':
            assert n['text'].strip(), n          # LINE ปฏิเสธ text ว่าง


def test_all_colors_and_sizes_are_valid_for_line():
    c = card()
    for n in nodes(c):
        for key in ('color', 'backgroundColor'):
            if key in n:
                assert HEX.match(n[key]), (key, n[key])
        if 'background' in n:
            assert HEX.match(n['background']['startColor']) and HEX.match(n['background']['endColor'])
        if n.get('type') == 'text' and 'size' in n:
            assert n['size'] in SIZES


def test_alt_text_is_within_line_limit_and_json_serialisable():
    c = card(scan(HR_FULLNAME='ก' * 1000))
    assert 0 < len(c['altText']) <= 400
    json.dumps({'messages': [c]}, ensure_ascii=False)


def test_default_theme_is_used_when_none_given():
    assert header(card(theme=None))['background']['startColor'] == DEFAULT_THEME['checkin_start']


def test_missing_logo_falls_back_to_default_logo():
    msgs = build_flex_message(scan(), 'รพ.', '', None)
    imgs = [n for n in nodes(msgs[0]) if n.get('type') == 'image']
    assert len(imgs) == 1 and imgs[0]['url'].startswith('https://')
