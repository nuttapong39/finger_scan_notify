# 🏥 Finger Scan Notify — โรงพยาบาลเชียงกลาง

ระบบแจ้งเตือนการสแกนเข้า-ออกงานผ่าน **MOPH Alert API**  
อ้างอิงจากตาราง `fingle_scan` ในฐานข้อมูล `hosoffice`

---

## ⚙️ การติดตั้ง

> **Server จริง (dashboard-srv):** ติดตั้งตาม [SERVER-SETUP-ALMALINUX.md](../SERVER-SETUP-ALMALINUX.md) อัปเดตตาม [DEPLOYMENT.md](../DEPLOYMENT.md)
> ระบบรันได้ **process เดียว เครื่องเดียว** ([ADR 0001](../docs/adr/0001-single-process.md)) — เครื่องที่ตั้ง `MONITOR_AUTOSTART=1` เท่านั้นที่เริ่ม Monitor เอง

### ความต้องการระบบ
- Python 3.10+ (Server ใช้ 3.11)
- เข้าถึงฐานข้อมูล MariaDB/MySQL (`hosoffice`) — ระบบบังคับ `READ ONLY` ทุก connection
- Token จาก MOPH Alert (ของแต่ละบุคคล)

### เครื่องพัฒนา
```bash
pip install -r requirements.txt
cp .env.example .env            # แก้ APP_ENV=development, MONITOR_AUTOSTART=0 (ห้าม 1 ถ้า Server ทำงานอยู่)
python manage.py init-admin     # ครั้งแรก: แสดงรหัสชั่วคราวของ admin
python app.py                   # Flask dev server — Server จริงใช้ serve.py (waitress) ผ่าน systemd
```

เปิด Browser ที่: **http://localhost:5050** → ล็อกอิน `admin` ด้วยรหัสชั่วคราว → ระบบบังคับเปลี่ยนรหัสก่อนใช้งาน

### ค่าใน `.env` (ดู `.env.example`)
| ค่า | ความหมาย |
|---|---|
| `APP_ENV` | `production` = ต้องมี `SECRET_KEY` และ `HOSOFFICE_*` ไม่งั้นไม่ยอมเริ่ม |
| `SECRET_KEY` | key ของ session (ไม่ตั้งบนเครื่องพัฒนา = สุ่มใหม่ทุกครั้งที่เปิด) |
| `HOSOFFICE_*` | ค่าเชื่อมต่อ hosoffice — มีแล้วหน้า "เชื่อมต่อฐานข้อมูล" แก้ไม่ได้ และรหัสใน SQLite ถูกลบ |
| `MONITOR_AUTOSTART` | `1` = เครื่องหลัก (ตั้งได้เครื่องเดียว) |
| `LISTEN_HOST` / `LISTEN_PORT` | ค่าเริ่มต้น `127.0.0.1:5050` |

### คำสั่งดูแลระบบ (`manage.py`)
`init-admin` · `reset-admin` (ลืมรหัส) · `backup [--keep=30]` · `can-restart` · `reset-checkpoint` · `check` (hosoffice READ ONLY / 4 ตาราง / MOPH / admin)

---

## 📋 โครงสร้างตาราง fingle_scan (hosoffice)

| Column | ประเภท | คำอธิบาย |
|--------|--------|-----------|
| `ID` | INT | Primary Key (ใช้ track record ใหม่) |
| `PERSON_ID` | INT | รหัสบุคลากร (match กับ Token) |
| `SCAN_DATE_TIME` | DATETIME | วันเวลาที่สแกน |
| `HR_FULLNAME` | VARCHAR | ชื่อ-สกุลผู้สแกน |
| `SCAN_TYPE` | VARCHAR | เข้า = `0`, `4` (เวลาพิเศษ), `IN` · ออก = `1`, `5` (เวลาพิเศษ), `OUT` · อื่น ๆ = ไม่ส่ง (กฎอยู่ที่ `scan_types.py`) |
| `HR_POSITION_NAME` | VARCHAR | ตำแหน่ง |
| `HR_DEP_NAME` | VARCHAR | กลุ่มงาน |
| `HR_DEP_SUB_NAME` | VARCHAR | งานย่อย |
| `ISINPUT` | VARCHAR | วิธีบันทึก (FACESCAN ฯลฯ) |

---

## 🔑 การตั้งค่า Token

1. ไปที่ **ตั้งค่า → จัดการ Token**
2. กด **เพิ่ม Token**
3. กรอก:
   - **Person ID** — PERSON_ID จากตาราง
   - **ชื่อ-สกุล** — เพื่อให้ระบุได้ง่าย
   - **MOPH Alert Token** — Token เฉพาะของบุคคลนั้น
   - เลือกว่าจะแจ้งเตือนเฉพาะ เข้างาน / ออกงาน / ทั้งคู่

---

## 🔗 MOPH Alert API

```
POST {API_URL}
Authorization: Bearer {TOKEN}
Content-Type: application/json

{
  "message": "🟢 เข้างาน\n👤 ชื่อ\n🕐 เวลา"
}
```

ตั้งค่า API URL ได้ที่ **ตั้งค่า → MOPH Alert API**

---

## 🔄 การทำงานของ Monitor

1. กด **▶ เริ่ม Monitor** บน Dashboard
2. ระบบจะ poll ฐานข้อมูลทุก N วินาที (default: 30 วินาที)
3. เมื่อพบ record ใหม่ → ค้นหา Token ของ PERSON_ID นั้น
4. ถ้ามี Token → ส่ง MOPH Alert ทันที
5. บันทึก log ทุกครั้งที่ส่ง

---

## 📁 โครงสร้างไฟล์

```
finger_scan_notify/
├── app.py              # Flask app: Monitor loop + routes เดิม + startup()
├── serve.py            # จุดเข้าของ Server: waitress process เดียว (systemd)
├── settings.py         # ค่าจาก .env (ความลับ, hosoffice, MONITOR_AUTOSTART)
├── auth.py             # ล็อกอิน admin + guard ทุก route
├── icons.py            # icon('ชื่อ') ใน template (Lucide / Simple Icons) + initial() ตัวอักษรย่อ
├── manage.py           # คำสั่งดูแลระบบบน Server (รวม tiger-check)
├── scanwatch.py        # ตรวจ Lost scan / Import stall + แจ้ง IT
├── tigersoft.py        # อ่านฐานข้อมูลเครื่องสแกน (SQL Server, SELECT อย่างเดียว)
├── .env.example        # ต้นแบบ .env (ไฟล์ .env จริง git ไม่เก็บ)
├── routes_phase2.py    # หน้า/API ตรวจสอบและส่งย้อนหลัง + เตือนยังไม่สแกน
├── scan_types.py       # กฎเดียวของทิศทาง Scan (เข้า/ออก)
├── hosoffice.py        # อ่าน MySQL hosoffice ทั้งหมด (read-only, fail closed)
├── database.py         # SQLite (config, token, log, theme, reminder, backfill job)
├── delivery.py         # จุดเดียวของการส่ง: เลือกการ์ด → ส่ง → บันทึก log + Send source
├── cards.py            # Flex card ทุกแบบ (ปกติ / ส่งย้อนหลัง / เตือน) + ข้อความ LINE Notify
├── notifier.py         # HTTP adapter: MOPH Alert / LINE Notify
├── backfill.py         # Delivery status + งานส่งย้อนหลัง
├── reminders.py        # เตือนยังไม่สแกน (ตัดสินใจ + loop ทุก 60 วินาที)
├── flex_theme.py       # Theme ของการ์ด + validation
├── requirements.txt / requirements-dev.txt
├── run.sh / run.bat    # Startup script (เครื่องพัฒนา)
├── tests/              # pytest (ไม่ต่อ MySQL จริง)
├── static/             # เสิร์ฟโดยแอปเอง (ไม่พึ่ง CDN): app.css (token สว่าง/Cyber), login.css/login.js (หน้า login), fonts/ (OFL), appearance.js,
│                       #   icons.svg (sprite — สร้างด้วย ../tools/build_icons.py อย่าแก้มือ), logo_ck.png
├── templates/
│   ├── base.html       # Base layout + sidebar (สไตล์อยู่ static/app.css)
│   ├── dashboard.html  # Main dashboard (+ _flex_editor.html)
│   ├── backfill.html   # ตรวจสอบและส่งย้อนหลัง (ปฏิทินไทย พ.ศ.)
│   ├── reminders.html  # เตือนยังไม่สแกน
│   ├── config_database.html / config_api.html / config_tokens.html
│   ├── debug.html
│   └── logs.html
└── data/               # git ไม่เก็บ — มี key ของ token ทุกคน (สิทธิ์ 600)
    ├── app_config.db   # SQLite (สร้างอัตโนมัติ)
    └── backups/        # manage.py backup
../deploy/              # systemd unit, nginx conf, sudoers, cron, preflight.sh, deploy.sh, smoke_after_deploy.sh
```

---

## 💡 Tips

- **Reset Last ID**: ตั้งให้ Monitor เริ่มนับจาก ID ล่าสุดของฐานข้อมูล (ไม่ส่งย้อนหลัง) — ถ้าต้องการส่งย้อนหลังจริง ๆ ต้องเพิ่ม `max_scan_age_minutes` และใช้ `/monitor/set-last-id`
- **ทดสอบ Token**: กดปุ่ม 🔔 ข้างแต่ละ Token เพื่อทดสอบส่งข้อความ
- **Log**: ดูประวัติการส่งได้ที่หน้า "Log การแจ้งเตือน"

---

**พัฒนาโดย:** IT Department — โรงพยาบาลเชียงกลาง จ.น่าน

---

## 🛡️ ระบบป้องกันแจ้งเตือนท่วม (เพิ่ม 4 พ.ย. 2569)

เหตุการณ์ 4 พ.ย. 2569 ~09:20: ตารางต้นทาง `fingle_scan` ถูกใส่ record ย้อนหลังปี 2023–2024 เข้ามาหลายพันแถว
ระบบเดิมมองเป็น "record ใหม่" แล้วแจ้งเตือนทุกแถวถึงเจ้าหน้าที่ — เวอร์ชันนี้ป้องกันด้วย 6 ชั้น

| # | กลไก | พฤติกรรม |
|---|------|-----------|
| 1 | กรอง scan เก่า | `SCAN_DATE_TIME` เก่ากว่า `max_scan_age_minutes` (ค่าเริ่มต้น 180 นาที) → ไม่ส่ง ข้ามและเลื่อน checkpoint |
| 2 | checkpoint ทีละแถว | เลื่อน `last_scan_id` *ก่อน* ส่งแต่ละแถว (compare-and-set) → หยุด/crash กลางทาง หรือรันซ้อนหลาย instance ก็ไม่ส่งซ้ำ |
| 3 | กัน record ซ้ำ | (คน, เวลาสแกน, ประเภท) ที่เคยส่งสำเร็จแล้ว ไม่ส่งอีก แม้ ID ใหม่ |
| 4 | โควตาต่อคน | ไม่เกิน `max_per_person_hour` (ค่าเริ่มต้น 6) ข้อความ/คน/ชั่วโมง |
| 5 | Circuit breaker | scan "สด" ในรอบเดียวเกิน `flood_limit` (ค่าเริ่มต้น 300) → **หยุด Monitor และไม่ส่งอะไร** รอคนตัดสินใจ (ดู `paused_reason` ที่ `/monitor/status`) |
| 6 | ไม่ baseline เป็น 0 | ต่อ MySQL ไม่ได้ตอนเริ่ม/รีเซ็ต → ไม่เริ่ม (เดิมจะดึงทั้งตารางมาส่ง) |

- ปรับค่า: `POST /api/guard-settings` ด้วย JSON `{"max_scan_age_minutes":180,"flood_limit":300,"max_per_person_hour":6}`
- ดูเหตุที่ระบบกันไว้: `GET /api/guard-events` (หรือดูที่ console บรรทัด `[GUARD]`)
- ถ้า Circuit breaker ทำงาน: ตรวจว่าต้นทางใส่ข้อมูลอะไรเข้ามา → ตั้ง `last_scan_id` ผ่าน `/monitor/set-last-id` (ข้ามชุดนั้น) หรือเพิ่ม `flood_limit` → กด Start

---

## 🎨 ปรับแต่ง Flex card (Theme)

การ์ดแจ้งเตือนที่ส่งผ่าน MOPH Alert เป็นโทนขาว (`mega`) มี header gradient แยกสีตามเข้างาน (เขียว) / ออกงาน (ส้มแดง)
ปรับได้ที่หน้า **Dashboard → 🎨 ปรับแต่ง Flex card** (การ์ดแบบพับได้ท้ายหน้า):

- สี gradient (เริ่ม/จบ/accent) ของเข้างานและออกงาน, มุม gradient
- คำที่หัวการ์ด ("เข้างาน"/"ออกงาน" — แบบพิเศษคงชื่อเดิม) และข้อความ footer
- ตัวอย่างสดสร้างจาก Flex JSON ตัวเดียวกับที่ส่งจริง (`POST /api/flex-preview`) — ประมาณหน้าตา ไม่ใช่ pixel-perfect
- ปุ่ม **ส่งทดสอบ** ส่งการ์ดตัวอย่าง (ข้อมูลสมมติ) ถึงบุคลากรที่เลือกจริง ด้วยค่าในฟอร์มแม้ยังไม่บันทึก
- Theme ชุดเดียวใช้กับทุกคน เก็บในตาราง SQLite `flex_theme` (API: `GET/POST /api/flex-theme`)

### รัน test
```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests
```

---

## 🔁 ตรวจสอบและส่งย้อนหลัง (Phase 2 · 6 พ.ย. 2569)

เมนู **🔁 ตรวจสอบและส่งย้อนหลัง** — เลือกช่วงวันที่ + บุคลากร → ดูสถานะของแต่ละ Scan → เลือกแล้วส่ง

| สถานะ | ความหมาย |
|---|---|
| ✅ ส่งสำเร็จ | MOPH Alert รับเรื่องแล้ว (**ไม่ได้แปลว่าเปิดอ่าน** — MOPH/LINE ไม่มีข้อมูลนี้) |
| ❌ ล้มเหลว | ส่งแล้ว API ตอบไม่สำเร็จ (ดู HTTP code / response) |
| ⏭️ ถูกข้าม | ระบบไม่ส่งพร้อมเหตุผล: เก่าเกิน, ซ้ำ, เกินโควตา, ปิดแจ้งเตือนประเภทนี้, ขอปิดแจ้งเตือนใน hosoffice, ไม่ทราบประเภท |
| ⚪ ไม่เคยส่ง | ไม่มีบันทึกเลย (เช่น ตอนนั้นแอปปิดอยู่) |

- ติ๊กอัตโนมัติเฉพาะ ⚪/❌ · ✅ ติ๊กเองได้ (= ส่งซ้ำ) · Scan จากคำร้องลืมสแกน (`MANUAL`) ไม่ติ๊กให้
- กด **ดูสรุปก่อนส่ง** → **ยืนยันส่ง** · ไม่เกิน 200 ข้อความต่อครั้ง · ช่วงวันที่เกิน 7 วันต้องพิมพ์ "ยืนยันส่งย้อนหลัง" · ทำได้ทีละงาน หยุดกลางทางได้
- ผู้รับเห็นการ์ดหัวเทา + "📨 ส่งย้อนหลัง · ส่งเมื่อ HH:MM" สีแดง
- การส่งย้อนหลัง/ทดสอบ **ไม่กินโควตา** `max_per_person_hour` ของการแจ้งเตือนสด

## ⏰ เตือนยังไม่สแกน

เมนู **⏰ เตือนยังไม่สแกน** — ทำงานอัตโนมัติทุก 60 วินาทีขณะแอปรัน (ปิดไว้เป็นค่าเริ่มต้น)

- **เตือนเข้างาน** (ค่าเริ่มต้น 09:30–12:00): คนที่ยังไม่มี Scan เข้างานวันนี้
- **เตือนออกงาน** (ค่าเริ่มต้น 17:00–22:00): คนที่สแกนเข้าแล้วแต่ยังไม่มี Scan ออก
- วันละครั้งต่อคนต่อแบบ · ตรวจ Scan สดอีกครั้งก่อนส่งทุกข้อความ · ใส่ลิงก์ปุ่มบนการ์ดได้ (https://)
- **ไม่เตือน:** เสาร์-อาทิตย์, วันหยุดราชการ (`leave_holiday`), คนที่มีใบลาวันนั้น (`leave_register` สถานะ Z/A ไม่ยกเลิก — ใบลาครึ่งวันข้ามทั้งวัน), คนใน `fingle_scan_notify_mute`, คนที่ไม่ได้เปิดรับ
- **ต่อ hosoffice ไม่ได้ = ไม่เตือน** (ไม่ตีความว่า "ไม่พบ Scan" = ลืมสแกน)
- ⚠️ ยังไม่รองรับคนเข้าเวร (พยาบาล ฯลฯ) — อย่าเปิดรับการเตือนให้กลุ่มนี้จนกว่าจะเชื่อมตารางเวร
- ปุ่ม **ถ้าเตือนตอนนี้ จะเตือนใครบ้าง** = จำลองโดยไม่ส่ง · สีการ์ดปรับที่ Dashboard → 🎨 ปรับแต่ง Flex card

รายละเอียดการออกแบบ: [`docs/design/phase2-reminder-backfill.md`](../docs/design/phase2-reminder-backfill.md) · ศัพท์: [`CONTEXT.md`](../CONTEXT.md)

## หน้าตาเว็บ (Appearance Cyber/สว่าง + ไอคอน)
- ค่าเริ่มต้น **Cyber** (ADR 0003); ปุ่มพระจันทร์/พระอาทิตย์สลับเป็นสว่าง จำต่อ browser ไม่ตามค่าของระบบปฏิบัติการ ; หน้า login เป็น Cyber เสมอ (ปุ่ม FX ปิดเอฟเฟกต์ได้)
- สีทุกจุดอ้าง token ใน `static/app.css` (`:root` = สว่าง, `[data-appearance="cyber"]` = Cyber) — ไม่ใส่ hex ในหน้า ยกเว้นตัวอย่างข้อความ/การ์ด LINE ในหน้า MOPH Alert API (ใช้สีของ LINE ตายตัว)
- ไอคอน: Lucide (ISC) และโลโก้แบรนด์ Simple Icons (CC0, ชื่อขึ้นต้น `brand-`) เรียกด้วย `{{ icon('ชื่อ') }}` ใน template หรือ `icon('ชื่อ')` ใน JS
- เพิ่มไอคอนใหม่: เรียก `icon('ชื่อ')` แล้วสร้าง sprite ใหม่
  ```
  npm pack lucide-static simple-icons        # แตก .tgz ไว้ที่ใดก็ได้
  python tools/build_icons.py --lucide <lucide>/package/icons --simple-icons <simple-icons>/package/icons
  ```
- Dashboard ยังโหลด Chart.js จาก cdnjs (ถ้า Server ออกอินเทอร์เน็ตไม่ได้ กราฟจะไม่แสดง ตัวเลขและตารางยังใช้ได้)
