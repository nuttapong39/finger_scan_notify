from datetime import datetime

import pytest

import database
import hosoffice
import notifier
import reminders

FRI_0935 = datetime(2026, 11, 6, 9, 35)     # ศุกร์
FRI_1705 = datetime(2026, 11, 6, 17, 5)


class Resp:
    status_code, text = 200, '{"message":"Succesfully"}'


def scan(pid, code, hour=8):
    return {'ID': pid * 100 + hour, 'PERSON_ID': pid, 'SCAN_TYPE': code,
            'SCAN_DATE_TIME': datetime(2026, 11, 6, hour, 0)}


@pytest.fixture
def env(tmp_db, monkeypatch):
    for pid in (1, 2, 3, 4):
        database.save_token(pid, f'คน{pid}', 'MOPH_ALERT', '', 'ck', 'sk', 'งาน', 1, 1)
    database.set_remind_enabled([1, 2, 3, 4], True)
    for kind in ('checkin', 'checkout'):
        c = database.get_reminder_config()[kind]
        database.save_reminder_config(kind, 1, c['remind_at'], c['remind_until'], '', c['button_label'])
    state = {'holiday': False, 'leave': set(), 'muted': set(), 'scans': [], 'down': False}

    def guard():
        if state['down']:
            raise hosoffice.HosofficeUnavailable('down')

    monkeypatch.setattr(hosoffice, 'is_holiday', lambda d: guard() or state['holiday'])
    monkeypatch.setattr(hosoffice, 'get_persons_on_leave', lambda d, pids: guard() or set(state['leave']) & set(pids))
    monkeypatch.setattr(hosoffice, 'get_muted_person_ids', lambda: guard() or set(state['muted']))
    monkeypatch.setattr(hosoffice, 'get_scans_on',
                        lambda d, pids: guard() or [s for s in state['scans'] if s['PERSON_ID'] in pids])
    state['sent'] = []
    monkeypatch.setattr(notifier.requests, 'post',
                        lambda url, **kw: state['sent'].append(kw['data'].decode('utf-8')) or Resp())
    monkeypatch.setattr(reminders, 'THROTTLE_SECONDS', 0)
    return state


def reminded(kind, day='2026-11-06'):
    return {r['person_id']: r['status'] for r in database.get_reminder_log(day) if r['kind'] == kind}


# ── ฟังก์ชันล้วน ───────────────────────────────────────────────
def test_decide_checkin_and_checkout():
    scans = [scan(1, '0'), scan(2, '0'), scan(2, '1', 17), scan(3, 'IN'), scan(5, '9')]
    assert reminders.decide('checkin', [1, 2, 3, 4, 5], scans) == [4, 5]       # 5 มีแต่ scan ประเภทไม่รู้จัก
    assert reminders.decide('checkout', [1, 2, 3, 4], scans) == [1, 3]         # 4 ไม่มีเข้า → ไม่เตือนออก


def test_out_scan_without_in_counts_as_scanned_for_checkout_only():
    scans = [scan(1, 'OUT', 17)]
    assert reminders.decide('checkin', [1], scans) == [1]
    assert reminders.decide('checkout', [1], scans) == []


@pytest.mark.parametrize('now,expected', [
    (datetime(2026, 11, 6, 9, 29), False), (datetime(2026, 11, 6, 9, 30), True),
    (datetime(2026, 11, 6, 11, 59), True), (datetime(2026, 11, 6, 12, 0), False),
])
def test_window(now, expected):
    assert reminders.in_window({'remind_at': '09:30', 'remind_until': '12:00'}, now) is expected


@pytest.mark.parametrize('data,field', [
    ({'remind_at': '9:30'}, 'remind_at'), ({'remind_at': '25:00'}, 'remind_at'),
    ({'remind_until': '09:00'}, 'remind_until'),
    ({'button_url': 'http://x.org'}, 'button_url'), ({'button_url': 'javascript:alert(1)'}, 'button_url'),
    ({'button_url': 'https://x.org', 'button_label': ''}, 'button_label'),
    ({'button_label': 'ก' * 21}, 'button_label'),
])
def test_validate_config_rejects(data, field):
    base = {'enabled': True, 'remind_at': '09:30', 'remind_until': '12:00', 'button_url': '', 'button_label': 'สแกน'}
    _, errors = reminders.validate_config({**base, **data})
    assert field in errors


def test_validate_config_ok():
    clean, errors = reminders.validate_config({'enabled': 1, 'remind_at': '09:30', 'remind_until': '12:00',
                                               'button_url': ' https://hos.example/scan ', 'button_label': 'สแกน'})
    assert errors == {} and clean['button_url'] == 'https://hos.example/scan' and clean['enabled'] is True


# ── รอบการเตือน ───────────────────────────────────────────────
def test_cycle_reminds_only_people_without_checkin(env):
    env['scans'] = [scan(1, '0'), scan(2, '4')]
    report = reminders.run_cycle(FRI_0935)
    assert report['checkin']['status'] == 'ok' and sorted(report['checkin']['reminded']) == [3, 4]
    assert reminded('checkin') == {3: 'success', 4: 'success'}
    assert len(env['sent']) == 2 and 'ยังไม่พบการสแกนเข้างาน' in env['sent'][0]
    assert report['checkout']['status'] == 'outside_window'


def test_cycle_is_once_per_day(env):
    reminders.run_cycle(FRI_0935)
    reminders.run_cycle(datetime(2026, 11, 6, 9, 36))
    assert len(env['sent']) == 4


def test_leave_mute_and_opt_out_are_excluded(env):
    env['leave'], env['muted'] = {1}, {2}
    database.set_remind_enabled([3], False)
    report = reminders.run_cycle(FRI_0935)
    assert report['checkin']['reminded'] == [4]


def test_weekend_and_holiday_send_nothing(env):
    assert reminders.run_cycle(datetime(2026, 11, 7, 9, 35))['checkin']['status'] == 'weekend'
    env['holiday'] = True
    assert reminders.run_cycle(FRI_0935)['checkin']['status'] == 'holiday'
    assert env['sent'] == []


def test_hosoffice_down_fails_closed(env):
    env['down'] = True
    report = reminders.run_cycle(FRI_0935)
    assert report['checkin']['status'] == 'error' and env['sent'] == []
    assert reminded('checkin') == {}


def test_recheck_after_claim_cancels_if_person_just_scanned(env, monkeypatch):
    calls = {'n': 0}
    base = hosoffice.get_scans_on

    def late_scan(day, pids):
        calls['n'] += 1
        if calls['n'] > 1:                     # หลังรอบแรก คน 3 เพิ่งสแกน
            env['scans'].append(scan(3, '0', 9))
        return base(day, pids)
    monkeypatch.setattr(hosoffice, 'get_scans_on', late_scan)
    report = reminders.run_cycle(FRI_0935)
    assert reminded('checkin')[3] == 'cancelled' and 3 in report['checkin']['cancelled']


def test_checkout_window(env):
    env['scans'] = [scan(1, '0'), scan(2, '0'), scan(2, '5', 17)]
    report = reminders.run_cycle(FRI_1705)
    assert report['checkout']['reminded'] == [1]
    assert 'ยังไม่พบการสแกนออกงาน' in env['sent'][0]


def test_disabled_kind_is_skipped(env):
    c = database.get_reminder_config()['checkin']
    database.save_reminder_config('checkin', 0, c['remind_at'], c['remind_until'], '', c['button_label'])
    assert reminders.run_cycle(FRI_0935)['checkin']['status'] == 'disabled'


def test_dry_run_sends_and_claims_nothing(env):
    report = reminders.run_cycle(datetime(2026, 11, 6, 8, 0), dry_run=True, ignore_window=True)
    assert sorted(report['checkin']['would_remind']) == [1, 2, 3, 4]
    assert env['sent'] == [] and reminded('checkin') == {}


def test_failed_send_is_recorded_as_error(env, monkeypatch):
    class Bad:
        status_code, text = 401, 'Unauthorized'
    monkeypatch.setattr(notifier.requests, 'post', lambda *a, **k: Bad())
    reminders.run_cycle(FRI_0935)
    assert set(reminded('checkin').values()) == {'error'}
