"""แกนตรรกะของ Scan importer ตัวใหม่ (ล้วน ไม่มี I/O) — ทดสอบผ่าน plan_batch / plan_release เท่านั้น

กติกา (ADR 0002 ข้อ 5–10): ผู้เขียนคนเดียวกุญแจ (เครื่อง, เลข) · แถวเก่ากว่า 24 ชม. กักรอคนปล่อย ·
รอบเดียวเขียนเกินเพดานกักทั้งรอบ · ไม่มีจุดล่าสุดห้ามเดาเริ่มจากต้นตาราง
"""
from datetime import datetime, timedelta

from scan_importer import plan

NOW = datetime(2026, 11, 8, 19, 0, 0)


def src(seq, minutes_ago=5, machine=1, person=7, seconds=14):
    t = (NOW - timedelta(minutes=minutes_ago)).replace(second=seconds)
    return {'seq': seq, 'machine': machine, 'person': person, 'time': t, 'device_time': t.replace(second=0)}


def run(rows, existing=(), checkpoint=100, **kw):
    return plan.plan_batch(rows, set(existing), NOW, checkpoint=checkpoint, **kw)


def test_fresh_rows_are_inserted_in_sequence_order_and_the_checkpoint_moves_to_the_last_one():
    p = run([src(103), src(101), src(102)])
    assert [r['seq'] for r in p['insert']] == [101, 102, 103]
    assert p['held'] == [] and p['duplicate'] == [] and p['next_checkpoint'] == 103
    assert p['needs_baseline'] is False and p['flood'] is False


def test_a_row_whose_machine_and_sequence_already_exist_is_a_duplicate_even_if_the_stored_time_has_no_seconds():
    # แถวของ SCAN-WATCH เก็บเวลาระดับนาที (วินาที = 00) — กุญแจเป็นเลขเครื่อง ไม่ใช่เวลา จึงไม่เขียนซ้ำ
    p = run([src(101), src(102, machine=2)], existing={(1, 101)})
    assert [r['seq'] for r in p['insert']] == [102] and [r['seq'] for r in p['duplicate']] == [101]
    assert p['next_checkpoint'] == 102                                      # ซ้ำก็ถือว่าจัดการแล้ว จุดล่าสุดต้องผ่านไป


def test_the_same_key_twice_in_one_batch_is_inserted_once():
    p = run([src(101), src(101)])
    assert len(p['insert']) == 1 and len(p['duplicate']) == 1


def test_a_row_older_than_24_hours_is_held_not_inserted():
    p = run([src(101, minutes_ago=24 * 60 + 1), src(102, minutes_ago=24 * 60 - 1)])
    assert [r['seq'] for r in p['held']] == [101] and [r['seq'] for r in p['insert']] == [102]
    assert p['next_checkpoint'] == 102                                      # แถวที่กักไว้แล้ว ไม่ต้องอ่านซ้ำทุกรอบ


def test_a_row_with_no_usable_time_is_held_because_its_age_cannot_be_judged():
    bad = {'seq': 101, 'machine': 1, 'person': 7, 'time': None, 'device_time': None}
    assert [r['seq'] for r in run([bad])['held']] == [101]


def test_a_round_that_would_insert_more_than_the_cap_is_held_whole_and_flagged_as_a_flood():
    rows = [src(1000 + i) for i in range(plan.BATCH_CAP + 1)]
    p = run(rows)
    assert p['insert'] == [] and len(p['held']) == plan.BATCH_CAP + 1 and p['flood'] is True
    assert p['next_checkpoint'] == 1000 + plan.BATCH_CAP


def test_a_round_exactly_at_the_cap_is_inserted_normally():
    p = run([src(1000 + i) for i in range(plan.BATCH_CAP)])
    assert len(p['insert']) == plan.BATCH_CAP and p['flood'] is False


def test_the_cap_counts_only_rows_that_would_really_be_inserted():
    rows = [src(1000 + i) for i in range(plan.BATCH_CAP + 50)]
    existing = {(1, 1000 + i) for i in range(60)}                          # 60 แถวมีแล้ว → เหลือเขียนจริง 290
    p = run(rows, existing=existing)
    assert len(p['insert']) == plan.BATCH_CAP - 10 and p['flood'] is False and len(p['duplicate']) == 60


def test_without_a_saved_checkpoint_nothing_is_written_and_the_importer_must_wait_for_a_person():
    p = run([src(101), src(102)], checkpoint=None)
    assert p['needs_baseline'] is True
    assert p['insert'] == [] and p['held'] == [] and p['duplicate'] == [] and p['next_checkpoint'] is None


def test_the_stored_time_is_the_commit_time_with_seconds_and_falls_back_to_the_device_minute():
    withsec = src(101)
    assert plan.row_time(withsec).second == 14
    only_minute = dict(src(102), time=None)
    assert plan.row_time(only_minute) == only_minute['device_time'] and plan.row_time(only_minute).second == 0


# ── ปล่อยแถวที่กัก ─────────────────────────────────────────────
def old(seq, days_ago, machine=1):
    r = src(seq, machine=machine)
    return dict(r, time=r['time'] - timedelta(days=days_ago), device_time=r['device_time'] - timedelta(days=days_ago))


HELD = [old(201, 3), old(202, 3), old(203, 2), old(204, 1), old(205, 1)]


def test_release_dry_run_summarises_the_count_the_dates_and_the_rows_per_day():
    r = plan.plan_release(HELD, set(), 201, 205)
    s = r['summary']
    assert s['count'] == 5 and s['oldest'] == (NOW - timedelta(days=3, minutes=5)).replace(second=14)
    assert s['per_day'] == {'2026-11-05': 2, '2026-11-06': 1, '2026-11-07': 2}
    assert [x['seq'] for x in r['insert']] == [201, 202, 203, 204, 205] and r['writer'] == 'SCAN-IMPORTER-RELEASED'


def test_release_only_takes_the_requested_sequence_range():
    r = plan.plan_release(HELD, set(), 202, 204)
    assert [x['seq'] for x in r['insert']] == [202, 203, 204] and r['summary']['count'] == 3


def test_releasing_twice_never_creates_duplicates():
    first = plan.plan_release(HELD, set(), 201, 205)
    existing = {plan.row_key(x) for x in first['insert']}
    again = plan.plan_release(HELD, existing, 201, 205)
    assert again['insert'] == [] and len(again['duplicate']) == 5 and again['summary']['count'] == 0


def test_releasing_an_empty_range_is_a_harmless_empty_plan():
    r = plan.plan_release(HELD, set(), 900, 999)
    assert r['insert'] == [] and r['summary'] == {'count': 0, 'oldest': None, 'newest': None, 'per_day': {}}


def test_a_row_dated_in_the_future_beyond_clock_skew_is_held_but_a_small_skew_is_tolerated():
    ahead_2h = dict(src(101), time=NOW + timedelta(hours=2), device_time=NOW + timedelta(hours=2))
    ahead_5m = dict(src(102), time=NOW + timedelta(minutes=5), device_time=NOW + timedelta(minutes=5))
    p = run([ahead_2h, ahead_5m])
    assert [r['seq'] for r in p['held']] == [101] and [r['seq'] for r in p['insert']] == [102]


def test_release_never_writes_a_row_without_a_scan_time_and_reports_it_separately():
    no_time = {'seq': 301, 'machine': 1, 'person': 7, 'time': None, 'device_time': None}
    r = plan.plan_release(HELD + [no_time], set(), 201, 305)
    assert [x['seq'] for x in r['unreleasable']] == [301] and 301 not in [x['seq'] for x in r['insert']]
    assert r['summary']['count'] == 5
