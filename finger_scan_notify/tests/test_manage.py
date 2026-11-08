"""คำสั่งบน Server: init-admin / reset-admin / backup / can-restart / check"""
import os
import sqlite3

import pytest

import auth
import database
import hosoffice
import manage
import settings
from test_settings import PROD, FakeConn


def run(capsys, *args):
    code = manage.main(list(args))
    return code, capsys.readouterr().out


# ── admin ─────────────────────────────────────────────────────
def test_init_admin_prints_temp_once(tmp_db, capsys):
    code, out = run(capsys, 'init-admin')
    acc = auth.get_account()
    assert code == 0 and acc['must_change'] == 1
    temp = [ln.split(':', 1)[1].strip() for ln in out.splitlines() if 'รหัสชั่วคราว' in ln][0]
    assert auth.verify_password('admin', temp)
    code, out = run(capsys, 'init-admin')
    assert code == 1 and 'reset-admin' in out


def test_reset_admin(tmp_db, capsys, admin_account):
    code, out = run(capsys, 'reset-admin')
    assert code == 0 and auth.get_account()['must_change'] == 1 and 'รหัสชั่วคราว' in out


def test_reset_admin_without_account_creates_one(tmp_db, capsys):
    assert run(capsys, 'reset-admin')[0] == 0 and auth.get_account()


# ── backup ────────────────────────────────────────────────────
def test_backup_creates_readable_copy_and_keeps_n(tmp_db, tmp_path, capsys):
    database.save_token(7, 'คน7', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    out_dir = tmp_path / 'backups'
    for i in range(4):
        (out_dir / f'app_config-2026010{i}-000000.db').parent.mkdir(exist_ok=True)
        (out_dir / f'app_config-2026010{i}-000000.db').write_bytes(b'old')
    code, out = run(capsys, 'backup', '--keep=3', f'--dir={out_dir}')
    files = sorted(os.listdir(out_dir))
    assert code == 0 and len(files) == 3 and 'สำรองแล้ว' in out
    newest = out_dir / files[-1]
    conn = sqlite3.connect(newest)
    assert conn.execute('SELECT COUNT(*) FROM person_tokens').fetchone()[0] == 1
    conn.close()
    if os.name == 'posix':
        assert os.stat(newest).st_mode & 0o777 == 0o600


# ── can-restart (deploy.sh ถามก่อน restart) ───────────────────
def test_can_restart_blocks_while_backfill_running(tmp_db, capsys):
    assert run(capsys, 'can-restart')[0] == 0
    database.create_backfill_job('2026-11-06', '2026-11-06', 5)
    code, out = run(capsys, 'can-restart')
    assert code == 1 and 'ส่งย้อนหลัง' in out


# ── reset-checkpoint (หลังกู้ไฟล์สำรอง: ไม่ส่ง scan เก่าซ้ำ) ───
def test_reset_checkpoint(tmp_db, capsys):
    database.advance_last_scan_id(340245)
    code, out = run(capsys, 'reset-checkpoint')
    assert code == 0 and database.get_last_scan_id() == 0 and 'ล่าสุด' in out


# ── check ─────────────────────────────────────────────────────
class CheckCursor:
    def __init__(self, conn):
        self.conn, self.result = conn, None

    def execute(self, sql, params=()):
        self.conn.executed.append(sql)
        if 'transaction_read_only' in sql:
            self.result = (self.conn.read_only,)
        elif any(t in sql for t in self.conn.missing):
            raise hosoffice.Error('no such table')
        else:
            self.result = (1,)

    def fetchone(self):
        return self.result

    def fetchall(self):
        return [self.result]

    def close(self):
        pass


class CheckConn(FakeConn):
    def __init__(self, read_only=1, missing=()):
        super().__init__()
        self.read_only, self.missing = read_only, missing

    def cursor(self, **kw):
        return CheckCursor(self)


@pytest.fixture
def check_env(tmp_db, monkeypatch, admin_account):
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    monkeypatch.setattr(manage, 'tcp_reachable', lambda host, port: True)
    return monkeypatch


def test_check_all_ok(check_env, capsys):
    conn = CheckConn()
    check_env.setattr(hosoffice, 'connect_readonly', lambda p, raise_errors=False: conn)
    code, out = run(capsys, 'check')
    assert code == 0, out
    assert 'READ ONLY' in out and 'morpromt2f.moph.go.th' in out
    assert all(sql.strip().upper().startswith(('SELECT', 'SET SESSION TRANSACTION READ ONLY'))
               for sql in conn.executed)                       # check ไม่เขียนอะไรเลย


def test_check_fails_when_session_not_read_only(check_env, capsys):
    check_env.setattr(hosoffice, 'connect_readonly', lambda p, raise_errors=False: CheckConn(read_only=0))
    code, out = run(capsys, 'check')
    assert code == 1 and 'READ ONLY' in out


def test_check_fails_when_table_unreadable(check_env, capsys):
    check_env.setattr(hosoffice, 'connect_readonly',
                      lambda p, raise_errors=False: CheckConn(missing=('fingle_scan_notify_mute',)))
    code, out = run(capsys, 'check')
    assert code == 1 and 'fingle_scan_notify_mute' in out


def test_check_fails_when_moph_unreachable(check_env, capsys):
    check_env.setattr(hosoffice, 'connect_readonly', lambda p, raise_errors=False: CheckConn())
    check_env.setattr(manage, 'tcp_reachable', lambda host, port: False)
    code, out = run(capsys, 'check')
    assert code == 1 and 'MOPH' in out


def test_check_fails_when_hosoffice_down(check_env, capsys):
    check_env.setattr(hosoffice, 'connect_readonly', lambda p, raise_errors=False: None)
    assert run(capsys, 'check')[0] == 1


def test_check_warns_without_admin(tmp_db, monkeypatch, capsys):
    monkeypatch.setattr(settings, 'current', settings.load(PROD))
    monkeypatch.setattr(manage, 'tcp_reachable', lambda host, port: True)
    monkeypatch.setattr(hosoffice, 'connect_readonly', lambda p, raise_errors=False: CheckConn())
    code, out = run(capsys, 'check')
    assert code == 1 and 'init-admin' in out


def test_check_reports_hosoffice_even_after_earlier_failure(tmp_db, monkeypatch, capsys):
    """APP_ENV ไม่ใช่ production (FAIL แรก) แล้ว hosoffice ต่อไม่ได้ ต้องยังรายงานหัวข้อ hosoffice"""
    monkeypatch.setattr(manage, 'tcp_reachable', lambda host, port: True)
    monkeypatch.setattr(hosoffice, 'connect_readonly', lambda p, raise_errors=False: None)
    code, out = run(capsys, 'check')
    hos = out.split('== hosoffice')[1].split('== MOPH')[0]
    assert code == 1 and 'FAIL' in hos


def test_commands_do_not_crash_on_a_thai_windows_console_that_cannot_print_check_marks(tmp_db, monkeypatch, capsys):
    import io
    import sys
    console = io.TextIOWrapper(io.BytesIO(), encoding='cp874', errors='strict')
    monkeypatch.setattr(sys, 'stdout', console)
    assert manage.main(['can-restart']) == 0
    console.flush()
    assert 'restart' in console.buffer.getvalue().decode('cp874')
