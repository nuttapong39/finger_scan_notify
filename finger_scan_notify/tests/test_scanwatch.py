"""Lost scan / Import stall / การแจ้ง IT (ช่วง A) — ทดสอบผ่าน scanwatch.run_cycle + delivery จริง (mock แค่ hosoffice และ HTTP)

เวลาในเรื่อง: ศุกร์ 6 พ.ย. 2569 (วันทำงาน) ; ช่วงเฝ้า = 30 นาทีก่อน "เวลาเตือน" ถึง "เตือนได้ถึง" (เช้า 09:00–12:00, เย็น 16:30–22:00)
"""
import json
from datetime import datetime, timedelta

import pytest

import database
import hosoffice
import notifier
import scanwatch

FRI = datetime(2026, 11, 6)


def at(h, m=0, s=0, day=FRI):
    return day.replace(hour=h, minute=m, second=s)


def row(seq, when, machine=1):
    return {'seq': seq, 'machine': machine, 'time': when}


class Resp:
    status_code, text = 200, '{"message":"Succesfully"}'


@pytest.fixture
def watch(tmp_db, monkeypatch):
    database.save_token(156, 'ผู้ดูแลระบบ', 'MOPH_ALERT', '', 'ck', 'sk', 'IT', 1, 1)
    st = {'rows': [], 'down': False, 'sent': [], 'holiday': False}

    def window(n):
        if st['down']:
            raise hosoffice.HosofficeUnavailable('down')
        return list(st['rows'])

    monkeypatch.setattr(hosoffice, 'get_device_window', window)
    monkeypatch.setattr(hosoffice, 'is_holiday', lambda d: st['holiday'])
    monkeypatch.setattr(notifier.requests, 'post',
                        lambda url, **kw: st['sent'].append(kw['data'].decode('utf-8')) or Resp())
    return st


def texts(st):
    return [json.loads(s)['messages'][0]['altText'] if s.startswith('{') else s for s in st['sent']]


def history(days=4):
    """สแกนจริงของวันทำงานก่อนหน้า (จ-พฤ) ช่วง 09:55–10:05 และ 16:55–17:05 — ใช้ประเมินว่า 'ปกติควรมีสแกน'"""
    out, seq = [], 1
    for d in range(2, 2 + days):
        day = datetime(2026, 11, d)
        for h, m in ((9, 55), (9, 58), (10, 1), (10, 4), (16, 55), (16, 58), (17, 1), (17, 4)):
            out.append(row(seq, at(h, m, day=day)))
            seq += 1
    return out, seq


# ── หาเลขที่ขาด (ล้วน) ─────────────────────────────────────────
def test_find_gaps_reports_each_missing_device_sequence_with_its_neighbours():
    rows = [row(10, at(8, 0)), row(11, at(8, 1)), row(14, at(8, 5)), row(15, at(8, 6)), row(17, at(8, 9))]
    gaps = scanwatch.find_gaps(rows)
    assert [g['seq'] for g in gaps] == [12, 13, 16]
    assert gaps[0]['prev_time'] == at(8, 1) and gaps[0]['next_time'] == at(8, 5)
    assert gaps[2]['prev_time'] == at(8, 6) and gaps[2]['next_time'] == at(8, 9)


def test_find_gaps_ignores_duplicates_unsorted_input_and_empty_or_single_rows():
    rows = [row(5, at(8, 1)), row(3, at(8, 0)), row(4, at(8, 0, 30)), row(4, at(8, 0, 40)), row(5, at(8, 1))]
    assert scanwatch.find_gaps(rows) == []
    assert scanwatch.find_gaps([]) == [] and scanwatch.find_gaps([row(1, at(8))]) == []


def test_expected_scans_is_the_average_for_the_same_clock_slot_on_earlier_workdays():
    rows, _ = history()
    assert scanwatch.expected_scans(rows, at(9, 51), at(10, 11)) == 4.0        # 4 สแกน/วัน ในช่วง 09:51–10:11
    assert scanwatch.expected_scans(rows, at(12, 40), at(13, 0)) == 0.0
    assert scanwatch.expected_scans([], at(9, 51), at(10, 11)) == 0.0
    weekend = [row(1, at(10, 0, day=datetime(2026, 11, 7)))]                  # เสาร์ไม่นับเป็นฐาน
    assert scanwatch.expected_scans(weekend, at(9, 51), at(10, 11)) == 0.0


def test_expected_scans_covers_a_stretch_that_crosses_midnight():
    rows, _ = history()
    since = at(16, 50, day=FRI - timedelta(days=1))                           # เงียบตั้งแต่เย็นวานจนเช้านี้
    assert scanwatch.expected_scans(rows, since, at(10, 11)) == 4.0           # ดูแค่ 20 นาทีล่าสุดของวันนี้ ไม่รวมหางเมื่อวาน
    assert scanwatch.expected_scans(rows, since, at(7, 30)) == 0.0            # ก่อนคนมาทำงาน เช้าเงียบเป็นปกติ


# ── Lost scan ────────────────────────────────────────────────
def test_a_missing_sequence_alerts_it_once_after_15_minutes_then_announces_recovery(watch):
    watch['rows'] = [row(1, at(8, 0)), row(2, at(8, 1)), row(4, at(8, 3))]
    scanwatch.run_cycle(at(8, 5))
    assert watch['sent'] == []                                   # ยังไม่ถึง 15 นาที (อาจแค่มาช้า)
    scanwatch.run_cycle(at(8, 19))
    assert watch['sent'] == []
    scanwatch.run_cycle(at(8, 21))
    assert len(watch['sent']) == 1 and '3' in watch['sent'][0] and 'Lost scan' in watch['sent'][0]
    scanwatch.run_cycle(at(8, 30))
    scanwatch.run_cycle(at(8, 40))
    assert len(watch['sent']) == 1                               # ครั้งเดียวต่อเหตุการณ์

    watch['rows'].append(row(3, at(8, 2)))                       # ตัวนำเข้าเติมเลข 3 ให้แล้ว
    scanwatch.run_cycle(at(8, 45))
    assert len(watch['sent']) == 2 and 'กลับมาปกติ' in watch['sent'][1]
    scanwatch.run_cycle(at(8, 50))
    assert len(watch['sent']) == 2
    assert [e['closed_at'] is not None for e in database.get_watch_events('lost_scan')] == [True]


def test_a_gap_filled_before_15_minutes_never_alerts(watch):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    scanwatch.run_cycle(at(8, 5))
    watch['rows'].append(row(2, at(8, 1)))
    scanwatch.run_cycle(at(8, 10))
    scanwatch.run_cycle(at(9, 0))
    assert watch['sent'] == []


def test_several_lost_scans_are_reported_in_one_card_and_recover_together(watch):
    watch['rows'] = [row(1, at(8, 0)), row(5, at(8, 4))]
    scanwatch.run_cycle(at(8, 5))
    scanwatch.run_cycle(at(8, 21))
    assert len(watch['sent']) == 1
    for s in (2, 3, 4):
        assert str(s) in watch['sent'][0]
    watch['rows'] += [row(2, at(8, 1)), row(3, at(8, 2))]        # เติมแค่บางส่วน → ยังไม่ประกาศปกติ
    scanwatch.run_cycle(at(8, 30))
    assert len(watch['sent']) == 1
    watch['rows'].append(row(4, at(8, 3)))
    scanwatch.run_cycle(at(8, 35))
    assert len(watch['sent']) == 2 and 'กลับมาปกติ' in watch['sent'][1]


def test_the_morning_summary_is_sent_once_at_0730_only_while_something_is_still_missing(watch):
    watch['rows'] = [row(1, at(6, 0)), row(3, at(6, 2))]
    scanwatch.run_cycle(at(6, 5))
    scanwatch.run_cycle(at(6, 30))
    assert len(watch['sent']) == 1                               # แจ้งเหตุการณ์ครั้งแรก
    scanwatch.run_cycle(at(7, 29))
    assert len(watch['sent']) == 1
    scanwatch.run_cycle(at(7, 31))
    assert len(watch['sent']) == 2 and 'สรุป' in watch['sent'][1]
    scanwatch.run_cycle(at(8, 0))
    assert len(watch['sent']) == 2                               # วันละครั้ง
    watch['rows'].append(row(2, at(6, 1)))
    scanwatch.run_cycle(at(8, 1))
    next_day = FRI + timedelta(days=1)
    scanwatch.run_cycle(at(7, 40, day=next_day))
    assert len(watch['sent']) == 3 and 'กลับมาปกติ' in watch['sent'][2]     # ไม่มีเลขค้างแล้ว → ไม่มีสรุป


def test_when_it_cannot_be_reached_the_alert_is_retried_not_lost(watch):
    database.delete_token(database.get_token_by_person_id(156)['id'])
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    scanwatch.run_cycle(at(8, 5))
    scanwatch.run_cycle(at(8, 21))                               # ไม่มี token ผู้รับ → ส่งไม่ได้ แต่ไม่ error
    assert watch['sent'] == []
    assert database.get_watch_events('lost_scan')[0]['alerted_at'] is None
    database.save_token(156, 'ผู้ดูแลระบบ', 'MOPH_ALERT', '', 'ck', 'sk', 'IT', 1, 1)
    scanwatch.run_cycle(at(8, 25))
    assert len(watch['sent']) == 1


def test_a_huge_jump_in_the_sequence_is_not_reported_as_thousands_of_lost_scans(watch):
    watch['rows'] = [row(1, at(8, 0)), row(2, at(8, 1)), row(900000, at(8, 2)), row(900002, at(8, 3))]
    assert [g['seq'] for g in scanwatch.find_gaps(watch['rows'])] == [900001]
    scanwatch.run_cycle(at(8, 5))
    assert [e['seq'] for e in database.get_watch_events('lost_scan')] == [900001]


def test_a_lost_scan_that_scrolls_out_of_the_window_is_closed_without_saying_recovered(watch):
    watch['rows'] = [row(10, at(8, 0)), row(12, at(8, 2))]
    scanwatch.run_cycle(at(8, 5))
    scanwatch.run_cycle(at(8, 21))
    assert len(watch['sent']) == 1
    watch['rows'] = [row(20, at(8, 30)), row(21, at(8, 31))]                  # เลข 11 หลุดพ้นหน้าต่างตรวจ
    scanwatch.run_cycle(at(8, 40))
    assert [e['open'] for e in database.get_watch_events('lost_scan')] == [False]
    assert len(watch['sent']) == 1


def test_a_failed_alert_is_retried_only_after_a_pause_and_logged_once_per_hour(watch):
    database.delete_token(database.get_token_by_person_id(156)['id'])
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    scanwatch.run_cycle(at(8, 5))
    for minute in (21, 22, 23, 25, 30, 40):
        scanwatch.run_cycle(at(8, minute))
    fails = [e for e in database.get_guard_events() if e['kind'] == 'it_alert_undeliverable']
    assert len(fails) == 1
    database.save_token(156, 'IT', 'MOPH_ALERT', '', 'ck', 'sk', '', 1, 1)
    scanwatch.run_cycle(at(8, 41))                                            # ยังอยู่ในช่วงพักจากความล้มเหลวครั้งล่าสุด (8:40)
    assert watch['sent'] == []
    scanwatch.run_cycle(at(8, 44))
    assert len(watch['sent']) == 1


def test_the_it_recipient_is_configurable_and_defaults_to_person_156(watch):
    assert database.get_watch_state()['it_person_id'] == 156
    database.save_token(200, 'คนใหม่', 'MOPH_ALERT', '', 'ck2', 'sk2', 'IT', 1, 1)
    database.save_watch_state(it_person_id=200)
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    posted = []
    notifier_post = notifier.requests.post
    notifier.requests.post = lambda url, **kw: posted.append(kw['headers']) or Resp()
    try:
        scanwatch.run_cycle(at(8, 5))
        scanwatch.run_cycle(at(8, 21))
    finally:
        notifier.requests.post = notifier_post
    assert len(posted) == 1 and 'ck2' in json.dumps(posted[0]) + str(posted[0])


def test_hosoffice_down_changes_nothing_and_does_not_raise(watch):
    watch['rows'] = [row(1, at(8, 0)), row(3, at(8, 2))]
    scanwatch.run_cycle(at(8, 5))
    watch['down'] = True
    scanwatch.run_cycle(at(9, 0))                                # fail closed: ไม่เปิด/ปิดเหตุการณ์ ไม่ส่งอะไร
    assert watch['sent'] == []
    st = database.get_watch_state()
    assert st['last_error'] and len(database.get_watch_events('lost_scan')) == 1


# ── Import stall ─────────────────────────────────────────────
def test_stall_is_detected_inside_the_watch_window_when_scans_normally_arrive(watch):
    rows, nxt = history()
    watch['rows'] = rows + [row(nxt, at(9, 49))]
    scanwatch.run_cycle(at(9, 50))
    assert scanwatch.import_stalled(at(9, 50)) is False
    scanwatch.run_cycle(at(10, 5))
    assert scanwatch.import_stalled(at(10, 5)) is False          # ยังไม่ครบ 20 นาที
    scanwatch.run_cycle(at(10, 11))
    assert scanwatch.import_stalled(at(10, 11)) is True
    assert len(watch['sent']) == 1 and 'การนำเข้า' in watch['sent'][0]
    scanwatch.run_cycle(at(10, 20))
    assert len(watch['sent']) == 1                               # ครั้งเดียวต่อเหตุการณ์

    watch['rows'].append(row(nxt + 1, at(10, 21)))               # มีสแกนใหม่ถึง hosoffice แล้ว
    scanwatch.run_cycle(at(10, 22))
    assert scanwatch.import_stalled(at(10, 22)) is False
    assert len(watch['sent']) == 2 and 'กลับมาปกติ' in watch['sent'][1]


def test_a_quiet_stretch_that_is_normal_for_that_time_of_day_is_not_a_stall(watch):
    rows, nxt = history()
    watch['rows'] = rows + [row(nxt, at(12, 0))]                 # ช่วงเที่ยง-บ่ายปกติไม่มีใครสแกน
    scanwatch.run_cycle(at(11, 30))
    scanwatch.run_cycle(at(11, 59))
    assert scanwatch.import_stalled(at(11, 59)) is False and watch['sent'] == []


def test_no_stall_outside_the_watch_window_or_on_weekends_and_holidays(watch):
    rows, nxt = history()
    watch['rows'] = rows + [row(nxt, at(13, 0))]
    scanwatch.run_cycle(at(13, 1))
    scanwatch.run_cycle(at(13, 30))
    assert scanwatch.import_stalled(at(13, 30)) is False
    saturday = at(10, 0, day=datetime(2026, 11, 7))
    scanwatch.run_cycle(saturday)
    scanwatch.run_cycle(saturday + timedelta(minutes=25))
    assert scanwatch.import_stalled(saturday + timedelta(minutes=25)) is False
    assert watch['sent'] == []


def test_stalled_is_false_when_the_watcher_itself_has_not_polled_recently(watch):
    rows, nxt = history()
    watch['rows'] = rows + [row(nxt, at(9, 49))]
    scanwatch.run_cycle(at(9, 50))
    scanwatch.run_cycle(at(10, 11))
    assert scanwatch.import_stalled(at(10, 11)) is True
    assert scanwatch.import_stalled(at(10, 11) + timedelta(minutes=30)) is False   # ข้อมูลเก่า → ไม่ตีความว่าหยุด


def test_a_morning_stall_is_caught_by_the_rush_hour_scans_that_never_arrived(watch):
    """เช้า 09:30 ที่เพิ่งเตือน: ปกติ 07:00–09:00 คนสแกนเยอะ ถ้าไม่มีเลขใหม่ตั้งแต่ 08:05 = ตัวนำเข้าหยุด"""
    rows, seq = [], 1
    for d in range(2, 6):
        for h in (7, 8):
            for m in range(0, 60, 5):
                rows.append(row(seq, at(h, m, day=datetime(2026, 11, d))))
                seq += 1
    watch['rows'] = rows + [row(seq, at(8, 5))]
    scanwatch.run_cycle(at(8, 6))
    assert scanwatch.import_stalled(at(8, 6)) is False
    scanwatch.run_cycle(at(9, 1))
    assert scanwatch.import_stalled(at(9, 1)) is True and len(watch['sent']) == 1


def test_starting_the_watcher_after_the_data_already_stopped_is_detected_immediately(watch):
    rows, nxt = history()
    watch['rows'] = rows + [row(nxt, at(9, 30))]                              # สแกนล่าสุด 09:30 แล้วเงียบ
    scanwatch.run_cycle(at(10, 10))                                           # เพิ่งเปิดแอป ไม่ต้องรอ 20 นาทีใหม่
    assert scanwatch.import_stalled(at(10, 10)) is True


def test_no_morning_summary_when_the_app_only_starts_in_the_afternoon(watch):
    watch['rows'] = [row(1, at(6, 0)), row(3, at(6, 2))]
    scanwatch.run_cycle(at(6, 5))
    scanwatch.run_cycle(at(6, 30))
    assert len(watch['sent']) == 1
    scanwatch.run_cycle(at(15, 0))                               # restart ตอนบ่าย: สรุปเช้าหมดเวลาแล้ว
    assert len(watch['sent']) == 1
