# อัปเดตและดูแล Finger Scan Notify บนเครื่องจริง

ติดตั้งเครื่องครั้งแรก / ย้ายจาก Windows ดู [SERVER-SETUP-ALMALINUX.md](SERVER-SETUP-ALMALINUX.md) เอกสารนี้ใช้เมื่อเครื่องตั้งค่าแล้ว

> ระบบนี้ **ไม่เขียน hosoffice เลย** และต้องรัน **process เดียว เครื่องเดียว** ([ADR 0001](docs/adr/0001-single-process.md))

## อัปเดตด้วยคำสั่งเดียว

ที่เครื่องพัฒนา: test ผ่านแล้วติด tag และ push

```bash
cd finger_scan_notify && python -m pytest -q && cd ..
git tag v1.0.1 && git push origin main v1.0.1
```

ที่ dashboard-srv:

```bash
cd /var/www/scan-notify
sudo -u scannotify ./deploy/deploy.sh v1.0.1
```

`deploy.sh` ทำตามลำดับ และหยุดที่ขั้นแรกที่พัง:

1. ตรวจ `.env` (สิทธิ์ 600) และว่า git ที่เซิร์ฟเวอร์ไม่มีไฟล์ถูกแก้
2. `git fetch` แล้วตรวจไวยากรณ์ Python และ `deploy/*.sh` ของรุ่นใหม่ **ก่อน** แตะโค้ดบนดิสก์
3. **ไม่ยอมทำต่อถ้ามีงานส่งย้อนหลังกำลังรัน** (`manage.py can-restart`) — รอให้เสร็จหรือกดหยุดที่หน้า 🔁 ก่อน
4. **สำรองฐานข้อมูลของระบบ** ไปที่ `finger_scan_notify/data/backups/`
5. `git merge --ff-only` → `pip install` (เฉพาะเมื่อ `requirements.txt` เปลี่ยน) → ตรวจงานส่งย้อนหลังอีกครั้ง → `systemctl restart scan-notify`
6. `deploy/smoke_after_deploy.sh`: ไฟล์ความลับเปิดไม่ได้, `/login` ตอบ 200, ทุกหน้า/API บังคับล็อกอิน, POST จากเว็บอื่นถูกปฏิเสธ, บริการ active

ระหว่าง restart หน้าเว็บและ Monitor หยุดไม่กี่วินาที scan ที่เข้ามาช่วงนั้นถูกส่งเมื่อเปิดใหม่ (Monitor ทำต่อจาก `last_scan_id`)

### ถ้า deploy พัง

หลังเริ่มแตะโค้ด ขั้นใดพัง สคริปต์ย้อนโค้ดและ dependency กลับรุ่นเดิมเอง (restart เฉพาะเมื่อได้ restart ไปแล้ว)
**ไม่คืนฐานข้อมูลจากไฟล์สำรองอัตโนมัติ** เพราะถ้ารุ่นใหม่ได้ส่งแจ้งเตือนไปแล้ว การคืนไฟล์เก่าจะย้อน `last_scan_id` และลบ log การส่ง → Monitor ส่งซ้ำ
การเปลี่ยนฐานข้อมูลของระบบนี้เป็นแบบเพิ่มอย่างเดียว (ตาราง/คอลัมน์ใหม่) รุ่นเก่าทำงานบนฐานข้อมูลใหม่ได้

ย้อนรุ่นโดยตั้งใจ:

```bash
sudo -u scannotify ./deploy/deploy.sh v1.0.0 --allow-rollback
```

## ตรวจสถานะเมื่อไหร่ก็ได้

```bash
cd /var/www/scan-notify
sudo -u scannotify ./deploy/preflight.sh                                  # เครื่องพร้อมไหม (อ่านอย่างเดียว)
cd finger_scan_notify && sudo -u scannotify ../.venv/bin/python manage.py check   # hosoffice READ ONLY, 4 ตาราง, MOPH, admin
systemctl status scan-notify --no-pager
journalctl -u scan-notify -f                                              # log ของ Monitor / การเตือน / [GUARD]
./deploy/smoke_after_deploy.sh http://scan-notify.ckhospital.local
```

## บัญชี admin

- ลืมรหัส: `cd /var/www/scan-notify/finger_scan_notify && sudo -u scannotify ../.venv/bin/python manage.py reset-admin` → รหัสชั่วคราวใหม่ ล็อกอินแล้วต้องเปลี่ยน ทุก session เดิมหลุด
- เปลี่ยนรหัสปกติ: เมนู 🔐 เปลี่ยนรหัสผ่าน (browser อื่นที่ล็อกอินอยู่จะหลุด)
- กรอกผิด 5 ครั้งจาก IP เดียวกัน ล็อก 15 นาที · ไม่ได้ใช้งาน 8 ชั่วโมง ต้องล็อกอินใหม่

## สำรองและกู้ข้อมูล

ข้อมูลที่ระบบสร้างเอง (token และ key ของบุคลากรทุกคน, log การส่ง, Theme, ค่าเตือน, บัญชี admin) อยู่ในไฟล์เดียว `finger_scan_notify/data/app_config.db` ส่วน scan มาจาก hosoffice จึงไม่ต้องสำรอง

- **อัตโนมัติ:** cron 02:30 (`/etc/cron.d/scan-notify`) เก็บ 30 ไฟล์ล่าสุดที่ `data/backups/` สิทธิ์ 600 และ deploy ทุกครั้งสำรองก่อนเริ่ม
- **ด้วยมือ:** `sudo -u scannotify ../.venv/bin/python manage.py backup` (ทำได้ขณะระบบทำงาน)
- **ไฟล์สำรองอยู่ดิสก์เดียวกับต้นฉบับ** ควรคัดลอกออกไปที่อื่นเป็นระยะ — ไฟล์มี key ของ token ทุกคน ดูแลเหมือนรหัสผ่าน

กู้คืน (ระวัง: scan ที่ถูกส่งหลังเวลาของไฟล์สำรองอาจถูกส่งซ้ำ เพราะ log และ `last_scan_id` ย้อนกลับ):

```bash
sudo systemctl stop scan-notify
cd /var/www/scan-notify/finger_scan_notify
sudo -u scannotify cp data/backups/app_config-YYYYMMDD-HHMMSS.db data/app_config.db
sudo chmod 600 data/app_config.db
sudo -u scannotify ../.venv/bin/python manage.py reset-checkpoint   # เริ่มนับจาก scan ล่าสุด ไม่ส่ง scan เก่าซ้ำ
sudo systemctl start scan-notify
# scan ที่ตกหล่นระหว่างนั้น: หน้า 🔁 ตรวจสอบและส่งย้อนหลัง
```

## เปลี่ยนค่าตั้ง

- ค่าในหน้าเว็บ (token, Theme, เวลาเตือน, โควตา, poll) อยู่ใน SQLite ไม่ต้อง deploy
- ค่าใน `.env` (`HOSOFFICE_*`, `SECRET_KEY`, `MONITOR_AUTOSTART`) แก้แล้ว `sudo systemctl restart scan-notify` (ตรวจด้วย `manage.py can-restart` ก่อน)
  เปลี่ยน `SECRET_KEY` = ทุกคนต้องล็อกอินใหม่
- ความลับอยู่ใน `.env` เท่านั้น ห้ามกรอกใน `.env.example` เพราะ git เก็บไฟล์นั้น

## ข้อควรรู้

- **nginx ใช้ร่วมกับระบบอื่น** (ipd-overview, ipd-food, ck-asset-registry, rdu) ใช้ `reload` ตลอด ห้าม restart และห้ามแก้ไฟล์ของระบบอื่น
- **สคริปต์ `deploy/*.sh` ต้องมีสิทธิ์ execute ใน git** (`git update-index --chmod=+x deploy/*.sh`) เพราะ commit จาก Windows ไม่เก็บสิทธิ์นี้ — ทำแล้วใน repo นี้
- **ห้ามรัน `python app.py` หรือ `serve.py` ด้วยมือบน Server** ขณะบริการทำงาน (ADR 0001)
- **ไฟล์ `.sh` ต้องเป็น LF** — `.gitattributes` บังคับไว้แล้ว
