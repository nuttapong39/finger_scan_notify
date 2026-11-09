# ติดตั้ง Finger Scan Notify บน dashboard-srv (AlmaLinux 9)

ทำครั้งเดียวต่อเครื่อง ใช้รูปแบบเดียวกับ ipd-overview / ipd-food: ชื่อเครื่องเฉพาะ (`scan-notify.ckhospital.local`), ผู้ใช้ระบบแยก, ความลับใน `.env`, HTTP ในวง VLAN ภายใน
การอัปเดตครั้งต่อไปใช้ [DEPLOYMENT.md](DEPLOYMENT.md) · เหตุผลของการออกแบบ: [docs/design/phase3-deploy-almalinux.md](docs/design/phase3-deploy-almalinux.md)

> **กฎสูงสุดของระบบนี้ (ADR [0001](docs/adr/0001-single-process.md)):** รันได้ **process เดียว บนเครื่องเดียว** เท่านั้น
> ถ้ามี 2 ที่เปิด Monitor พร้อมกัน (เช่น Server กับเครื่อง Windows เดิม) บุคลากร**ได้แจ้งเตือนซ้ำ** และเรียกคืนไม่ได้
> ระหว่างติดตั้งให้ `MONITOR_AUTOSTART=0` จนถึงขั้นย้ายเครื่อง (หัวข้อ 9)
>
> ระบบอ่าน hosoffice อย่างเดียว: ทุก connection ถูกบังคับ `SET SESSION TRANSACTION READ ONLY` ไม่มีขั้นตอนไหนเขียน hosoffice

**สถานะการทดสอบ:** ผู้ใช้เป็นคนทดสอบบนเครื่องจริง ผู้เขียนไม่ได้รันสคริปต์ `deploy/*.sh` บน Linux จริง (ตรวจไวยากรณ์ด้วย `bash -n` แล้วเท่านั้น) ถ้าคำสั่งใดไม่เป็นไปตามที่เขียน ให้แจ้งเพื่อแก้เอกสาร

## 0. สิ่งที่ต้องมีบนเครื่อง

| รายการ | ค่า |
|---|---|
| ระบบ | AlmaLinux 9, nginx (ใช้ร่วมกับระบบอื่น — `reload` เท่านั้น ห้าม restart) |
| Python | 3.11 จาก dnf (ค่ามาตรฐาน 3.9 ใช้ไม่ได้) |
| เครื่องมือ | `git`, `curl` |
| ฐานข้อมูลระบบ | SQLite (`finger_scan_notify/data/app_config.db` ไม่ต้องติดตั้งเซิร์ฟเวอร์) |
| เครือข่ายขาออก | hosoffice MySQL `192.168.1.250:3306` · MOPH Alert `morpromt2f.moph.go.th:443` · GitHub · PyPI (ตอนติดตั้ง package) |

```bash
sudo dnf install -y python3.11 python3.11-pip git policycoreutils-python-utils
python3.11 --version
```

## 0.1 นาฬิกาของเครื่อง (สำคัญ)

เวลาเตือนยังไม่สแกน (09:30 / 17:00) อายุ scan ที่ยอมส่ง และเวลาบนการ์ด ใช้นาฬิกาของเครื่อง:

```bash
sudo timedatectl set-timezone Asia/Bangkok
sudo systemctl enable --now chronyd
timedatectl show -p NTPSynchronized --value      # ต้องได้ yes
```

## 1. ผู้ใช้ระบบและโฟลเดอร์

```bash
sudo useradd -r -s /sbin/nologin -d /var/www/scan-notify scannotify
sudo install -d -o scannotify -g scannotify -m 0755 /var/www/scan-notify
```

deploy key แบบอ่านอย่างเดียว (repo เป็น private) ไว้นอกโฟลเดอร์แอป:

```bash
sudo install -d -o scannotify -g scannotify -m 0700 /var/www/.scan-notify-ssh
sudo -u scannotify ssh-keygen -t ed25519 -f /var/www/.scan-notify-ssh/deploy_key -N "" -C "scan-notify deploy"
sudo -u scannotify cat /var/www/.scan-notify-ssh/deploy_key.pub
# เอาบรรทัดที่ได้ไปเพิ่มที่ GitHub: repo → Settings → Deploy keys (ไม่ต้องติ๊ก Allow write access)

SSH="ssh -i /var/www/.scan-notify-ssh/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
sudo -u scannotify git -c core.sshCommand="$SSH" clone git@github.com:nuttapong39/finger_scan_notify.git /var/www/scan-notify
cd /var/www/scan-notify
sudo -u scannotify git config core.sshCommand "$SSH"
sudo -u scannotify git checkout v1.0.0          # หรือ main ถ้ายังไม่ได้ติด tag
```

## 2. Python venv และ package

```bash
cd /var/www/scan-notify
sudo -u scannotify python3.11 -m venv .venv
sudo -u scannotify .venv/bin/pip install --upgrade pip
sudo -u scannotify .venv/bin/pip install -r finger_scan_notify/requirements.txt
sudo -u scannotify install -d -m 0700 finger_scan_notify/data
```

## 3. `.env` (ความลับ — git ไม่เก็บ)

```bash
cd /var/www/scan-notify/finger_scan_notify
sudo -u scannotify cp .env.example .env
sudo chmod 600 .env
python3.11 -c "import secrets; print(secrets.token_hex(32))"     # เอาค่านี้ไปใส่ SECRET_KEY
sudo -u scannotify nano .env
```

ค่าที่ต้องกรอก:

| ค่า | ใส่ |
|---|---|
| `APP_ENV` | `production` (ไม่ครบแล้วแอปไม่ยอมเริ่ม — ตั้งใจ) |
| `SECRET_KEY` | ค่าที่สุ่มด้านบน (≥ 32 ตัว) |
| `HOSOFFICE_HOST` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | `192.168.1.250` / `3306` / บัญชี / รหัส / `hosoffice` |
| `MONITOR_AUTOSTART` | **`0`** จนถึงขั้นย้ายเครื่อง (หัวข้อ 9) |
| `LISTEN_HOST` / `LISTEN_PORT` | `127.0.0.1` / `5050` (เข้าได้ผ่าน nginx เท่านั้น) |
| `TIGER_HOST` / `PORT` / `DATABASE` / `USER` / `PASSWORD` | เครื่องสแกน `192.168.1.77` / `1433` / `TigerWebServerPlus` / บัญชีอ่านอย่างเดียว (runbook ส่วน B) / รหัส — ต้องเปิด firewall ที่ .77 ให้ IP ของเครื่องนี้ ; ตรวจด้วย `manage.py tiger-check` |
| `SCAN_IMPORTER_MODE` | **`shadow`** ระหว่างติดตั้ง (เทียบอย่างเดียว ไม่เขียน) → `write` ตอนสลับผู้เขียน (หัวข้อ 9.1) · **เริ่มเองตามโหมดนี้ ไม่ผูกกับ `MONITOR_AUTOSTART`** |
| `HOSOFFICE_WRITE_USER` / `HOSOFFICE_WRITE_PASSWORD` / `SCAN_IMPORTER_COM_NAME` | **ใส่ตอน cutover เท่านั้น** (บัญชี `scan_importer_rw` runbook ส่วน C, ชื่อ `SCAN-IMPORTER`) |

**บัญชี hosoffice:** ตอนนี้ใช้ `root` ไปก่อน (ตกลงไว้ใน Q5) ด่านกันเขียนคือ READ ONLY session ในโค้ด
ควรขอผู้ดูแล hosoffice สร้างบัญชีที่มีแค่ `SELECT` บน `fingle_scan`, `leave_holiday`, `leave_register`, `fingle_scan_notify_mute` แล้วเปลี่ยนใน `.env` (ไม่ต้อง deploy แค่ `sudo systemctl restart scan-notify`)

เมื่อมี `HOSOFFICE_*` ใน `.env` หน้า "เชื่อมต่อฐานข้อมูล" จะแสดงค่าอย่างเดียว และรหัส MySQL ที่ค้างใน SQLite ถูกลบทิ้งเอง

## 4. บัญชี admin

```bash
cd /var/www/scan-notify/finger_scan_notify
sudo -u scannotify ../.venv/bin/python manage.py init-admin
```

จดรหัสชั่วคราวที่แสดง (แสดงครั้งเดียว) ล็อกอินครั้งแรกระบบจะบังคับเปลี่ยนรหัส (≥ 10 ตัว)
ลืมรหัสภายหลัง: `manage.py reset-admin` (ออกรหัสชั่วคราวใหม่ ทุก session หลุด)

## 5. SELinux (ถ้า `getenforce` เป็น Enforcing)

nginx ต้องต่อ `127.0.0.1:5050` ได้ ให้ติดป้ายพอร์ตเป็นพอร์ตเว็บ (ไม่กระทบระบบอื่น):

```bash
sudo semanage port -a -t http_port_t -p tcp 5050 || sudo semanage port -m -t http_port_t -p tcp 5050
sudo semanage port -l | grep -w http_port_t       # ต้องเห็น 5050
```

ถ้ายังได้ 502 และ `ausearch -m avc -ts recent` มี `name_connect` ของ nginx: ทางสำรองคือ `sudo setsebool -P httpd_can_network_connect 1` (มีผลกับ nginx ทั้งเครื่อง — แจ้งเจ้าของระบบอื่นก่อน)
บริการ Python เองรันนอก SELinux confinement (`unconfined_service_t`) จึงต่อ MySQL และ HTTPS ขาออกได้โดยไม่ต้องตั้ง boolean

## 6. systemd, nginx, sudoers, cron

```bash
cd /var/www/scan-notify
sudo cp deploy/scan-notify.service /etc/systemd/system/scan-notify.service
sudo systemctl daemon-reload
sudo systemctl enable --now scan-notify
systemctl status scan-notify --no-pager               # ต้อง active (running) · log: journalctl -u scan-notify -f

sudo cp deploy/nginx-scan-notify.conf /etc/nginx/conf.d/scan-notify.conf
sudo nginx -t && sudo systemctl reload nginx

sudo cp deploy/sudoers-scan-notify /etc/sudoers.d/scan-notify
sudo chmod 440 /etc/sudoers.d/scan-notify && sudo visudo -c

sudo cp deploy/scan-notify.cron /etc/cron.d/scan-notify
sudo chmod 644 /etc/cron.d/scan-notify
```

เพิ่มชื่อ `scan-notify.ckhospital.local` ใน DNS ภายในให้ชี้ไปที่ dashboard-srv (หรือไฟล์ hosts ของเครื่องที่ใช้ทดสอบ)

ทดสอบสำรองด้วยมือหนึ่งครั้ง: `cd finger_scan_notify && sudo -u scannotify ../.venv/bin/python manage.py backup` ต้องได้ไฟล์ใน `data/backups/`

## 7. ตรวจความพร้อม

```bash
cd /var/www/scan-notify
sudo -u scannotify ./deploy/preflight.sh
sudo -u scannotify ./deploy/smoke_after_deploy.sh http://scan-notify.ckhospital.local
```

`preflight.sh` ต้องขึ้น "เครื่องพร้อม" (WARN เรื่อง `MONITOR_AUTOSTART` ตอนนี้ถูกต้อง) และ `smoke_after_deploy.sh` ต้องขึ้น "ผ่านทุกข้อ"
`preflight.sh` เรียก `manage.py check` ซึ่งตรวจว่า session ของ hosoffice เป็น READ ONLY จริง อ่านได้ครบ 4 ตาราง ต่อ MOPH Alert ได้ (เปิด TCP เท่านั้น ไม่ส่งข้อความ)

## 8. ทดลองใช้ก่อนย้าย (Monitor ยังปิด)

เปิด `http://scan-notify.ckhospital.local/login` → ล็อกอินด้วยรหัสชั่วคราว → เปลี่ยนรหัส
ตอนนี้ Server ยังใช้ฐานข้อมูลเปล่า (ไม่มี token) ยังไม่ต้องตั้งค่าอะไรในหน้าเว็บ — ข้อมูลจริงมากับขั้นที่ 9

## 9. ย้ายจากเครื่อง Windows (cutover)

ทำ**นอกช่วงเข้า-ออกงาน** (เช่น 10:00–11:00 หรือหลัง 20:00) ใช้เวลาประมาณ 15 นาที

1. **หยุดแอปบน Windows:** ปิดหน้าต่างที่รัน `python app.py` (เปิดด้วยมือ ไม่มี autostart — ตรวจแล้ว)
   ตรวจว่าหยุดจริง: `netstat -ano | findstr :5050` ต้องไม่มี `LISTENING`
   แล้วแก้ `finger_scan_notify\.env` บน Windows เป็น `MONITOR_AUTOSTART=0` **ทันที** — ถ้ามีคนเผลอเปิดแอปบน Windows อีกครั้ง Monitor จะไม่เริ่มเอง
2. **คัดลอกฐานข้อมูล** (token 94 คน, log, Theme, ค่าเตือน, ตำแหน่ง scan ล่าสุด) จาก Windows:
   ```powershell
   scp C:\Dev\Projects\notify_fingle_scan-master\finger_scan_notify\data\app_config.db <ผู้ใช้>@dashboard-srv:/tmp/app_config.db
   ```
   บน Server:
   ```bash
   sudo systemctl stop scan-notify
   cd /var/www/scan-notify/finger_scan_notify
   sudo install -o scannotify -g scannotify -m 600 /tmp/app_config.db data/app_config.db
   sudo shred -u /tmp/app_config.db                  # ไฟล์มี key ของ token ทุกคน
   ```
3. **บัญชี admin:** ฐานข้อมูลจาก Windows อาจยังไม่มีบัญชี admin
   `sudo -u scannotify ../.venv/bin/python manage.py init-admin` (ถ้าขึ้นว่ามีแล้ว = ใช้รหัสเดิมจาก Windows หรือ `reset-admin`)
4. **เปิดเป็นเครื่องหลัก:** แก้ `.env` เป็น `MONITOR_AUTOSTART=1` แล้ว
   ```bash
   sudo systemctl start scan-notify
   journalctl -u scan-notify -n 20 --no-pager        # ต้องเห็น "MOPH Alert Monitor: AUTO-STARTED"
   cd /var/www/scan-notify && sudo -u scannotify ./deploy/preflight.sh && sudo -u scannotify ./deploy/smoke_after_deploy.sh http://scan-notify.ckhospital.local
   ```
   ตอนเริ่ม แอปลบรหัส MySQL ที่ติดมากับฐานข้อมูลจาก Windows ทิ้งเอง (เพราะ `.env` มีแล้ว)
5. **ทดสอบ:** ล็อกอิน → Dashboard → 🎨 ปรับแต่ง Flex card → ส่งทดสอบไปที่ token ของตัวเอง
6. **scan ระหว่างช่วงย้าย:** Monitor ส่งต่อจาก `last_scan_id` เดิมเอง (เฉพาะที่เก่าไม่เกิน 180 นาที) ที่ตกหล่นใช้หน้า 🔁 ตรวจสอบและส่งย้อนหลัง
7. **เครื่อง Windows:** นับจากนี้เป็นเครื่องพัฒนา (`MONITOR_AUTOSTART=0` ตั้งแล้วในขั้นที่ 1)
   ถ้ามีคนกดเริ่ม Monitor บน Windows หน้าเว็บจะขึ้นแถบแดงเตือนว่าไม่ใช่เครื่องหลัก
8. **ตรวจว่า Monitor ทำงาน:** `smoke_after_deploy.sh` ตรวจได้แค่ว่าบริการ active (Monitor อยู่หลังล็อกอิน และผู้ใช้ระบบ `scannotify` อ่าน journal ไม่ได้)
   ดูเองด้วย `sudo journalctl -u scan-notify -n 20` หรือแถบ Monitor Status ใน sidebar หลังล็อกอิน

## 9.1 สลับผู้เขียนเป็น Scan importer ตัวใหม่ (ทำหลังหัวข้อ 9 หรือก่อนก็ได้ — ไม่ผูกกัน)

ตัวใหม่เป็น **ผู้เขียน `fingle_scan` คนเดียว** แทนตัวนำเข้าเดิม (`LZ-…`) และ SCAN-WATCH ของ HOSOffice (ออกแบบ: `docs/design/scan-importer-single-writer.md`)
**ห้ามตั้ง `SCAN_IMPORTER_MODE=write` บนเครื่องมากกว่าหนึ่งเครื่อง** — เครื่อง Windows ต้องเป็น `off` หรือ `shadow`

1. **โหมดเงาบน Server:** `SCAN_IMPORTER_MODE=shadow` + ใส่ `TIGER_*` → `manage.py tiger-check` ผ่าน → restart → เปิด `/watch` กด "ตั้งจุดเริ่มต้น" แล้วดูการ์ดจนตัวเลขนิ่ง
   (importer เริ่มตามโหมดของมันเอง จึงรันเงาได้ทั้งที่ `MONITOR_AUTOSTART=0` — Monitor ยังไม่ส่งแจ้งเตือนซ้ำกับเครื่องเดิม)
   ตรวจ `python manage.py hr-compare` และ `shadow-report` บน Server — ต้องเหมือนตัวเดิมเกือบทุกแถว ; ใช้ "ค้นหาที่ยังไม่เข้า hosoffice" ดูสแกนที่ขาดทั้งหมดก่อนสลับ
2. **สิทธิ์ฐานข้อมูลสำหรับ IP ของ Server:** runbook `docs/runbooks/db-accounts.md` ส่วน A, B, D (อ่านอย่างเดียว) — ส่วน C (บัญชีเขียน) สร้างในขั้นที่ 4
3. **ก่อนสลับ:** สำรองตาราง `fingle_scan` · ทำนอกช่วงเข้า-ออกงาน
4. **สลับ (ช่วงสั้นที่สุด):** หยุดผู้เขียนเดิมทั้งสอง (ส่วนนำเข้าของ `LZ-…` และ SCAN-WATCH — ยืนยันว่าหน้า Watch ไม่มีเหตุการณ์ "ผู้เขียนที่ปิดแล้วกลับมาเขียน" ใหม่)
   → ผู้ดูแลสร้างบัญชี `scan_importer_rw` (ส่วน C) → ใส่ `HOSOFFICE_WRITE_USER`, `HOSOFFICE_WRITE_PASSWORD`, `SCAN_IMPORTER_COM_NAME=SCAN-IMPORTER` ใน `.env` → `SCAN_IMPORTER_MODE=write` → `sudo systemctl restart scan-notify`
   สแกนที่เข้ามาระหว่างผู้เขียนเดิมหยุดจนถึงเปิด `write` ไม่ถูกเขียนเอง (โหมดเงาขยับจุดล่าสุดไปแล้ว) → เก็บทีหลังด้วย "ค้นหาที่ยังไม่เข้า hosoffice" แล้ว "ดูตัวอย่างนำเข้าที่ขาดทั้งหมด" → "ยืนยันนำเข้า"
5. **ตรวจหลังสลับ:** การ์ดขึ้น "โหมดเขียน", ตัวเลข "เขียนแล้ว" เพิ่มตามสแกนจริง, เปิดแถวล่าสุดใน HeidiSQL ดู `CREATE_BY='SCAN-IMPORTER'` + `COM_NAME='SCAN-IMPORTER'`, ไม่มีเหตุการณ์ SCAN-WATCH ใหม่
6. **ปุ่ม "ค้นหาที่ยังไม่เข้า hosoffice" / "นำเข้าตามเลข"** บนหน้า Watch: ค้นหาอ่านอย่างเดียวใช้ได้ทุกโหมด ; ปุ่มนำเข้าใช้ได้เฉพาะ `write` — ตรวจตัวอย่าง (เลข เวลา เข้า/ออก เจ้าของ) ก่อนกดยืนยันทุกครั้ง

## 10. ถ้าติดปัญหา

| อาการ | ตรวจ |
|---|---|
| 502 Bad Gateway | `systemctl status scan-notify`; SELinux พอร์ต 5050 (หัวข้อ 5, `ausearch -m avc -ts recent`) |
| บริการเริ่มไม่ได้ | `journalctl -u scan-notify -n 50` — ข้อความ `ต้องตั้ง ... ใน .env` = `.env` ไม่ครบ; `.env` ต้องอ่านได้โดย `scannotify` |
| Monitor ไม่เริ่ม | `.env` ต้อง `MONITOR_AUTOSTART=1`; log `ต่อ MySQL ไม่ได้` → `manage.py check`, firewall ไป 3306 |
| ส่งไม่ออก | `manage.py check` หัวข้อ MOPH (firewall/proxy ขาออก 443); หน้า Log การแจ้งเตือน |
| ล็อกอินแล้วเด้งกลับ / "คำขอไม่ได้มาจากหน้าของระบบ" | เปิดด้วยชื่อ `scan-notify.ckhospital.local` ตรงกับ `server_name`; nginx ต้องส่ง `Host` ต่อ (มีใน conf แล้ว) |
| เข้าไม่ได้ "ลองใหม่ในอีก N นาที" | กรอกรหัสผิด 5 ครั้ง รอ 15 นาที หรือ `manage.py reset-admin` |
| เวลาเตือนผิด | `timedatectl` ต้อง `Asia/Bangkok` (หัวข้อ 0.1) |
| แจ้งเตือนซ้ำ 2 ครั้ง | มี 2 ที่รัน Monitor — หยุดเครื่อง Windows / ตรวจว่า `.env` ของเครื่องอื่นไม่มี `MONITOR_AUTOSTART=1` |
