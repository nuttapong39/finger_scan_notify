"""การเปิดแอป: เครื่องหลัก (MONITOR_AUTOSTART=1) เท่านั้นที่เริ่ม Monitor/การเตือนเอง — ADR 0001"""
import pytest

import app as app_module
import database
import reminders
import scanwatch
import serve
import settings
from test_settings import PROD


@pytest.fixture
def calls(tmp_db, monkeypatch):
    log = []
    monkeypatch.setattr(app_module, '_do_start_monitor', lambda: log.append('monitor') or True)
    monkeypatch.setattr(reminders, 'start', lambda: log.append('reminders'))
    monkeypatch.setattr(scanwatch, 'start', lambda: log.append('scanwatch'))
    return log


def test_non_primary_machine_starts_nothing(calls):
    stuck = database.create_backfill_job('2026-11-06', '2026-11-06', 1)
    app_module.startup()
    assert calls == []
    assert database.get_backfill_job(stuck)['status'] == 'error'        # job ค้างถูกปิดตอนเปิดแอป


def test_primary_machine_starts_monitor_and_reminders(calls, monkeypatch):
    monkeypatch.setattr(settings, 'current', settings.load({**PROD, 'MONITOR_AUTOSTART': '1'}))
    app_module.startup()
    assert calls == ['monitor', 'reminders', 'scanwatch']


def test_status_tells_ui_whether_this_is_the_primary_machine(admin_client, monkeypatch):
    assert admin_client.get('/monitor/status').get_json()['primary'] is False
    monkeypatch.setattr(settings, 'current', settings.load({'MONITOR_AUTOSTART': '1'}))
    assert admin_client.get('/monitor/status').get_json()['primary'] is True


def test_layout_has_not_primary_banner_slot(admin_client):
    assert 'id="not-primary-banner"' in admin_client.get('/logs').get_data(as_text=True)


def test_production_trusts_one_proxy_hop(monkeypatch):
    """หลัง nginx: IP จริงของ browser (ใช้นับรหัสผิด) มาจาก X-Forwarded-For ชั้นเดียว"""
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    wsgi = serve.wsgi_app()
    assert (wsgi.x_for, wsgi.x_proto, wsgi.x_host) == (1, 1, 0)


def test_development_does_not_trust_forwarded_for():
    """เครื่องพัฒนาไม่มี nginx ข้างหน้า — เชื่อ X-Forwarded-For = ใครก็ปลอม IP หลบการล็อกได้"""
    assert not hasattr(serve.wsgi_app(), 'x_for')
