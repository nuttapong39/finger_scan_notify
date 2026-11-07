import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import hosoffice  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_mysql(monkeypatch):
    """test ห้ามต่อ hosoffice จริง — ทุก query ล้มเหลวเหมือน DB ล่ม เว้นแต่ test จะ mock เอง"""
    monkeypatch.setattr(hosoffice, 'get_mysql_connection', lambda: None)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """SQLite ชั่วคราวต่อ test — ไม่แตะ data/app_config.db จริง"""
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'test.db'))
    database.init_db()
    return database.DB_PATH
