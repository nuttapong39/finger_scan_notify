import pytest

import app as app_module
import database
import notifier
from flex_theme import DEFAULT_THEME


@pytest.fixture
def client(tmp_db):
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


def header_bg(msg):
    return msg['contents']['body']['contents'][0]['background']


def test_get_theme_returns_saved_and_defaults(client):
    r = client.get('/api/flex-theme').get_json()
    assert r['theme'] == DEFAULT_THEME and r['defaults'] == DEFAULT_THEME


def test_post_valid_theme_is_persisted(client):
    t = {**DEFAULT_THEME, 'checkin_start': '#010203'}
    r = client.post('/api/flex-theme', json=t)
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert database.get_flex_theme()['checkin_start'] == '#010203'


def test_post_invalid_theme_is_rejected_and_not_saved(client):
    r = client.post('/api/flex-theme', json={**DEFAULT_THEME, 'checkin_start': 'red', 'angle': 999})
    assert r.status_code == 400
    body = r.get_json()
    assert body['success'] is False and {'checkin_start', 'angle'} <= set(body['errors'])
    assert database.get_flex_theme() == DEFAULT_THEME


def test_post_without_json_is_400(client):
    assert client.post('/api/flex-theme', data='x').status_code == 400


def test_preview_uses_draft_theme_without_saving(client):
    draft = {**DEFAULT_THEME, 'checkin_start': '#0A0B0C'}
    r = client.post('/api/flex-preview', json={'theme': draft, 'scan_type': '0'})
    assert r.status_code == 200
    assert header_bg(r.get_json()['messages'][0])['startColor'] == '#0A0B0C'
    assert database.get_flex_theme() == DEFAULT_THEME


def test_preview_checkout_and_saved_theme_default(client):
    database.save_flex_theme({**DEFAULT_THEME, 'checkout_start': '#0D0E0F'})
    r = client.post('/api/flex-preview', json={'scan_type': '1'})
    assert r.status_code == 200
    assert header_bg(r.get_json()['messages'][0])['startColor'] == '#0D0E0F'


def test_preview_invalid_theme_is_400(client):
    r = client.post('/api/flex-preview', json={'theme': {**DEFAULT_THEME, 'angle': -5}})
    assert r.status_code == 400


def test_preview_rejects_unknown_scan_type(client):
    assert client.post('/api/flex-preview', json={'scan_type': 'zzz'}).status_code == 400


class _Resp:
    status_code = 200
    text = '{"ok":true}'


def _token():
    database.save_token(7, 'ทดสอบ', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    return database.get_all_tokens()[0]['id']


def _capture(monkeypatch):
    sent = {}
    monkeypatch.setattr(notifier.requests, 'post',
                        lambda url, **kw: sent.update(body=kw['data'].decode('utf-8')) or _Resp())
    return sent


def test_test_notify_sends_draft_theme(client, monkeypatch):
    tid = _token()
    sent = _capture(monkeypatch)
    draft = {**DEFAULT_THEME, 'checkin_start': '#AA00AA'}
    r = client.post(f'/api/test-notify/{tid}', json={'theme': draft})
    assert r.get_json()['success'] is True
    assert '#AA00AA' in sent['body']
    assert database.get_flex_theme() == DEFAULT_THEME           # draft ไม่ถูกบันทึก


def test_test_notify_without_body_uses_saved_theme(client, monkeypatch):
    tid = _token()
    database.save_flex_theme({**DEFAULT_THEME, 'checkin_start': '#BB11BB'})
    sent = _capture(monkeypatch)
    assert client.post(f'/api/test-notify/{tid}').get_json()['success'] is True
    assert '#BB11BB' in sent['body']


def test_test_notify_invalid_draft_sends_nothing(client, monkeypatch):
    tid = _token()
    monkeypatch.setattr(notifier.requests, 'post',
                        lambda *a, **k: pytest.fail('ต้องไม่ส่งเมื่อ draft ไม่ถูกต้อง'))
    r = client.post(f'/api/test-notify/{tid}', json={'theme': {**DEFAULT_THEME, 'angle': 'x'}})
    assert r.status_code == 400 and r.get_json()['success'] is False


def test_test_notify_scan_type_selects_checkout_colors(client, monkeypatch):
    tid = _token()
    sent = _capture(monkeypatch)
    client.post(f'/api/test-notify/{tid}', json={'scan_type': '1'})
    assert DEFAULT_THEME['checkout_start'] in sent['body']
    assert DEFAULT_THEME['checkin_start'] not in sent['body']


def test_dashboard_lists_only_active_moph_tokens_without_secrets(client):
    database.save_token(7, 'คนมี Flex', 'MOPH_ALERT', '', 'ck-SECRET', 'sk-SECRET', '', 1, 1)
    database.save_token(8, 'คน Line', 'LINE_NOTIFY', 'line-SECRET', '', '', '', 1, 1)
    html = client.get('/dashboard').get_data(as_text=True)
    assert 'คนมี Flex' in html
    assert 'คน Line' not in html
    assert 'SECRET' not in html
