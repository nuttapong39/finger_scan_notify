import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import hosoffice  # noqa: E402
import settings  # noqa: E402


@pytest.fixture(autouse=True)
def dev_settings(monkeypatch):
    """test ไม่อ่าน .env ของเครื่อง — เริ่มจากค่าเครื่องพัฒนาเสมอ"""
    monkeypatch.setattr(settings, 'current', settings.load({}))


@pytest.fixture(autouse=True)
def no_real_mysql(monkeypatch):
    """test ห้ามต่อ hosoffice จริง — ทุก query ล้มเหลวเหมือน DB ล่ม เว้นแต่ test จะ mock เอง"""
    monkeypatch.setattr(hosoffice, 'get_mysql_connection', lambda: None)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """SQLite ชั่วคราวต่อ test — ไม่แตะ data/app_config.db จริง"""
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'test.db'))
    database.init_db()
    return database.DB_PATH


NEW_PASSWORD = 'test-admin-pass-123'


@pytest.fixture
def anon_client(tmp_db):
    """client ที่ยังไม่ล็อกอิน (ส่ง Origin ของระบบเหมือน browser จริง)"""
    import app as app_module
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()
    c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
    return c


@pytest.fixture
def admin_account(tmp_db):
    """บัญชี admin ที่เปลี่ยนรหัสชั่วคราวแล้ว (รหัส = NEW_PASSWORD)"""
    import auth
    temp = auth.create_admin()
    auth.change_password(temp, NEW_PASSWORD, NEW_PASSWORD)


@pytest.fixture
def admin_client(anon_client, admin_account):
    """client ที่ล็อกอินเป็น admin แล้ว — ผ่านหน้า /login จริง"""
    r = anon_client.post('/login', data={'username': 'admin', 'password': NEW_PASSWORD})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    return anon_client
