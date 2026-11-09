"""ข้อมูลที่ importer ใหม่ต้องอ่านจาก hosoffice ก่อนสร้างแถว: PERSON_ID ของ FINGLE_ID และแถวล่าสุดของแต่ละคน (ต้นแบบคัดลอก HR_*)
ทดสอบผ่านฟังก์ชันสาธารณะของ hosoffice โดยจำลองแค่การ query"""
import hosoffice


def fake(monkeypatch, responder):
    calls = []

    def q(sql, params=()):
        calls.append((sql, params))
        return responder(sql, params)
    monkeypatch.setattr(hosoffice, '_query', q)
    return calls


def test_person_ids_map_the_text_fingle_id_to_hr_person_id_with_bound_parameters(monkeypatch):
    calls = fake(monkeypatch, lambda s, p: [{'FINGLE_ID': ' 77 ', 'ID': 9}])
    assert hosoffice.get_person_ids([77, '77', 78]) == {'77': 9}
    sql, params = calls[0]
    assert 'hr_person' in sql and params == ('77', '78') and '77' not in sql


def test_no_fingle_ids_means_no_query(monkeypatch):
    calls = fake(monkeypatch, lambda s, p: [])
    assert hosoffice.get_person_ids([]) == {} and hosoffice.get_latest_rows_by_person([]) == {} and calls == []


def test_latest_row_per_person_comes_from_the_old_importer_only_and_uses_the_person_index(monkeypatch):
    rows = {9: {'HR_FULLNAME': 'a', 'HR_DEP_NAME': 'd'}, 10: {'HR_FULLNAME': 'b', 'HR_DEP_NAME': 'e'}}
    calls = fake(monkeypatch, lambda s, p: [rows[p[0]]] if p[0] in rows else [])
    got = hosoffice.get_latest_rows_by_person([9, 10, 11])
    assert got == {9: rows[9], 10: rows[10]}                       # คน 11 ไม่มีแถวเดิม → ไม่อยู่ในผล (ผู้เรียกต้องกัก)
    sql = calls[0][0]
    assert 'PERSON_ID = %s' in sql and 'CREATE_BY IS NULL' in sql and 'ORDER BY SCAN_DATE_TIME DESC LIMIT 1' in sql
    assert 'EXISTS' not in sql.upper() and len(calls) == 3


# ── ข้อมูล HR ตรง ๆ สำหรับพนักงานใหม่ที่ยังไม่เคยมีแถว ──
HR = {'pid': 265, 'prefix': 'นาย', 'fname': 'สมชาย', 'lname': 'ใจดี', 'position': 'นักเทคนิคการแพทย์', 'level': None,
      'dep': 'กลุ่มงานเทคนิคการแพทย์', 'sub': 'งานเทคนิคการแพทย์', 'ptype': 'ลูกจ้างรายวัน'}


def test_hr_profile_is_built_like_the_old_importer_with_a_three_space_name_separator(monkeypatch):
    calls = fake(monkeypatch, lambda s, p: [HR])
    got = hosoffice.get_hr_profiles([265])
    assert got == {265: {'HR_FULLNAME': 'นายสมชาย   ใจดี', 'HR_POSITION_NAME': 'นักเทคนิคการแพทย์', 'HR_LEAVEL_NAME': '',
                         'HR_DEP_NAME': 'กลุ่มงานเทคนิคการแพทย์', 'HR_DEP_SUB_NAME': 'งานเทคนิคการแพทย์', 'HR_PERSON_TYPE_NAME': 'ลูกจ้างรายวัน'}}
    sql, params = calls[0]
    assert params == (265,) and '265' not in sql and 'hr_person' in sql and sql.lstrip().upper().startswith('SELECT')
    assert 'PASSWORD' not in sql.upper() and 'TOKEN' not in sql.upper() and 'HR_CID' not in sql      # ไม่แตะคอลัมน์อ่อนไหว


def test_an_incomplete_hr_record_is_left_out_so_the_row_is_held(monkeypatch):
    fake(monkeypatch, lambda s, p: [dict(HR, sub=None), dict(HR, pid=266, fname='')])
    assert hosoffice.get_hr_profiles([265, 266]) == {}


def test_no_privilege_on_the_hr_tables_gives_none_not_an_error(monkeypatch):
    def denied(sql, params=()):
        err = hosoffice.Error('SELECT command denied')
        err.errno = 1142
        raise hosoffice.HosofficeUnavailable('query hosoffice ล้มเหลว') from err
    monkeypatch.setattr(hosoffice, '_query', denied)
    assert hosoffice.get_hr_profiles([265]) is None


def test_a_connection_failure_is_still_an_error(monkeypatch):
    def down(sql, params=()):
        raise hosoffice.HosofficeUnavailable('ต่อไม่ได้')
    monkeypatch.setattr(hosoffice, '_query', down)
    import pytest
    with pytest.raises(hosoffice.HosofficeUnavailable):
        hosoffice.get_hr_profiles([265])
