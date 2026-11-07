from datetime import date, datetime

import pytest

import backfill
import database
import hosoffice
import notifier

TODAY = date(2026, 11, 6)


class Resp:
    status_code, text = 200, '{"message":"Succesfully"}'


def scan(sid, pid=7, code='0', isinput='FACESCAN', hour=8):
    return {'ID': sid, 'PERSON_ID': pid, 'HR_FULLNAME': f'คน{pid}', 'HR_DEP_SUB_NAME': 'งาน',
            'SCAN_TYPE': code, 'SCAN_DATE_TIME': datetime(2026, 11, 6, hour, 0, sid % 60), 'ISINPUT': isinput}


SCANS = {s['ID']: s for s in [
    scan(1), scan(2, code='1', hour=17), scan(3, isinput='MANUAL'), scan(4, code='9'),
    scan(5, pid=8), scan(6, pid=99),               # 99 = ไม่มี token
]}


@pytest.fixture
def env(tmp_db, monkeypatch):
    database.save_token(7, 'คน7', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    database.save_token(8, 'คน8', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    monkeypatch.setattr(hosoffice, 'get_scans_by_ids', lambda ids: [SCANS[i] for i in ids if i in SCANS])
    monkeypatch.setattr(hosoffice, 'get_muted_person_ids', lambda: set())
    sent = []
    monkeypatch.setattr(notifier.requests, 'post', lambda url, **kw: sent.append(kw['data']) or Resp())
    monkeypatch.setattr(backfill, 'THROTTLE_SECONDS', 0)
    return sent


def log(scan_id, status, source='live'):
    database.add_notify_log(scan_id, 7, 'คน7', 'เข้างาน', '2026-11-06 08:00:00', 'MOPH_ALERT', status, 200, '',
                            source=source)


# ── Delivery status ────────────────────────────────────────────
def test_delivery_status_priority(env):
    log(1, 'error'); log(1, 'success', 'backfill')          # เคยล้มแล้วส่งสำเร็จ → สำเร็จ
    log(2, 'success'); log(2, 'error')                      # สำเร็จแล้วค่อยพัง → ยังถือว่าสำเร็จ
    log(3, 'error')
    database.add_scan_skips([(4, 7, 'live', 'stale'), (5, 8, 'live', 'quota')])
    st = backfill.delivery_status([1, 2, 3, 4, 5, 6])
    assert st[1]['status'] == 'success' and st[1]['source'] == 'backfill'
    assert st[2]['status'] == 'success'
    assert st[3]['status'] == 'error'
    assert st[4]['status'] == 'skipped' and st[4]['detail'] == 'stale'
    assert st[5]['status'] == 'skipped' and st[5]['detail'] == 'quota'
    assert st[6]['status'] == 'never'


def test_rows_default_selection(env):
    log(1, 'success')
    rows = {r['id']: r for r in backfill.build_rows(list(SCANS.values()), backfill.delivery_status(list(SCANS)),
                                                    {7, 8})}
    assert rows[1]['default_checked'] is False                 # ส่งแล้ว
    assert rows[2]['default_checked'] is True and rows[2]['direction'] == 'out'
    assert rows[3]['manual'] is True and rows[3]['default_checked'] is False   # Manual scan ไม่ติ๊กอัตโนมัติ
    assert rows[4]['direction'] is None and rows[4]['default_checked'] is False
    assert rows[5]['default_checked'] is True
    assert rows[6]['has_token'] is False and rows[6]['default_checked'] is False


# ── ช่วงวันที่ ────────────────────────────────────────────────
def test_parse_range():
    assert backfill.parse_range('2026-11-01', '2026-11-06', TODAY) == (date(2026, 11, 1), TODAY)
    for bad in [('2026-11-07', '2026-11-07'), ('2026-11-06', '2026-11-01'), ('x', '2026-11-01'), ('', '')]:
        with pytest.raises(backfill.BackfillError):
            backfill.parse_range(*bad, TODAY)


def test_needs_confirm_only_beyond_7_days():
    assert backfill.needs_confirm(date(2026, 10, 30), TODAY) is False
    assert backfill.needs_confirm(date(2026, 10, 29), TODAY) is True


# ── Preview ───────────────────────────────────────────────────
def test_preview_counts_and_exclusions(env):
    log(1, 'success')
    p = backfill.preview([1, 2, 3, 4, 5, 6], resend_ids=[], date_from=TODAY, date_to=TODAY, today=TODAY)
    assert p['messages'] == 3                       # 2, 3(manual แต่ติ๊กเอง), 5
    assert p['persons'] == 2
    assert p['excluded'] == {'already_sent': 1, 'unknown_type': 1, 'no_token': 1}
    assert p['over_cap'] is False and p['needs_confirm'] is False
    p2 = backfill.preview([1], resend_ids=[1], date_from=TODAY, date_to=TODAY, today=TODAY)
    assert p2['messages'] == 1 and p2['resend'] == 1


def test_preview_respects_mute_and_type_flags(env, monkeypatch):
    monkeypatch.setattr(hosoffice, 'get_muted_person_ids', lambda: {8})
    database.save_token(7, 'คน7', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 0)      # ปิดแจ้งออกงาน
    p = backfill.preview([2, 5], [], TODAY, TODAY, TODAY)
    assert p['messages'] == 0 and p['excluded'] == {'type_off': 1, 'muted': 1}


# ── Job ───────────────────────────────────────────────────────
def test_job_sends_backfill_cards_and_logs(env):
    job_id = backfill.start_job([2, 5, 6], [], TODAY, TODAY, '', TODAY, run_async=False)
    job = database.get_backfill_job(job_id)
    assert (job['status'], job['total'], job['sent'], job['failed']) == ('done', 2, 2, 0)
    assert len(env) == 2 and all('ส่งย้อนหลัง' in b.decode('utf-8') for b in env)
    st = backfill.delivery_status([2, 5])
    assert st[2]['status'] == 'success' and st[2]['source'] == 'backfill'


def test_job_does_not_resend_unless_asked(env):
    log(2, 'success')
    with pytest.raises(backfill.BackfillError):          # ไม่มีอะไรให้ส่ง
        backfill.start_job([2], [], TODAY, TODAY, '', TODAY, run_async=False)
    backfill.start_job([2], [2], TODAY, TODAY, '', TODAY, run_async=False)
    assert len(env) == 1


def test_job_skips_item_sent_live_meanwhile(env, monkeypatch):
    real = backfill.delivery_status
    calls = {'n': 0}

    def racing(ids):                                      # ระหว่าง job รัน Monitor ส่ง scan 5 ไปแล้ว
        calls['n'] += 1
        if calls['n'] > 1:
            log(5, 'success')
        return real(ids)
    monkeypatch.setattr(backfill, 'delivery_status', racing)
    job_id = backfill.start_job([2, 5], [], TODAY, TODAY, '', TODAY, run_async=False)
    job = database.get_backfill_job(job_id)
    assert job['sent'] == 1 and job['skipped'] == 1


def test_job_cap(env, monkeypatch):
    monkeypatch.setattr(backfill, 'MAX_PER_JOB', 1)
    with pytest.raises(backfill.BackfillError, match='เกิน'):
        backfill.start_job([2, 5], [], TODAY, TODAY, '', TODAY, run_async=False)
    assert env == []


def test_job_older_than_7_days_needs_typed_confirmation(env):
    old = date(2026, 10, 1)
    with pytest.raises(backfill.BackfillError, match='ยืนยัน'):
        backfill.start_job([2], [], old, TODAY, 'ok', TODAY, run_async=False)
    backfill.start_job([2], [], old, TODAY, backfill.CONFIRM_TEXT, TODAY, run_async=False)
    assert len(env) == 1


def test_only_one_job_at_a_time(env):
    database.create_backfill_job('2026-11-06', '2026-11-06', 5)        # job ค้างสถานะ running
    with pytest.raises(backfill.BackfillError, match='กำลังทำงาน'):
        backfill.start_job([2], [], TODAY, TODAY, '', TODAY, run_async=False)


def test_cancel_stops_remaining_items(env, monkeypatch):
    original = backfill.deliver_scan

    def send_then_cancel(*a, **kw):
        res = original(*a, **kw)
        backfill.cancel_job(kw['job_id'])
        return res
    monkeypatch.setattr(backfill, 'deliver_scan', send_then_cancel)
    job_id = backfill.start_job([2, 5], [], TODAY, TODAY, '', TODAY, run_async=False)
    job = database.get_backfill_job(job_id)
    assert job['status'] == 'cancelled' and job['sent'] == 1 and len(env) == 1


def test_hosoffice_down_raises_without_sending(env, monkeypatch):
    def down(ids):
        raise hosoffice.HosofficeUnavailable('down')
    monkeypatch.setattr(hosoffice, 'get_scans_by_ids', down)
    with pytest.raises(hosoffice.HosofficeUnavailable):
        backfill.start_job([2], [], TODAY, TODAY, '', TODAY, run_async=False)
    assert env == [] and database.get_running_backfill_job() is None


def test_job_left_running_by_crash_is_closed_on_startup(env):
    stuck = database.create_backfill_job('2026-11-06', '2026-11-06', 5)     # แอปดับระหว่างส่ง
    database.init_db()                                                     # manage.py / init_db ไม่แตะ job
    assert database.get_backfill_job(stuck)['status'] == 'running'
    assert database.close_stuck_backfill_jobs() == 1                       # เปิดแอปใหม่ (startup)
    job = database.get_backfill_job(stuck)
    assert job['status'] == 'error' and job['finished_at']
    assert database.get_running_backfill_job() is None
    backfill.start_job([2], [], TODAY, TODAY, '', TODAY, run_async=False)  # ส่งใหม่ได้
    assert len(env) == 1
