"""หน้า ตั้งค่าฐานข้อมูล: ไม่ส่งรหัส MySQL ออกไปที่ browser และเมื่อใช้ .env แก้ค่าการเชื่อมต่อไม่ได้ (Q6)"""
import database
import hosoffice
import settings
from test_settings import PROD

FORM = {'host': 'new-host', 'port': '3307', 'username': 'u2', 'password': '', 'database_name': 'db2',
        'table_name': 'scan_tbl', 'poll_interval': '15'}


def test_page_never_contains_stored_password(admin_client):
    database.save_db_config('h', 3306, 'root', 'TOP-SECRET-PW', 'hosoffice', 'fingle_scan', 10)
    html = admin_client.get('/config/database').get_data(as_text=True)
    assert 'TOP-SECRET-PW' not in html


def test_blank_password_keeps_existing(admin_client):
    database.save_db_config('h', 3306, 'root', 'old-pw', 'hosoffice', 'fingle_scan', 10)
    admin_client.post('/config/database', data=FORM)
    cfg = database.get_db_config()
    assert cfg['password'] == 'old-pw' and cfg['host'] == 'new-host' and cfg['poll_interval'] == 15
    admin_client.post('/config/database', data={**FORM, 'password': 'changed'})
    assert database.get_db_config()['password'] == 'changed'


def test_env_mode_shows_env_values_read_only(admin_client, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    html = admin_client.get('/config/database').get_data(as_text=True)
    assert '10.0.0.5' in html and 'ตั้งค่าใน .env' in html
    assert 'name="password"' not in html and 'name="host"' not in html
    assert 'name="poll_interval"' in html and 'name="table_name"' in html


def test_env_mode_post_changes_only_non_secret_values(admin_client, monkeypatch):
    database.save_db_config('h', 3306, 'root', '', 'hosoffice', 'fingle_scan', 10)
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    admin_client.post('/config/database', data={**FORM, 'password': 'injected'})
    cfg = database.get_db_config()
    assert (cfg['host'], cfg['username'], cfg['password']) == ('h', 'root', '')
    assert (cfg['table_name'], cfg['poll_interval']) == ('scan_tbl', 15)


def test_env_mode_test_button_uses_env_connection(admin_client, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    seen = []
    monkeypatch.setattr(hosoffice, 'connect_readonly',
                        lambda params, raise_errors=False: seen.append(params) or None)
    r = admin_client.post('/config/database', data={**FORM, 'test': '1'}).get_json()
    assert seen == [settings.current.hosoffice] and r['success'] is False


def test_poll_interval_minimum(admin_client):
    admin_client.post('/config/database', data={**FORM, 'poll_interval': '1'})
    assert database.get_db_config()['poll_interval'] == 10
