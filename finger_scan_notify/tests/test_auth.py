"""ล็อกอิน Admin: บัญชีเดียว, รหัสชั่วคราว → บังคับเปลี่ยน, กันเดารหัส, session หมดอายุ, กัน POST ข้ามเว็บ"""
import time

import pytest

import app as app_module
import auth
from conftest import NEW_PASSWORD

PAGES_ALLOWED_WHILE_MUST_CHANGE = {'/dashboard'}


def login(client, username='admin', password=NEW_PASSWORD, **extra):
    return client.post('/login', data={'username': username, 'password': password, **extra})


# ── บัญชีและรหัส ──────────────────────────────────────────────
def test_create_admin_returns_temporary_password_stored_as_hash(tmp_db):
    temp = auth.create_admin()
    assert len(temp) >= 12
    acc = auth.get_account()
    assert acc['username'] == 'admin' and acc['must_change'] == 1
    assert temp not in acc['password_hash'] and acc['password_hash'].startswith(('scrypt:', 'pbkdf2:'))


def test_create_admin_does_not_overwrite_existing(tmp_db):
    auth.create_admin()
    with pytest.raises(auth.AuthError, match='reset-admin'):
        auth.create_admin()


def test_reset_admin_issues_new_temp_and_forces_change(admin_client):
    temp = auth.create_admin(reset=True)
    assert auth.get_account()['must_change'] == 1
    assert admin_client.get('/api/logs').status_code == 401              # session เดิมหลุด

    def fresh():
        c = admin_client.application.test_client()
        c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
        return c
    assert login(fresh(), password=NEW_PASSWORD).status_code == 401      # รหัสเดิมใช้ไม่ได้
    assert login(fresh(), password=temp).status_code == 302


@pytest.mark.parametrize('current,new,confirm,msg', [
    ('wrong-current', 'abcdefghij12', 'abcdefghij12', 'รหัสปัจจุบันไม่ถูกต้อง'),
    (None, 'short', 'short', 'อย่างน้อย 10'),
    (None, 'abcdefghij12', 'abcdefghij13', 'ไม่ตรงกัน'),
    (None, None, None, 'ซ้ำกับรหัสเดิม'),
])
def test_password_rules(tmp_db, current, new, confirm, msg):
    temp = auth.create_admin()
    current = temp if current is None else current
    new = temp if new is None else new
    confirm = temp if confirm is None else confirm
    with pytest.raises(auth.AuthError, match=msg):
        auth.change_password(current, new, confirm)
    assert auth.get_account()['must_change'] == 1


def test_change_password_clears_must_change_and_old_password_stops_working(tmp_db):
    temp = auth.create_admin()
    auth.change_password(temp, NEW_PASSWORD, NEW_PASSWORD)
    assert auth.get_account()['must_change'] == 0
    assert auth.verify_password('admin', temp) is False
    assert auth.verify_password('admin', NEW_PASSWORD) is True


# ── ไม่ล็อกอิน = เข้าไม่ได้ทุก route ──────────────────────────
def _all_routes():
    for rule in app_module.app.url_map.iter_rules():
        if rule.endpoint in ('static', 'auth.login'):
            continue
        url = rule.rule
        for arg in rule.arguments:
            url = url.replace(f'<int:{arg}>', '1').replace(f'<{arg}>', '1')
        method = 'GET' if 'GET' in rule.methods else sorted(rule.methods - {'HEAD', 'OPTIONS'})[0]
        yield rule.endpoint, url, method


@pytest.mark.parametrize('endpoint,url,method', list(_all_routes()))
def test_every_route_requires_login(anon_client, endpoint, url, method):
    r = anon_client.open(url, method=method, json={})
    assert r.status_code in (302, 401), f'{endpoint} {method} {url} → {r.status_code}'
    if r.status_code == 302:
        assert '/login' in r.headers['Location']


def test_api_without_login_gets_json_401(anon_client):
    r = anon_client.get('/api/logs')
    assert r.status_code == 401 and r.get_json()['login_required'] is True


def test_login_page_is_public(anon_client):
    r = anon_client.get('/login')
    assert r.status_code == 200 and 'name="password"' in r.get_data(as_text=True)


# ── ล็อกอิน ───────────────────────────────────────────────────
def test_login_success_and_logout(anon_client, admin_account):
    r = login(anon_client)
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')
    assert anon_client.get('/api/logs').status_code == 200
    assert anon_client.post('/logout').status_code == 302
    assert anon_client.get('/api/logs').status_code == 401


def test_login_wrong_password(anon_client, admin_account):
    r = login(anon_client, password='nope-nope-nope')
    assert r.status_code == 401 and 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง' in r.get_data(as_text=True)
    assert login(anon_client, username='root').status_code == 401


def test_login_next_only_allows_local_paths(anon_client, admin_account):
    assert login(anon_client, next='/backfill').headers['Location'].endswith('/backfill')
    anon_client.post('/logout')
    for evil in ('//evil.example', 'https://evil.example/x', '/\\evil.example'):
        assert login(anon_client, next=evil).headers['Location'].endswith('/dashboard')
        anon_client.post('/logout')


def test_lockout_after_5_failures_for_15_minutes(anon_client, admin_account, monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(auth, '_now', lambda: clock[0])
    for _ in range(5):
        assert login(anon_client, password='bad-password-x').status_code == 401
    r = login(anon_client)                                   # รหัสถูกก็เข้าไม่ได้ระหว่างถูกล็อก
    assert r.status_code == 429 and 'ลองใหม่' in r.get_data(as_text=True)
    clock[0] += 15 * 60 + 1
    assert login(anon_client).status_code == 302


def test_success_resets_failure_count(anon_client, admin_account):
    for _ in range(4):
        login(anon_client, password='bad-password-x')
    login(anon_client)
    anon_client.post('/logout')
    for _ in range(4):
        login(anon_client, password='bad-password-x')
    assert login(anon_client).status_code == 302


# ── บังคับเปลี่ยนรหัส ─────────────────────────────────────────
@pytest.fixture
def temp_client(anon_client, tmp_db):
    temp = auth.create_admin()
    assert login(anon_client, password=temp).status_code == 302
    anon_client.temp_password = temp
    return anon_client


def test_must_change_shows_unclosable_popup_on_dashboard(temp_client):
    html = temp_client.get('/dashboard').get_data(as_text=True)
    assert 'id="force-password-modal"' in html and 'เปลี่ยนรหัสผ่านก่อนใช้งาน' in html


@pytest.mark.parametrize('endpoint,url,method', [x for x in _all_routes()
                                                 if not x[0].startswith('auth.') and x[1] != '/dashboard'])
def test_must_change_blocks_everything_else(temp_client, endpoint, url, method):
    r = temp_client.open(url, method=method, **({} if method == 'GET' else {'json': {}}))
    if method == 'GET' and not url.startswith(('/api/', '/monitor/')) and url != '/':
        assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard'), endpoint
    else:
        assert r.status_code in (302, 403), f'{endpoint} → {r.status_code}'


def test_must_change_api_answer(temp_client):
    r = temp_client.get('/api/logs')
    assert r.status_code == 403 and r.get_json()['must_change_password'] is True


def test_change_password_via_api_unlocks(temp_client):
    r = temp_client.post('/account/password', json={'current': temp_client.temp_password,
                                                    'new': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert temp_client.get('/api/logs').status_code == 200
    assert 'id="force-password-modal"' not in temp_client.get('/dashboard').get_data(as_text=True)


def test_change_password_errors_are_400(temp_client):
    r = temp_client.post('/account/password', json={'current': temp_client.temp_password,
                                                    'new': 'short', 'confirm': 'short'})
    assert r.status_code == 400 and 'อย่างน้อย 10' in r.get_json()['message']


def test_password_page_available_after_change(admin_client):
    html = admin_client.get('/account/password').get_data(as_text=True)
    assert 'รหัสผ่านปัจจุบัน' in html


# ── session ───────────────────────────────────────────────────
def test_password_change_signs_out_other_sessions(admin_client):
    other = admin_client.application.test_client()               # browser อีกเครื่อง
    other.environ_base['HTTP_ORIGIN'] = 'http://localhost'
    assert login(other).status_code == 302
    r = admin_client.post('/account/password', json={'current': NEW_PASSWORD, 'new': 'another-pass-99',
                                                     'confirm': 'another-pass-99'})
    assert r.status_code == 200
    assert other.get('/api/logs').status_code == 401
    assert admin_client.get('/api/logs').status_code == 200          # คนที่เปลี่ยนเองยังอยู่


def test_idle_8_hours_logs_out(admin_client):
    with admin_client.session_transaction() as s:
        s['last_active'] = time.time() - 8 * 3600 - 5
    assert admin_client.get('/logs').status_code == 302


def test_background_polling_does_not_count_as_activity(admin_client):
    with admin_client.session_transaction() as s:
        s['last_active'] = before = time.time() - 3600
    admin_client.get('/monitor/status')
    with admin_client.session_transaction() as s:
        assert s['last_active'] == before
    admin_client.get('/logs')
    with admin_client.session_transaction() as s:
        assert s['last_active'] > before


def test_session_cookie_flags(anon_client, admin_account):
    cookie = login(anon_client).headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie


# ── กัน POST ข้ามเว็บ ─────────────────────────────────────────
def test_post_from_other_origin_is_rejected(admin_client):
    r = admin_client.post('/api/flex-preview', json={}, headers={'Origin': 'http://evil.example'})
    assert r.status_code == 403


def test_post_without_origin_or_referer_is_rejected(admin_client):
    admin_client.environ_base.pop('HTTP_ORIGIN')
    assert admin_client.post('/api/flex-preview', json={}).status_code == 403
    r = admin_client.post('/api/flex-preview', json={}, headers={'Referer': 'http://localhost/dashboard'})
    assert r.status_code == 200


def test_login_post_also_checks_origin(anon_client, admin_account):
    r = anon_client.post('/login', data={'username': 'admin', 'password': NEW_PASSWORD},
                         headers={'Origin': 'http://evil.example'})
    assert r.status_code == 403
