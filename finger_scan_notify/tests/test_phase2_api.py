"""API ของหน้า ตรวจสอบและส่งย้อนหลัง / เตือนยังไม่สแกน"""
from datetime import datetime

import pytest

import backfill
import database
import hosoffice
import notifier


class Resp:
    status_code, text = 200, '{"message":"Succesfully"}'


TODAY = datetime.now().date().isoformat()


def scan(sid, pid=7, code='0'):
    return {'ID': sid, 'PERSON_ID': pid, 'HR_FULLNAME': f'คน{pid}', 'SCAN_TYPE': code,
            'SCAN_DATE_TIME': datetime.now().replace(microsecond=0), 'ISINPUT': 'FACESCAN'}


SCANS = {1: scan(1), 2: scan(2, code='1'), 3: scan(3, pid=8)}


@pytest.fixture
def client(admin_client, monkeypatch):
    database.save_token(7, 'คน7', 'MOPH_ALERT', '', 'ck-SECRET', 'sk-SECRET', 'งาน', 1, 1)
    database.save_token(8, 'คน8', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    monkeypatch.setattr(hosoffice, 'get_scans_between',
                        lambda d1, d2, pids: [s for s in SCANS.values() if s['PERSON_ID'] in pids])
    monkeypatch.setattr(hosoffice, 'get_scans_by_ids', lambda ids: [SCANS[i] for i in ids if i in SCANS])
    monkeypatch.setattr(hosoffice, 'get_muted_person_ids', lambda: set())
    monkeypatch.setattr(backfill, 'THROTTLE_SECONDS', 0)
    sent = []
    monkeypatch.setattr(notifier.requests, 'post', lambda url, **kw: sent.append(kw['data']) or Resp())
    c = admin_client
    c.sent = sent
    return c


def body(**kw):
    return {'date_from': TODAY, 'date_to': TODAY, **kw}


# ── หน้าเว็บ ──────────────────────────────────────────────────
@pytest.mark.parametrize('path', ['/backfill', '/reminders', '/dashboard'])
def test_pages_render_without_secrets(client, path):
    r = client.get(path)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'SECRET' not in html
    if path != '/dashboard':
        assert 'คน7' in html


# ── Recheck ───────────────────────────────────────────────────
def test_status_lists_rows_with_delivery_status(client):
    database.add_notify_log(1, 7, 'คน7', 'เข้างาน', 'x', 'MOPH_ALERT', 'success', 200, '')
    r = client.post('/api/backfill/status', json=body(person_ids=[7]))
    rows = {x['id']: x for x in r.get_json()['rows']}
    assert set(rows) == {1, 2}
    assert rows[1]['status'] == 'success' and rows[2]['status'] == 'never' and rows[2]['default_checked']


@pytest.mark.parametrize('payload', [
    body(person_ids=[]), body(person_ids='7'), body(person_ids=['x']),
    {'date_from': '2099-01-01', 'date_to': '2099-01-01', 'person_ids': [7]},
    {'date_from': 'bad', 'date_to': TODAY, 'person_ids': [7]},
])
def test_status_rejects_bad_input(client, payload):
    assert client.post('/api/backfill/status', json=payload).status_code == 400


def test_status_reports_hosoffice_down(client, monkeypatch):
    def down(*a):
        raise hosoffice.HosofficeUnavailable('down')
    monkeypatch.setattr(hosoffice, 'get_scans_between', down)
    assert client.post('/api/backfill/status', json=body(person_ids=[7])).status_code == 503


# ── Preview + job ─────────────────────────────────────────────
def test_preview_then_job(client, monkeypatch):
    p = client.post('/api/backfill/preview', json=body(scan_ids=[1, 2, 3])).get_json()
    assert p['messages'] == 3 and p['persons'] == 2 and p['needs_confirm'] is False
    assert client.sent == []
    started = []
    real = backfill.start_job
    monkeypatch.setattr(backfill, 'start_job',
                        lambda *a, **k: started.append(1) or real(*a, **{**k, 'run_async': False}))
    r = client.post('/api/backfill/jobs', json=body(scan_ids=[1, 2, 3]))
    job_id = r.get_json()['job_id']
    job = client.get(f'/api/backfill/jobs/{job_id}').get_json()['job']
    assert job['status'] == 'done' and job['sent'] == 3 and len(client.sent) == 3


def test_job_errors_are_400(client):
    r = client.post('/api/backfill/jobs', json=body(scan_ids=[]))
    assert r.status_code == 400 and 'ไม่มีรายการ' in r.get_json()['message']
    old = {'date_from': '2020-01-01', 'date_to': TODAY, 'scan_ids': [1]}
    r = client.post('/api/backfill/jobs', json=old)
    assert r.status_code == 400 and 'ยืนยัน' in r.get_json()['message']


def test_job_not_found(client):
    assert client.get('/api/backfill/jobs/999').status_code == 404


# ── Reminders ─────────────────────────────────────────────────
def test_reminder_config_roundtrip_and_validation(client):
    ok = {'checkin': {'enabled': True, 'remind_at': '09:15', 'remind_until': '11:00',
                      'button_url': 'https://hos.example/scan', 'button_label': 'สแกนเลย'}}
    r = client.post('/api/reminders/config', json=ok)
    assert r.get_json()['config']['checkin']['remind_at'] == '09:15'
    bad = {'checkout': {'enabled': True, 'remind_at': '18:00', 'remind_until': '17:00',
                        'button_url': 'http://insecure', 'button_label': 'x'}}
    r = client.post('/api/reminders/config', json=bad)
    assert r.status_code == 400 and set(r.get_json()['errors']['checkout']) == {'remind_until', 'button_url'}
    assert database.get_reminder_config()['checkout']['remind_at'] == '17:00'      # ไม่ถูกบันทึก


def test_recipients_bulk_toggle(client):
    r = client.post('/api/reminders/recipients', json={'person_ids': [7, 8], 'enabled': True})
    assert r.get_json()['updated'] == 2
    assert all(t['remind_enabled'] for t in database.get_all_tokens())


def test_reminder_preview_fails_closed_when_hosoffice_down(client):
    database.set_remind_enabled([7], True)
    r = client.post('/api/reminders/preview').get_json()
    # วันเสาร์-อาทิตย์จะได้ weekend ; วันทำการ: ต่อ MySQL ไม่ได้ (conftest) → error ไม่ใช่ "เตือนทุกคน"
    assert r['report']['checkin']['status'] in ('error', 'weekend')
    assert client.sent == []


@pytest.mark.parametrize('variant', ['scan', 'backfill', 'remind_in', 'remind_out'])
def test_flex_preview_variants(client, variant):
    r = client.post('/api/flex-preview', json={'variant': variant})
    assert r.status_code == 200 and r.get_json()['messages'][0]['type'] == 'flex'


def test_test_notify_reminder_variant(client):
    tid = [t for t in database.get_all_tokens() if t['person_id'] == 7][0]['id']
    r = client.post(f'/api/test-notify/{tid}', json={'variant': 'remind_in'})
    assert r.get_json()['success'] is True
    assert 'ยังไม่พบการสแกนเข้างาน' in client.sent[0].decode('utf-8')
