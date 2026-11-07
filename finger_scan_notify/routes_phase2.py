"""หน้าเว็บ + API ของ phase 2: ตรวจสอบและส่งย้อนหลัง (/backfill) และเตือนยังไม่สแกน (/reminders)"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

import backfill
import database
import hosoffice
import reminders

bp = Blueprint('phase2', __name__)
MAX_IDS = 5000


def _today():
    return datetime.now().date()


def _ids(value, field):
    """list ของ int จาก JSON — raise backfill.BackfillError ถ้าไม่ถูกต้อง"""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_IDS:
        raise backfill.BackfillError(f'{field} ต้องเป็นรายการไม่เกิน {MAX_IDS} ค่า')
    try:
        return [int(v) for v in value]
    except (TypeError, ValueError):
        raise backfill.BackfillError(f'{field} ต้องเป็นตัวเลข') from None


def _bad(msg, code=400):
    return jsonify({'success': False, 'message': msg}), code


def _hos_down(e):
    return _bad(f'ต่อฐานข้อมูล hosoffice ไม่ได้ — ยังไม่ได้ทำอะไร ({e})', 503)


def _token_people():
    return [{'person_id': t['person_id'], 'name': t.get('person_name') or f"Person {t['person_id']}",
             'department': t.get('department') or '', 'provider': (t.get('provider') or '').upper(),
             'active': bool(t.get('active')), 'remind_enabled': bool(t.get('remind_enabled'))}
            for t in database.get_all_tokens()]


# ── ตรวจสอบและส่งย้อนหลัง ─────────────────────────────────────
@bp.route('/backfill')
def backfill_page():
    people = [p for p in _token_people() if p['active']]
    return render_template('backfill.html', people=people, today=_today().isoformat(),
                           confirm_days=backfill.CONFIRM_DAYS, confirm_text=backfill.CONFIRM_TEXT,
                           max_per_job=backfill.MAX_PER_JOB)


@bp.route('/api/backfill/status', methods=['POST'])
def api_backfill_status():
    body = request.get_json(silent=True) or {}
    try:
        d1, d2 = backfill.parse_range(body.get('date_from'), body.get('date_to'), _today())
        pids = _ids(body.get('person_ids'), 'person_ids')
        if not pids:
            raise backfill.BackfillError('เลือกบุคลากรอย่างน้อย 1 คน')
        scans = hosoffice.get_scans_between(d1, d2, pids)
    except backfill.BackfillError as e:
        return _bad(str(e))
    except hosoffice.HosofficeUnavailable as e:
        return _hos_down(e)
    token_pids = {p['person_id'] for p in _token_people() if p['active']}
    rows = backfill.build_rows(scans, backfill.delivery_status([s['ID'] for s in scans]), token_pids)
    return jsonify({'success': True, 'rows': rows, 'truncated': len(scans) >= hosoffice.MAX_ROWS,
                    'needs_confirm': backfill.needs_confirm(d1, _today())})


def _job_args(body):
    d1, d2 = backfill.parse_range(body.get('date_from'), body.get('date_to'), _today())
    return d1, d2, _ids(body.get('scan_ids'), 'scan_ids'), _ids(body.get('resend_ids'), 'resend_ids')


@bp.route('/api/backfill/preview', methods=['POST'])
def api_backfill_preview():
    body = request.get_json(silent=True) or {}
    try:
        d1, d2, scan_ids, resend_ids = _job_args(body)
        return jsonify({'success': True, **backfill.preview(scan_ids, resend_ids, d1, d2, _today())})
    except backfill.BackfillError as e:
        return _bad(str(e))
    except hosoffice.HosofficeUnavailable as e:
        return _hos_down(e)


@bp.route('/api/backfill/jobs', methods=['POST'])
def api_backfill_start():
    body = request.get_json(silent=True) or {}
    try:
        d1, d2, scan_ids, resend_ids = _job_args(body)
        job_id = backfill.start_job(scan_ids, resend_ids, d1, d2, body.get('confirm_text', ''), _today())
    except backfill.BackfillError as e:
        return _bad(str(e))
    except hosoffice.HosofficeUnavailable as e:
        return _hos_down(e)
    return jsonify({'success': True, 'job_id': job_id})


@bp.route('/api/backfill/jobs/<int:job_id>')
def api_backfill_job(job_id):
    job = database.get_backfill_job(job_id)
    return jsonify({'success': True, 'job': job}) if job else _bad('ไม่พบงาน', 404)


@bp.route('/api/backfill/jobs/<int:job_id>/cancel', methods=['POST'])
def api_backfill_cancel(job_id):
    backfill.cancel_job(job_id)
    return jsonify({'success': True, 'message': 'สั่งหยุดแล้ว (หยุดหลังข้อความที่กำลังส่ง)'})


# ── เตือนยังไม่สแกน ────────────────────────────────────────────
@bp.route('/reminders')
def reminders_page():
    return render_template('reminders.html', cfg=database.get_reminder_config(),
                           people=[p for p in _token_people() if p['active']],
                           log=database.get_reminder_log(_today().isoformat()), status=reminders.status)


@bp.route('/api/reminders/config', methods=['GET', 'POST'])
def api_reminders_config():
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        cleaned, errors = {}, {}
        for kind in reminders.KINDS:
            if kind in body:
                clean, err = reminders.validate_config(body[kind])
                if err:
                    errors[kind] = err
                cleaned[kind] = clean
        if not cleaned:
            return _bad('ไม่มีค่าที่จะบันทึก')
        if errors:
            return jsonify({'success': False, 'message': 'ค่าไม่ถูกต้อง', 'errors': errors}), 400
        for kind, c in cleaned.items():
            database.save_reminder_config(kind, c['enabled'], c['remind_at'], c['remind_until'],
                                          c['button_url'], c['button_label'])
    return jsonify({'success': True, 'config': database.get_reminder_config()})


@bp.route('/api/reminders/recipients', methods=['POST'])
def api_reminders_recipients():
    body = request.get_json(silent=True) or {}
    try:
        pids = _ids(body.get('person_ids'), 'person_ids')
    except backfill.BackfillError as e:
        return _bad(str(e))
    n = database.set_remind_enabled(pids, bool(body.get('enabled')))
    return jsonify({'success': True, 'updated': n})


@bp.route('/api/reminders/today')
def api_reminders_today():
    return jsonify({'success': True, 'log': database.get_reminder_log(_today().isoformat()),
                    'status': {k: v for k, v in reminders.status.items() if k != 'last_report'},
                    'last_report': reminders.status.get('last_report')})


@bp.route('/api/reminders/preview', methods=['POST'])
def api_reminders_preview():
    """dry-run: ถ้าเตือนตอนนี้ (ไม่สนช่วงเวลา) จะเตือนใครบ้าง — ไม่ส่ง ไม่จอง"""
    return jsonify({'success': True, 'report': reminders.run_cycle(dry_run=True, ignore_window=True)})
