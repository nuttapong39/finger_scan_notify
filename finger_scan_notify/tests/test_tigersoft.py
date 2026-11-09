"""ช่วง C: อ่านฐานข้อมูลเครื่องสแกน (Tigersoft / SQL Server) — อ่านอย่างเดียว

ใช้ connection จำลอง (ไม่ต่อ SQL Server จริง); ทดสอบผ่าน tigersoft.*, scanwatch.run_cycle, manage.main และหน้า/API
"""
import json
from datetime import datetime, timedelta

import pytest

import app as app_module
import database
import hosoffice
import manage
import notifier
import scanwatch
import settings
import tigersoft
from test_settings import PROD

FRI = datetime(2026, 11, 6)


def at(h, m=0, s=0, day=FRI):
    return day.replace(hour=h, minute=m, second=s)


def row(seq, when, machine=1):
    return {'seq': seq, 'machine': machine, 'time': when}


TIGER_ENV = {'TIGER_HOST': '192.168.1.77', 'TIGER_DATABASE': 'TigerWebServerPlus', 'TIGER_USER': 'office',
             'TIGER_PASSWORD': 'p@ss-secret'}


class Resp:
    status_code, text = 200, '{"message":"Succesfully"}'


# ── ค่าใน .env ───────────────────────────────────────────────
def test_tiger_is_off_unless_a_host_is_configured():
    assert settings.load({}).tiger is None
    assert settings.load(PROD).tiger is None


def test_tiger_settings_are_read_with_defaults():
    t = settings.load({**PROD, **TIGER_ENV}).tiger
    assert t == {'host': '192.168.1.77', 'port': 1433, 'database': 'TigerWebServerPlus', 'user': 'office',
                 'password': 'p@ss-secret', 'person_column': 'PersonCardID', 'machine_column': 'MachID'}
    t = settings.load({**TIGER_ENV, 'TIGER_PORT': '14330', 'TIGER_PERSON_COLUMN': 'FingerID',
                       'TIGER_MACHINE_COLUMN': 'MachineNo'}).tiger
    assert t['port'] == 14330 and t['person_column'] == 'FingerID' and t['machine_column'] == 'MachineNo'


@pytest.mark.parametrize('missing', ['TIGER_DATABASE', 'TIGER_USER', 'TIGER_PASSWORD'])
def test_a_half_configured_tiger_refuses_to_start_naming_the_missing_value(missing):
    env = {k: v for k, v in TIGER_ENV.items() if k != missing}
    with pytest.raises(settings.SettingsError, match=missing):
        settings.load(env)


def test_bad_tiger_port_is_rejected():
    with pytest.raises(settings.SettingsError, match='TIGER_PORT'):
        settings.load({**TIGER_ENV, 'TIGER_PORT': 'x'})


# ── ตัวอ่านเครื่องสแกน (connection จำลอง) ───────────────────────
class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=()):
        self.conn.executed.append((sql, tuple(params)))
        if self.conn.fail:
            raise RuntimeError('boom')
        self.rows = self.conn.respond(sql, tuple(params))

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class FakeConn:
    def __init__(self, respond, fail=False):
        self.respond, self.fail, self.executed, self.closed = respond, fail, [], False

    def cursor(self, *a, **kw):
        return FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def tiger(monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**PROD, **TIGER_ENV, 'TIGER_PERSON_COLUMN': 'FingerID',
                                                            'TIGER_MACHINE_COLUMN': 'MachineNo'}))
    st = {'rows': {}, 'conn': None, 'max': 0}

    def respond(sql, params):
        if 'MAX(' in sql.upper():
            return [{'m': st['max']}]
        if 'INFORMATION_SCHEMA' in sql.upper():
            return [{'COLUMN_NAME': 'ID_TimeInout', 'DATA_TYPE': 'int'}, {'COLUMN_NAME': 'TimeInout', 'DATA_TYPE': 'datetime'},
                    {'COLUMN_NAME': 'CommitTime', 'DATA_TYPE': 'datetime'},
                    {'COLUMN_NAME': 'FingerID', 'DATA_TYPE': 'int'}, {'COLUMN_NAME': 'MachineNo', 'DATA_TYPE': 'int'}]
        if '>=' in sql:                       # get_rows_between (ช่วงเวลา)
            return st.get('between', [])
        if sql.upper().count('TOP 1') and 'ORDER BY' in sql.upper():
            return [st['rows'][max(st['rows'])]] if st['rows'] else []
        return [st['rows'][p] for p in params if p in st['rows']]

    def connect():
        st['conn'] = FakeConn(respond, fail=st.get('fail', False))
        return st['conn']

    monkeypatch.setattr(tigersoft, '_connect', connect)
    return st


def src_row(seq, when, person=7, machine=2):
    return {'ID_TimeInout': seq, 'TimeInout': when, 'FingerID': person, 'MachineNo': machine}


def test_not_configured_means_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({}))
    assert tigersoft.configured() is False
    with pytest.raises(tigersoft.TigerUnavailable):
        tigersoft.get_latest_seq()


def test_rows_are_looked_up_by_sequence_with_bound_parameters_and_normalised(tiger):
    tiger['rows'] = {10: src_row(10, at(8, 1)), 11: src_row(11, at(8, 2), person=9, machine=3)}
    got = tigersoft.get_rows([10, 11, 12])
    assert got == {10: {'seq': 10, 'time': at(8, 1), 'device_time': at(8, 1), 'person': 7, 'machine': 2, 'mode': None},
                   11: {'seq': 11, 'time': at(8, 2), 'device_time': at(8, 2), 'person': 9, 'machine': 3, 'mode': None}}      # 12 ไม่มีที่เครื่อง → ไม่อยู่ในผล
    sql, params = tiger['conn'].executed[0]
    assert params == (10, 11, 12) and '10' not in sql and 'ZFP_TimeInOut' in sql
    assert tiger['conn'].closed


def test_large_lookups_are_split_into_chunks_on_one_connection(tiger):
    tigersoft.get_rows(list(range(1, 1201)))
    assert [len(p) for _, p in tiger['conn'].executed] == [500, 500, 200]


def test_only_select_statements_are_ever_sent(tiger):
    tiger['max'] = 99
    tigersoft.get_latest_seq()
    tigersoft.get_columns()
    tigersoft.get_rows([1])
    assert all(sql.lstrip().upper().startswith('SELECT') for sql, _ in tiger['conn'].executed)
    with pytest.raises(ValueError):
        tigersoft._select('DELETE FROM ZFP_TimeInOut')
    with pytest.raises(ValueError):
        tigersoft._select('SELECT 1; DROP TABLE x')


def test_latest_sequence_and_columns(tiger):
    tiger['max'] = 365900
    assert tigersoft.get_latest_seq() == 365900
    assert tigersoft.get_columns()[:2] == [('ID_TimeInout', 'int'), ('TimeInout', 'datetime')]


def test_any_driver_error_becomes_tiger_unavailable(tiger):
    tiger['fail'] = True
    with pytest.raises(tigersoft.TigerUnavailable):
        tigersoft.get_latest_seq()


def test_the_password_never_appears_in_error_messages(monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**TIGER_ENV}))

    def boom():
        raise RuntimeError('Login failed for user office password=p@ss-secret')

    monkeypatch.setattr(tigersoft, '_connect', boom)
    with pytest.raises(tigersoft.TigerUnavailable) as e:
        tigersoft.get_latest_seq()
    assert 'p@ss-secret' not in str(e.value)


# ── ระบุ Lost scan ว่าเป็นของใคร ───────────────────────────────
@pytest.fixture
def watch(tmp_db, tiger, monkeypatch):
    database.save_token(156, 'ผู้ดูแลระบบ', 'MOPH_ALERT', '', 'ck', 'sk', 'IT', 1, 1)
    st = {'rows': [], 'sent': []}
    monkeypatch.setattr(hosoffice, 'get_device_window', lambda n: list(st['rows']))
    monkeypatch.setattr(hosoffice, 'is_holiday', lambda d: False)
    monkeypatch.setattr(hosoffice, 'get_names_by_fingle_ids', lambda ids: {7: 'สมชาย ใจดี'})
    monkeypatch.setattr(notifier.requests, 'post',
                        lambda url, **kw: st['sent'].append(kw['data'].decode('utf-8')) or Resp())
    return st


def test_a_lost_scan_is_identified_with_name_time_and_machine(watch, tiger):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    tiger['rows'] = {1: src_row(1, at(8, 0)), 2: src_row(2, at(8, 1, 20), person=7, machine=2), 3: src_row(3, at(8, 2))}
    tiger['max'] = 3
    scanwatch.run_cycle(at(8, 5))
    d = database.get_watch_events('lost_scan')[0]['detail']
    assert d['name'] == 'สมชาย ใจดี' and d['scan_time'].endswith('08:01:20') and d['machine'] == 2
    scanwatch.run_cycle(at(8, 21))
    assert len(watch['sent']) == 1 and 'สมชาย ใจดี' in watch['sent'][0] and '08:01:20' in watch['sent'][0]


def test_a_missing_number_the_scanner_never_issued_is_not_a_lost_scan(watch, tiger):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    tiger['rows'] = {1: src_row(1, at(8, 0)), 3: src_row(3, at(8, 2))}      # เครื่องไม่มีเลข 2 → เป็นรูในการนับ ไม่ใช่การสแกนที่หาย
    tiger['max'] = 3
    scanwatch.run_cycle(at(8, 5))
    assert [e['open'] for e in database.get_watch_events('lost_scan')] == [True]           # ยังเร็วไป: แถวอาจ commit ช้า
    scanwatch.run_cycle(at(8, 21))
    assert watch['sent'] == []
    events = database.get_watch_events('lost_scan')
    assert [e['open'] for e in events] == [False] and events[0]['detail']['not_in_source'] is True


def test_when_the_scanner_database_is_unreachable_lost_scans_still_alert_without_names(watch, tiger):
    tiger['fail'] = True
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    scanwatch.run_cycle(at(8, 5))
    scanwatch.run_cycle(at(8, 21))
    assert len(watch['sent']) == 1 and 'Lost scan' in watch['sent'][0]
    assert database.get_watch_state()['tiger_error']


def test_without_a_person_column_the_name_is_simply_left_out(watch, tiger, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**PROD, **TIGER_ENV, 'TIGER_PERSON_COLUMN': ''}))
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    tiger['rows'] = {1: src_row(1, at(8, 0)), 2: src_row(2, at(8, 1, 20)), 3: src_row(3, at(8, 2))}
    tiger['max'] = 3
    scanwatch.run_cycle(at(8, 5))
    d = database.get_watch_events('lost_scan')[0]['detail']
    assert 'name' not in d and d['scan_time'].endswith('08:01:20')


# ── Import stall ตลอดวัน เทียบเลขล่าสุดสองฝั่ง ───────────────────
def test_stall_is_decided_all_day_by_comparing_the_latest_number_on_both_sides(watch, tiger):
    watch['rows'] = [row(100, at(13, 0))]
    tiger['max'] = 100
    scanwatch.run_cycle(at(13, 1))
    assert scanwatch.import_stalled(at(13, 1)) is False
    tiger['max'] = 103                                            # เครื่องมีสแกนใหม่ 3 รายการ แต่ hosoffice ไม่ได้รับ
    scanwatch.run_cycle(at(13, 10))                               # เริ่มเห็น "ตามหลัง" ตอน 13:10
    scanwatch.run_cycle(at(13, 22))
    assert scanwatch.import_stalled(at(13, 22)) is False          # ยังไม่ครบ 20 นาทีจากครั้งแรกที่เห็น
    scanwatch.run_cycle(at(13, 31))
    assert scanwatch.import_stalled(at(13, 31)) is True           # นอกช่วงเฝ้าก็ตรวจได้ (ไม่มีข้อมูลเงียบปกติมาปน)
    assert len(watch['sent']) == 1 and 'การนำเข้า' in watch['sent'][0]
    watch['rows'].append(row(103, at(13, 33)))
    scanwatch.run_cycle(at(13, 34))
    assert scanwatch.import_stalled(at(13, 34)) is False
    assert len(watch['sent']) == 2 and 'กลับมาปกติ' in watch['sent'][1]


def test_a_quiet_afternoon_with_both_sides_equal_is_never_a_stall(watch, tiger):
    watch['rows'] = [row(100, at(9, 0))]
    tiger['max'] = 100
    for m in range(0, 180, 20):
        scanwatch.run_cycle(at(9, 30) + timedelta(minutes=m))
    assert watch['sent'] == [] and scanwatch.import_stalled(at(12, 20)) is False


def test_the_scanner_being_ahead_only_by_a_lost_tail_number_is_not_a_permanent_stall(watch, tiger):
    watch['rows'] = [row(100, at(16, 0))]
    tiger['max'] = 101                                            # เลข 101 หาย แต่แล้วก็มีสแกนใหม่ถึง hosoffice ตามปกติ
    scanwatch.run_cycle(at(16, 1))
    watch['rows'].append(row(102, at(16, 15)))
    tiger['max'] = 102
    scanwatch.run_cycle(at(16, 16))
    scanwatch.run_cycle(at(16, 40))
    assert scanwatch.import_stalled(at(16, 40)) is False


def test_when_the_scanner_side_is_unreachable_the_rate_rule_still_applies_but_never_blocks_by_itself(watch, tiger):
    tiger['fail'] = True
    watch['rows'] = [row(100, at(13, 0))]
    scanwatch.run_cycle(at(13, 1))
    scanwatch.run_cycle(at(13, 30))
    assert scanwatch.import_stalled(at(13, 30)) is False and watch['sent'] == []


# ── manage.py tiger-check ──────────────────────────────────────
def test_tiger_check_needs_configuration(tmp_db, monkeypatch, capsys):
    monkeypatch.setattr(settings, 'current', settings.load({}))
    assert manage.main(['tiger-check']) == 1
    assert 'TIGER_HOST' in capsys.readouterr().out


def test_tiger_check_reports_columns_and_matches_without_printing_the_password(tmp_db, tiger, monkeypatch, capsys):
    tiger['max'] = 365900
    monkeypatch.setattr(hosoffice, 'get_device_window',
                        lambda n: [row(365898, at(8, 0)), row(365899, at(8, 1)), row(365900, at(8, 2))])
    tiger['rows'] = {365898: src_row(365898, at(8, 0)), 365899: src_row(365899, at(8, 1)),
                     365900: src_row(365900, at(8, 2, 59))}                    # แถวหนึ่งเวลาไม่ตรง
    assert manage.main(['tiger-check']) == 0
    out = capsys.readouterr().out
    assert 'ID_TimeInout' in out and 'TimeInout' in out and '365900' in out
    assert 'p@ss-secret' not in out
    assert '2/3' in out or '2 จาก 3' in out                                    # เลขตรง + เวลาตรง 2 จาก 3 แถว


def test_tiger_check_fails_clearly_when_unreachable(tmp_db, tiger, capsys):
    tiger['fail'] = True
    assert manage.main(['tiger-check']) == 1
    assert 'ต่อ' in capsys.readouterr().out


# ── หน้าเว็บ ────────────────────────────────────────────────
def test_events_api_exposes_who_and_when_for_logged_in_admins_only(admin_client):
    database.open_watch_event('lost_scan', 365000, {'name': 'สมชาย ใจดี', 'scan_time': '2026-11-06 08:01:20', 'machine': 2}, 1.0)
    body = admin_client.get('/api/watch/status').get_json()
    assert body['events'][0]['detail']['name'] == 'สมชาย ใจดี'
    assert body['state']['tiger_configured'] is False
    anon = app_module.app.test_client()
    anon.environ_base['HTTP_ORIGIN'] = 'http://localhost'
    assert anon.get('/api/watch/status').status_code == 401


def test_the_page_script_shows_the_name_and_scan_time_columns(admin_client):
    html = admin_client.get('/watch').get_data(as_text=True)
    assert 'e.detail.name' in html and 'scan_time' in html
    assert json.dumps('p@ss-secret') not in html


def test_the_first_scan_after_a_long_quiet_period_is_not_mistaken_for_a_stall(watch, tiger):
    watch['rows'] = [row(100, at(7, 0))]
    tiger['max'] = 100
    scanwatch.run_cycle(at(7, 1))
    tiger['max'] = 101                                            # 09:00 มีสแกนแรกของวัน เครื่องเห็นก่อน hosoffice ไม่กี่วินาที
    scanwatch.run_cycle(at(9, 0, 5))
    assert scanwatch.import_stalled(at(9, 0, 5)) is False and watch['sent'] == []


def test_a_wrong_scanner_database_never_wipes_open_lost_scans(watch, tiger):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    tiger['rows'] = {}                                            # ฐานข้อมูล/ตารางผิดที่: ว่างเปล่าโดยไม่ error
    tiger['max'] = 3
    scanwatch.run_cycle(at(8, 5))
    assert [e['open'] for e in database.get_watch_events('lost_scan')] == [True]
    assert 'ไม่พบที่เครื่องสแกนเลย' in database.get_watch_state()['tiger_error']
    scanwatch.run_cycle(at(8, 21))
    assert len(watch['sent']) == 1                                # ยังแจ้งตามปกติ ไม่ถูกปิดเงียบ ๆ


def test_a_failed_name_lookup_is_retried_next_round(watch, tiger, monkeypatch):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    tiger['rows'] = {1: src_row(1, at(8, 0)), 2: src_row(2, at(8, 1, 20)), 3: src_row(3, at(8, 2))}
    tiger['max'] = 3

    def down(ids):
        raise hosoffice.HosofficeUnavailable('down')

    monkeypatch.setattr(hosoffice, 'get_names_by_fingle_ids', down)
    scanwatch.run_cycle(at(8, 5))
    assert 'name' not in database.get_watch_events('lost_scan')[0]['detail']
    monkeypatch.setattr(hosoffice, 'get_names_by_fingle_ids', lambda ids: {7: 'สมชาย ใจดี'})
    scanwatch.run_cycle(at(8, 6))
    assert database.get_watch_events('lost_scan')[0]['detail']['name'] == 'สมชาย ใจดี'


def test_a_broken_scanner_response_cannot_stop_the_main_check(watch, tiger):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    tiger['rows'] = {2: {'unexpected': 1}}
    tiger['max'] = 3
    scanwatch.run_cycle(at(8, 5))
    assert database.get_watch_state()['last_poll_at'] and len(database.get_watch_events('lost_scan')) == 1


@pytest.mark.parametrize('sql', [
    'DELETE FROM ZFP_TimeInOut', 'SELECT 1 EXEC xp_cmdshell', 'SELECT 1 DELETE FROM x', 'SELECT * INTO t FROM x',
    'SELECT 1 -- hi', 'SELECT 1 /* x */', 'select 1; select 2', 'UPDATE x SET a=1', 'INSERT INTO x VALUES (1)',
    "SELECT 1 FROM x WHERE a = 1 DROP TABLE x", 'WITH c AS (SELECT 1) DELETE FROM c'])
def test_the_select_guard_rejects_anything_that_could_write_or_chain(sql):
    with pytest.raises(ValueError):
        tigersoft._check_select(sql)


def test_the_password_is_not_in_the_settings_repr():
    assert 'p@ss-secret' not in repr(settings.load({**TIGER_ENV}))


def test_a_scanner_row_that_commits_late_is_still_identified_not_dismissed(watch, tiger):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    tiger['rows'] = {1: src_row(1, at(8, 0)), 3: src_row(3, at(8, 2))}                   # เลข 2 ยังไม่ commit ที่เครื่อง
    tiger['max'] = 3
    scanwatch.run_cycle(at(8, 5))
    scanwatch.run_cycle(at(8, 9))
    tiger['rows'][2] = src_row(2, at(8, 1, 20), person=7)                                # commit ทีหลัง
    scanwatch.run_cycle(at(8, 12))
    d = database.get_watch_events('lost_scan')[0]
    assert d['open'] and d['detail']['name'] == 'สมชาย ใจดี'
    scanwatch.run_cycle(at(8, 21))
    assert len(watch['sent']) == 1 and 'สมชาย ใจดี' in watch['sent'][0]


def test_a_lost_latest_scan_with_no_later_scan_is_reported_as_a_stall_with_both_numbers(watch, tiger):
    watch['rows'] = [row(100, at(16, 0))]
    tiger['max'] = 101                                                                   # เลข 101 หาย และไม่มีสแกนถัดไปแล้ว
    scanwatch.run_cycle(at(16, 5))                                                       # ครั้งแรก: ตั้งฐานเลข
    scanwatch.run_cycle(at(16, 6))                                                       # เริ่มเห็น "ตามหลัง"
    scanwatch.run_cycle(at(16, 30))
    assert scanwatch.import_stalled(at(16, 30)) is True
    assert len(watch['sent']) == 1 and '101' in watch['sent'][0] and '100' in watch['sent'][0]
    watch['rows'].append(row(102, at(7, 5, day=FRI + timedelta(days=1))))               # เช้าวันถัดไปมีสแกนใหม่ → จบเอง
    tiger['max'] = 102
    scanwatch.run_cycle(at(7, 6, day=FRI + timedelta(days=1)))
    assert scanwatch.import_stalled(at(7, 6, day=FRI + timedelta(days=1))) is False


def test_tiger_check_fails_when_no_hosoffice_number_matches_the_scanner_table(tmp_db, tiger, monkeypatch, capsys):
    tiger['max'] = 500
    tiger['rows'] = {}
    monkeypatch.setattr(hosoffice, 'get_device_window', lambda n: [row(365898, at(8, 0)), row(365899, at(8, 1))])
    assert manage.main(['tiger-check']) == 1
    out = capsys.readouterr().out
    assert '0/2' in out and 'FAIL' in out


def test_only_the_needed_columns_are_selected_not_the_whole_table(tiger):
    tigersoft.get_rows([1])
    sql = tiger['conn'].executed[0][0]
    assert '*' not in sql and 'FingerID' in sql and 'MachineNo' in sql and 'ID_TimeInout' in sql


def test_a_configured_column_name_with_odd_characters_is_refused(tiger, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**TIGER_ENV, 'TIGER_PERSON_COLUMN': 'a; DROP TABLE x'}))
    with pytest.raises(tigersoft.TigerUnavailable):
        tigersoft.get_rows([1])


# ── ตรวจกับเครื่องจริง (8 พ.ย. 2569): TimeInout ระดับนาที / CommitTime ถึงวินาที / numeric เป็น Decimal ──
def test_the_scan_time_is_the_commit_time_with_seconds_and_numeric_ids_become_plain_integers(tiger):
    from decimal import Decimal
    tiger['rows'] = {5: {'ID_TimeInout': Decimal('5'), 'TimeInout': at(18, 59), 'CommitTime': at(18, 59, 14),
                         'FingerID': Decimal('77'), 'MachineNo': Decimal('3')}}
    r = tigersoft.get_rows([5])[5]
    assert r['time'] == at(18, 59, 14) and r['device_time'] == at(18, 59)
    assert r['person'] == 77 and type(r['person']) is int and r['machine'] == 3


def test_a_hosoffice_time_matches_either_the_commit_second_or_the_device_minute():
    src = {'time': at(18, 59, 14), 'device_time': at(18, 59)}
    assert tigersoft.same_scan_time(at(18, 59, 14), src) and tigersoft.same_scan_time(at(18, 59, 15), src)
    assert tigersoft.same_scan_time(at(18, 59), src)                          # แถวที่ตัวนำเข้าเก็บเวลาระดับนาที
    assert not tigersoft.same_scan_time(at(18, 40), src) and not tigersoft.same_scan_time(at(18, 59), None)


def test_tiger_check_counts_a_row_as_matching_when_hosoffice_stored_the_commit_second(tmp_db, tiger, monkeypatch, capsys):
    tiger['max'] = 10
    monkeypatch.setattr(hosoffice, 'get_device_window', lambda n: [row(9, at(8, 1, 14)), row(10, at(8, 2))])
    tiger['rows'] = {9: dict(src_row(9, at(8, 1)), CommitTime=at(8, 1, 14)), 10: dict(src_row(10, at(8, 2)), CommitTime=at(8, 2, 9))}
    assert manage.main(['tiger-check']) == 0
    assert '2/2' in capsys.readouterr().out


def test_the_scanner_in_out_mode_is_carried_as_text_and_requested_in_the_select(tiger):
    tiger['rows'] = {10: dict(src_row(10, at(8, 1)), InOutMode='4')}
    got = tigersoft.get_rows([10])
    assert got[10]['mode'] == '4'
    assert 'InOutMode' in tiger['conn'].executed[0][0]


def test_rows_between_two_times_are_read_in_sequence_order_with_bound_parameters(tiger):
    tiger['between'] = [src_row(5, at(8, 1)), src_row(6, at(8, 2), person=9, machine=3)]
    got = tigersoft.get_rows_between(at(0), at(23), limit=10)
    assert [r['seq'] for r in got] == [5, 6] and got[1]['person'] == 9 and got[1]['machine'] == 3
    sql, params = tiger['conn'].executed[0]
    assert params == (at(0), at(23)) and 'TOP 10' in sql and 'ORDER BY ID_TimeInout' in sql and '2026' not in sql
    assert sql.lstrip().upper().startswith('SELECT') and tiger['conn'].closed


def test_rows_between_never_sends_anything_but_a_select(tiger):
    tigersoft.get_rows_between(at(0), at(23))
    assert all(sql.lstrip().upper().startswith('SELECT') for sql, _ in tiger['conn'].executed)
