"""ล็อกอิน Admin (บัญชีเดียว) — Q9–Q13 ของ docs/design/phase3-deploy-almalinux.md

- รหัสเริ่มต้นสุ่มด้วย manage.py init-admin / reset-admin (แสดงครั้งเดียว เก็บแค่ hash) และต้องเปลี่ยนก่อนใช้งาน
- guard ครอบทุก route: ไม่ล็อกอิน → /login (API → 401 JSON) ; ยังไม่เปลี่ยนรหัสชั่วคราว → ใช้ได้แค่ Dashboard
  (ซึ่งแสดง pop-up ปิดไม่ได้) เปลี่ยนรหัส และออกจากระบบ
- ผิด 5 ครั้งล็อก 15 นาทีต่อ IP · ไม่ได้ใช้งาน 8 ชม. ออกจากระบบ (การ poll เบื้องหลังไม่นับ)
- non-GET ทุกคำขอต้องมี Origin/Referer เป็นของระบบเอง
- เปลี่ยนรหัส = เพิ่ม session_version → session ใน browser อื่นหลุดทั้งหมด
"""
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import database

USERNAME = 'admin'
MIN_LENGTH = 10
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
IDLE_SECONDS = 8 * 3600
TEMP_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # ไม่มี 0 O 1 l I

PUBLIC_ENDPOINTS = {'auth.login', 'static'}
MUST_CHANGE_ENDPOINTS = {'auth.change_password', 'auth.logout', 'dashboard'}
# คำขอที่หน้าเว็บยิงเองเป็นระยะ — ไม่นับเป็นการใช้งาน (ไม่งั้น session ไม่มีวันหมดอายุ)
BACKGROUND_ENDPOINTS = {'monitor_status_api', 'api_dashboard_data', 'phase2.api_backfill_job',
                        'phase2.api_reminders_today', 'watch.api_status'}

bp = Blueprint('auth', __name__)


class AuthError(ValueError):
    """ข้อความภาษาไทยที่แสดงบนหน้าเว็บได้"""


def _now():
    return time.time()


# ── บัญชี ─────────────────────────────────────────────────────
def get_account():
    conn = database.get_sqlite_conn()
    row = conn.execute("SELECT * FROM admin_account WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else None


def _temp_password():
    raw = ''.join(secrets.choice(TEMP_ALPHABET) for _ in range(12))
    return f'{raw[:4]}-{raw[4:8]}-{raw[8:]}'


def create_admin(reset=False):
    """สร้างบัญชี admin (หรือ reset) ด้วยรหัสชั่วคราว — คืนรหัสชั่วคราวให้แสดงครั้งเดียว"""
    if get_account() and not reset:
        raise AuthError('มีบัญชี admin แล้ว — ถ้าลืมรหัสใช้คำสั่ง reset-admin')
    temp = _temp_password()
    conn = database.get_sqlite_conn()
    conn.execute("""INSERT INTO admin_account (id, username, password_hash, must_change, session_version)
                    VALUES (1, ?, ?, 1, 1)
                    ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash, must_change=1,
                        session_version=admin_account.session_version + 1, changed_at=CURRENT_TIMESTAMP""",
                 (USERNAME, generate_password_hash(temp)))
    conn.commit(); conn.close()
    return temp


def verify_password(username, password):
    acc = get_account()
    return bool(acc and username == acc['username'] and check_password_hash(acc['password_hash'], password or ''))


def change_password(current, new, confirm):
    """คืน session_version ใหม่ — raise AuthError ถ้าไม่ผ่านเงื่อนไข"""
    acc = get_account()
    if not acc or not check_password_hash(acc['password_hash'], current or ''):
        raise AuthError('รหัสปัจจุบันไม่ถูกต้อง')
    if len(new or '') < MIN_LENGTH:
        raise AuthError(f'รหัสใหม่ต้องยาวอย่างน้อย {MIN_LENGTH} ตัวอักษร')
    if new != confirm:
        raise AuthError('รหัสใหม่ทั้งสองช่องไม่ตรงกัน')
    if new == current:
        raise AuthError('รหัสใหม่ซ้ำกับรหัสเดิม')
    conn = database.get_sqlite_conn()
    conn.execute("""UPDATE admin_account SET password_hash=?, must_change=0,
                    session_version=session_version + 1, changed_at=CURRENT_TIMESTAMP WHERE id=1""",
                 (generate_password_hash(new),))
    conn.commit(); conn.close()
    return get_account()['session_version']


# ── กันเดารหัส (ต่อ IP) ───────────────────────────────────────
def _attempts(ip):
    conn = database.get_sqlite_conn()
    row = conn.execute("SELECT failures, locked_until FROM login_attempts WHERE ip=?", (ip,)).fetchone()
    conn.close()
    return (row['failures'], row['locked_until']) if row else (0, 0)


def locked_seconds(ip):
    _, until = _attempts(ip)
    return max(0, int(until - _now())) if until else 0


def _record_failure(ip):
    """อ่าน-เพิ่ม-เขียน ใน transaction เดียว (BEGIN IMMEDIATE) — waitress มีหลาย thread การนับต้องไม่หาย"""
    conn = database.get_sqlite_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT failures FROM login_attempts WHERE ip=?", (ip,)).fetchone()
        failures = (row['failures'] if row else 0) + 1
        locked_until = _now() + LOCK_SECONDS if failures >= MAX_FAILURES else 0
        conn.execute("""INSERT INTO login_attempts (ip, failures, locked_until) VALUES (?, ?, ?)
                        ON CONFLICT(ip) DO UPDATE SET failures=excluded.failures, locked_until=excluded.locked_until""",
                     (ip, 0 if locked_until else failures, locked_until))
        conn.commit()
    finally:
        conn.close()


def _clear_failures(ip):
    conn = database.get_sqlite_conn()
    conn.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
    conn.commit(); conn.close()


# ── Flask ─────────────────────────────────────────────────────
def _wants_json():
    return (request.path.startswith(('/api/', '/monitor/')) or request.method != 'GET'
            or request.is_json)


def _same_origin():
    own = f'{request.scheme}://{request.host}'
    origin = request.headers.get('Origin')
    if origin:
        return origin == own
    referer = request.headers.get('Referer', '')
    return referer == own or referer.startswith(own + '/')


def _safe_next(target):
    """รับเฉพาะ path ภายในระบบ (กัน open redirect)"""
    target = (target or '').strip()
    parts = urlsplit(target)
    if not target.startswith('/') or target.startswith(('//', '/\\')) or parts.scheme or parts.netloc:
        return url_for('dashboard')
    return target


def _login_required_response():
    if _wants_json():
        return jsonify({'success': False, 'message': 'กรุณาเข้าสู่ระบบ', 'login_required': True}), 401
    return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))


def guard():
    """before_request ของทั้งแอป — คืน response เมื่อไม่อนุญาต"""
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not _same_origin():
        return jsonify({'success': False, 'message': 'คำขอไม่ได้มาจากหน้าของระบบ'}), 403
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if request.endpoint is None:
        return None                      # ไม่มี route นี้ → Flask ตอบ 404 (ไม่มีอะไรให้ป้องกัน)

    acc = get_account()
    now = _now()
    if (not acc or session.get('admin') != acc['username']
            or session.get('ver') != acc['session_version']
            or now - float(session.get('last_active') or 0) > IDLE_SECONDS):
        session.clear()
        return _login_required_response()
    if request.endpoint not in BACKGROUND_ENDPOINTS:
        session['last_active'] = now

    if acc['must_change'] and request.endpoint not in MUST_CHANGE_ENDPOINTS:
        if request.method == 'GET' and not _wants_json():
            return redirect(url_for('dashboard'))
        return jsonify({'success': False, 'message': 'ต้องเปลี่ยนรหัสผ่านชั่วคราวก่อนใช้งาน',
                        'must_change_password': True}), 403
    return None


def must_change_password():
    """admin ที่ล็อกอินอยู่ยังใช้รหัสชั่วคราว (หน้า Dashboard ใช้ตัดสินว่าจะแสดงข้อมูลหรือไม่)"""
    acc = get_account() if session.get('admin') else None
    return bool(acc and acc['must_change'])


def template_context():
    return {'must_change_password': must_change_password(), 'admin_name': session.get('admin')}


def install(app, secret_key):
    app.secret_key = secret_key
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=False,                   # HTTP ในวง VLAN (Q13)
                      SESSION_COOKIE_NAME='scan_notify_session',
                      PERMANENT_SESSION_LIFETIME=timedelta(seconds=IDLE_SECONDS))
    app.register_blueprint(bp)
    app.before_request(guard)
    app.context_processor(template_context)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.values.get('next', '')
    if request.method == 'GET':
        if session.get('admin'):
            return redirect(_safe_next(next_url))
        return render_template('login.html', next=next_url, error=None)

    ip = request.remote_addr or '-'
    wait = locked_seconds(ip)
    if wait:
        return render_template('login.html', next=next_url,
                               error=f'กรอกรหัสผิดหลายครั้ง ลองใหม่ในอีก {wait // 60 + 1} นาที'), 429
    username = (request.form.get('username') or '').strip()
    if not verify_password(username, request.form.get('password')):
        _record_failure(ip)
        return render_template('login.html', next=next_url, error='บัญชีหรือรหัสผ่านไม่ถูกต้อง'), 401

    _clear_failures(ip)
    acc = get_account()
    session.clear()
    session.permanent = True
    session.update(admin=acc['username'], ver=acc['session_version'], last_active=_now())
    return redirect(url_for('dashboard') if acc['must_change'] else _safe_next(next_url))


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/account/password', methods=['GET', 'POST'], endpoint='change_password')
def change_password_view():
    if request.method == 'GET':
        return render_template('account_password.html')
    d = request.get_json(silent=True) or request.form
    try:
        ver = change_password(d.get('current'), d.get('new'), d.get('confirm'))
    except AuthError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    session['ver'] = ver                       # session นี้อยู่ต่อ ที่อื่นหลุด
    return jsonify({'success': True, 'message': 'เปลี่ยนรหัสผ่านแล้ว'})
