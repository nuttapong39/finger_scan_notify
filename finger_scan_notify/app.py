import threading
import time
import json as _json
from datetime import datetime, timedelta
import requests as _req
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from database import (
    init_db, get_db_config, save_db_config, save_db_settings, get_api_config, save_api_config,
    get_all_tokens, save_token, delete_token, toggle_token,
    get_last_scan_id, update_last_scan_id,
    get_notify_logs, get_token_by_person_id,
    advance_last_scan_id, was_notified, count_recent_notified,
    add_guard_event, get_guard_events, save_guard_settings,
    get_flex_theme, save_flex_theme, get_sqlite_conn, add_scan_skips, get_reminder_config,
    close_stuck_backfill_jobs
)
from hosoffice import (
    get_recent_scans_from_mysql, get_today_stats, get_new_scans_from_mysql,
    get_max_scan_id, test_mysql_connection, get_muted_person_ids
)
import auth
import reminders
import scan_types
import settings
from cards import THAI_DAYS, build_flex_message, build_backfill_card, build_reminder_card, build_line_message
from delivery import deliver_scan, deliver_reminder, scan_skip_reason
from flex_theme import DEFAULT_THEME, validate_theme
from notifier import LINE_NOTIFY_URL
from routes_phase2 import bp as phase2_bp

app = Flask(__name__)
auth.install(app, settings.current.secret_key)     # ทุก route ต้องล็อกอิน (ยกเว้น /login)
app.register_blueprint(phase2_bp)

# ── Monitor state ─────────────────────────────────────────────
monitor_status = {
    'running': False, 'last_check': None,
    'last_new_count': 0, 'total_notified': 0,
    'errors': 0, 'connected': False, 'last_error': None,
    'skipped_stale': 0, 'skipped_dup': 0, 'skipped_limit': 0,
    'paused_reason': None
}

BATCH_LIMIT = 500   # จำนวน scan สูงสุดที่ดึงต่อรอบ

# ── ระบบป้องกันแจ้งเตือนท่วม ─────────────────────────────────────
#  1) กรอง scan เก่า      : SCAN_DATE_TIME เก่ากว่า max_scan_age_minutes → ไม่ส่ง (ข้ามและเลื่อน checkpoint)
#  2) checkpoint ทีละแถว  : เลื่อน last_scan_id 'ก่อน' ส่งแต่ละแถว (compare-and-set) → crash/หยุดกลางทางก็ไม่ส่งซ้ำ
#                           และถ้ารันหลาย instance ก็ไม่ส่งซ้ำกัน
#  3) กัน record ซ้ำ     : (คน, เวลาสแกน, ประเภท) ที่เคยส่งสำเร็จแล้ว ไม่ส่งอีกแม้ ID ใหม่
#  4) โควตาต่อคน          : ส่งให้คนเดียวไม่เกิน max_per_person_hour ข้อความ/ชั่วโมง
#  5) circuit breaker     : scan 'สด' ในรอบเดียวเกิน flood_limit → หยุด monitor และไม่ส่งอะไร รอคนตัดสินใจ
#  6) ไม่ baseline เป็น 0 : last_scan_id=0 → ตั้งเป็น MAX(ID) (ไม่ส่งย้อนหลัง); ต่อ DB ไม่ได้ → ไม่ทำอะไร

def _to_dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(v[:19], fmt)
            except ValueError:
                pass
    return None

def _guard_log(kind, detail):
    print(f"[GUARD] {kind}: {detail}", flush=True)
    try:
        add_guard_event(kind, detail)
    except Exception:
        pass

def monitor_cycle():
    """ทำงาน 1 รอบ  คืน True ถ้า batch เต็ม (ควรวนต่อทันทีโดยไม่ต้องรอ)"""
    cfg       = get_db_config()
    max_age   = max(1, int(cfg.get('max_scan_age_minutes') or 180))
    flood_lim = max(1, int(cfg.get('flood_limit') or 300))
    per_hour  = max(1, int(cfg.get('max_per_person_hour') or 6))

    last_id = get_last_scan_id()
    if last_id == 0:
        base = get_max_scan_id()
        if base is None:
            raise RuntimeError('ต่อ MySQL ไม่ได้ จึงตั้งจุดเริ่มต้น (baseline) ไม่ได้ — ไม่ส่งแจ้งเตือนใด ๆ')
        advance_last_scan_id(base)
        last_id = base
        _guard_log('baseline', f'last_scan_id เป็น 0 → ตั้งเป็น MAX(ID)={base} (ไม่ส่งย้อนหลัง)')

    new_scans = get_new_scans_from_mysql(last_id, BATCH_LIMIT)   # raise ถ้า DB มีปัญหา
    monitor_status.update(last_check=datetime.now().strftime('%H:%M:%S'),
                          connected=True, last_error=None, last_new_count=len(new_scans))
    if not new_scans:
        return False
    full   = len(new_scans) >= BATCH_LIMIT
    cutoff = datetime.now() - timedelta(minutes=max_age)

    def is_stale(s):
        dt = _to_dt(s.get('SCAN_DATE_TIME'))
        return dt is None or dt < cutoff

    n_fresh = sum(1 for s in new_scans if not is_stale(s))
    if n_fresh > flood_lim:
        reason = (f"พบ scan ใหม่ {n_fresh} แถวในรอบเดียว (เกินเพดาน {flood_lim}) "
                  f"ID {new_scans[0]['ID']}-{new_scans[-1]['ID']} — หยุด Monitor และยังไม่ส่งอะไร "
                  "ตรวจต้นทางก่อน แล้วค่อยตั้ง last_scan_id หรือเพิ่ม flood_limit ก่อนกด Start")
        monitor_status.update(running=False, paused_reason=reason, last_error=reason)
        _guard_log('flood_guard', reason)
        return False

    n_stale = n_dup = n_limit = 0
    stale_first = stale_last = None
    claimed_max = last_id       # ID สูงสุดที่ commit checkpoint แล้ว
    pending_max = last_id       # ID สูงสุดที่ตรวจผ่านแล้ว (รวมแถวที่ข้าม)
    token_pids = {t['person_id'] for t in get_all_tokens() if t.get('active')}
    muted = get_muted_person_ids()          # raise ถ้า DB มีปัญหา → รอบนี้ไม่ส่งอะไร
    skips = []                              # เหตุผลการข้าม (Delivery status "ถูกข้าม") เฉพาะคนที่มี token

    def skip(scan, pid, reason):
        skips.append((int(scan.get('ID') or 0), pid, 'live', reason))

    for scan in new_scans:
        if not monitor_status['running']:
            break
        sid = int(scan.get('ID') or 0)
        if sid <= last_id:
            continue
        pid = scan.get('PERSON_ID')

        if is_stale(scan):
            n_stale += 1
            pending_max = max(pending_max, sid)
            stale_first = sid if stale_first is None else stale_first
            stale_last = sid
            if pid in token_pids:
                skip(scan, pid, 'stale')
            continue

        # รับแถวนี้ 'ก่อนส่ง' — False = process อื่นรับไปแล้ว ข้าม
        if not advance_last_scan_id(sid):
            continue
        claimed_max = pending_max = max(claimed_max, sid)

        token_row = get_token_by_person_id(pid)
        if not token_row:
            continue

        label = scan_types.label(scan.get('SCAN_TYPE'))
        if label is None:
            skip(scan, pid, 'unknown_type')
            continue
        if pid in muted:
            skip(scan, pid, 'muted')
            continue
        dt_str = _to_dt(scan.get('SCAN_DATE_TIME')).strftime('%Y-%m-%d %H:%M:%S')
        if was_notified(pid, dt_str, label):
            n_dup += 1
            skip(scan, pid, 'duplicate')
            continue
        if count_recent_notified(pid, 60) >= per_hour:
            n_limit += 1
            skip(scan, pid, 'quota')
            continue
        reason = scan_skip_reason(scan, token_row)
        if reason:
            skip(scan, pid, reason)
            continue

        success, _, _ = deliver_scan(scan, token_row, source='live')
        if success:
            monitor_status['total_notified'] += 1
        else:
            monitor_status['errors'] += 1

    if pending_max > claimed_max:
        advance_last_scan_id(pending_max)
    add_scan_skips(skips)

    monitor_status['skipped_stale'] += n_stale
    monitor_status['skipped_dup']   += n_dup
    monitor_status['skipped_limit'] += n_limit
    if n_stale or n_dup or n_limit:
        _guard_log('skipped',
                   f"ข้าม: scan เก่ากว่า {max_age} นาที {n_stale} แถว"
                   f"{f' (ID {stale_first}-{stale_last})' if n_stale else ''}, "
                   f"ซ้ำ {n_dup}, เกินโควตา {per_hour}/ชม. {n_limit}")
    return full


def monitor_loop():
    while monitor_status['running']:
        full = False
        try:
            full = monitor_cycle()
        except Exception as e:
            monitor_status['connected']  = False
            monitor_status['errors']    += 1
            monitor_status['last_error'] = str(e)

        if full and monitor_status['running']:
            continue            # ยังมี backlog (ซึ่งส่วนใหญ่ถูกข้าม) วนต่อทันที
        try:
            interval = int(get_db_config().get('poll_interval', 10))
        except Exception:
            interval = 10
        for _ in range(interval * 2):
            if not monitor_status['running']: break
            time.sleep(0.5)


def _do_start_monitor():
    """เริ่ม monitor loop — ใช้ได้ทั้งจาก route และ auto-start ตอน startup
    คืน True ถ้าเริ่มแล้ว/ทำงานอยู่ , False ถ้าเริ่มไม่ได้"""
    if monitor_status['running']:
        return True
    if get_last_scan_id() == 0:
        base = get_max_scan_id()
        if base is None:
            monitor_status['last_error'] = 'ต่อ MySQL ไม่ได้ จึงตั้งจุดเริ่มต้นไม่ได้ — ไม่เริ่ม Monitor (กันส่งย้อนหลังทั้งตาราง)'
            return False
        advance_last_scan_id(base)
    monitor_status.update({'running': True, 'errors': 0, 'last_error': None, 'paused_reason': None})
    threading.Thread(target=monitor_loop, daemon=True).start()
    return True

# ── Routes ─────────────────────────────────────────────────────
@app.route('/')
def index(): return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    data   = get_today_stats()
    recent = get_recent_scans_from_mysql(50)
    for r in recent:
        if r.get('SCAN_DATE_TIME') and hasattr(r['SCAN_DATE_TIME'], 'strftime'):
            r['SCAN_DATE_TIME'] = r['SCAN_DATE_TIME'].strftime('%d/%m/%Y %H:%M:%S')
        r['DIRECTION'] = scan_types.direction(r.get('SCAN_TYPE'))
    flex_tokens = [{'id': t['id'], 'name': t.get('person_name') or f"Person {t['person_id']}"}
                   for t in get_all_tokens()
                   if t.get('active') and (t.get('provider') or '').upper() == 'MOPH_ALERT']
    return render_template('dashboard.html',
                           stats=data.get('stats',{}), hourly=data.get('hourly',[]),
                           dept=data.get('dept',[]), trend=data.get('trend',[]),
                           recent=recent, monitor=monitor_status,
                           flex_tokens=flex_tokens,
                           today=datetime.now().strftime('%d/%m/%Y'))

@app.route('/api/dashboard-data')
def api_dashboard_data():
    data   = get_today_stats()
    recent = get_recent_scans_from_mysql(20)
    for r in recent:
        if r.get('SCAN_DATE_TIME') and hasattr(r['SCAN_DATE_TIME'], 'strftime'):
            r['SCAN_DATE_TIME'] = r['SCAN_DATE_TIME'].strftime('%d/%m/%Y %H:%M:%S')
        r['DIRECTION'] = scan_types.direction(r.get('SCAN_TYPE'))
    return jsonify({
        'stats': data.get('stats',{}), 'hourly': data.get('hourly',[]),
        'dept':  data.get('dept',[]),
        'trend': [{**t,'d':str(t.get('d',''))} for t in data.get('trend',[])],
        'recent': recent, 'monitor': monitor_status
    })

# ── Config DB ─────────────────────────────────────────────────
@app.route('/config/database', methods=['GET','POST'])
def config_database():
    """ค่าการเชื่อมต่อ: ถ้าอยู่ใน .env แก้ที่นี่ไม่ได้ (Q6) · รหัสผ่านไม่ถูกส่งออกไปที่ browser เลย (ช่องว่าง = ใช้รหัสเดิม)"""
    env_conn = settings.current.hosoffice
    cur = get_db_config()
    f = request.form
    if request.method == 'POST':
        tbl = f.get('table_name', '').strip() or 'fingle_scan'
        try:
            itv = max(10, min(3600, int(f.get('poll_interval') or 10)))
        except ValueError:
            itv = 10
        if env_conn:
            if 'test' in f:
                ok, msg = test_mysql_connection()
                return jsonify({'success': ok, 'message': msg})
            save_db_settings(tbl, itv)
        else:
            host, user, db = f.get('host','').strip(), f.get('username','').strip(), f.get('database_name','').strip()
            port = f.get('port','3306').strip() or '3306'
            pw = f.get('password') or cur.get('password', '')
            if 'test' in f:
                ok, msg = test_mysql_connection({'host': host, 'port': port, 'user': user,
                                                 'password': pw, 'database': db})
                return jsonify({'success': ok, 'message': msg})
            save_db_config(host, port, user, pw, db, tbl, itv)
        flash('บันทึกการตั้งค่าฐานข้อมูลเรียบร้อย', 'success')
        return redirect(url_for('config_database'))
    cfg = {k: v for k, v in cur.items() if k != 'password'}
    return render_template('config_database.html', cfg=cfg, env_conn=env_conn,
                           has_password=bool(cur.get('password')))

# ── Config API ────────────────────────────────────────────────
@app.route('/config/api', methods=['GET','POST'])
def config_api():
    if request.method == 'POST':
        save_api_config(request.form.get('moph_api_url','').strip(),
                        request.form.get('hospital_name','').strip(),
                        request.form.get('hospital_logo','').strip())
        flash('บันทึกการตั้งค่า API เรียบร้อย', 'success')
        return redirect(url_for('config_api'))
    return render_template('config_api.html', cfg=get_api_config())

# ── Tokens ────────────────────────────────────────────────────
@app.route('/config/tokens')
def config_tokens():
    return render_template('config_tokens.html', tokens=get_all_tokens())

@app.route('/api/tokens', methods=['POST'])
def api_save_token():
    d        = request.get_json()
    pid      = d.get('person_id')
    provider = (d.get('provider') or 'LINE_NOTIFY').upper()
    if not pid:
        return jsonify({'success':False, 'message':'กรุณากรอก Person ID'})
    if provider == 'LINE_NOTIFY' and not (d.get('line_token','').strip()):
        return jsonify({'success':False, 'message':'กรุณากรอก Line Notify Token'})
    if provider == 'MOPH_ALERT' and not (d.get('client_key','').strip()):
        return jsonify({'success':False, 'message':'กรุณากรอก Client Key และ Secret Key'})
    try:
        save_token(pid, d.get('person_name',''), provider,
                   d.get('line_token',''), d.get('client_key',''), d.get('secret_key',''),
                   d.get('department',''),
                   int(d.get('notify_checkin',1)), int(d.get('notify_checkout',1)))
        return jsonify({'success':True, 'message':'บันทึกเรียบร้อย'})
    except Exception as e:
        return jsonify({'success':False, 'message':str(e)})

@app.route('/api/tokens/<int:tid>', methods=['DELETE'])
def api_delete_token(tid):
    delete_token(tid)
    return jsonify({'success':True})

@app.route('/api/tokens/<int:tid>/toggle', methods=['POST'])
def api_toggle_token(tid):
    toggle_token(tid, int(request.get_json().get('active',1)))
    return jsonify({'success':True})

@app.route('/api/test-notify/<int:tid>', methods=['POST'])
def api_test_notify(tid):
    body = request.get_json(silent=True) or {}
    theme, err = _theme_from_body(body)
    if err:
        return err
    scan_type, variant = str(body.get('scan_type', '0')), str(body.get('variant', 'scan'))
    if scan_type not in PREVIEW_SCAN_TYPES or variant not in PREVIEW_VARIANTS:
        return jsonify({'success':False, 'message':'scan_type/variant ไม่ถูกต้อง'}), 400
    conn = get_sqlite_conn()
    row  = conn.execute("SELECT * FROM person_tokens WHERE id=?", (tid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'success':False, 'message':'ไม่พบข้อมูล'})
    row = dict(row)
    provider = (row.get('provider') or 'LINE_NOTIFY').upper()
    if variant.startswith('remind'):
        kind = 'checkin' if variant == 'remind_in' else 'checkout'
        cfg = get_reminder_config()[kind]
        success, code, resp = deliver_reminder(kind, row, cfg, theme=theme)
    else:
        test_scan = {**_preview_scan(scan_type), 'PERSON_ID': row['person_id'],
                     'HR_FULLNAME': row['person_name'] or 'ทดสอบระบบ',
                     'HR_DEP_SUB_NAME': row['department'] or 'งานสารสนเทศทางการแพทย์'}
        success, code, resp = deliver_scan(test_scan, row, source='test', theme=theme,
                                           backfill=(variant == 'backfill'), check_type_flags=False)
    if code == 0 and resp == 'no_keys':
        return jsonify({'success':False, 'message':'token ไม่ครบ (ไม่พบ key)', 'provider':provider})
    try: resp_json = _json.loads(resp)
    except Exception: resp_json = None
    return jsonify({'success':success, 'http_code':code, 'response_raw':resp,
                    'response_json':resp_json, 'provider':provider})

# ── Flex theme (หน้า Dashboard) ───────────────────────────────
PREVIEW_SCAN_TYPES = ('0', '1')   # 0 = เข้างาน, 1 = ออกงาน
PREVIEW_VARIANTS = ('scan', 'backfill', 'remind_in', 'remind_out')

def _preview_scan(scan_type):
    """scan ตัวอย่างสำหรับ preview — ข้อมูลสมมติ ไม่ใช่ข้อมูลบุคลากรจริง"""
    now = datetime.now()
    return {
        'ID': 0, 'PERSON_ID': 0, 'HR_FULLNAME': 'นายตัวอย่าง ทดสอบ',
        'HR_POSITION_NAME': 'พยาบาลวิชาชีพ', 'HR_DEP_NAME': 'กลุ่มงานการพยาบาล',
        'HR_DEP_SUB_NAME': 'งานผู้ป่วยใน', 'HR_PERSON_TYPE_NAME': 'ข้าราชการ',
        'SCAN_TYPE': scan_type, 'SCAN_DATE_TIME': now,
        'DAY_TYPE_NAME': THAI_DAYS[now.weekday()],
        'ISINPUT': 'FACESCAN',
    }

def _theme_from_body(body):
    """(theme, None) หรือ (None, response 400) — ไม่มี theme ใน body = None (ใช้ที่บันทึกไว้)"""
    if body.get('theme') is None:
        return None, None
    theme, errors = validate_theme(body['theme'])
    if errors:
        return None, (jsonify({'success':False, 'message':'Theme ไม่ถูกต้อง', 'errors':errors}), 400)
    return theme, None

@app.route('/api/flex-theme', methods=['GET', 'POST'])
def api_flex_theme():
    if request.method == 'POST':
        theme, errors = validate_theme(request.get_json(silent=True))
        if errors:
            return jsonify({'success':False, 'message':'Theme ไม่ถูกต้อง', 'errors':errors}), 400
        save_flex_theme(theme)
        return jsonify({'success':True, 'message':'บันทึก Theme เรียบร้อย', 'theme':theme})
    return jsonify({'theme': get_flex_theme(), 'defaults': DEFAULT_THEME})

@app.route('/api/flex-preview', methods=['POST'])
def api_flex_preview():
    """สร้าง Flex JSON ตัวเดียวกับที่ส่งจริง เพื่อให้หน้า editor วาด preview (ไม่บันทึกอะไร)"""
    body = request.get_json(silent=True) or {}
    scan_type, variant = str(body.get('scan_type', '0')), str(body.get('variant', 'scan'))
    if scan_type not in PREVIEW_SCAN_TYPES:
        return jsonify({'success':False, 'message':'scan_type ต้องเป็น 0 (เข้างาน) หรือ 1 (ออกงาน)'}), 400
    if variant not in PREVIEW_VARIANTS:
        return jsonify({'success':False, 'message':'variant ไม่ถูกต้อง'}), 400
    theme, err = _theme_from_body(body)
    if err:
        return err
    theme = theme if theme is not None else get_flex_theme()
    api_cfg = get_api_config()
    hosp, logo = api_cfg.get('hospital_name', ''), api_cfg.get('hospital_logo', '')
    if variant == 'backfill':
        messages = build_backfill_card(_preview_scan(scan_type), hosp, logo, theme)
    elif variant.startswith('remind'):
        kind = 'checkin' if variant == 'remind_in' else 'checkout'
        cfg = get_reminder_config()[kind]
        messages = build_reminder_card(kind, {'name': 'นายตัวอย่าง ทดสอบ', 'department': 'งานผู้ป่วยใน'},
                                       hosp, logo, theme, button_url=cfg.get('button_url', ''),
                                       button_label=cfg.get('button_label', ''))
    else:
        messages = build_flex_message(_preview_scan(scan_type), hosp, logo, theme)
    return jsonify({'success':True, 'messages':messages})

# ── Debug ─────────────────────────────────────────────────────
@app.route('/debug')
def debug_page():
    return render_template('debug.html', tokens=get_all_tokens(), api_cfg=get_api_config())

@app.route('/api/debug-send', methods=['POST'])
def api_debug_send():
    d          = request.get_json()
    provider   = (d.get('provider') or 'LINE_NOTIFY').upper()
    api_url    = d.get('api_url','').strip()
    scan_type  = d.get('scan_type','0')

    api_cfg    = get_api_config()
    hosp_name  = api_cfg.get('hospital_name','โรงพยาบาลเชียงกลาง')
    hosp_logo  = api_cfg.get('hospital_logo','')

    test_scan = {
        'ID':0,'PERSON_ID':0,
        'HR_FULLNAME':'ทดสอบระบบ ('+provider+')',
        'HR_POSITION_NAME':'นักวิชาการคอมพิวเตอร์',
        'HR_DEP_NAME':'กลุ่มงานสารสนเทศ',
        'HR_DEP_SUB_NAME':'งานสารสนเทศทางการแพทย์',
        'HR_PERSON_TYPE_NAME':'พนักงานกระทรวงสาธารณสุข',
        'SCAN_TYPE': scan_type,
        'SCAN_DATE_TIME': datetime.now(),
        'DAY_TYPE_NAME': THAI_DAYS[datetime.now().weekday()],
        'ISINPUT':'FACESCAN'
    }

    if provider == 'LINE_NOTIFY':
        line_token = d.get('line_token','').strip()
        if not line_token:
            return jsonify({'error':'กรุณากรอก Line Notify Token'})
        message  = build_line_message(test_scan, hosp_name)
        payload_str = f'message={message}'
        headers  = {'Authorization': f'Bearer {line_token[:8]}...'}
        try:
            r = _req.post(LINE_NOTIFY_URL,
                          headers={'Authorization': f'Bearer {line_token}'},
                          data={'message': message}, timeout=15)
            try: resp_json = r.json()
            except: resp_json = None
            return jsonify({'http_code':r.status_code,'success':r.status_code==200,
                            'response_raw':r.text,'response_json':resp_json,
                            'payload':payload_str,'url':LINE_NOTIFY_URL,
                            'headers_sent':headers,'provider':'LINE_NOTIFY'})
        except Exception as e:
            return jsonify({'http_code':0,'success':False,'response_raw':str(e),
                            'payload':payload_str,'provider':'LINE_NOTIFY'})

    else:  # MOPH_ALERT
        client_key = d.get('client_key','').strip()
        secret_key = d.get('secret_key','').strip()
        if not client_key or not secret_key or not api_url:
            return jsonify({'error':'กรุณากรอก URL, Client Key และ Secret Key'})
        messages    = build_flex_message(test_scan, hosp_name, hosp_logo, get_flex_theme())
        payload     = {"messages": messages}
        payload_str = _json.dumps(payload, ensure_ascii=False, indent=2)
        headers_show = {'client-key': client_key[:8]+'...', 'secret-key': secret_key[:8]+'...',
                        'Content-Type': 'application/json'}
        try:
            r = _req.post(api_url,
                          headers={'client-key':client_key,'secret-key':secret_key,'Content-Type':'application/json'},
                          json=payload, timeout=15)
            try: resp_json = r.json()
            except: resp_json = None
            return jsonify({'http_code':r.status_code,'success':r.status_code==200,
                            'response_raw':r.text[:2000],'response_json':resp_json,
                            'payload':payload_str[:4000],'url':api_url,
                            'headers_sent':headers_show,'provider':'MOPH_ALERT'})
        except Exception as e:
            return jsonify({'http_code':0,'success':False,'response_raw':str(e),
                            'payload':payload_str[:2000],'provider':'MOPH_ALERT'})

# ── Monitor ───────────────────────────────────────────────────
@app.route('/monitor/start', methods=['POST'])
def monitor_start():
    if monitor_status['running']:
        return jsonify({'success':False,'message':'Monitor กำลังทำงานอยู่แล้ว'})
    if not _do_start_monitor():
        return jsonify({'success':False,'message':monitor_status.get('last_error') or 'เริ่ม Monitor ไม่สำเร็จ'})
    return jsonify({'success':True,'message':'เริ่มต้น Monitor แล้ว',
                    'last_scan_id': get_last_scan_id()})

@app.route('/monitor/stop', methods=['POST'])
def monitor_stop():
    monitor_status['running'] = False
    return jsonify({'success':True,'message':'หยุด Monitor แล้ว'})

@app.route('/monitor/reset', methods=['POST'])
def monitor_reset():
    update_last_scan_id(0)
    return jsonify({'success':True,'message':'Reset แล้ว — Monitor จะเริ่มนับจาก ID ล่าสุดของฐานข้อมูล (ไม่ส่งย้อนหลัง)'})

@app.route('/monitor/set-last-id', methods=['POST'])
def monitor_set_last_id():
    """ตั้งค่า last_scan_id สำหรับทดสอบ — ระบบจะดึง scan ที่ ID > ค่านี้"""
    scan_id = int(request.get_json().get('scan_id', 0))
    update_last_scan_id(scan_id)
    return jsonify({'success':True,'last_scan_id':scan_id,
                    'message':f'ตั้งค่า last_scan_id={scan_id} แล้ว monitor จะดึง ID > {scan_id}'})

@app.route('/monitor/status')
def monitor_status_api():
    # primary=False + running=True → หน้าเว็บแสดงแถบแดง: เครื่องนี้ไม่ใช่เครื่องหลัก อาจส่งซ้ำกับ Server (ADR 0001)
    return jsonify({**monitor_status, 'last_scan_id': get_last_scan_id(),
                    'primary': settings.current.monitor_autostart})

# ── Guard settings / events ───────────────────────────────────
@app.route('/api/guard-settings', methods=['GET','POST'])
def api_guard_settings():
    if request.method == 'POST':
        d = request.get_json() or {}
        cur = get_db_config()
        try:
            save_guard_settings(d.get('max_scan_age_minutes', cur.get('max_scan_age_minutes', 180)),
                                d.get('flood_limit',          cur.get('flood_limit', 300)),
                                d.get('max_per_person_hour',  cur.get('max_per_person_hour', 6)))
        except (TypeError, ValueError):
            return jsonify({'success':False,'message':'ค่าต้องเป็นจำนวนเต็ม'})
    cfg = get_db_config()
    return jsonify({'success':True,
                    'max_scan_age_minutes': cfg.get('max_scan_age_minutes'),
                    'flood_limit':          cfg.get('flood_limit'),
                    'max_per_person_hour':  cfg.get('max_per_person_hour')})

@app.route('/api/guard-events')
def api_guard_events():
    return jsonify(get_guard_events(100))

# ── Logs ──────────────────────────────────────────────────────
@app.route('/logs')
def logs():
    return render_template('logs.html', logs=get_notify_logs(500))

@app.route('/api/logs')
def api_logs():
    return jsonify(get_notify_logs(100))

# ── Startup ───────────────────────────────────────────────────
def startup():
    """เรียกครั้งเดียวตอนเปิด process (serve.py หรือ python app.py)
    เริ่ม Monitor/การเตือนเองเฉพาะเครื่องหลัก (MONITOR_AUTOSTART=1) — ADR 0001"""
    init_db()
    closed = close_stuck_backfill_jobs()
    s = settings.current
    print("=" * 55)
    print("  Finger Scan Notify — โรงพยาบาลเชียงกลาง")
    if closed:
        print(f"  ปิดงานส่งย้อนหลังที่ค้างจากการปิดแอปกลางคัน {closed} งาน")
    if s.monitor_autostart:
        started = _do_start_monitor()
        print("  MOPH Alert Monitor: " + ("AUTO-STARTED" if started
                                         else "NOT STARTED — " + str(monitor_status["last_error"])))
        reminders.start()
        print("  เตือนยังไม่สแกน: ตรวจทุก 60 วินาที")
    else:
        print("  เครื่องนี้ไม่ใช่เครื่องหลัก (MONITOR_AUTOSTART ไม่ได้ตั้ง) — Monitor และการเตือนไม่เริ่มเอง")
    if auth.get_account() is None:
        print("  ยังไม่มีบัญชี admin — รัน: python manage.py init-admin")
    print("=" * 55, flush=True)


if __name__ == '__main__':
    # เครื่องพัฒนา: Flask dev server · Server จริงใช้ serve.py (waitress) ผ่าน systemd
    startup()
    app.run(host=settings.current.listen_host, port=settings.current.listen_port, debug=False)
