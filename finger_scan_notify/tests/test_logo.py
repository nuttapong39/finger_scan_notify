"""Logo ใน header ของ Flex card = ตราโรงพยาบาลเชียงกลาง (logo_ck.png ที่อยู่บนเว็บ รพ.)"""
import database
from cards import DEFAULT_LOGO_URL, build_flex_message

OLD_PLACEHOLDER = 'https://morpromt2c.moph.go.th/image/image_3771a3e8-57d0-4fe0-b0f8-3c97427eb201.png'


def test_fresh_install_uses_hospital_logo(tmp_db):
    assert database.get_api_config()['hospital_logo'] == DEFAULT_LOGO_URL


def test_old_placeholder_logo_is_replaced_on_startup(tmp_db):
    database.save_api_config('https://x', 'รพ.', OLD_PLACEHOLDER)
    database.init_db()
    assert database.get_api_config()['hospital_logo'] == DEFAULT_LOGO_URL


def test_logo_chosen_by_admin_is_kept(tmp_db):
    database.save_api_config('https://x', 'รพ.', 'https://example.org/other.png')
    database.init_db()
    assert database.get_api_config()['hospital_logo'] == 'https://example.org/other.png'


def test_card_header_shows_hospital_logo(tmp_db):
    logo = database.get_api_config()['hospital_logo']
    msgs = build_flex_message({'ID': 1, 'PERSON_ID': 1, 'HR_FULLNAME': 'ก', 'SCAN_TYPE': '0',
                               'SCAN_DATE_TIME': '2026-11-06 08:00:00'}, 'รพ.', logo)
    header = msgs[0]['contents']['body']['contents'][0]
    assert header['contents'][0]['contents'][0]['url'] == 'https://www.ckhospital.net/home/PDF/Logo_ck.png'
