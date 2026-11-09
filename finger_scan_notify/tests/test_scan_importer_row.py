"""สร้างแถว fingle_scan ที่ importer ตัวใหม่จะเขียน — ทดสอบผ่าน plan.build_row เท่านั้น (ล้วน ไม่มี I/O)

กติกาที่ถอดจากตัวนำเข้าเดิม (docs/design/scan-importer-single-writer.md, B1): SCAN_TYPE = InOutMode ของเครื่อง ·
DAY_TYPE จากวันในสัปดาห์ (อาทิตย์=1 … เสาร์=7) · ค่าคงที่ WIN/False/FACESCAN · HR_* คัดลอกจากแถวล่าสุดของคนเดียวกัน
"""
from datetime import datetime

from scan_importer import plan

CONSTS = {'COM_NAME': 'PC-1'}
TEMPLATE = {'HR_FULLNAME': 'x y', 'HR_POSITION_NAME': 'p', 'HR_LEAVEL_NAME': None, 'HR_DEP_NAME': 'd',
            'HR_DEP_SUB_NAME': 's', 'HR_PERSON_TYPE_NAME': 't', 'SCAN_DATE_TIME': datetime(2026, 11, 1)}


def src(**kw):
    base = {'seq': 500, 'machine': 3, 'person': 77, 'time': datetime(2026, 11, 6, 8, 1, 14), 'device_time': datetime(2026, 11, 6, 8, 1),
            'mode': '0'}
    return dict(base, **kw)


def build(s=None, person_id=9, template=TEMPLATE):
    return plan.build_row(s or src(), person_id, template, CONSTS)


def test_a_row_carries_the_key_the_commit_time_and_the_scanner_mode_as_is():
    r = build(src(mode='5'))
    assert (r['MACHIN_ID'], r['MACHIN_SCAN_ID'], r['FINGLE_ID'], r['PERSON_ID']) == (3, 500, '77', 9)      # FINGLE_ID เป็นข้อความใน hosoffice
    assert r['SCAN_DATE_TIME'] == datetime(2026, 11, 6, 8, 1, 14)
    assert r['SCAN_TYPE'] == '5'


def test_constants_of_the_old_importer_are_filled():
    r = build()
    assert (r['APP_TYPE_SAVE'], r['ISCONFIRM'], r['ISINPUT'], r['COM_NAME']) == ('WIN', 'False', 'FACESCAN', 'PC-1')


def test_day_type_is_the_weekday_numbered_from_sunday():
    assert (build(src(time=datetime(2026, 11, 8, 9)))['DAY_TYPE_ID'], build(src(time=datetime(2026, 11, 8, 9)))['DAY_TYPE_NAME']) == (1, 'อาทิตย์')
    assert build(src(time=datetime(2026, 11, 6, 9)))['DAY_TYPE_ID'] == 6        # ศุกร์
    assert build(src(time=datetime(2026, 11, 7, 9)))['DAY_TYPE_NAME'] == 'เสาร์'
    assert build(src(time=datetime(2026, 11, 9, 9)))['DAY_TYPE_ID'] == 2        # จันทร์


def test_hr_columns_are_copied_from_the_latest_row_of_the_same_person_and_nothing_else_is_copied():
    r = build()
    assert r['HR_FULLNAME'] == 'x y' and r['HR_DEP_SUB_NAME'] == 's' and r['HR_LEAVEL_NAME'] is None
    assert 'SCAN_DATE_TIME' in r and r['SCAN_DATE_TIME'] != TEMPLATE['SCAN_DATE_TIME']


def test_a_person_without_an_earlier_row_or_a_person_id_cannot_be_built():
    assert build(template=None) is None
    assert build(person_id=None) is None


def test_a_row_with_no_usable_time_cannot_be_built():
    assert build(src(time=None, device_time=None)) is None


def test_a_missing_mode_stays_empty_rather_than_guessed():
    assert build(src(mode=None))['SCAN_TYPE'] is None


def test_compare_rows_counts_columns_that_differ_without_showing_values():
    real = dict(build(), SCAN_TYPE='1')
    assert plan.differing_columns(build(), real) == ['SCAN_TYPE']
    assert plan.differing_columns(build(), build()) == []


def test_a_commit_time_that_differs_by_a_second_or_two_is_not_a_difference_but_a_minute_is():
    real = dict(build())
    real['SCAN_DATE_TIME'] = real['SCAN_DATE_TIME'].replace(second=15)
    assert plan.differing_columns(build(), real) == []
    real['SCAN_DATE_TIME'] = real['SCAN_DATE_TIME'].replace(minute=5)
    assert plan.differing_columns(build(), real) == ['SCAN_DATE_TIME']
