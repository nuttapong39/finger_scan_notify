# UI แนว Cyber Security / Terminal (หน้า Login + ทั้งระบบ)

สถานะ: **ออกแบบ (8 พ.ย. 2569)** — รอยืนยันก่อนลงมือ · การตัดสินใจ: `docs/adr/0003-appearance-cyber.md`
แทนที่บางส่วนของ `docs/design/ui-minimal-light-dark.md` (ค่าเริ่มต้น, ข้อ "ตัดเงา glow")

## ไฟล์อ้างอิง
- `finger_scan_notify/login1.html` — **ไม่ครบ** (ตัดจบที่บรรทัด 291 กลางคอมเมนต์ ไม่มีฟอร์ม/การ์ด/สคริปต์) ใช้เป็นตัวอย่าง CSS และ HUD เท่านั้น
- `finger_scan_notify/login2.html` — ครบ ใช้เป็นโครงหลัก แต่เป็นเทมเพลต Django (`{% load static %}`, `{% csrf_token %}`, `form.username`, `messages`, `request.META`) ต้องแปลงเป็น Jinja/Flask
  ฟอร์มจริง: `POST /login` ฟิลด์ `username`, `password`, `next` ; ตัวแปรเทมเพลต `next`, `error` ; ไม่มี CSRF token ; ข้อผิดพลาด 401/429 render หน้าเดิมพร้อม `error`

## การตัดสินใจ (Q1–Q12)
| # | เรื่อง | ตัดสินใจ |
|---|---|---|
| Q1 | ขอบเขต | เฟส 1 = หน้า Login + token ชุด Cyber กลาง ; เฟส 2 = ทุกหน้าที่เหลือ (ตรวจด้วย Chrome หลังเฟส 1 ก่อนเริ่มเฟส 2) |
| Q2/Q9 | Appearance | Cyber (เริ่มต้น) + สว่าง ; หน้า Login เป็น Cyber เสมอ ไม่มีปุ่มสลับ มีเฉพาะ FX ; ค่าที่จำไว้ `dark` อ่านเป็น `cyber` |
| Q3 | ไฟล์ฐาน | `login2.html` เป็นโครง ; ยืมจาก `login1.html` status bar ด้านล่างและการเรียงสี |
| Q4 | ศัพท์ | **Appearance: Cyber** (ไม่ใช่ Theme ซึ่งหมายถึงการ์ด LINE) |
| Q5 | เอฟเฟกต์ | อยู่ที่หน้า Login เท่านั้น ; FX toggle ; ปิดเองเมื่อ reduced-motion ; glitch สั้นครั้งเดียว ; ไม่มี boot screen |
| Q6 | ข้อมูลตกแต่ง | ไม่อ้างสถานะจริง (ไม่มี SECURE/ONLINE/TLS/NODE ; log และมิเตอร์เป็นของจำลองชัดเจน) ; ไม่แสดง IP |
| Q7 | ฟอนต์ | ฝัง `static/fonts/` เอง (IBM Plex Sans Thai, IBM Plex Mono, Orbitron ; OFL) |
| Q8 | หน้าที่เหลือ | palette Cyber ไม่มีเอฟเฟกต์เคลื่อนไหว/scanlines ; คอนทราสต์ AA |
| Q10 | บันทึก | ADR 0003 + เอกสารนี้ + หมายเหตุแทนที่ในเอกสาร UI เดิม |
| Q11 | ฟอนต์ | อนุญาตให้ดาวน์โหลด `@fontsource` ผ่าน jsDelivr (woff2) |
| Q12 | ภาษา | ข้อความที่ต้องอ่านเป็นไทย ; ป้ายอังกฤษเป็นของตกแต่งเสริม |

## เฟส 1 — หน้า Login + token
**ไฟล์**
- ใหม่: `static/fonts/*.woff2` + `LICENSE-ofl`, `static/login.css`, `static/login.js`
- แก้: `static/app.css` (token Cyber แทน `[data-appearance="dark"]`, `@font-face`), `static/appearance.js` (ค่าเริ่มต้น cyber, แปลง `dark`→`cyber`), `templates/login.html` (แปลงจาก login2), `templates/_head_assets.html`, `templates/_appearance_toggle.html` (เปลี่ยนไอคอน/ข้อความ "Cyber"), `tools/` ถ้ามีสคริปต์ที่อ้าง `dark`
- ไม่แตะ: `auth.py` (ฟอร์ม/ฟิลด์/`next`/ล็อกเอาต์ไม่เปลี่ยน)

**ลำดับ**
1. ดาวน์โหลดฟอนต์ (woff2 + LICENSE) → `static/fonts/` ; ตรวจขนาดรวม
2. token Cyber ใน `app.css` + แปลงค่า `dark`→`cyber` ใน `appearance.js` ; หน้า Login ตั้ง `data-appearance="cyber"` ตายตัวและไม่โหลดสคริปต์สลับ
3. `login.html` : แปลง Django→Jinja (ตัด `{% load static %}`/`csrf_token`/`messages`/`request.META`) ; ใช้ `static_url()`, `icon()`, `error`, `next` ; ฟอร์มทำงานได้โดยไม่ต้องมี JS
4. `login.css`/`login.js` : ฝนตัวอักษร, scanlines, glow, glitch ครั้งเดียว, นาฬิกา, ตัวพิมพ์ข้อความ, ปุ่มแสดงรหัสผ่าน, เตือน Caps Lock, ปุ่ม FX (จำใน `localStorage`, ตัดสินครั้งแรกจาก `prefers-reduced-motion`), สถานะ loading ตอนกดส่ง
5. test + ตรวจด้วย Chrome (จอกว้าง/แคบ, FX เปิด/ปิด, reduced-motion, ข้อความผิดพลาด 401 และ 429)

**Seams ที่จะทดสอบ**
1. `GET /login` — HTML ที่ render (ฟิลด์/`next`/โลโก้จาก `/static`/ไม่มีไวยากรณ์ Django เหลือ/ไม่เรียกภายนอก `fonts.googleapis.com` / `data-appearance="cyber"` ตายตัว/มีปุ่ม FX ไม่มีปุ่ม Appearance)
2. `POST /login` ผิดรหัส (401) และถูกล็อก (429) — หน้าเดิมแสดงข้อความไทยเดิม `error` ต้องไม่เปลี่ยน
3. ไฟล์ static — ฟอนต์/`login.css`/`login.js` เปิดได้ก่อนล็อกอิน (200) และ URL มี `?v=`
4. เทมเพลตทุกหน้าในแอป — ค่าเริ่มต้นเป็น Cyber และสลับสว่างได้ (ปรับ test เดิม `test_every_page_has_the_appearance_toggle…` ให้ยกเว้น `/login`)
ไม่ทดสอบ: ภาพเคลื่อนไหวและตำแหน่งพิกเซล (ตรวจด้วย Chrome)

**เสร็จเมื่อ:** test ทั้งชุดผ่าน · ใน Chrome หน้า Login อ่านได้ชัดทั้ง FX เปิด/ปิด · login จริง (สำเร็จ/ผิดรหัส/ถูกล็อก/`next`) ทำงานเหมือนเดิม

## เฟส 2 — ทั้งระบบ (หลังตรวจเฟส 1)
- `base.html` (sidebar, topbar, ปุ่มสลับ Appearance), `dashboard`, `logs`, `reminders`, `backfill`, `watch`, `config_*`, `account_password`, `debug`, `_flex_editor`, `_password_form`
- ใช้ token เดียวกัน ไม่เพิ่ม CSS ซ้ำรายหน้า ; หัวข้อ/ตัวเลขใช้ฟอนต์ mono ; ตาราง/ฟอร์มพื้นเรียบ เส้นขอบบางสีเขียวจาง ; ปุ่มหลักสี neon ตัวอักษรเข้มคอนทราสต์ AA
- พรีวิว Flex card ในหน้าแก้ไขแสดงสีของการ์ดตามเดิม (ไม่ใช่ Appearance)
- ตรวจด้วย Chrome ทุกหน้าทั้งสองโหมด

## ความเสี่ยง
- เอฟเฟกต์หน้า Login กิน CPU บนเครื่องเก่า → ปุ่ม FX + ปิดเองตาม reduced-motion + จำกัดเฟรมเรตของ canvas
- ฟอนต์ไทยของ Plex ขนาดไฟล์ใหญ่ → เลือกเฉพาะน้ำหนักที่ใช้ (`font-display: swap`) ฝังเฉพาะ subset `thai` และ `latin`
- test เดิมเกี่ยวกับ `dark` และ "ทุกหน้ามีปุ่มสลับ" ต้องแก้พร้อมโค้ด ห้ามผ่านเพราะผ่อนการตรวจ
- ผู้ที่ตั้ง `light` ไว้จะไม่เห็นการเปลี่ยนแปลงภายในระบบจนกว่าจะสลับเอง (ตั้งใจ)
