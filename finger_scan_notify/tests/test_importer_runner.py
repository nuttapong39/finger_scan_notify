"""รอบของ Scan importer ตัวใหม่ (โหมดเงา) และ API บนหน้า Watch — ไม่เขียน hosoffice ไม่ว่ากรณีใด
ทดสอบผ่าน runner.run_round และ /api/importer/* โดยจำลองเฉพาะเครื่องสแกนกับ hosoffice"""
from datetime import datetime, timedelta

import pytest

import database
import hosoffice
import settings
import tigersoft
from scan_importer import runner
from test_settings import PROD

NOW = datetime(2026, 11, 9, 10, 0, 0)


def src(seq, minutes_ago=5, machine=1, person=7):
    t = (NOW - timedelta(minutes=minutes_ago)).replace(second=14)
    return {'seq': seq, 'machine': machine, 'person': person, 'time': t, 'device_time': t.replace(second=0), 'mode': '0'}


@pytest.fixture
def imp(tmp_db, monkeypatch):
    st = {'rows': [], 'existing': set(), 'tiger_fail': False, 'hos_fail': False, 'latest': 500, 'writes': 0}
    monkeypatch.setattr(settings, 'current', settings.load({**PROD, 'SCAN_IMPORTER_MODE': 'shadow', 'TIGER_HOST': 'h',
                                                            'TIGER_DATABASE': 'd', 'TIGER_USER': 'u', 'TIGER_PASSWORD': 'pw'}))

    def rows_after(seq, limit=3000):
        if st['tiger_fail']:
            raise tigersoft.TigerUnavailable('เครื่องสแกนต่อไม่ได้')
        return [r for r in st['rows'] if r['seq'] > seq]

    def existing(since):
        if st['hos_fail']:
            raise hosoffice.HosofficeUnavailable('hosoffice ล่ม')
        return set(st['existing'])

    TPL = {'HR_FULLNAME': 'n', 'HR_POSITION_NAME': 'p', 'HR_LEAVEL_NAME': None, 'HR_DEP_NAME': 'd', 'HR_DEP_SUB_NAME': 's',
           'HR_PERSON_TYPE_NAME': 't'}
    st.update(inserted=[], insert_fail=False, people={'7': 1007, '8': 1008}, templates={1007: TPL})

    def insert(rows, writer):
        if st['insert_fail']:
            raise hosoffice.HosofficeWriteError('เขียนไม่ได้ (ย้อนกลับแล้ว)')
        st['inserted'].append((writer, list(rows)))
        return len(rows)

    monkeypatch.setattr(hosoffice, 'get_person_ids', lambda ids: {str(i): st['people'][str(i)] for i in ids if str(i) in st['people']})
    monkeypatch.setattr(hosoffice, 'get_latest_rows_by_person', lambda ids: {i: st['templates'][i] for i in ids if i in st['templates']})
    monkeypatch.setattr(hosoffice, 'insert_scan_rows', insert)
    st['profiles'] = {}
    monkeypatch.setattr(hosoffice, 'get_hr_profiles', lambda ids: {i: st['profiles'][i] for i in ids if i in st['profiles']})
    monkeypatch.setattr(tigersoft, 'get_rows_after', rows_after)
    monkeypatch.setattr(tigersoft, 'get_latest_seq', lambda: st['latest'])
    monkeypatch.setattr(hosoffice, 'get_existing_keys', existing)
    return st


def test_when_the_importer_is_off_nothing_happens(imp, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**PROD}))
    assert runner.run_round(NOW) == {'status': 'off'}
    assert database.get_importer_state()['last_round_at'] is None


def test_without_a_checkpoint_it_asks_for_a_baseline_and_does_not_read_the_scanner(imp, monkeypatch):
    monkeypatch.setattr(tigersoft, 'get_rows_after', lambda *a, **k: pytest.fail('ห้ามอ่านเครื่องสแกนโดยไม่มีจุดเริ่มต้น'))
    assert runner.run_round(NOW)['status'] == 'needs_baseline'
    assert database.get_importer_state()['checkpoint'] is None


def test_a_normal_round_counts_what_it_would_write_and_moves_the_checkpoint(imp):
    database.save_importer_state(checkpoint=100)
    imp['rows'] = [src(101), src(102), src(103)]
    imp['existing'] = {(1, 102)}
    r = runner.run_round(NOW)
    assert (r['status'], r['would_insert'], r['duplicate'], r['held']) == ('ok', 2, 1, 0)
    st = database.get_importer_state()
    assert st['checkpoint'] == 103 and st['last_counts']['would_insert'] == 2 and st['last_error'] is None


def test_old_rows_are_held_with_a_reason_and_remembered_for_a_later_release(imp):
    database.save_importer_state(checkpoint=100)
    imp['rows'] = [src(101), src(102, minutes_ago=3000)]
    r = runner.run_round(NOW)
    assert r['held'] == 1 and r['would_insert'] == 1
    held = database.get_importer_held()
    assert [(h['seq'], h['reason']) for h in held] == [(102, 'old')]
    runner.run_round(NOW)                                   # รอบซ้ำ ไม่เพิ่มแถวกักซ้ำ
    assert len(database.get_importer_held()) == 1


def test_a_flood_holds_the_whole_round(imp):
    database.save_importer_state(checkpoint=1000)
    imp['rows'] = [src(1001 + i) for i in range(301)]
    r = runner.run_round(NOW)
    assert r['flood'] is True and r['would_insert'] == 0 and r['held'] == 301
    assert {h['reason'] for h in database.get_importer_held()} == {'flood'}


@pytest.mark.parametrize('which', ['tiger_fail', 'hos_fail'])
def test_when_a_database_is_down_the_round_is_recorded_and_the_checkpoint_stays(imp, which):
    database.save_importer_state(checkpoint=100)
    imp['rows'] = [src(101)]
    imp[which] = True
    assert runner.run_round(NOW)['status'] == 'error'
    st = database.get_importer_state()
    assert st['checkpoint'] == 100 and st['last_error']
    imp[which] = False
    assert runner.run_round(NOW)['would_insert'] == 1 and database.get_importer_state()['checkpoint'] == 101


def test_the_round_never_writes_to_hosoffice(imp, monkeypatch):
    for name in ('_query',):
        monkeypatch.setattr(hosoffice, name, lambda *a, **k: pytest.fail('ไม่ควร query ตรง ๆ ในโหมดเงา'))
    database.save_importer_state(checkpoint=100)
    imp['rows'] = [src(101)]
    assert runner.run_round(NOW)['status'] == 'ok'


# ── API ───────────────────────────────────────────────────────
def test_api_needs_login(anon_client):
    assert anon_client.get('/api/importer/status').status_code == 401
    assert anon_client.post('/api/importer/baseline').status_code == 401
    assert anon_client.get('/api/importer/release-preview?from=1&to=2').status_code == 401


def test_status_shows_mode_checkpoint_and_held_summary_without_personal_data(admin_client, imp):
    database.save_importer_state(checkpoint=100)
    imp['rows'] = [src(101, person=424242), src(102, minutes_ago=3000, person=424242)]       # 424242 ไม่มีใน hr_person
    runner.run_round(NOW)
    d = admin_client.get('/api/importer/status').get_json()
    assert d['mode'] == 'shadow' and d['checkpoint'] == 102 and d['can_write'] is False
    assert d['held']['count'] == 2 and d['held']['reasons'] == {'old': 1, 'no_person': 1}
    assert d['held']['people_without_template'] == 1
    assert '424242' not in admin_client.get('/api/importer/status').get_data(as_text=True)


def test_shadow_mode_holds_rows_it_could_not_build_but_never_writes(imp):
    database.save_importer_state(checkpoint=100)
    imp['rows'] = [src(101), src(102, person=8)]            # 8 มี PERSON_ID แต่ยังไม่เคยมีแถวต้นแบบ
    r = runner.run_round(NOW)
    assert (r['would_insert'], r['held']) == (1, 1) and imp['inserted'] == []
    assert [(h['seq'], h['reason']) for h in database.get_importer_held()] == [(102, 'no_template')]


def test_baseline_sets_the_checkpoint_to_the_scanner_latest_once(admin_client, imp):
    r = admin_client.post('/api/importer/baseline')
    assert r.status_code == 200 and database.get_importer_state()['checkpoint'] == 500
    imp['latest'] = 900
    assert admin_client.post('/api/importer/baseline').status_code == 409
    assert database.get_importer_state()['checkpoint'] == 500


def test_totals_accumulate_across_rounds_and_a_quiet_round_does_not_erase_what_was_seen(admin_client, imp):
    admin_client.post('/api/importer/baseline')                                  # จุดเริ่มต้น 500
    imp['rows'] = [src(501), src(502)]
    imp['existing'] = {(1, 501)}
    runner.run_round(NOW)
    runner.run_round(NOW + timedelta(seconds=15))                                # รอบเงียบ: ไม่มีแถวใหม่
    t = admin_client.get('/api/importer/status').get_json()['totals']
    assert (t['read'], t['existing'], t['would_insert'], t['held'], t['inserted']) == (2, 1, 1, 0, 0)
    assert t['last_new_seq'] == 502 and t['last_new_at'] and t['since']
    imp['rows'].append(src(503, minutes_ago=3000))                               # แถวเก่า → กัก
    runner.run_round(NOW + timedelta(seconds=30))
    t = admin_client.get('/api/importer/status').get_json()['totals']
    assert (t['read'], t['would_insert'], t['held'], t['last_new_seq']) == (3, 1, 1, 503)


def test_baseline_is_refused_when_the_importer_is_off(admin_client, imp, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**PROD}))
    assert admin_client.post('/api/importer/baseline').status_code == 409


def test_release_preview_summarises_without_writing_and_skips_rows_already_present(admin_client, imp):
    database.save_importer_state(checkpoint=100)
    imp['rows'] = [src(101, minutes_ago=3000), src(102, minutes_ago=3000), src(103, minutes_ago=2900)]
    runner.run_round(NOW)
    imp['existing'] = {(1, 102)}
    d = admin_client.get('/api/importer/release-preview?from=101&to=103').get_json()
    assert d['dry_run'] is True and d['held_in_range'] == 3 and d['will_write'] == 2 and d['already_present'] == 1
    assert d['writer'] == 'SCAN-IMPORTER-RELEASED' and sum(d['per_day'].values()) == 2
    assert len(database.get_importer_held()) == 3                 # ดูตัวอย่างไม่เปลี่ยนอะไร


@pytest.mark.parametrize('qs', ['', '?from=a&to=2', '?from=5&to=2'])
def test_release_preview_rejects_a_bad_range(admin_client, imp, qs):
    assert admin_client.get('/api/importer/release-preview' + qs).status_code == 400


def test_the_watch_page_has_the_importer_card_with_a_disabled_release_button(admin_client, imp):
    html = admin_client.get('/watch').get_data(as_text=True)
    assert 'id="imp-card"' in html and 'id="imp-release"' in html and '/api/importer/status' in html
