# ขั้นตอนสร้างบัญชีฐานข้อมูล (least privilege)

ผู้ที่รันคำสั่งคือ **ผู้ดูแลฐานข้อมูล** (HeidiSQL / SSMS) ไม่ใช่โค้ดของโปรเจกต์ · **ผู้ดูแลตั้งรหัสผ่านเอง** แทนค่า `<ตั้งรหัสผ่านเอง>` ห้ามวางรหัสผ่านในแชต/Git/เอกสาร
ใส่รหัสในไฟล์ `.env` ของเครื่องที่รันเท่านั้น (สิทธิ์ 600) · ตอนนี้แอปต่อ MariaDB ด้วย `root` — ขั้นตอนนี้ทำให้เลิกใช้ได้

| บัญชี | ฐานข้อมูล | ใช้โดย | สิทธิ์ | สถานะ |
|---|---|---|---|---|
| `scan_notify_ro` | MariaDB hosoffice | แอปแจ้งเตือน | SELECT เฉพาะตาราง/คอลัมน์ที่ใช้ | **ทำได้เลย (ส่วน A)** |
| `scan_notify_scanner_ro` | SQL Server TigerWebServerPlus | แอปแจ้งเตือน (`tigersoft`) | SELECT เฉพาะ `ZFP_TimeInOut` | **ทำได้เลย (ส่วน B)** |
| `scan_importer_rw` | MariaDB hosoffice | Scan importer ตัวใหม่ | SELECT + INSERT เฉพาะ `fingle_scan` | รอช่วง B (ส่วน C) |

**ห้ามแก้หรือลบบัญชี `office` (SQL Server) และ `root`/บัญชีเดิมของ HOSOffice ERP** — ตัวนำเข้าเดิมและระบบอื่นยังใช้อยู่ ให้สร้างบัญชีใหม่ข้าง ๆ แล้วสลับเฉพาะแอปนี้

เครื่องที่ต่อเข้าฐานข้อมูล: เครื่องพัฒนา/รันตอนนี้ `192.168.1.25` และ production `dashboard-srv` (ใช้ **IP** ไม่ใช้ชื่อ เพราะบางเครื่องหาชื่อไม่เจอ) — แทน `<IP_dashboard-srv>` ด้วย IP จริง

---

## A. MariaDB: บัญชีอ่านอย่างเดียวของแอปแจ้งเตือน

ตารางที่แอปอ่านจริง (ตรวจจาก `hosoffice.py`): `fingle_scan`, `hr_person` (เฉพาะ 4 คอลัมน์), `leave_register`, `leave_holiday`, `fingle_scan_notify_mute`
แอปยังบังคับ `SET SESSION TRANSACTION READ ONLY` ทุก connection อยู่แล้ว — บัญชีนี้เป็นชั้นป้องกันที่สองในฝั่งเซิร์ฟเวอร์

```sql
-- 1) สร้างผู้ใช้ (หนึ่งบรรทัดต่อเครื่อง)
CREATE USER 'scan_notify_ro'@'192.168.1.25'        IDENTIFIED BY '<ตั้งรหัสผ่านเอง>';
CREATE USER 'scan_notify_ro'@'<IP_dashboard-srv>'  IDENTIFIED BY '<ตั้งรหัสผ่านเอง>';

-- 2) ให้สิทธิ์ (ทำซ้ำสำหรับแต่ละ host ด้านบน)
GRANT SELECT ON hosoffice.fingle_scan               TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT ON hosoffice.leave_register            TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT ON hosoffice.leave_holiday             TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT ON hosoffice.fingle_scan_notify_mute   TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT (ID, FINGLE_ID, HR_FNAME, HR_LNAME) ON hosoffice.hr_person TO 'scan_notify_ro'@'192.168.1.25';
-- ไม่ให้ SELECT ทั้ง hr_person: ตารางนี้มีเลขบัตรประชาชน ที่อยู่ ฯลฯ ที่แอปไม่ต้องใช้

FLUSH PRIVILEGES;
SHOW GRANTS FOR 'scan_notify_ro'@'192.168.1.25';
```

**สลับแอปมาใช้บัญชีใหม่ (เก็บ root ไว้จนกว่าจะผ่านทุกข้อ)**
1. แก้ `.env` บนเครื่องที่รัน: `HOSOFFICE_USER=scan_notify_ro` และ `HOSOFFICE_PASSWORD=` (พิมพ์เองในไฟล์) · สำรองไฟล์เดิมไว้ก่อน
2. restart แอป แล้วรัน `python manage.py check` — ต้องผ่านทุกข้อ
3. เปิดหน้า Dashboard, `/watch`, `/backfill` (ตรวจสอบ), ปฏิทินวันหยุด/ใบลา, เทสต์การเตือนยังไม่สแกน — ต้องไม่มี error สิทธิ์ (`SELECT command denied`)
4. ถ้ามี error `denied` ที่ตาราง/คอลัมน์ใด ให้เพิ่ม GRANT เฉพาะรายการนั้น (อย่าให้ทั้งฐาน) แล้วทดสอบซ้ำ
5. ผ่านแล้วหยุดใช้ `root` ในแอป (ไม่ต้องลบ `root`)

**ย้อนกลับ:** คืนค่า `.env` เดิม แล้ว restart

---

## B. SQL Server: บัญชีอ่านอย่างเดียวของเครื่องสแกน

แอปอ่านเฉพาะ `ZFP_TimeInOut` (คอลัมน์ `ID_TimeInout`, `TimeInout`, `CommitTime`, `PersonCardID`, `MachID`) และใช้ `tigersoft.py` ที่ส่งได้เฉพาะ SELECT คำสั่งเดียวอยู่แล้ว

```sql
-- เชื่อมด้วยบัญชีผู้ดูแล (sa หรือ Windows admin) ที่ master
CREATE LOGIN scan_notify_scanner_ro WITH PASSWORD = '<ตั้งรหัสผ่านเอง>', CHECK_POLICY = ON, DEFAULT_DATABASE = TigerWebServerPlus;

USE TigerWebServerPlus;
CREATE USER scan_notify_scanner_ro FOR LOGIN scan_notify_scanner_ro;
GRANT SELECT ON dbo.ZFP_TimeInOut TO scan_notify_scanner_ro;
-- ไม่ใส่ในบทบาท db_datareader : ให้เฉพาะตารางที่ใช้ (ตารางอื่นของ Tigersoft อาจมีรูปใบหน้า/ข้อมูลส่วนบุคคล)
```

**สลับ:** แก้ `.env` `TIGER_USER=scan_notify_scanner_ro` และ `TIGER_PASSWORD=` (พิมพ์เอง) → restart → `python manage.py tiger-check` ต้องขึ้น `✓ พร้อมใช้` และ "พบที่เครื่อง n/n · เวลาตรงกัน n/n"
ถ้าขึ้น `SELECT permission was denied` ที่ `INFORMATION_SCHEMA` ให้ดูว่าตารางชื่อและ schema (`dbo`) ถูกต้องก่อน — อย่าแก้ด้วยการให้ `db_owner`
**ย้อนกลับ:** คืนค่า `TIGER_USER` เดิมใน `.env` (บัญชี `office` ไม่ถูกแตะ)

ไฟร์วอลล์ของ .77: จำกัดพอร์ต 1433 ให้เฉพาะ `192.168.1.25`, `<IP_dashboard-srv>` (ตามที่ตั้งไว้แล้ว) — ตรวจว่าไม่เปิดให้ทั้งเครือข่าย

---

## C. MariaDB: บัญชีเขียนของ Scan importer (รอช่วง B — **อย่าสร้างก่อนผ่านโหมดเงา**)

หลักการ (ADR 0002 ข้อ 3): เขียนได้เฉพาะ `INSERT` ลง `fingle_scan` ห้าม `UPDATE`/`DELETE`/DDL · อยู่เครื่อง production ที่ importer รัน (`<IP_dashboard-srv>`) เท่านั้น

```sql
CREATE USER 'scan_importer_rw'@'<IP_dashboard-srv>' IDENTIFIED BY '<ตั้งรหัสผ่านเอง>';
GRANT SELECT, INSERT ON hosoffice.fingle_scan TO 'scan_importer_rw'@'<IP_dashboard-srv>';
-- ตารางอ้างอิงสำหรับเติมคอลัมน์ (HR_*, DAY_TYPE_*): จะสรุปรายชื่อคอลัมน์จากโหมดเงา (B1) แล้วค่อยเพิ่ม GRANT SELECT ระดับคอลัมน์ทีละรายการ
FLUSH PRIVILEGES;
```

ลำดับ: (1) โหมดเงาใช้บัญชี **อ่านอย่างเดียว** ก่อน (ยังไม่ต้องมี INSERT) (2) HR ยืนยันกติกาเติมคอลัมน์ (3) ~~ล้างแถวซ้ำ + unique index~~ ตัดออกแล้ว (9 พ.ย. 2569: แถวซ้ำเก็บไว้ไม่แตะ กันซ้ำด้วยกุญแจในโค้ด) (4) สร้างบัญชีเขียน ใส่ใน `.env` ของเครื่อง importer (`HOSOFFICE_WRITE_USER`, `HOSOFFICE_WRITE_PASSWORD`, `SCAN_IMPORTER_COM_NAME`) แล้วเปลี่ยน `SCAN_IMPORTER_MODE=write` + restart
สำรอง `fingle_scan` ก่อนเปิด `write` ทุกครั้ง ; ปิดส่วนนำเข้าของตัวเดิมก่อน เพื่อให้เหลือผู้เขียนคนเดียว

---

## D. MariaDB: สิทธิ์อ่านข้อมูล HR เพิ่ม (ให้ importer สร้างแถวของพนักงานใหม่ได้)

ตัวเดิม (LZ-… และ SCAN-WATCH) เติม `HR_FULLNAME`, `HR_POSITION_NAME`, `HR_DEP_NAME`, `HR_DEP_SUB_NAME`, `HR_PERSON_TYPE_NAME`, `HR_LEAVEL_NAME` จากข้อมูล HR ตรง ๆ
importer ตัวใหม่ทำเหมือนกัน **กับทุกคนทุกครั้งที่เขียน** (v1.2.1) : ใช้ข้อมูล HR ปัจจุบันก่อน (ตรงกับแถวจริง 399/400 ; แถวที่ต่างคือ HR เพิ่งเปลี่ยนหลังแถวนั้น) แล้วใช้แถวล่าสุดของตัวเดิมเป็นตัวสำรองเมื่อ HR ไม่ครบหรืออ่านตาราง HR ไม่ได้
(v1.2.0 คัดลอกจากแถวเก่าก่อน ทำให้คนที่ HR เปลี่ยนตำแหน่ง/แผนกหลังตัวเดิมหยุดได้ค่าเก่าค้างไปเรื่อย ๆ)

**ให้สิทธิ์เฉพาะคอลัมน์ที่ใช้เท่านั้น** — `hr_person` มีรหัสผ่าน โทเคน LINE/Telegram เลขบัญชี เลขบัตรประชาชน และตาราง `hr_department*` มีโทเคน ห้ามให้ทั้งตาราง
ผู้ดูแลรันเอง (แทน IP เหมือนส่วน A ; ทำซ้ำสำหรับ `<IP_dashboard-srv>`) :

```sql
GRANT SELECT (HR_PREFIX_ID, HR_POSITION_ID, HR_DEPARTMENT_ID, HR_DEPARTMENT_SUB_ID, HR_PERSON_TYPE_ID, HR_LEVEL_ID) ON hosoffice.hr_person TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT (HR_PREFIX_ID, HR_PREFIX_NAME)                                  ON hosoffice.hr_prefix         TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT (HR_POSITION_ID, HR_POSITION_NAME)                              ON hosoffice.hr_position       TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT (HR_LEVEL_ID, HR_LEVEL_NAME)                                    ON hosoffice.hr_level          TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT (HR_DEPARTMENT_ID, HR_DEPARTMENT_NAME)                          ON hosoffice.hr_department     TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT (HR_DEPARTMENT_SUB_ID, HR_DEPARTMENT_ID, HR_DEPARTMENT_SUB_NAME) ON hosoffice.hr_department_sub TO 'scan_notify_ro'@'192.168.1.25';
GRANT SELECT (HR_PERSON_TYPE_ID, HR_PERSON_TYPE_NAME)                        ON hosoffice.hr_person_type    TO 'scan_notify_ro'@'192.168.1.25';
FLUSH PRIVILEGES;
```

ตรวจ : `python manage.py hr-compare` (อ่านอย่างเดียว ไม่แสดงชื่อ) ต้องเหมือนตัวเดิมเกือบทุกแถวก่อนเปิด `write` ; ถ้ายังไม่ให้สิทธิ์ คำสั่งแจ้งว่าไม่มีสิทธิ์ และ importer ใช้ต้นแบบจากแถวเก่าอย่างเดียว (พนักงานใหม่ถูกกัก ; คนเดิมได้ค่า HR_* ค้างตามแถวเก่า จึงควรให้สิทธิ์ส่วน D ก่อนเปิด `write`)
ผลตรวจที่ทราบแล้ว (9 พ.ย. 2569) : `HR_FULLNAME` ของตัวเดิม = คำนำหน้า + ชื่อ + ช่องว่าง 3 ตัว + นามสกุล ตรงทั้ง 400 แถว

---

## ตรวจสอบหลังสร้าง

```sql
-- MariaDB: ดูสิทธิ์ทั้งหมด และตรวจว่าไม่มีสิทธิ์เกินจำเป็น
SHOW GRANTS FOR 'scan_notify_ro'@'192.168.1.25';
SELECT user, host FROM mysql.user WHERE user LIKE 'scan\_%';
-- ทดสอบด้วยบัญชีใหม่ใน HeidiSQL (session แยก): ต้องล้มทั้งสองคำสั่ง
--   INSERT INTO fingle_scan (PERSON_ID) VALUES (0);     -- ERROR 1142 command denied
--   SELECT HR_CID FROM hr_person LIMIT 1;               -- ERROR 1143 column denied
```
```sql
-- SQL Server
SELECT dp.name, p.permission_name, p.state_desc, OBJECT_NAME(p.major_id) AS obj
FROM sys.database_permissions p JOIN sys.database_principals dp ON dp.principal_id = p.grantee_principal_id
WHERE dp.name = 'scan_notify_scanner_ro';
```

## การเก็บรหัสผ่าน
- ผู้ดูแลตั้งรหัสเองและเก็บในที่เก็บรหัสผ่านของทีม ; ใส่ในไฟล์ `.env` ของเครื่องที่รันเท่านั้น (ไม่ commit, สิทธิ์ 600)
- เปลี่ยนรหัสเมื่อคนในทีมที่เคยรู้ย้ายออก ; ถ้าสงสัยว่ารั่ว ให้ `ALTER USER … IDENTIFIED BY` / `ALTER LOGIN … WITH PASSWORD` แล้วแก้ `.env` + restart
