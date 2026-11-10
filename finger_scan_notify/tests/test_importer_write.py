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
    monkeypatch.setattr(tigersoft, 'get_rows', lambda seqs: {r['seq']: r for r in st['rows'] if r['seq'] in set(seqs)})
    st['names'] = {'7': 'สมชาย ใจดี'}
    monkeypatch.setattr(tigersoft, 'get_rows_between', lambda start, end, limit=5000: [r for r in st['rows'] if start <= r['device_time'] < end][:limit])
    monkeypatch.setattr(hosoffice, 'get_names_by_fingle_ids', lambda ids: {str(i): st['names'][str(i)] for i in ids if str(i) in st['names']})
    st['profiles'] = {}
    monkeypatch.setattr(hosoffice, 'get_hr_profiles', lambda ids: {i: st['profiles'][i] for i in ids if i in st['profiles']})
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


def test_a_new_employee_without_earlier_rows_is_written_from_the_hr_data(wr):
    HR_NEW = dict(TPL, HR_FULLNAME='ใหม่   พนักงาน')
    wr['profiles'] = {1008: HR_NEW}                           # คน 8: ไม่มีแถวเก่า แต่มีข้อมูล HR ครบ
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101, person=8), src(102, person=7)]
    r = runner.run_round(NOW)
    assert r['inserted'] == 2 and r['held'] == 0
    by_seq = {x['MACHIN_SCAN_ID']: x for x in wr['inserted'][0][1]}
    assert by_seq[101]['HR_FULLNAME'] == 'ใหม่   พนักงาน' and by_seq[102]['HR_FULLNAME'] == 'n'     # คนเดิมที่ HR ไม่ครบ/อ่านไม่ได้ ใช้แถวเก่าสำรอง


def test_the_current_hr_data_wins_over_an_earlier_row(wr):
    """ตัวเดิมอ่าน HR สดทุกครั้ง: คนที่ HR เปลี่ยนตำแหน่ง/แผนกหลังแถวล่าสุดของตัวเดิม ต้องได้ค่าใหม่ ไม่ค้างค่าเก่าหลังตัวเดิมหยุด"""
    wr['profiles'] = {1007: dict(TPL, HR_FULLNAME='จาก HR', HR_POSITION_NAME='ตำแหน่งใหม่')}
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101)]
    runner.run_round(NOW)
    row = wr['inserted'][0][1][0]
    assert row['HR_FULLNAME'] == 'จาก HR' and row['HR_POSITION_NAME'] == 'ตำแหน่งใหม่'


def test_an_earlier_row_is_the_fallback_when_the_hr_data_is_incomplete(wr):
    wr['profiles'] = {}                                       # HR ของคน 7 ไม่ครบ → ไม่มีโปรไฟล์
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101)]
    r = runner.run_round(NOW)
    assert r['inserted'] == 1 and wr['inserted'][0][1][0]['HR_FULLNAME'] == 'n'


def test_an_earlier_row_is_the_fallback_when_the_account_cannot_read_the_hr_tables(wr, monkeypatch):
    monkeypatch.setattr(hosoffice, 'get_hr_profiles', lambda ids: None)       # ไม่มีสิทธิ์ตาราง HR
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101)]
    r = runner.run_round(NOW)
    assert r['inserted'] == 1 and wr['inserted'][0][1][0]['HR_FULLNAME'] == 'n'


# ── นำเข้าตามเลขที่ระบุเอง (สแกนที่ตกหล่นก่อนจุดเริ่มต้น) ──
def fresh(seq, person=7, machine=1):
    t = datetime.now() - timedelta(minutes=5)
    return {'seq': seq, 'machine': machine, 'person': person, 'time': t, 'device_time': t.replace(second=0), 'mode': '0'}


def test_manual_preview_reads_the_scanner_and_writes_nothing(admin_client, wr):
    wr['rows'] = [fresh(201), fresh(202), fresh(203, person=9)]       # 9 ไม่มีใน hr_person → สร้างแถวไม่ได้
    wr['existing'] = {(1, 202)}
    d = admin_client.get('/api/importer/manual-preview?from=200&to=203').get_json()
    assert (d['on_scanner'], d['not_on_scanner'], d['will_write'], d['already_present'], d['unreleasable']) == (3, 1, 1, 1, [203])
    assert d['writer'] == 'SCAN-IMPORTER-MANUAL' and wr['inserted'] == []


def test_manual_preview_says_whose_scan_it_is_when_and_in_or_out(admin_client, wr):
    wr['rows'] = [fresh(201), dict(fresh(202), mode='1'), fresh(203, person=9)]       # 202 = ออกงาน ; 9 ไม่มีใน hr_person
    wr['existing'] = {(1, 202)}
    d = admin_client.get('/api/importer/manual-preview?from=201&to=203').get_json()
    by_seq = {r['seq']: r for r in d['rows']}
    assert [r['seq'] for r in d['rows']] == [201, 202, 203]
    a, b, c = by_seq[201], by_seq[202], by_seq[203]
    assert (a['status'], a['direction'], a['person_id'], a['name'], a['machine']) == ('will_write', 'เข้างาน', 1007, 'สมชาย ใจดี', 1)
    assert len(a['time']) == 19 and a['time'][10] == ' '                              # YYYY-MM-DD HH:MM:SS (ถึงวินาที)
    assert (b['status'], b['direction']) == ('present', 'ออกงาน')
    assert (c['status'], c['reason'], c['person_id']) == ('unwritable', 'no_person', None)


def test_manual_preview_lists_at_most_fifty_rows(admin_client, wr):
    wr['rows'] = [fresh(1000 + i) for i in range(80)]
    d = admin_client.get('/api/importer/manual-preview?from=1000&to=1079').get_json()
    assert len(d['rows']) == 50 and d['rows_truncated'] is True and d['will_write'] == 80


def test_manual_preview_and_import_accept_a_list_of_sequences(admin_client, wr):
    wr['rows'] = [fresh(s) for s in range(201, 211)]
    d = admin_client.get('/api/importer/manual-preview?seqs=201,205,207').get_json()
    assert d['on_scanner'] == 3 and d['will_write'] == 3 and [r['seq'] for r in d['rows']] == [201, 205, 207]
    r = admin_client.post('/api/importer/manual-import', json={'seqs': [201, 205, 207], 'expect': 3})
    assert r.status_code == 200 and r.get_json()['written'] == 3
    assert [x['MACHIN_SCAN_ID'] for x in wr['inserted'][-1][1]] == [201, 205, 207]           # ไม่แตะเลขอื่นที่อยู่ระหว่างกลาง


def test_a_sequence_list_is_validated(admin_client, wr):
    too_many = ','.join(str(i) for i in range(1, 202))
    assert admin_client.get(f'/api/importer/manual-preview?seqs={too_many}').status_code == 400
    assert admin_client.get('/api/importer/manual-preview?seqs=a,b').status_code == 400
    assert admin_client.post('/api/importer/manual-import', json={'seqs': [], 'expect': 0}).status_code == 400
    assert admin_client.post('/api/importer/manual-import', json={'seqs': ['x'], 'expect': 0}).status_code == 400


def test_missing_scans_are_found_by_key_and_scans_newer_than_fifteen_minutes_are_set_aside(admin_client, wr):
    def at_ago(seq, minutes, person=7):
        t = datetime.now() - timedelta(minutes=minutes)
        return {'seq': seq, 'machine': 1, 'person': person, 'time': t, 'device_time': t.replace(second=0), 'mode': '0'}
    wr['rows'] = [at_ago(301, 60), at_ago(302, 60), at_ago(303, 5), at_ago(304, 60, person=9)]
    wr['existing'] = {(1, 302)}                                       # 302 มีแล้ว ; 303 ใหม่เกินไป (ตัวเดิมยังไม่ทันเขียน) ; 304 ไม่มีใน hr_person
    d = admin_client.get('/api/importer/missing?days=1').get_json()
    assert (d['scanned'], d['missing'], d['too_recent'], d['will_write'], d['unwritable']) == (4, 2, 1, 1, 1)
    assert d['seqs'] == [301] and [(r['seq'], r['status']) for r in d['rows']] == [(301, 'will_write'), (304, 'unwritable')]
    assert wr['inserted'] == []                                       # ค้นหาไม่เขียนอะไร


def test_missing_search_works_in_shadow_mode_and_rejects_bad_windows(admin_client, wr, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**WRITE_ENV, 'SCAN_IMPORTER_MODE': 'shadow'}))
    assert admin_client.get('/api/importer/missing?days=1').status_code == 200
    assert admin_client.get('/api/importer/missing?days=8').status_code == 400
    assert admin_client.get('/api/importer/missing?days=0').status_code == 400
    assert admin_client.get('/api/importer/missing?days=x').status_code == 400


def test_missing_search_reports_an_unreachable_scanner(admin_client, wr, monkeypatch):
    def down(*a, **k):
        raise tigersoft.TigerUnavailable('เครื่องสแกนต่อไม่ได้')
    monkeypatch.setattr(tigersoft, 'get_rows_between', down)
    assert admin_client.get('/api/importer/missing?days=1').status_code == 503


def test_missing_search_needs_login(anon_client, wr):
    assert anon_client.get('/api/importer/missing?days=1').status_code == 401


def test_manual_import_writes_with_the_manual_label_and_records_who(admin_client, wr):
    wr['rows'] = [fresh(201), fresh(202)]
    r = admin_client.post('/api/importer/manual-import', json={'from': 201, 'to': 202, 'expect': 2})
    assert r.status_code == 200 and r.get_json()['written'] == 2
    writer, rows = wr['inserted'][-1]
    assert writer == 'SCAN-IMPORTER-MANUAL' and [x['MACHIN_SCAN_ID'] for x in rows] == [201, 202]
    audit = admin_client.get('/api/importer/status').get_json()['audit'][0]
    assert audit['action'] == 'manual-import' and audit['who'] == 'admin' and audit['detail']['written'] == 2


def test_manual_import_with_a_stale_count_or_outside_write_mode_writes_nothing(admin_client, wr, monkeypatch):
    wr['rows'] = [fresh(201), fresh(202)]
    assert admin_client.post('/api/importer/manual-import', json={'from': 201, 'to': 202, 'expect': 1}).status_code == 409
    monkeypatch.setattr(settings, 'current', settings.load({**WRITE_ENV, 'SCAN_IMPORTER_MODE': 'shadow'}))
    assert admin_client.post('/api/importer/manual-import', json={'from': 201, 'to': 202, 'expect': 2}).status_code == 409
    assert wr['inserted'] == []


def test_manual_import_twice_does_not_write_twice(admin_client, wr):
    wr['rows'] = [fresh(201)]
    assert admin_client.post('/api/importer/manual-import', json={'from': 201, 'to': 201, 'expect': 1}).status_code == 200
    wr['existing'] = {(1, 201)}
    r = admin_client.post('/api/importer/manual-import', json={'from': 201, 'to': 201, 'expect': 0})
    assert r.get_json()['written'] == 0 and sum(len(rows) for _, rows in wr['inserted']) == 1


def test_manual_import_rejects_oversized_ranges_and_incomplete_bodies(admin_client, wr):
    assert admin_client.get('/api/importer/manual-preview?from=1&to=100000').status_code == 400
    assert admin_client.get('/api/importer/manual-preview?from=5&to=1').status_code == 400
    assert admin_client.post('/api/importer/manual-import', json={'from': 1, 'to': 2}).status_code == 400


def test_manual_import_needs_login(anon_client, wr):
    assert anon_client.get('/api/importer/manual-preview?from=1&to=2').status_code == 401
    assert anon_client.post('/api/importer/manual-import', json={}).status_code == 401


def test_manual_import_clears_a_matching_held_row(admin_client, wr):
    database.save_importer_state(checkpoint=100)
    wr['rows'] = [src(101, minutes_ago=3000)]
    runner.run_round(NOW)                                             # 101 ถูกกัก (เก่าเกิน 24 ชม.)
    assert len(database.get_importer_held()) == 1
    wr['rows'] = [fresh(101)]                                          # เครื่องสแกนมีเลข 101 (เวลาปัจจุบันจำลอง เพื่อไม่ชนเพดานอายุ)
    assert admin_client.post('/api/importer/manual-import', json={'from': 101, 'to': 101, 'expect': 1}).status_code == 200
    assert database.get_importer_held() == []


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


def test_a_seqs_import_records_the_exact_list_in_the_audit(admin_client, wr):
    wr['rows'] = [fresh(s) for s in range(201, 211)]
    admin_client.post('/api/importer/manual-import', json={'seqs': [201, 205, 207], 'expect': 3})
    d = admin_client.get('/api/importer/status').get_json()['audit'][0]['detail']
    assert d['seqs'] == [201, 205, 207] and d['written'] == 3                    # ไม่ใช่แค่ช่วง 201–207


def test_non_numeric_input_gets_a_neutral_message_not_the_raw_python_error(admin_client, wr):
    m = admin_client.get('/api/importer/manual-preview?seqs=a,b').get_json()['message']
    assert m == 'ระบุเลขเป็นตัวเลข' and 'invalid literal' not in m
    assert 'เกิน' in admin_client.get('/api/importer/manual-preview?from=1&to=100000').get_json()['message']      # ข้อความตรวจอินพุตของเราแสดงตามจริง
