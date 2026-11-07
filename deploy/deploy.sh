#!/usr/bin/env bash
# อัปเดต Finger Scan Notify บน dashboard-srv (AlmaLinux 9 · nginx → waitress · Python 3.11 + SQLite)
#
# รันในนามผู้ใช้ของแอป (เจ้าของโฟลเดอร์และ .env):
#   cd /var/www/scan-notify && sudo -u scannotify ./deploy/deploy.sh v1.0.1                 # ขึ้นรุ่นตาม tag (แนะนำ)
#   sudo -u scannotify ./deploy/deploy.sh main                                              # ตาม branch
#   sudo -u scannotify ./deploy/deploy.sh v1.0.0 --allow-rollback                           # ย้อนรุ่นโดยตั้งใจ
#
# ลำดับ: ตรวจความพร้อม → ตรวจไวยากรณ์ Python ของรุ่นใหม่ก่อนแตะโค้ด → ไม่มีงานส่งย้อนหลังค้าง → สำรอง SQLite
#        → git merge --ff-only → pip install (เฉพาะเมื่อ requirements เปลี่ยน) → restart → ตรวจหลัง deploy
# พังหลังเริ่มแตะโค้ด: ย้อนโค้ด + dependency กลับรุ่นเดิมแล้ว restart เอง
#
# ไม่คืนฐานข้อมูลจากไฟล์สำรองอัตโนมัติ (ต่างจาก ipd-overview): ถ้ารุ่นใหม่ได้ส่งแจ้งเตือนไปแล้ว การคืนไฟล์เก่าจะ
# ย้อน last_scan_id และลบ log การส่ง → Monitor ส่งซ้ำ · การเปลี่ยน schema ของระบบนี้เพิ่มอย่างเดียว (CREATE IF NOT
# EXISTS / ADD COLUMN) รุ่นเก่าจึงทำงานบนฐานข้อมูลใหม่ได้ ถ้าจำเป็นต้องคืนจริงทำด้วยมือตาม DEPLOYMENT.md
#
# restart = หน้าเว็บและ Monitor หยุดไม่กี่วินาที (scan ที่เข้ามาช่วงนั้นถูกส่งต่อเมื่อเปิดใหม่)
# ไม่แตะ hosoffice (ไม่มีการเขียน) และไม่แตะ .env กับ data/ ที่อยู่นอก git
set -Eeuo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
APP="$ROOT/finger_scan_notify"
PY="$ROOT/.venv/bin/python"
PIP="$ROOT/.venv/bin/pip"
UNIT="scan-notify"
APP_URL="${SCAN_NOTIFY_URL:-http://scan-notify.ckhospital.local}"

ref="${1:-}"
allow_rollback=false
[ "${2:-}" = "--allow-rollback" ] && allow_rollback=true
[ -n "$ref" ] || { echo "usage: deploy/deploy.sh <tag|branch> [--allow-rollback]" >&2; exit 2; }

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
manage() { (cd "$APP" && "$PY" manage.py "$@"); }

# ---- ตรวจความปลอดภัยก่อนแตะโค้ด ----------------------------------------------------------------
[ -f "$APP/.env" ] || fail "ไม่พบ finger_scan_notify/.env — นี่ไม่ใช่เครื่องที่ตั้งค่าแล้ว (ดู SERVER-SETUP-ALMALINUX.md)"
mode=$(stat -c %a "$APP/.env")
[ "$mode" = "600" ] || fail ".env มีสิทธิ์ $mode — ไฟล์ความลับต้องเป็น 600"
[ -r "$APP/.env" ] || fail "อ่าน .env ไม่ได้ — ต้องรันในนามผู้ใช้ของแอป: sudo -u scannotify ./deploy/deploy.sh ..."
[ -x "$PY" ] || fail "ไม่พบ $PY"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no
    fail "ไฟล์ที่ git ติดตามถูกแก้บนเซิร์ฟเวอร์ — แก้ที่เครื่องพัฒนาแล้วดึงลงมาแทน"
fi

# ---- ดึงโค้ด ------------------------------------------------------------------------------------
step "ดึงจาก GitHub"
before_full=$(git rev-parse HEAD)
before=$(git rev-parse --short HEAD)
git fetch --quiet --tags origin
target=$(git rev-parse --verify --quiet "refs/tags/$ref^{commit}" || git rev-parse --verify --quiet "refs/remotes/origin/$ref^{commit}" || true)
[ -n "$target" ] || fail "ไม่พบ tag หรือ branch ชื่อ '$ref' บน origin"
after=$(git rev-parse --short "$target")
if [ "$before" = "$after" ]; then
    echo "โค้ดเป็นรุ่น $after อยู่แล้ว ไม่มีอะไรต้อง deploy"
    exit 0
fi
if ! git merge-base --is-ancestor HEAD "$target"; then
    $allow_rollback || fail "$after ไม่ใช่รุ่นที่ใหม่กว่า $before — ถ้าตั้งใจย้อนกลับ ให้เติม --allow-rollback"
fi

step "ตรวจไวยากรณ์ของรุ่นใหม่ก่อนแตะโค้ดบนดิสก์"
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
git archive "$target" | tar -x -C "$tmp"
"$PY" -m compileall -q -x '/tests/' "$tmp/finger_scan_notify" >/dev/null \
    || fail "รุ่น $after มีไฟล์ Python ไวยากรณ์ผิด — ยังไม่ได้เปลี่ยนโค้ด (ยังเป็นรุ่น $before)"
for f in "$tmp"/deploy/*.sh; do bash -n "$f" || fail "รุ่น $after: $(basename "$f") ไวยากรณ์ผิด"; done
req_changed=false
git diff --quiet "$before_full" "$target" -- finger_scan_notify/requirements.txt || req_changed=true

step "ตรวจว่าไม่มีงานส่งย้อนหลังกำลังรัน (restart กลางคันทำให้งานค้าง)"
manage can-restart || fail "หยุด deploy — ลองใหม่เมื่องานส่งย้อนหลังเสร็จ"

step "สำรองฐานข้อมูลของระบบ (SQLite)"
backup=$(manage backup --keep=30 | sed -n 's/^สำรองแล้ว: \(.*\) ([0-9,]* ไบต์)$/\1/p' | head -1)
[ -n "$backup" ] && [ -f "$backup" ] || fail "สำรองฐานข้อมูลไม่สำเร็จ — หยุด deploy"
echo "สำรองแล้ว: $backup"

# ---- ใช้โค้ดใหม่ (ตั้งแต่ตรงนี้ ถ้าพังจะย้อนกลับเอง) -----------------------------------------------
touched=false
restarted=false
rollback() {
    trap - ERR
    printf '\n\033[1;31m✗ Deploy พังที่บรรทัด %s — ย้อนกลับไปรุ่น %s\033[0m\n' "${1:-?}" "$before" >&2
    if $touched; then
        git checkout --quiet --detach "$before_full" || echo "ย้อนโค้ดไม่สำเร็จ — ตรวจด้วยมือ" >&2
        if $req_changed; then "$PIP" install --quiet -r "$APP/requirements.txt" || echo "ติดตั้ง dependency เดิมไม่สำเร็จ" >&2; fi
        # ยังไม่ได้ restart = process ที่รันอยู่ยังเป็นรุ่นเดิมในหน่วยความจำ ไม่ต้อง restart (ไม่ไปฆ่างานส่งย้อนหลัง)
        if $restarted; then sudo systemctl restart "$UNIT" || echo "restart ไม่สำเร็จ — sudo systemctl status $UNIT" >&2; fi
    fi
    echo "ระบบกลับเป็นรุ่น $before แล้ว (ไฟล์สำรองก่อน deploy: $backup — ไม่ได้คืนอัตโนมัติ ดู DEPLOYMENT.md)" >&2
    exit 1
}
trap 'rollback $LINENO' ERR

step "อัปเดตโค้ด ($before → $after)"
touched=true
if git merge-base --is-ancestor HEAD "$target"; then
    git --no-pager log --oneline "$before..$after"
    git merge --ff-only --quiet "$target"
else
    git checkout --quiet --detach "$target"
fi

if $req_changed; then
    step "ติดตั้ง dependency (requirements.txt เปลี่ยน)"
    "$PIP" install --quiet -r "$APP/requirements.txt"
fi

step "restart บริการ (ตรวจงานส่งย้อนหลังอีกครั้งก่อน restart)"
manage can-restart
restarted=true
sudo systemctl restart "$UNIT"

step "ตรวจหลัง deploy"
./deploy/smoke_after_deploy.sh "$APP_URL"

trap - ERR
printf '\n\033[1;32m✓ Deploy %s → %s\033[0m (ไฟล์สำรองก่อน deploy: %s)\n' "$before" "$after" "$backup"
