"""HTTP adapter ของผู้ให้บริการส่งข้อความ — ไม่มีตรรกะการ์ดหรือการบันทึก
(การ์ดอยู่ใน cards.py, การเลือกการ์ด + บันทึก log อยู่ใน delivery.py)
"""
import json

import requests

LINE_NOTIFY_URL = 'https://notify-api.line.me/api/notify'
DEFAULT_MOPH_URL = 'https://morpromt2f.moph.go.th/api/notify/send?messages=yes'


def _call(do_post):
    """คืน (success, http_code, response_text) — success = HTTP 200 (MOPH รับเรื่องแล้ว ไม่ได้แปลว่าอ่านแล้ว)"""
    try:
        resp = do_post()
        return resp.status_code == 200, resp.status_code, resp.text[:500]
    except requests.exceptions.ConnectionError as e:
        return False, 0, f'Connection error: {str(e)[:200]}'
    except requests.exceptions.Timeout:
        return False, 0, 'Timeout'
    except Exception as e:   # noqa: BLE001 — ห้ามให้การส่งข้อความเดียวทำ loop ล่ม
        return False, 0, str(e)[:200]


def post_moph(api_url, client_key, secret_key, messages):
    """ส่ง Flex messages ผ่าน MOPH Alert — headers และ JSON encoding ตรงกับ PHP เดิม (JSON_UNESCAPED_UNICODE)"""
    body = json.dumps({"messages": messages}, ensure_ascii=False)
    return _call(lambda: requests.post(
        api_url or DEFAULT_MOPH_URL,
        headers={'client-key': client_key, 'secret-key': secret_key,
                 'Content-Type': 'application/json; charset=UTF-8', 'Accept': 'application/json'},
        data=body.encode('utf-8'), timeout=15))


def post_line_notify(token, message):
    """ส่งข้อความ text ผ่าน LINE Notify (บริการปิดแล้วตั้งแต่ มี.ค. 2025 — คงไว้เพื่อ token เดิม)"""
    return _call(lambda: requests.post(
        LINE_NOTIFY_URL, headers={'Authorization': f'Bearer {token}'},
        data={'message': message}, timeout=15))
