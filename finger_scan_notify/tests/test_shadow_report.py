"""โหมดเงา (B1): เทียบแถวเครื่องสแกนกับ hosoffice แล้วรายงานว่า importer ตัวใหม่ "จะ" เขียน/กัก/ข้ามกี่แถว — ไม่เขียนอะไรเลย
ทดสอบผ่าน shadow.build_report (ล้วน) และ manage.py shadow-report"""
from datetime import datetime, timedelta

from scan_importer import shadow

NOW = datetime(2026, 11, 8, 19, 0, 0)


def src(seq, minutes_ago=5, machine=1, sec=14):
    t = (NOW - timedelta(minutes=minutes_ago)).replace(second=sec)
    return {'seq': seq, 'machine': machine, 'person': 7, 'time': t, 'device_time': t.replace(second=0)}


def hos(seq, minutes_ago=5, machine=1, sec=14):
    return {'seq': seq, 'machine': machine, 'time': (NOW - timedelta(minutes=minutes_ago)).replace(second=sec)}


def test_report_counts_what_the_new_importer_would_write_hold_and_skip():
    rows = [src(10), src(11), src(12, minutes_ago=3000), src(13)]
    r = shadow.build_report(rows, [hos(10), hos(11)], NOW, checkpoint=9)
    assert r['source_rows'] == 4 and r['would_insert'] == 1 and r['would_hold'] == 1 and r['already_present'] == 2
    assert r['insert_seqs'] == [13] and r['held_seqs'] == [12] and r['flood'] is False


def test_report_checks_that_stored_times_match_either_commit_second_or_device_minute():
    rows = [src(10), src(11), src(12)]
    stored = [hos(10), hos(11, sec=0), hos(12, minutes_ago=90)]          # ตรง / เวลาระดับนาที (ตรง) / เวลาต่างกัน
    r = shadow.build_report(rows, stored, NOW, checkpoint=9)
    assert r['time_matches'] == 2 and r['time_mismatch_seqs'] == [12]


def test_report_lists_rows_hosoffice_has_twice_and_rows_the_scanner_lacks():
    r = shadow.build_report([src(10)], [hos(10), hos(10, sec=0), hos(99)], NOW, checkpoint=9)
    assert r['duplicated_in_hosoffice'] == 1 and r['only_in_hosoffice_seqs'] == [99]


def test_report_flags_a_flood_and_a_missing_checkpoint():
    rows = [src(1000 + i) for i in range(301)]
    assert shadow.build_report(rows, [], NOW, checkpoint=999)['flood'] is True
    assert shadow.build_report(rows, [], NOW, checkpoint=None)['needs_baseline'] is True


def test_the_report_never_contains_names_or_person_ids():
    r = shadow.build_report([src(10)], [], NOW, checkpoint=9)
    assert 'person' not in repr(r) and 'name' not in repr(r)


def test_fill_profile_says_which_columns_the_current_importer_always_fills_never_fills_or_sometimes():
    rows = [{'A': 1, 'B': None, 'C': 'x'}, {'A': 2, 'B': None, 'C': None}, {'A': 3, 'B': None, 'C': 'y'}]
    p = shadow.fill_profile(rows)
    assert p['always'] == ['A'] and p['never'] == ['B'] and p['sometimes'] == {'C': 2 / 3}


# ── manage.py shadow-report ───────────────────────────────────
import hosoffice  # noqa: E402
import manage  # noqa: E402
import settings  # noqa: E402
import tigersoft  # noqa: E402
from test_settings import PROD  # noqa: E402


def test_shadow_report_command_prints_counts_and_fill_profile_without_personal_data(tmp_db, monkeypatch, capsys):
    monkeypatch.setattr(settings, 'current', settings.load({**PROD, 'TIGER_HOST': 'h', 'TIGER_DATABASE': 'd',
                                                            'TIGER_USER': 'u', 'TIGER_PASSWORD': 'secret-pw'}))
    monkeypatch.setattr(hosoffice, 'get_device_window', lambda n: [hos(10), hos(11)])
    monkeypatch.setattr(tigersoft, 'get_rows_after', lambda seq, limit=3000: [src(10), src(11), src(12), src(13, minutes_ago=3000)])
    monkeypatch.setattr(hosoffice, 'get_fill_sample', lambda max_rows=400: [{'ID': 1, 'HR_FULLNAME': 'สมชาย ใจดี', 'SCAN_TIME': None}])
    assert manage.main(['shadow-report']) == 0
    out = capsys.readouterr().out
    assert 'จะเขียน 1' in out and 'จะกัก 1' in out and 'มีอยู่แล้ว 2' in out
    assert 'ID' in out and 'SCAN_TIME' in out
    assert 'สมชาย' not in out and 'secret-pw' not in out


def test_shadow_report_command_needs_the_scanner_configuration(tmp_db, monkeypatch, capsys):
    monkeypatch.setattr(settings, 'current', settings.load({}))
    assert manage.main(['shadow-report']) == 1
    assert 'TIGER_' in capsys.readouterr().out


# ── เทียบทีละคอลัมน์กับแถวจริงของตัวเดิม ─────────────────────────
def real(seq, person=7, mode='0', when=None, **kw):
    t = when or (NOW - timedelta(minutes=5)).replace(second=14)
    base = {'ID': seq, 'PERSON_ID': 1000 + person, 'FINGLE_ID': str(person), 'SCAN_DATE_TIME': t, 'MACHIN_ID': 1, 'MACHIN_SCAN_ID': seq,
            'SCAN_TYPE': mode, 'DAY_TYPE_ID': (t.weekday() + 1) % 7 + 1, 'COM_NAME': 'PC', 'APP_TYPE_SAVE': 'WIN',
            'ISCONFIRM': 'False', 'ISINPUT': 'FACESCAN', 'HR_FULLNAME': 'n', 'HR_POSITION_NAME': 'p', 'HR_LEAVEL_NAME': None,
            'HR_DEP_NAME': 'd', 'HR_DEP_SUB_NAME': 's', 'HR_PERSON_TYPE_NAME': 't', 'DAY_TYPE_NAME': None}
    base['DAY_TYPE_NAME'] = __import__('scan_importer.plan', fromlist=['x']).WEEKDAYS_TH[t.weekday()]
    return dict(base, **kw)


def source(seq, person=7, mode='0'):
    return dict(src(seq, person=None) if False else src(seq), person=person, mode=mode)


def test_comparison_says_which_columns_differ_from_the_old_importer_and_how_many_rows_it_could_check():
    sample = [real(10), real(11), real(12, mode='1'), real(13, person=8)]            # 13 เป็นคนเดียวที่ไม่มีแถวอื่นให้คัดลอก HR_*
    sources = {s: source(s, mode='0') for s in (10, 11, 12)}
    sources[13] = source(13, person=8)
    c = shadow.compare_with_real(sample, sources, {'COM_NAME': 'PC'})
    assert c['compared'] == 3 and c['no_template'] == 1 and c['source_missing'] == 0
    assert c['identical'] == 2 and c['differing'] == {'SCAN_TYPE': 1}


def test_comparison_skips_rows_the_scanner_no_longer_has_and_rows_without_a_sequence():
    c = shadow.compare_with_real([real(10), real(11), {'ID': 1}], {10: source(10)}, {'COM_NAME': 'PC'})
    assert c['source_missing'] == 1 and c['compared'] == 1 and c['no_template'] == 0
    assert 'p' not in repr(c['differing'])


def test_templates_from_the_database_replace_peers_in_the_sample():
    sample = [real(13, person=8)]
    tpl = {1008: {'HR_FULLNAME': 'n', 'HR_POSITION_NAME': 'p', 'HR_LEAVEL_NAME': None, 'HR_DEP_NAME': 'd', 'HR_DEP_SUB_NAME': 's',
                  'HR_PERSON_TYPE_NAME': 't'}}
    c = shadow.compare_with_real(sample, {13: source(13, person=8)}, {'COM_NAME': 'PC'}, tpl)
    assert c['no_template'] == 0 and c['identical'] == 1
