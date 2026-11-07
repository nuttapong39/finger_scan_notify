# ระบบรันเป็น process เดียวเท่านั้น บนเครื่องเดียวเท่านั้น

งานเบื้องหลังทั้งหมด (Monitor, Missing-scan reminder, job ของ Backfill) เป็น thread อยู่ใน process เดียวกับหน้าเว็บ
ปุ่มเริ่ม/หยุด Monitor และตัวกันไม่ให้ Backfill ซ้อนกันอยู่ใน memory ของ process นั้น
บน Server จึงรันด้วย waitress (process เดียวหลาย thread) เป็น systemd service หนึ่งตัวหลัง nginx
ไม่ใช้ gunicorn หลาย worker และไม่แยก Monitor ออกเป็น service ของตัวเอง

ถ้ามี 2 process หรือ 2 เครื่องที่เปิด Monitor พร้อมกัน บุคลากรจะได้ Flex card ซ้ำ ระบบนี้ส่งข้อความถึงมือถือคนจริงซึ่งเรียกคืนไม่ได้
เราจึงยอมเสียความสามารถในการขยายหลาย worker (ปริมาณงานจริงคือ 1 hospital, ~100 คน, poll ทุก 10 วินาที ซึ่ง process เดียวรับได้)
และเปิด Monitor เองเฉพาะเครื่องที่ตั้ง `MONITOR_AUTOSTART=1` (เครื่องหลัก) เครื่องพัฒนาไม่ตั้ง

## Considered Options

- **gunicorn 1 worker**: ต้องย้ายการเริ่ม thread ไปไว้ใน hook ของ gunicorn และมีโอกาสที่คนแก้ `--workers` ภายหลังโดยไม่รู้ผล
- **แยก service งานเบื้องหลังกับหน้าเว็บ**: ขยายได้ แต่ต้องเขียนปุ่มเริ่ม/หยุด Monitor และตัวกัน Backfill ซ้อนใหม่ให้สื่อสารข้าม process

## Consequences

- deploy ต้อง restart service (หน้าเว็บและ Monitor หยุดไม่กี่วินาที) และห้าม restart ขณะ job Backfill กำลังรัน
- ย้ายเครื่องต้องหยุดเครื่องเดิมก่อนเปิด Monitor ที่เครื่องใหม่เสมอ
