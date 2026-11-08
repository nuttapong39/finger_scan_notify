"""ช่วง A ส่วนที่ต่อกับของเดิม: เตือนยังไม่สแกนงดเมื่อ Import stall, Monitor อ่านซ้ำแถวที่มาช้า, หน้า Dashboard,
การ์ดแจ้งเตือนระบบ (delivery)"""
import json
from datetime import datetime, timedelta

import pytest

import app as app_module
import backfill
import cards
import database
import delivery
import hosoffice
import notifier
import reminders
import scanwatch

FRI = datetime(2026, 11, 6)


def at(h, m=0, day=FRI):
    return day.replace(hour=h, minute=m)


class Resp:
    status_code, text = 200, '{"message":"Succesfully"}'


# ── การ์ดแจ้งเตือนระบบ ────────────────────────────────────────
def test_system_alert_card_is_a_flex_message_with_title_and_lines():
    msgs = cards.build_system_alert_card('problem', 'Scan ตกหล่น 2 รายการ', ['เลข 12', 'เลข 13'], 'รพ.ทดสอบ', '', at(8, 30))
    flex = msgs[0]
    assert flex['type'] == 'flex' and 'แจ้งเตือนระบบ' in flex['altText'] and 'Scan ตกหล่น 2 รายการ' in flex['altText']
    blob = json.dumps(flex, ensure_ascii=False)
    assert 'เลข 12' in blob and 'เลข 13' in blob and 'แจ้งเตือนระบบ' in blob


def test_system_alert_card_colours_differ_by_kind():
    blobs = {k: json.dumps(cards.build_system_alert_card(k, 't', ['x'], 'h', '', at(8)), ensure_ascii=False)
             for k in ('problem', 'recovered', 'summary')}
    assert len(set(blobs.values())) == 3
    with pytest.raises(ValueError):
        cards.build_system_alert_card('nope', 't', [], 'h', '', at(8))


def test_deliver_system_alert_posts_to_the_token_and_is_not_counted_as_a_scan_notification(tmp_db, monkeypatch):
    database.save_token(156, 'IT', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    sent = []
    monkeypatch.setattr(notifier.requests, 'post', lambda url, **kw: sent.append(kw) or Resp())
    ok, code, _ = delivery.deliver_system_alert('problem', 'หัวข้อ', ['บรรทัด'], database.get_token_by_person_id(156), now=at(8))
    assert ok and code == 200 and len(sent) == 1
    assert database.get_notify_logs() == []                        # ไม่ปนสถิติการส่ง Scan / โควตา
    assert database.count_recent_notified(156, 60) == 0
    assert any(e['kind'] == 'it_alert' for e in database.get_guard_events())


def test_deliver_system_alert_refuses_a_token_without_keys(tmp_db):
    database.save_token(5, 'x', 'MOPH_ALERT', '', '', '', '', 1, 1)
    ok, code, why = delivery.deliver_system_alert('problem', 't', [], database.get_token_by_person_id(5), now=at(8))
    assert (ok, code, why) == (False, 0, 'no_keys')


# ── เตือนยังไม่สแกน งดเมื่อ Import stall ───────────────────────
@pytest.fixture
def rem(tmp_db, monkeypatch):
    database.save_token(1, 'คน1', 'MOPH_ALERT', '', 'ck', 'sk', 'งาน', 1, 1)
    database.set_remind_enabled([1], True)
    c = database.get_reminder_config()['checkin']
    database.save_reminder_config('checkin', 1, c['remind_at'], c['remind_until'], '', c['button_label'])
    st = {'sent': [], 'stalled': False}
    monkeypatch.setattr(hosoffice, 'is_holiday', lambda d: False)
    monkeypatch.setattr(hosoffice, 'get_persons_on_leave', lambda d, pids: set())
    monkeypatch.setattr(hosoffice, 'get_muted_person_ids', lambda: set())
    monkeypatch.setattr(hosoffice, 'get_scans_on', lambda d, pids: [])
    monkeypatch.setattr(scanwatch, 'import_stalled', lambda now: st['stalled'])
    monkeypatch.setattr(notifier.requests, 'post', lambda url, **kw: st['sent'].append(kw) or Resp())
    monkeypatch.setattr(reminders, 'THROTTLE_SECONDS', 0)
    return st


def test_reminder_is_held_back_during_an_import_stall_and_sent_when_data_returns(rem):
    rem['stalled'] = True
    report = reminders.run_cycle(at(10, 0))
    assert report['checkin']['status'] == 'import_stall' and rem['sent'] == []
    assert database.get_reminder_log('2026-11-06') == []             # ไม่ claim → ลองใหม่ได้
    rem['stalled'] = False
    report = reminders.run_cycle(at(10, 1))
    assert report['checkin']['reminded'] == [1] and len(rem['sent']) == 1


def test_dry_run_preview_ignores_the_stall(rem):
    rem['stalled'] = True
    report = reminders.run_cycle(at(10, 0), dry_run=True)
    assert report['checkin']['status'] == 'ok' and report['checkin']['would_remind'] == [1]


# ── Monitor อ่านซ้ำแถวที่มาช้า ──────────────────────────────────
@pytest.fixture
def mon(tmp_db, monkeypatch):
    database.save_token(7, 'คน7', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    database.update_last_scan_id(100)
    st = {'scans': [], 'sent': [], 'late_calls': []}
    monkeypatch.setattr(app_module, 'get_new_scans_from_mysql',
                        lambda last_id, limit: [s for s in st['scans'] if s['ID'] > last_id])

    def late(lo, hi, pids):
        st['late_calls'].append((lo, hi, sorted(pids)))
        return [s for s in st['scans'] if lo < s['ID'] <= hi and s['PERSON_ID'] in pids]

    monkeypatch.setattr(app_module, 'get_scans_in_id_range', late)
    monkeypatch.setattr(app_module, 'get_muted_person_ids', lambda: set())
    monkeypatch.setattr(notifier.requests, 'post', lambda url, **kw: st['sent'].append(kw['data']) or Resp())
    app_module.monitor_status.update(running=True, total_notified=0, errors=0)
    yield st
    app_module.monitor_status['running'] = False


def scan(sid, pid=7, minutes_ago=1, code='0'):
    return {'ID': sid, 'PERSON_ID': pid, 'HR_FULLNAME': f'คน{pid}', 'SCAN_TYPE': code,
            'SCAN_DATE_TIME': datetime.now() - timedelta(minutes=minutes_ago), 'ISINPUT': 'FACESCAN'}


def status(sid):
    return backfill.delivery_status([sid])[sid]


def test_a_row_committed_late_with_a_lower_id_is_still_notified_exactly_once(mon):
    mon['scans'] = [scan(101)]
    app_module.monitor_cycle()                                       # รอบแรกเห็น 101 เท่านั้น
    assert len(mon['sent']) == 1 and database.get_last_scan_id() == 101
    mon["scans"].append(scan(99, minutes_ago=3))           # ID 99: commit ช้ากว่า 101 แต่ ID ต่ำกว่า
    app_module.monitor_cycle()
    assert len(mon['sent']) == 2 and status(99)['status'] == 'success'
    app_module.monitor_cycle()
    app_module.monitor_cycle()
    assert len(mon['sent']) == 2                                     # ไม่ส่งซ้ำ
    assert database.get_last_scan_id() == 101                        # จุดตรวจไม่ถอยหลัง


def test_late_rows_are_handled_even_when_there_is_nothing_new(mon):
    database.update_last_scan_id(105)
    mon['scans'] = [scan(103)]
    app_module.monitor_cycle()
    assert status(103)['status'] == 'success'


def test_a_late_row_older_than_the_stale_limit_is_recorded_as_skipped_not_sent(mon):
    database.update_last_scan_id(105)
    mon['scans'] = [scan(103, minutes_ago=600)]
    app_module.monitor_cycle()
    assert mon['sent'] == [] and status(103)['detail'] == 'stale'
    app_module.monitor_cycle()
    assert status(103)['detail'] == 'stale'                          # บันทึกแล้วไม่อ่านซ้ำ


def test_rows_of_people_without_a_token_are_never_reread_or_recorded(mon):
    database.update_last_scan_id(105)
    mon['scans'] = [scan(103, pid=99)]
    app_module.monitor_cycle()
    assert mon['sent'] == [] and status(103)['status'] == 'never'
    assert all(call[2] == [7] for call in mon['late_calls'])          # ถามเฉพาะคนที่มี token


def test_monitor_survives_when_the_late_row_query_fails(mon, monkeypatch):
    def boom(lo, hi, pids):
        raise hosoffice.HosofficeUnavailable('x')

    monkeypatch.setattr(app_module, 'get_scans_in_id_range', boom)
    mon['scans'] = [scan(101)]
    app_module.monitor_cycle()
    assert len(mon['sent']) == 1


# ── หน้า Dashboard + API ──────────────────────────────────────
def test_watch_page_and_status_api_show_events_and_state(admin_client, monkeypatch):
    database.open_watch_event('lost_scan', 365000, {'prev_time': '2026-11-06 08:00:00', 'next_time': '2026-11-06 08:02:00'}, 1.0)
    database.save_watch_state(max_seq=365010, last_new_at=2.0, last_poll_at=3.0)
    page = admin_client.get('/watch')
    assert page.status_code == 200 and 'Scan ที่อาจตกหล่น' in page.get_data(as_text=True)
    body = admin_client.get('/api/watch/status').get_json()
    assert body['state']['max_seq'] == 365010
    assert [e['seq'] for e in body['events']] == [365000] and body['events'][0]['open'] is True
    assert 'it_person_id' in body['state']


def test_duplicates_api_lists_pairs_of_rows_sharing_a_device_sequence(admin_client, monkeypatch):
    rows = [
        {'ID': 11, 'seq': 365100, 'PERSON_ID': 5, 'HR_FULLNAME': 'ก ข', 'SCAN_DATE_TIME': datetime(2026, 11, 4, 8, 0, 0)},
        {'ID': 12, 'seq': 365100, 'PERSON_ID': 5, 'HR_FULLNAME': 'ก ข', 'SCAN_DATE_TIME': datetime(2026, 11, 4, 8, 0, 33)},
        {'ID': 20, 'seq': 365200, 'PERSON_ID': 6, 'HR_FULLNAME': 'ค ง', 'SCAN_DATE_TIME': datetime(2026, 11, 4, 9, 0, 0)},
        {'ID': 21, 'seq': 365200, 'PERSON_ID': 6, 'HR_FULLNAME': 'ค ง', 'SCAN_DATE_TIME': datetime(2026, 11, 4, 9, 0, 41)},
    ]
    monkeypatch.setattr(hosoffice, 'get_duplicate_device_sequences', lambda days=60: rows)
    body = admin_client.get('/api/watch/duplicates').get_json()
    assert body['success'] and [g['seq'] for g in body['groups']] == [365100, 365200]
    assert [r['id'] for r in body['groups'][0]['rows']] == [11, 12]
    assert body['groups'][0]['rows'][1]['time'] == '2026-11-04 08:00:33'


def test_duplicates_api_reports_when_hosoffice_is_down(admin_client, monkeypatch):
    def boom(days=60):
        raise hosoffice.HosofficeUnavailable('down')

    monkeypatch.setattr(hosoffice, 'get_duplicate_device_sequences', boom)
    r = admin_client.get('/api/watch/duplicates')
    assert r.status_code == 503 and r.get_json()['success'] is False


def test_it_recipient_can_be_changed_only_to_a_person_with_a_token(admin_client):
    database.save_token(156, 'IT', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    database.save_token(200, 'อีกคน', 'MOPH_ALERT', '', 'ck2', 'sk2', '', 1, 1)
    ok = admin_client.post('/api/watch/config', json={'it_person_id': 200})
    assert ok.status_code == 200 and database.get_watch_state()['it_person_id'] == 200
    for bad in ({'it_person_id': 999}, {'it_person_id': 'x'}, {}):
        r = admin_client.post('/api/watch/config', json=bad)
        assert r.status_code == 400
    assert database.get_watch_state()['it_person_id'] == 200


def test_watch_pages_require_login(tmp_db):
    c = app_module.app.test_client()
    c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
    assert c.get('/watch').status_code == 302
    assert c.get('/api/watch/status').status_code == 401
    assert c.get('/api/watch/duplicates').status_code == 401


def test_the_scan_watcher_starts_with_the_primary_machine_only(tmp_db, monkeypatch):
    started = []
    monkeypatch.setattr(scanwatch, 'start', lambda: started.append('watch'))
    monkeypatch.setattr(reminders, 'start', lambda: None)
    monkeypatch.setattr(app_module, '_do_start_monitor', lambda: True)
    import settings
    monkeypatch.setattr(settings, 'current', settings.load({'MONITOR_AUTOSTART': '1'}))
    app_module.startup()
    assert started == ['watch']
    started.clear()
    monkeypatch.setattr(settings, 'current', settings.load({}))
    app_module.startup()
    assert started == []
