import pytest

from scan_types import CHECKIN, CHECKOUT, direction, label, sql_list


@pytest.mark.parametrize('code,expected', [
    ('0', 'in'), ('4', 'in'), ('IN', 'in'),
    ('1', 'out'), ('5', 'out'), ('OUT', 'out'),
    (' 0 ', 'in'), ('in', 'in'), ('out', 'out'), (0, 'in'), (1, 'out'),
])
def test_direction_of_every_known_code(code, expected):
    assert direction(code) == expected


@pytest.mark.parametrize('code', ['2', '3', '', None, 'X', '9'])
def test_unknown_codes_have_no_direction(code):
    assert direction(code) is None
    assert label(code) is None


@pytest.mark.parametrize('code,expected', [
    ('0', 'เข้างาน'), ('IN', 'เข้างาน'), ('4', 'เข้างานพิเศษ'),
    ('1', 'ออกงาน'), ('OUT', 'ออกงาน'), ('5', 'ออกงานพิเศษ'),
])
def test_labels_stay_compatible_with_existing_logs(code, expected):
    # notify_logs.scan_type เก็บ label นี้ และใช้กันส่งซ้ำ — ต้องไม่เปลี่ยน
    assert label(code) == expected


def test_sets_are_disjoint_and_sql_list_is_quoted():
    assert not (CHECKIN & CHECKOUT)
    assert sql_list(CHECKIN) == "'0','4','IN'"
    assert sql_list(CHECKOUT) == "'1','5','OUT'"
