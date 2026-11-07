"""จุดเข้าของ Server: waitress process เดียว (ADR 0001) ฟังที่ 127.0.0.1 หลัง nginx

    .venv/bin/python serve.py          # systemd: deploy/scan-notify.service

ห้ามรันหลาย process / หลาย worker / หลายเครื่องพร้อมกัน — Monitor และการเตือนอยู่ใน process นี้
"""
from werkzeug.middleware.proxy_fix import ProxyFix

import settings


def wsgi_app():
    from app import app
    if settings.current.is_production:
        # nginx ข้างหน้าชั้นเดียว: IP ของ browser (นับรหัสผิด) และ http/https (ตรวจ Origin) มาจาก header ของ nginx
        return ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0)
    return app.wsgi_app


def main():
    import os
    from waitress import serve
    import app as app_module
    os.umask(0o077)              # SQLite มี key ของ token ทุกคน (systemd ตั้ง UMask=0077 ด้วย)
    s = settings.current
    app_module.startup()
    print(f'Finger Scan Notify — {s.app_env} — http://{s.listen_host}:{s.listen_port}', flush=True)
    serve(wsgi_app(), host=s.listen_host, port=s.listen_port, threads=8, ident=None)


if __name__ == '__main__':
    main()
