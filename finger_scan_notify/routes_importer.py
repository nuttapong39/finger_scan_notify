"""API ของ Scan importer บนหน้า Watch: สถานะสด, ตั้งจุดเริ่มต้น, ดูตัวอย่างการปล่อยแถวที่กัก (dry-run)
ไม่ส่งชื่อหรือรหัสบุคคลออกไปเลย — มีแต่เลข จำนวน และวันที่ ; ยังไม่มีปุ่มเขียนจริง (โหมดเขียนยังไม่เปิด)"""
from datetime import datetime

from flask import Blueprint, jsonify, request

import database
import hosoffice
import settings
import tigersoft

from scan_importer import plan

bp = Blueprint('importer', __name__)


def _when(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else None


def _minute(t):
    return t.strftime('%Y-%m-%d %H:%M') if t else None


def _held_summary(rows):
    times = sorted(t for t in (plan.row_time(r) for r in rows) if t)
    reasons = {}
    for r in rows:
        reasons[r['reason']] = reasons.get(r['reason'], 0) + 1
    return {'count': len(rows), 'reasons': reasons, 'seq_min': rows[0]['seq'] if rows else None,
            'seq_max': rows[-1]['seq'] if rows else None,
            'oldest': _minute(times[0]) if times else None, 'newest': _minute(times[-1]) if times else None}


@bp.route('/api/importer/status')
def api_status():
    st = database.get_importer_state()
    mode = settings.current.importer_mode
    return jsonify({'success': True, 'mode': mode, 'checkpoint': st['checkpoint'],
                    'needs_baseline': mode != 'off' and st['checkpoint'] is None,
                    'last_round_at': _when(st['last_round_at']), 'last_error': st['last_error'], 'counts': st['last_counts'],
                    'held': _held_summary(database.get_importer_held()), 'tiger_configured': tigersoft.configured(),
                    'can_write': False, 'write_blocked_reason': 'โหมดเขียนยังไม่เปิดใช้ (ตอนนี้เทียบอย่างเดียว)'})


@bp.route('/api/importer/baseline', methods=['POST'])
def api_baseline():
    if settings.current.importer_mode == 'off':
        return jsonify({'success': False, 'message': 'importer ปิดอยู่ (SCAN_IMPORTER_MODE=off)'}), 409
    if database.get_importer_state()['checkpoint'] is not None:
        return jsonify({'success': False, 'message': 'มีจุดล่าสุดอยู่แล้ว ไม่เปลี่ยนซ้ำ'}), 409
    try:
        latest = tigersoft.get_latest_seq()
    except tigersoft.TigerUnavailable as e:
        return jsonify({'success': False, 'message': f'อ่านเครื่องสแกนไม่ได้ ({e})'}), 503
    if latest is None:
        return jsonify({'success': False, 'message': 'เครื่องสแกนยังไม่มีเลขล่าสุด'}), 503
    database.save_importer_state(checkpoint=latest)
    return jsonify({'success': True, 'checkpoint': latest, 'message': f'ตั้งจุดเริ่มต้นที่เลข {latest} (จะนำเข้าเฉพาะเลขที่ใหม่กว่านี้)'})


@bp.route('/api/importer/release-preview')
def api_release_preview():
    try:
        lo, hi = int(request.args['from']), int(request.args['to'])
    except (KeyError, ValueError):
        return jsonify({'success': False, 'message': 'ระบุช่วงเลข from และ to เป็นตัวเลข'}), 400
    if lo > hi:
        return jsonify({'success': False, 'message': 'from ต้องไม่มากกว่า to'}), 400
    held = database.get_importer_held(lo, hi)
    existing = set()
    if held:
        oldest = min((plan.row_time(r) for r in held if plan.row_time(r)), default=datetime.now())
        try:
            existing = hosoffice.get_existing_keys(oldest)
        except hosoffice.HosofficeUnavailable as e:
            return jsonify({'success': False, 'message': f'ต่อ hosoffice ไม่ได้ ({e})'}), 503
    p = plan.plan_release(held, existing, lo, hi)
    s = p['summary']
    return jsonify({'success': True, 'dry_run': True, 'held_in_range': len(held), 'will_write': s['count'],
                    'already_present': len(p['duplicate']), 'unreleasable': [r['seq'] for r in p['unreleasable']][:50],
                    'oldest': _minute(s['oldest']), 'newest': _minute(s['newest']), 'per_day': s['per_day'], 'writer': p['writer']})
