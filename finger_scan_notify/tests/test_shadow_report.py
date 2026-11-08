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
