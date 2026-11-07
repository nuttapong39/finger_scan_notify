"""Monitor (แจ้งเตือนสด): กฎ scan_types, บันทึกเหตุผลการข้าม, เคารพ mute, โควตาเฉพาะ live"""
from datetime import datetime, timedelta

import pytest

import app as app_module
import backfill
import database
import hosoffice
import notifier


class Resp:
    status_code, text = 200, '{"message":"Succesfully"}'


@pytest.fixture
def mon(tmp_db, monkeypatch):
    database.save_token(7, 'คน7', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    database.update_last_scan_id(100)
    state = {'scans': [], 'muted': set(), 'sent': []}
    monkeypatch.setattr(app_module, 'get_new_scans_from_mysql',
                        lambda last_id, limit: [s for s in state['scans'] if s['ID'] > last_id])
    monkeypatch.setattr(app_module, 'get_muted_person_ids', lambda: set(state['muted']))
    monkeypatch.setattr(notifier.requests, 'post', lambda url, **kw: state['sent'].append(kw['data']) or Resp())
    app_module.monitor_status.update(running=True, total_notified=0, errors=0)
    yield state
    app_module.monitor_status['running'] = False


def scan(sid, code='0', pid=7, minutes_ago=1):
    return {'ID': sid, 'PERSON_ID': pid, 'HR_FULLNAME': f'คน{pid}', 'SCAN_TYPE': code,
            'SCAN_DATE_TIME': datetime.now() - timedelta(minutes=minutes_ago), 'ISINPUT': 'FACESCAN'}


def status(sid):
    return backfill.delivery_status([sid])[sid]


def test_out_scan_is_sent_as_checkout(mon):
    mon['scans'] = [scan(101, 'OUT')]
    app_module.monitor_cycle()
    assert len(mon['sent']) == 1 and 'ออกงาน' in mon['sent'][0].decode('utf-8')
    assert status(101)['status'] == 'success' and status(101)['source'] == 'live'


def test_skips_are_recorded_with_reasons(mon):
    mon['scans'] = [scan(101, '9'), scan(102, '0', minutes_ago=600), scan(103, '0', pid=99, minutes_ago=600)]
    app_module.monitor_cycle()
    assert mon['sent'] == []
    assert status(101) == {**status(101), 'status': 'skipped', 'detail': 'unknown_type'}
    assert status(102)['detail'] == 'stale'
    assert status(103)['status'] == 'never'          # ไม่มี token → ไม่บันทึก
    assert database.get_last_scan_id() == 103


def test_muted_person_is_not_notified(mon):
    mon['muted'] = {7}
    mon['scans'] = [scan(101)]
    app_module.monitor_cycle()
    assert mon['sent'] == [] and status(101)['detail'] == 'muted'


def test_duplicate_and_type_off(mon):
    database.save_token(7, 'คน7', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 0)     # ปิดแจ้งออกงาน
    first = scan(101)
    mon['scans'] = [first, {**first, 'ID': 102}, scan(103, '1')]
    app_module.monitor_cycle()
    assert len(mon['sent']) == 1
    assert status(102)['detail'] == 'duplicate' and status(103)['detail'] == 'type_off'


def test_backfill_sends_do_not_block_live_notifications(mon, monkeypatch):
    for i in range(10):                                   # ส่งย้อนหลังไป 10 ข้อความในชั่วโมงนี้
        database.add_notify_log(i, 7, 'คน7', 'เข้างาน', f'2026-11-0{i % 5 + 1} 08:00:0{i}', 'MOPH_ALERT',
                                'success', 200, '', source='backfill')
    mon['scans'] = [scan(101)]
    app_module.monitor_cycle()
    assert len(mon['sent']) == 1


def test_mute_lookup_failure_sends_nothing(mon, monkeypatch):
    def down():
        raise hosoffice.HosofficeUnavailable('down')
    monkeypatch.setattr(app_module, 'get_muted_person_ids', down)
    mon['scans'] = [scan(101)]
    with pytest.raises(hosoffice.HosofficeUnavailable):
        app_module.monitor_cycle()
    assert mon['sent'] == []
