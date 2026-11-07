#!/usr/bin/env bash
# ตรวจหลัง deploy ผ่าน HTTP จริง (ไม่ล็อกอิน ไม่เขียนอะไร ไม่ส่งข้อความ):
# ไฟล์ความลับต้องเปิดไม่ได้, /login ขึ้น, ทุกหน้าและ API บังคับล็อกอิน, POST ข้ามเว็บถูกปฏิเสธ, บริการทำงาน
#
#   ./deploy/smoke_after_deploy.sh http://scan-notify.ckhospital.local
#   SCAN_NOTIFY_CURL_EXTRA="--resolve scan-notify.ckhospital.local:80:127.0.0.1" ./deploy/smoke_after_deploy.sh http://scan-notify.ckhospital.local
#
# ออกด้วยรหัส ≠ 0 ถ้ามีข้อใดไม่ผ่าน
set -euo pipefail

cd "$(dirname "$0")/.."
base="${1:-http://scan-notify.ckhospital.local}"
base="${base%/}"
extra=()
if [ -n "${SCAN_NOTIFY_CURL_EXTRA:-}" ]; then read -r -a extra <<< "$SCAN_NOTIFY_CURL_EXTRA"; fi

bad=0
pass() { printf '  ok   %-52s %s\n' "$1" "$2"; }
nope() { printf '  FAIL %-52s %s  (%s)\n' "$1" "$2" "$3"; bad=1; }
status() { curl -s -o /dev/null -m 20 -w '%{http_code}' ${extra[@]+"${extra[@]}"} "$@" || echo 000; }

# บริการต้องทำงาน (รอได้ถึง 20 วินาทีหลัง restart)
for _ in $(seq 1 20); do
    code=$(status "$base/login")
    [ "$code" = "200" ] && break
    sleep 1
done
state=$(systemctl is-active scan-notify 2>/dev/null || true)
[ "$state" = "active" ] && pass "systemctl is-active scan-notify" "$state" || nope "systemctl is-active scan-notify" "$state" "ต้อง active"

# ต้องเปิดไม่ได้ (403 หรือ 404): ความลับ ฐานข้อมูล โค้ด git เอกสาร
for p in .env finger_scan_notify/.env .env.example .git/config .git/HEAD .gitignore \
         data/app_config.db finger_scan_notify/data/app_config.db app_config.db data/backups/ \
         app.py settings.py manage.py serve.py finger_scan_notify/app.py requirements.txt \
         deploy/deploy.sh deploy/scan-notify.service README.md CONTEXT.md docs/adr/0001-single-process.md \
         templates/base.html static/../.env; do
    code=$(status "$base/$p")
    case "$code" in 403|404) pass "$p" "$code";; *) nope "$p" "$code" "ต้องได้ 403 หรือ 404";; esac
done

# หน้า login ต้องขึ้น และไม่มีข้อมูลภายใน
code=$(status "$base/login")
[ "$code" = "200" ] && pass "login" "$code" || nope "login" "$code" "ต้องได้ 200"
body=$(curl -s -m 20 ${extra[@]+"${extra[@]}"} "$base/login" 2>/dev/null || true)
if printf '%s' "$body" | grep -qiE 'Traceback|SECRET_KEY|HOSOFFICE_|client_key|secret_key'; then
    nope "หน้า login มีข้อมูลภายใน" "-" "ตรวจ log ของบริการ"
else
    pass "หน้า login ไม่มีข้อมูลภายใน" "-"
fi

# ทุกหน้าต้องพาไป login (302) และทุก API ต้องตอบ 401 เมื่อไม่ล็อกอิน
for p in "" dashboard backfill reminders logs debug config/tokens config/database config/api account/password; do
    code=$(status "$base/$p")
    [ "$code" = "302" ] && pass "/$p (ไม่ล็อกอิน → login)" "$code" || nope "/$p" "$code" "ต้องได้ 302"
done
for p in api/logs api/dashboard-data monitor/status api/flex-theme api/reminders/today; do
    code=$(status "$base/$p")
    [ "$code" = "401" ] && pass "/$p (ไม่ล็อกอิน)" "$code" || nope "/$p" "$code" "ต้องได้ 401"
done

# สั่งงานโดยไม่ล็อกอิน / จากเว็บอื่น ต้องถูกปฏิเสธ (คำขอเหล่านี้ไม่ผ่าน guard จึงไม่มีอะไรเกิดขึ้นจริง)
code=$(status -X POST -H "Origin: $base" "$base/monitor/stop")
[ "$code" = "401" ] && pass "POST /monitor/stop ไม่ล็อกอิน" "$code" || nope "POST /monitor/stop ไม่ล็อกอิน" "$code" "ต้องได้ 401"
code=$(status -X POST -H "Origin: http://evil.example" "$base/api/test-notify/1")
[ "$code" = "403" ] && pass "POST จากเว็บอื่น" "$code" || nope "POST จากเว็บอื่น" "$code" "ต้องได้ 403"

# ส่วนหัวไม่เปิดเผยเวอร์ชัน
hdrs=$(curl -sI -m 20 ${extra[@]+"${extra[@]}"} "$base/login" 2>/dev/null || true)
if printf '%s' "$hdrs" | grep -qiE '^(x-powered-by:|server: (nginx/[0-9]|waitress|werkzeug))'; then
    nope "ส่วนหัวเปิดเผยเวอร์ชัน" "-" "server_tokens off / ident=None"
else
    pass "ส่วนหัวไม่เปิดเผยเวอร์ชัน" "-"
fi
printf '%s' "$hdrs" | grep -qi 'x-frame-options' && pass "X-Frame-Options" "-" || nope "X-Frame-Options" "-" "ตรวจ nginx conf"

if [ "$bad" = 0 ]; then printf '\n\033[1;32m✓ ผ่านทุกข้อ\033[0m\n'; else printf '\n\033[1;31m✗ มีข้อที่ไม่ผ่าน — ห้ามเปิดให้ผู้ใช้จนกว่าจะแก้\033[0m\n' >&2; exit 1; fi
