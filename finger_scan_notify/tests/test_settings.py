"""ค่าจาก .env: ความลับ, การเชื่อมต่อ hosoffice, เครื่องหลัก (MONITOR_AUTOSTART)"""
import pytest

import database
import hosoffice
import settings

PROD = {'APP_ENV': 'production', 'SECRET_KEY': 'x' * 40,
        'HOSOFFICE_HOST': '10.0.0.5', 'HOSOFFICE_USER': 'ro', 'HOSOFFICE_PASSWORD': 'pw',
        'HOSOFFICE_DATABASE': 'hosoffice'}


# ── settings.load ─────────────────────────────────────────────
def test_development_defaults():
    s = settings.load({})
    assert s.app_env == 'development' and s.is_production is False
    assert len(s.secret_key) >= 32                       # สุ่มให้ ไม่ใช่ค่าตายตัวในโค้ด
    assert s.hosoffice is None                           # ไม่มี .env → ใช้ค่าใน SQLite (เครื่องพัฒนา)
    assert s.monitor_autostart is False                  # เครื่องที่ไม่ได้ตั้ง = ไม่ใช่เครื่องหลัก
    assert (s.listen_host, s.listen_port) == ('127.0.0.1', 5050)


def test_random_secret_differs_per_load():
    assert settings.load({}).secret_key != settings.load({}).secret_key


def test_production_complete():
    s = settings.load(PROD)
    assert s.is_production and s.secret_key == 'x' * 40
    assert s.hosoffice == {'host': '10.0.0.5', 'port': 3306, 'user': 'ro', 'password': 'pw',
                           'database': 'hosoffice'}


@pytest.mark.parametrize('missing', ['SECRET_KEY', 'HOSOFFICE_HOST', 'HOSOFFICE_USER', 'HOSOFFICE_DATABASE'])
def test_production_refuses_to_start_without_required(missing):
    env = {k: v for k, v in PROD.items() if k != missing}
    with pytest.raises(settings.SettingsError, match=missing):
        settings.load(env)


def test_production_rejects_short_secret():
    with pytest.raises(settings.SettingsError, match='SECRET_KEY'):
        settings.load({**PROD, 'SECRET_KEY': 'short'})


@pytest.mark.parametrize('value,expected', [('1', True), ('true', True), ('YES', True), ('0', False),
                                            ('', False), ('no', False)])
def test_monitor_autostart_flag(value, expected):
    assert settings.load({'MONITOR_AUTOSTART': value}).monitor_autostart is expected


def test_bad_port_is_rejected():
    with pytest.raises(settings.SettingsError, match='HOSOFFICE_PORT'):
        settings.load({**PROD, 'HOSOFFICE_PORT': 'abc'})
    with pytest.raises(settings.SettingsError, match='LISTEN_PORT'):
        settings.load({'LISTEN_PORT': '99999'})


def test_env_file_is_read(tmp_path):
    f = tmp_path / '.env'
    f.write_text('MONITOR_AUTOSTART=1\nHOSOFFICE_HOST=db.local\nHOSOFFICE_USER=u\nHOSOFFICE_DATABASE=h\n',
                 encoding='utf-8')
    s = settings.load_file(str(f), environ={})
    assert s.monitor_autostart is True and s.hosoffice['host'] == 'db.local'
    assert s.hosoffice['password'] == ''


# ── การเชื่อมต่อ hosoffice ────────────────────────────────────
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise hosoffice.Error('denied')
        self.conn.executed.append(sql)

    def close(self):
        pass


class FakeConn:
    def __init__(self, fail_on=None):
        self.fail_on, self.executed, self.closed = fail_on, [], False

    def cursor(self, **kw):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def test_connection_params_prefer_env(tmp_db, monkeypatch):
    database.save_db_config('sqlite-host', 3306, 'root', 'old', 'hosoffice', 'fingle_scan', 10)
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    assert hosoffice.connection_params()['host'] == '10.0.0.5'
    monkeypatch.setattr(settings, 'current', settings.load({}))
    p = hosoffice.connection_params()
    assert (p['host'], p['user'], p['password']) == ('sqlite-host', 'root', 'old')


def test_every_connection_is_read_only(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(hosoffice.mysql.connector, 'connect', lambda **kw: conn)
    assert hosoffice.connect_readonly(settings.load(PROD).hosoffice) is conn
    assert conn.executed == ['SET SESSION TRANSACTION READ ONLY']


def test_connection_unusable_if_read_only_fails(monkeypatch):
    conn = FakeConn(fail_on='READ ONLY')
    monkeypatch.setattr(hosoffice.mysql.connector, 'connect', lambda **kw: conn)
    assert hosoffice.connect_readonly(settings.load(PROD).hosoffice) is None     # fail closed
    assert conn.closed


def test_connection_error_returns_none(monkeypatch):
    def boom(**kw):
        raise hosoffice.Error('refused')
    monkeypatch.setattr(hosoffice.mysql.connector, 'connect', boom)
    assert hosoffice.connect_readonly(settings.load(PROD).hosoffice) is None


# ── รหัส MySQL ที่ค้างใน SQLite ───────────────────────────────
def test_stored_password_cleared_when_env_has_connection(tmp_db, monkeypatch):
    database.save_db_config('h', 3306, 'root', 'old-root-pw', 'hosoffice', 'fingle_scan', 10)
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    database.init_db()
    assert database.get_db_config()['password'] == ''


def test_stored_password_kept_without_env(tmp_db):
    database.save_db_config('h', 3306, 'root', 'old-root-pw', 'hosoffice', 'fingle_scan', 10)
    database.init_db()
    assert database.get_db_config()['password'] == 'old-root-pw'
