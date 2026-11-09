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
