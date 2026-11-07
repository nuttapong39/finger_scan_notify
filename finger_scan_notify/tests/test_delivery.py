import json
from datetime import datetime

import pytest

import database
import delivery
import notifier


class Resp:
    def __init__(self, code=200, text='{"message":"Succesfully"}'):
        self.status_code, self.text = code, text


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        body = kw.get('data')
        calls.append({'url': url, 'headers': kw.get('headers'),
                      'body': body.decode('utf-8') if isinstance(body, bytes) else body})
        return Resp()
    monkeypatch.setattr(notifier.requests, 'post', fake_post)
    return calls


def scan(scan_type='0', sid=11, pid=7):
    return {'ID': sid, 'PERSON_ID': pid, 'HR_FULLNAME': 'สมชาย', 'SCAN_TYPE': scan_type,
            'SCAN_DATE_TIME': datetime(2026, 11, 6, 8, 3, 12), 'ISINPUT': 'FACESCAN'}


MOPH = {'person_id': 7, 'person_name': 'สมชาย', 'provider': 'MOPH_ALERT', 'client_key': 'ck',
        'secret_key': 'sk', 'notify_checkin': 1, 'notify_checkout': 1, 'department': 'งานไอที'}
LINE = {'person_id': 7, 'provider': 'LINE_NOTIFY', 'line_token': 'lt', 'notify_checkin': 1, 'notify_checkout': 1}


def logs():
    conn = database.get_sqlite_conn()
    rows = [dict(r) for r in conn.execute('SELECT * FROM notify_logs ORDER BY id')]
    conn.close()
    return rows


@pytest.mark.parametrize('code,token,expected', [
    ('9', MOPH, 'unknown_type'),
    ('0', {**MOPH, 'notify_checkin': 0}, 'type_off'),
    ('OUT', {**MOPH, 'notify_checkout': 0}, 'type_off'),
    ('0', {**MOPH, 'secret_key': ''}, 'no_keys'),
    ('0', {**LINE, 'line_token': ''}, 'no_keys'),
    ('0', MOPH, None), ('OUT', MOPH, None), ('1', LINE, None),
])
def test_scan_skip_reason(code, token, expected):
    assert delivery.scan_skip_reason(scan(code), token) == expected


def test_type_flags_can_be_ignored_for_test_sends():
    assert delivery.scan_skip_reason(scan('0'), {**MOPH, 'notify_checkin': 0}, check_type_flags=False) is None


def test_live_moph_send_logs_source_and_label(tmp_db, sent):
    ok, code, _ = delivery.deliver_scan(scan('OUT'), MOPH, source='live')
    assert ok and code == 200
    body = json.loads(sent[0]['body'])
    assert body['messages'][0]['type'] == 'flex'
    assert sent[0]['headers']['client-key'] == 'ck'
    row = logs()[0]
    assert (row['scan_id'], row['scan_type'], row['source'], row['status']) == (11, 'ออกงาน', 'live', 'success')
    assert row['scan_datetime'] == '2026-11-06 08:03:12'


def test_backfill_send_uses_backfill_card_and_job_id(tmp_db, sent):
    delivery.deliver_scan(scan('0'), MOPH, source='backfill', backfill=True, job_id=5,
                          now=datetime(2026, 11, 6, 14, 5))
    assert '📨 ส่งย้อนหลัง · ส่งเมื่อ 14:05' in sent[0]['body']
    row = logs()[0]
    assert (row['source'], row['job_id']) == ('backfill', 5)


def test_skipped_scan_is_not_sent_or_logged(tmp_db, sent):
    ok, code, reason = delivery.deliver_scan(scan('9'), MOPH, source='live')
    assert (ok, code, reason) == (False, 0, 'unknown_type')
    assert sent == [] and logs() == []


def test_failed_send_is_logged_as_error(tmp_db, monkeypatch):
    monkeypatch.setattr(notifier.requests, 'post', lambda *a, **k: Resp(401, '{"message":"Unauthorized"}'))
    ok, code, resp = delivery.deliver_scan(scan('0'), MOPH, source='live')
    assert not ok and code == 401
    assert logs()[0]['status'] == 'error'


def test_line_notify_backfill_text(tmp_db, sent):
    delivery.deliver_scan(scan('0'), LINE, source='backfill', backfill=True)
    assert 'ส่งย้อนหลัง' in sent[0]['body']['message']


def test_invalid_source_rejected(tmp_db, sent):
    with pytest.raises(ValueError):
        delivery.deliver_scan(scan('0'), MOPH, source='reminder')


def test_quota_counts_only_live_sends(tmp_db, sent):
    delivery.deliver_scan(scan('0', sid=1), MOPH, source='backfill', backfill=True)
    delivery.deliver_scan(scan('0', sid=2), MOPH, source='test')
    assert database.count_recent_notified(7, 60) == 0
    delivery.deliver_scan(scan('0', sid=3), MOPH, source='live')
    assert database.count_recent_notified(7, 60) == 1


def test_reminder_moph_card_with_button(tmp_db, sent):
    cfg = {'button_url': 'https://hos.example/scan', 'button_label': 'สแกนเข้างานตอนนี้'}
    ok, _, _ = delivery.deliver_reminder('checkin', MOPH, cfg, now=datetime(2026, 11, 6, 9, 30))
    assert ok
    body = json.loads(sent[0]['body'])
    flat = json.dumps(body, ensure_ascii=False)
    assert 'ยังไม่พบการสแกนเข้างาน' in flat and 'https://hos.example/scan' in flat and 'งานไอที' in flat
    assert logs() == []          # reminder ไม่ปน notify_logs


def test_reminder_without_keys(tmp_db, sent):
    assert delivery.deliver_reminder('checkin', {**MOPH, 'client_key': ''}, {}) == (False, 0, 'no_keys')
    assert sent == []
