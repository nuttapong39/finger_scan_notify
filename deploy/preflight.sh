#!/usr/bin/env bash
# ตรวจความพร้อมของเครื่อง (dashboard-srv) ก่อน/หลังติดตั้ง — อ่านอย่างเดียว ไม่แก้ไขอะไร ไม่ส่งข้อความ
#
#   cd /var/www/scan-notify && sudo -u scannotify ./deploy/preflight.sh
#
# รายการตรวจ: เขตเวลา/NTP, SELinux (nginx ต่อ 127.0.0.1:5050 ได้), Python 3.11 + .venv + package,
# ผู้ใช้/สิทธิ์ไฟล์ (.env 600, data/ เขียนได้, SQLite 600), ค่าใน .env (โหลด settings.py),
# บริการ systemd / nginx / sudoers / cron ติดตั้งแล้ว,
# และ manage.py check: hosoffice READ ONLY + อ่านครบ 4 ตาราง (SELECT อย่างเดียว), ต่อ MOPH ได้, มีบัญชี admin
# ออกด้วยรหัส ≠ 0 ถ้ามีข้อที่ต้องแก้
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
APP="$ROOT/finger_scan_notify"
PY="$ROOT/.venv/bin/python"
APP_USER="${SCAN_NOTIFY_USER:-scannotify}"
PORT="${SCAN_NOTIFY_PORT:-5050}"
bad=0
ok()   { printf '  ok   %s\n' "$1"; }
warn() { printf '  WARN %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; bad=1; }

echo "== ระบบ"
printf '  %s · %s\n' "$(cat /etc/almalinux-release 2>/dev/null || echo 'ไม่ใช่ AlmaLinux')" "$(systemd-detect-virt 2>/dev/null || echo '?')"
tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo '?')
[ "$tz" = "Asia/Bangkok" ] && ok "เขตเวลาเครื่อง Asia/Bangkok" || fail "เขตเวลาเครื่องเป็น $tz (เวลาเตือน 09:30/17:00 ใช้นาฬิกาเครื่อง: sudo timedatectl set-timezone Asia/Bangkok)"
case "$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo '?')" in
    yes) ok "นาฬิกาเครื่องตรงกับ NTP";;
    no)  fail "นาฬิกาเครื่องยังไม่ตรงกับ NTP (sudo systemctl enable --now chronyd; chronyc tracking)";;
    *)   warn "ตรวจ NTP ไม่ได้ — ตรวจเองด้วย chronyc tracking";;
esac

se=$(getenforce 2>/dev/null || echo 'n/a')
if [ "$se" = "Enforcing" ]; then
    if command -v semanage >/dev/null 2>&1 && semanage port -l 2>/dev/null | awk '$1=="http_port_t"' | grep -qw "$PORT"; then
        ok "SELinux Enforcing: พอร์ต $PORT เป็น http_port_t (nginx ส่งต่อได้)"
    elif [ "$(getsebool httpd_can_network_connect 2>/dev/null | awk '{print $3}')" = "on" ]; then
        ok "SELinux Enforcing: httpd_can_network_connect = on"
    else
        fail "SELinux Enforcing แต่ nginx ยังต่อ 127.0.0.1:$PORT ไม่ได้ (sudo semanage port -a -t http_port_t -p tcp $PORT — ดู SERVER-SETUP-ALMALINUX.md ข้อ 5)"
    fi
else
    ok "SELinux: $se"
fi

echo "== Python"
if [ -x "$PY" ]; then
    ver=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' && ok ".venv Python $ver" || fail ".venv Python $ver (ต้อง 3.10 ขึ้นไป — ใช้ python3.11)"
    "$PY" -c 'import flask, waitress, mysql.connector, dotenv, requests' 2>/dev/null \
        && ok "ติดตั้ง package ครบ (flask, waitress, mysql-connector, python-dotenv, requests)" \
        || fail "package ไม่ครบ (.venv/bin/pip install -r finger_scan_notify/requirements.txt)"
else
    fail "ไม่พบ $PY (sudo -u $APP_USER python3.11 -m venv $ROOT/.venv)"
fi
command -v git >/dev/null 2>&1 && ok "git" || fail "ไม่พบ git"

echo "== ผู้ใช้และสิทธิ์ไฟล์"
if id "$APP_USER" >/dev/null 2>&1; then
    ok "มีผู้ใช้ $APP_USER"
    [ "$(id -un)" = "$APP_USER" ] || warn "ควรรันสคริปต์นี้ในนาม $APP_USER: sudo -u $APP_USER ./deploy/preflight.sh"
else
    fail "ไม่มีผู้ใช้ระบบ $APP_USER (sudo useradd -r -s /sbin/nologin -d /var/www/scan-notify $APP_USER)"
fi
[ -d "$APP/data" ] && [ -w "$APP/data" ] && ok "เขียน finger_scan_notify/data/ ได้" || fail "เขียน finger_scan_notify/data/ ไม่ได้ (sudo install -d -o $APP_USER -g $APP_USER -m 0700 $APP/data)"
if [ -f "$APP/.env" ]; then
    mode=$(stat -c %a "$APP/.env"); owner=$(stat -c %U "$APP/.env")
    [ "$mode" = "600" ] && ok ".env สิทธิ์ 600" || fail ".env สิทธิ์ $mode (ต้อง 600: chmod 600 finger_scan_notify/.env)"
    [ "$owner" = "$APP_USER" ] && ok ".env เจ้าของ $owner" || fail ".env เจ้าของ $owner (ต้องเป็น $APP_USER)"
else
    fail "ไม่พบ finger_scan_notify/.env (คัดลอกจาก .env.example)"
fi
db="$APP/data/app_config.db"
if [ -f "$db" ]; then
    mode=$(stat -c %a "$db")
    [ "$mode" = "600" ] && ok "app_config.db สิทธิ์ 600" || fail "app_config.db สิทธิ์ $mode (มี key ของ token ทุกคน ต้อง 600)"
else
    warn "ยังไม่มี data/app_config.db (สร้างเองตอนเปิดแอปครั้งแรก หรือคัดลอกมาจากเครื่องเดิมตอนย้าย)"
fi
for f in serve.py manage.py settings.py; do [ -f "$APP/$f" ] && ok "พบ $f" || fail "ไม่พบ finger_scan_notify/$f (โค้ดไม่ครบ?)"; done
[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ] && warn "ไฟล์ที่ git ติดตามถูกแก้บนเซิร์ฟเวอร์ (deploy.sh จะไม่ยอมทำงาน)"

echo "== ค่าใน .env"
if [ -r "$APP/.env" ] && [ -x "$PY" ]; then
    if out=$(cd "$APP" && "$PY" -c 'import settings as s; c=s.current; print(c.app_env, c.monitor_autostart, c.listen_host, c.listen_port)' 2>&1); then
        read -r env autostart lhost lport <<< "$out"
        [ "$env" = "production" ] && ok "APP_ENV=production" || fail "APP_ENV=$env (Server ต้องเป็น production)"
        [ "$lhost" = "127.0.0.1" ] && ok "LISTEN_HOST=127.0.0.1 (เข้าได้ผ่าน nginx เท่านั้น)" || warn "LISTEN_HOST=$lhost (ควรเป็น 127.0.0.1)"
        [ "$lport" = "$PORT" ] || warn "LISTEN_PORT=$lport แต่ nginx/SELinux ตั้งไว้ที่ $PORT"
        [ "$autostart" = "True" ] && ok "MONITOR_AUTOSTART=1 (เครื่องหลัก)" \
            || warn "MONITOR_AUTOSTART ไม่ได้ตั้ง — Monitor ไม่เริ่มเอง (ถูกต้องระหว่างติดตั้ง ก่อนหยุดเครื่อง Windows)"
    else
        fail "settings.py ไม่ยอมโหลด .env: $(printf '%s' "$out" | tail -1)"
    fi
else
    warn "ข้ามการตรวจ .env (อ่านไม่ได้ในนามผู้ใช้นี้ หรือยังไม่มี .venv)"
fi

echo "== บริการ"
[ -f /etc/systemd/system/scan-notify.service ] && ok "ติดตั้ง scan-notify.service แล้ว ($(systemctl is-active scan-notify 2>/dev/null))" || warn "ยังไม่ได้ติดตั้ง /etc/systemd/system/scan-notify.service"
[ "$(systemctl list-units --type=service --all 2>/dev/null | grep -c 'scan-notify')" -le 1 ] && ok "มีบริการ scan-notify ตัวเดียว" || fail "พบบริการ scan-notify มากกว่า 1 ตัว (ADR 0001: ต้องมี process เดียว)"
[ -f /etc/nginx/conf.d/scan-notify.conf ] && ok "ติดตั้ง nginx conf แล้ว" || warn "ยังไม่ได้ติดตั้ง /etc/nginx/conf.d/scan-notify.conf"
[ -f /etc/sudoers.d/scan-notify ] && ok "ติดตั้ง sudoers แล้ว" || warn "ยังไม่ได้ติดตั้ง /etc/sudoers.d/scan-notify (deploy.sh restart ไม่ได้)"
[ -f /etc/cron.d/scan-notify ] && ok "ติดตั้ง cron สำรองข้อมูลแล้ว" || warn "ยังไม่ได้ติดตั้ง /etc/cron.d/scan-notify"

echo "== hosoffice / MOPH Alert / บัญชี admin (manage.py check — SELECT อย่างเดียว ไม่ส่งข้อความ)"
if [ -r "$APP/.env" ] && [ -x "$PY" ]; then
    out=$(cd "$APP" && "$PY" manage.py check 2>&1) && rc=0 || rc=$?
    printf '%s\n' "$out" | sed 's/^/  | /'
    [ "$rc" = 0 ] && ok "manage.py check ผ่าน" || fail "manage.py check ไม่ผ่าน"
else
    warn "ข้าม manage.py check"
fi

if [ "$bad" = 0 ]; then printf '\n\033[1;32m✓ เครื่องพร้อม\033[0m\n'; else printf '\n\033[1;31m✗ มีข้อที่ต้องแก้ก่อนไปขั้นถัดไป\033[0m\n' >&2; exit 1; fi
