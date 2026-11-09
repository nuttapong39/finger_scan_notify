"""Scan importer โหมดเขียน: รอบเขียน, ปล่อยแถวที่กัก (ต้องล็อกอิน + จำนวนตรงกับตัวอย่าง), และตัวเขียน hosoffice (จำลอง mysql connector)
ไม่มีการเขียนฐานข้อมูลจริงในเทสต์เหล่านี้"""
from datetime import datetime, timedelta

import pytest

import database
import hosoffice
import settings
import tigersoft
from scan_importer import runner
from test_settings import PROD

NOW = datetime(2026, 11, 9, 10, 0, 0)
WRITE_ENV = {**PROD, 'SCAN_IMPORTER_MODE': 'write', 'TIGER_HOST': 'h', 'TIGER_DATABASE': 'd', 'TIGER_USER': 'u', 'TIGER_PASSWORD': 'pw',
             'HOSOFFICE_WRITE_USER': 'scan_importer_rw', 'HOSOFFICE_WRITE_PASSWORD': 'w-secret', 'SCAN_IMPORTER_COM_NAME': 'PC-X'}
TPL = {'HR_FULLNAME': 'n', 'HR_POSITION_NAME': 'p', 'HR_LEAVEL_NAME': None, 'HR_DEP_NAME': 'd', 'HR_DEP_SUB_NAME': 's',
       'HR_PERSON_TYPE_NAME': 't'}


def src(seq, minutes_ago=5, machine=1, person=7):
    t = (NOW - timedelta(minutes=minutes_ago)).replace(second=14)
    return {'seq': seq, 'machine': machine, 'person': person, 'time': t, 'device_time': t.replace(second=0), 'mode': '0'}


# ── settings ──
@pytest.mark.parametrize('missing', ['HOSOFFICE_WRITE_USER', 'HOSOFFICE_WRITE_PASSWORD', 'SCAN_IMPORTER_COM_NAME', 'TIGER_HOST'])
def test_write_mode_refuses_to_start_naming_what_is_missing(missing):
    with pytest.raises(settings.SettingsError, match=missing):
        settings.load({k: v for k, v in WRITE_ENV.items() if k != missing})


def test_write_mode_keeps_the_write_password_out_of_repr():
    s = settings.load(WRITE_ENV)
    assert s.importer_mode == 'write' and s.importer_write['user'] == 'scan_importer_rw' and 'w-secret' not in repr(s)


def test_an_unknown_mode_is_rejected():
    with pytest.raises(settings.SettingsError):
        settings.load({**PROD, 'SCAN_IMPORTER_MODE': 'yolo'})


# ── รอบเขียน / ปล่อย ──
@pytest.fixture
def wr(tmp_db, monkeypatch):
    st = {'rows': [], 'existing': set(), 'inserted': [], 'insert_fail': False, 'people': {'7': 1007, '8': 1008},
          'templates': {1007: TPL}}
    monkeypatch.setattr(settings, 'current', settings.load(WRITE_ENV))

    def insert(rows, writer):
        if st['insert_fail']:
            raise hosoffice.HosofficeWriteError('เขียนไม่ได้ (ย้อนกลับแล้ว)')
        st['inserted'].append((writer, list(rows)))
        return len(rows)

    monkeypatch.setattr(tigersoft, 'get_rows_after', lambda seq, limit=3000: [r for r in st['rows'] if r['seq'] > seq])
    monkeypatch.setattr(hosoffice, 'get_existing_keys', lambda since: set(st['existing']))
    monkeypatch.setattr(hosoffice, 'get_person_ids', lambda ids: {str(i): st['people'][str(i)] for i in ids if str(i) in st['people']})
    monkeypatch.setattr(hosoffice, 'get_latest_rows_by_person', lambda ids: {i: st['templates'][i] for i in ids if i in st['templates']})
    monkeypatch.setattr(hosoffice, 'insert_scan_rows', insert)
    return st


def test_a_write_round_inserts_fresh_rows_with_the_importer_label_then_moves_the_checkpoint(wr):
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101), src(102)]
    r = runner.run_round(NOW)
    assert r['inserted'] == 2
    writer, rows = wr['inserted'][0]
    assert writer == 'SCAN-IMPORTER' and [x['MACHIN_SCAN_ID'] for x in rows] == [101, 102]
    assert rows[0]['COM_NAME'] == 'PC-X' and rows[0]['PERSON_ID'] == 1007 and rows[0]['HR_FULLNAME'] == 'n'
    assert database.get_importer_state()['checkpoint'] == 102


def test_a_person_without_an_earlier_row_is_held_not_guessed(wr):
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101), src(102, person=8), src(103, person=9)]        # 8 ไม่มีต้นแบบ, 9 ไม่มีใน hr_person
    r = runner.run_round(NOW)
    assert r['inserted'] == 1 and r['held'] == 2
    assert {(h['seq'], h['reason']) for h in database.get_importer_held()} == {(102, 'no_template'), (103, 'no_person')}


def test_a_failed_insert_keeps_the_checkpoint_and_the_next_round_retries(wr):
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101)]
    wr['insert_fail'] = True
    assert runner.run_round(NOW)['status'] == 'error'
    st = database.get_importer_state()
    assert st['checkpoint'] == 100 and 'ย้อนกลับ' in st['last_error']
    wr['insert_fail'] = False
    assert runner.run_round(NOW)['inserted'] == 1


def test_a_flood_round_writes_nothing_even_in_write_mode(wr):
    database.save_importer_state(checkpoint=1000)
    wr['rows'] = [src(1001 + i) for i in range(301)]
    r = runner.run_round(NOW)
    assert r['flood'] is True and wr['inserted'] == []


def test_shadow_mode_never_calls_the_writer(wr, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**WRITE_ENV, 'SCAN_IMPORTER_MODE': 'shadow'}))
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101)]
    runner.run_round(NOW)
    assert wr['inserted'] == []


def held_old(wr, seqs=(101, 102, 103)):
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(s, minutes_ago=3000) for s in seqs]
    runner.run_round(NOW)


def test_release_writes_the_range_with_the_released_label_marks_rows_and_records_who(admin_client, wr):
    held_old(wr)
    r = admin_client.post('/api/importer/release', json={'from': 101, 'to': 103, 'expect': 3})
    assert r.status_code == 200 and r.get_json()['written'] == 3
    writer, rows = wr['inserted'][-1]
    assert writer == 'SCAN-IMPORTER-RELEASED' and len(rows) == 3
    assert database.get_importer_held() == []
    audit = admin_client.get('/api/importer/status').get_json()['audit'][0]
    assert audit['who'] == 'admin' and audit['action'] == 'release' and audit['detail']['written'] == 3


def test_release_with_a_stale_preview_count_is_refused_and_writes_nothing(admin_client, wr):
    held_old(wr)
    wr['existing'] = {(1, 101)}                       # ระหว่างนั้นแถว 101 ถูกเขียนโดยคนอื่น → จะเขียนได้ 2 ไม่ใช่ 3
    r = admin_client.post('/api/importer/release', json={'from': 101, 'to': 103, 'expect': 3})
    assert r.status_code == 409 and 'ดูตัวอย่างใหม่' in r.get_json()['message']
    assert wr['inserted'] == [] and len(database.get_importer_held()) == 3


def test_release_twice_does_not_write_twice(admin_client, wr):
    held_old(wr)
    assert admin_client.post('/api/importer/release', json={'from': 101, 'to': 103, 'expect': 3}).status_code == 200
    wr['existing'] = {(1, 101), (1, 102), (1, 103)}
    r = admin_client.post('/api/importer/release', json={'from': 101, 'to': 103, 'expect': 0})
    assert r.status_code == 200 and r.get_json()['written'] == 0 and sum(len(rows) for _, rows in wr['inserted']) == 3


def test_release_is_refused_outside_write_mode(admin_client, wr, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**WRITE_ENV, 'SCAN_IMPORTER_MODE': 'shadow'}))
    r = admin_client.post('/api/importer/release', json={'from': 1, 'to': 2, 'expect': 0})
    assert r.status_code == 409 and wr['inserted'] == []


def test_release_needs_login_and_a_complete_body(anon_client):
    assert anon_client.post('/api/importer/release', json={}).status_code == 401


def test_release_needs_a_complete_body(admin_client, wr):
    assert admin_client.post('/api/importer/release', json={'from': 1, 'to': 2}).status_code == 400


def test_release_failing_to_write_marks_nothing_released(admin_client, wr):
    held_old(wr)
    wr['insert_fail'] = True
    r = admin_client.post('/api/importer/release', json={'from': 101, 'to': 103, 'expect': 3})
    assert r.status_code == 503 and len(database.get_importer_held()) == 3


# ── ตัวเขียน hosoffice (จำลอง mysql connector) ──
class FakeCur:
    def __init__(self, conn):
        self.conn = conn

    def executemany(self, sql, rows):
        self.conn.log.append(('many', sql, rows))
        if self.conn.fail:
            raise hosoffice.Error('boom')

    def close(self):
        pass


class FakeMy:
    def __init__(self, fail=False):
        self.log, self.fail, self.closed = [], fail, False

    def cursor(self):
        return FakeCur(self)

    def commit(self):
        self.log.append(('commit',))

    def rollback(self):
        self.log.append(('rollback',))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_my(monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load(WRITE_ENV))
    holder = {}

    def connect(**kw):
        holder['kw'] = kw
        holder['conn'] = FakeMy(holder.get('fail', False))
        return holder['conn']

    monkeypatch.setattr(hosoffice.mysql.connector, 'connect', connect)
    return holder


def full_row():
    row = {c: None for c in hosoffice.INSERT_COLS if c != 'CREATE_BY'}
    row.update(MACHIN_SCAN_ID=5, PERSON_ID=9)
    return row


def test_insert_uses_the_write_account_binds_values_labels_the_writer_and_commits_once(fake_my):
    assert hosoffice.insert_scan_rows([full_row(), full_row()], 'SCAN-IMPORTER') == 2
    assert fake_my['kw']['user'] == 'scan_importer_rw' and fake_my['kw']['autocommit'] is False
    _, sql, rows = fake_my['conn'].log[0]
    assert sql.startswith('INSERT INTO `fingle_scan`') and '%s' in sql and rows[0][-1] == 'SCAN-IMPORTER'
    assert fake_my['conn'].log[-1] == ('commit',) and fake_my['conn'].closed


def test_insert_failure_rolls_the_whole_batch_back(fake_my):
    fake_my['fail'] = True
    with pytest.raises(hosoffice.HosofficeWriteError, match='ย้อนกลับ'):
        hosoffice.insert_scan_rows([full_row()], 'SCAN-IMPORTER')
    assert ('rollback',) in fake_my['conn'].log and ('commit',) not in fake_my['conn'].log and fake_my['conn'].closed


def test_insert_without_a_write_account_refuses_and_without_rows_does_nothing(monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**PROD}))
    assert hosoffice.insert_scan_rows([], 'X') == 0
    with pytest.raises(hosoffice.HosofficeWriteError):
        hosoffice.insert_scan_rows([full_row()], 'X')


def test_insert_ignores_any_column_outside_the_whitelist(fake_my):
    row = dict(full_row(), ID=1, EVIL='x); DROP TABLE y;--')
    hosoffice.insert_scan_rows([row], 'W')
    sql = fake_my['conn'].log[0][1]
    assert 'EVIL' not in sql and 'DROP' not in sql and ' ID,' not in sql
