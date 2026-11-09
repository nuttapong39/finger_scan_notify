"""API ของ Scan importer บนหน้า Watch: สถานะสด, ตั้งจุดเริ่มต้น, ดูตัวอย่างการปล่อยแถวที่กัก (dry-run)
ไม่ส่งชื่อหรือรหัสบุคคลออกไปเลย — มีแต่เลข จำนวน และวันที่ ; ยังไม่มีปุ่มเขียนจริง (โหมดเขียนยังไม่เปิด)"""
from datetime import datetime

from flask import Blueprint, jsonify, request, session

import database
import hosoffice
import settings
import tigersoft

from scan_importer import plan, runner

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
                    'totals': {**st['totals'], 'since': _when(st['totals'].get('since')), 'last_new_at': _when(st['totals'].get('last_new_at'))},
                    'held': _held_summary(database.get_importer_held()), 'tiger_configured': tigersoft.configured(),
                    'can_write': mode == 'write', 'write_blocked_reason': 'ปล่อยแถวที่กักได้เฉพาะโหมด write (SCAN_IMPORTER_MODE=write ใน .env)',
                    'audit': [{'at': _when(a['at']), 'who': a['who'], 'action': a['action'], 'detail': a['detail']}
                              for a in database.get_importer_audit(5)]})


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
    database.save_importer_state(checkpoint=latest, totals={'since': datetime.now().timestamp()})
    return jsonify({'success': True, 'checkpoint': latest, 'message': f'ตั้งจุดเริ่มต้นที่เลข {latest} (จะนำเข้าเฉพาะเลขที่ใหม่กว่านี้)'})


@bp.route('/api/importer/release-preview')
def api_release_preview():
    try:
        lo, hi = int(request.args['from']), int(request.args['to'])
    except (KeyError, ValueError):
        return jsonify({'success': False, 'message': 'ระบุช่วงเลข from และ to เป็นตัวเลข'}), 400
    if lo > hi:
        return jsonify({'success': False, 'message': 'from ต้องไม่มากกว่า to'}), 400
    try:
        rp = runner.release_plan(lo, hi)
    except hosoffice.HosofficeUnavailable as e:
        return jsonify({'success': False, 'message': f'ต่อ hosoffice ไม่ได้ ({e})'}), 503
    p = rp['plan']
    s = p['summary']
    bad = sorted({r['seq'] for r in p['unreleasable']} | {r['seq'] for r, _ in rp['unbuildable']})
    return jsonify({'success': True, 'dry_run': True, 'held_in_range': rp['held_in_range'], 'will_write': len(rp['built']),
                    'already_present': len(p['duplicate']), 'unreleasable': bad[:50],
                    'oldest': _minute(s['oldest']), 'newest': _minute(s['newest']), 'per_day': s['per_day'], 'writer': p['writer']})


@bp.route('/api/importer/release', methods=['POST'])
def api_release():
    body = request.get_json(silent=True) or {}
    try:
        lo, hi, expect = int(body['from']), int(body['to']), int(body['expect'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'success': False, 'message': 'ต้องระบุ from, to และ expect (จำนวนที่เห็นในตัวอย่าง) เป็นตัวเลข'}), 400
    if lo > hi:
        return jsonify({'success': False, 'message': 'from ต้องไม่มากกว่า to'}), 400
    try:
        n = runner.release(lo, hi, expect, session.get('admin'))
    except runner.ReleaseRefused as e:
        return jsonify({'success': False, 'message': str(e)}), 409
    except (hosoffice.HosofficeUnavailable, hosoffice.HosofficeWriteError) as e:
        return jsonify({'success': False, 'message': f'ปล่อยไม่สำเร็จ ไม่มีแถวถูกเขียน ({e})'}), 503
    return jsonify({'success': True, 'written': n, 'message': f'ปล่อยแล้ว {n} แถว'})
