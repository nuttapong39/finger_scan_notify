"""ค่าของเครื่องและความลับ — อ่านจาก .env (ไฟล์ที่ git ไม่เก็บ สิทธิ์ 600) ตอน import ครั้งเดียว

- APP_ENV=production   : ต้องมี SECRET_KEY (≥ 32 ตัว) และ HOSOFFICE_HOST/USER/DATABASE ไม่งั้นไม่ยอมเริ่ม
- HOSOFFICE_*           : มี HOSOFFICE_HOST = ใช้ค่าจาก .env และไม่ใช้รหัสที่ค้างใน SQLite (ล้างทิ้งตอน init_db)
                          ไม่มี = เครื่องพัฒนา ใช้ค่าจากหน้าตั้งค่าฐานข้อมูล (SQLite) แบบเดิม
- MONITOR_AUTOSTART=1   : เครื่องหลัก — เริ่ม Monitor และการเตือนเองตอนเปิดแอป (ADR 0001: เปิดได้เครื่องเดียว)

อ่านค่าผ่าน settings.current เสมอ (อย่า from settings import current) เพื่อให้ test สลับค่าได้
"""
import os
import secrets
from dataclasses import dataclass

from dotenv import dotenv_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
MIN_SECRET_LEN = 32
TRUE_VALUES = ('1', 'true', 'yes', 'on')


class SettingsError(RuntimeError):
    """.env ไม่ครบหรือค่าผิด — ข้อความบอกชื่อค่าที่ต้องแก้"""


@dataclass(frozen=True)
class Settings:
    app_env: str
    secret_key: str
    hosoffice: dict | None
    monitor_autostart: bool
    listen_host: str
    listen_port: int

    @property
    def is_production(self):
        return self.app_env == 'production'


def _port(env, name, default):
    raw = (env.get(name) or '').strip() or str(default)
    try:
        port = int(raw)
    except ValueError:
        raise SettingsError(f'{name} ต้องเป็นตัวเลข (ได้ "{raw}")') from None
    if not 0 < port < 65536:
        raise SettingsError(f'{name} ต้องอยู่ระหว่าง 1-65535')
    return port


def load(env):
    """สร้าง Settings จาก dict ของค่า (os.environ หรือค่าจาก .env)"""
    env = {k: (v or '') for k, v in env.items()}
    app_env = env.get('APP_ENV', '').strip().lower() or 'development'
    prod = app_env == 'production'

    secret = env.get('SECRET_KEY', '').strip()
    if prod and not secret:
        raise SettingsError('ต้องตั้ง SECRET_KEY ใน .env (สร้างด้วย: python -c "import secrets; print(secrets.token_hex(32))")')
    if prod and len(secret) < MIN_SECRET_LEN:
        raise SettingsError(f'SECRET_KEY ต้องยาวอย่างน้อย {MIN_SECRET_LEN} ตัว')
    secret = secret or secrets.token_hex(32)       # เครื่องพัฒนา: สุ่มใหม่ทุกครั้งที่เปิดแอป (ต้องล็อกอินใหม่)

    hosoffice = None
    if env.get('HOSOFFICE_HOST', '').strip():
        hosoffice = {
            'host': env['HOSOFFICE_HOST'].strip(),
            'port': _port(env, 'HOSOFFICE_PORT', 3306),
            'user': env.get('HOSOFFICE_USER', '').strip(),
            'password': env.get('HOSOFFICE_PASSWORD', ''),
            'database': env.get('HOSOFFICE_DATABASE', '').strip(),
        }
    if prod:
        for name, ok in (('HOSOFFICE_HOST', hosoffice), ('HOSOFFICE_USER', hosoffice and hosoffice['user']),
                         ('HOSOFFICE_DATABASE', hosoffice and hosoffice['database'])):
            if not ok:
                raise SettingsError(f'ต้องตั้ง {name} ใน .env')

    return Settings(
        app_env=app_env, secret_key=secret, hosoffice=hosoffice,
        monitor_autostart=env.get('MONITOR_AUTOSTART', '').strip().lower() in TRUE_VALUES,
        listen_host=env.get('LISTEN_HOST', '').strip() or '127.0.0.1',
        listen_port=_port(env, 'LISTEN_PORT', 5050),
    )


def load_file(path=ENV_PATH, environ=None):
    """ค่าใน .env ก่อน แล้วตัวแปรระบบทับได้ (systemd Environment=)"""
    values = dotenv_values(path) if os.path.exists(path) else {}
    return load({**values, **(os.environ if environ is None else environ)})


current = load_file()
