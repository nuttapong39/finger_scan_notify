"""หน้า Dashboard "Scan ที่อาจตกหล่น" และ API: เหตุการณ์ Lost scan / Import stall, แถวที่นำเข้าซ้ำ, ผู้รับแจ้ง IT"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

import database
import hosoffice
import scanwatch

bp = Blueprint('watch', __name__)


def _when(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else None


def _event(e):
    return {'id': e['id'], 'kind': e['kind'], 'seq': e['seq'], 'open': e['open'], 'detail': e['detail'],
            'first_seen': _when(e['first_seen']), 'closed_at': _when(e['closed_at']),
            'alerted': bool(e['alerted_at']), 'alerted_at': _when(e['alerted_at'])}


def _state():
    st = database.get_watch_state()
    return {'it_person_id': st['it_person_id'], 'max_seq': st['max_seq'], 'last_new_at': _when(st['last_new_at']),
            'last_poll_at': _when(st['last_poll_at']), 'last_error': st['last_error'], 'stalled': bool(st['stalled']),
            'lost_after_min': scanwatch.LOST_AFTER_S // 60, 'stall_after_min': scanwatch.STALL_AFTER_S // 60}


def _recipients():
    return [{'person_id': t['person_id'], 'name': t.get('person_name') or f"Person {t['person_id']}"}
            for t in database.get_all_tokens() if t.get('active')]


@bp.route('/watch')
def page():
    return render_template('watch.html', recipients=_recipients(), state=_state())


@bp.route('/api/watch/status')
def api_status():
    return jsonify({'success': True, 'state': _state(), 'recipients': _recipients(),
                    'events': [_event(e) for e in database.get_watch_events(limit=300)]})


@bp.route('/api/watch/duplicates')
def api_duplicates():
    try:
        rows = hosoffice.get_duplicate_device_sequences()
    except hosoffice.HosofficeUnavailable as e:
        return jsonify({'success': False, 'message': f'ต่อฐานข้อมูล hosoffice ไม่ได้ ({e})'}), 503
    groups = {}
    for r in rows:
        t = r['SCAN_DATE_TIME']
        groups.setdefault(r['seq'], []).append(
            {'id': r['ID'], 'person_id': r['PERSON_ID'], 'name': (r.get('HR_FULLNAME') or '').strip(),
             'time': t.strftime('%Y-%m-%d %H:%M:%S') if hasattr(t, 'strftime') else str(t)})
    return jsonify({'success': True, 'groups': [{'seq': s, 'rows': rs} for s, rs in groups.items()]})


@bp.route('/api/watch/config', methods=['POST'])
def api_config():
    body = request.get_json(silent=True) or {}
    try:
        pid = int(body.get('it_person_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'it_person_id ต้องเป็นตัวเลข'}), 400
    if not database.get_token_by_person_id(pid):
        return jsonify({'success': False, 'message': 'ต้องเป็นบุคลากรที่มี token ที่เปิดใช้งาน'}), 400
    database.save_watch_state(it_person_id=pid)
    return jsonify({'success': True, 'message': 'บันทึกผู้รับแจ้ง IT แล้ว'})
