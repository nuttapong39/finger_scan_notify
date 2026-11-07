import sqlite3

import database
from flex_theme import DEFAULT_THEME


def test_defaults_when_nothing_saved(tmp_db):
    assert database.get_flex_theme() == DEFAULT_THEME


def test_save_and_load_roundtrip(tmp_db):
    theme = {**DEFAULT_THEME, 'checkin_start': '#ABCDEF', 'footer_text': 'ทดสอบ'}
    database.save_flex_theme(theme)
    assert database.get_flex_theme() == theme
    database.save_flex_theme({**theme, 'angle': 45})          # ทับแถวเดิม ไม่เพิ่มแถว
    assert database.get_flex_theme()['angle'] == 45
    conn = sqlite3.connect(tmp_db)
    assert conn.execute('SELECT COUNT(*) FROM flex_theme').fetchone()[0] == 1
    conn.close()


def test_corrupt_stored_json_falls_back_to_defaults(tmp_db):
    conn = sqlite3.connect(tmp_db)
    conn.execute("INSERT OR REPLACE INTO flex_theme (id, data) VALUES (1, '{not json')")
    conn.commit()
    conn.close()
    assert database.get_flex_theme() == DEFAULT_THEME


def test_init_db_is_idempotent_and_keeps_saved_theme(tmp_db):
    database.save_flex_theme({**DEFAULT_THEME, 'angle': 10})
    database.init_db()
    assert database.get_flex_theme()['angle'] == 10
