@echo off
chcp 65001 >nul
title Finger Scan Notify - โรงพยาบาลเชียงกลาง

echo.
echo ================================================
echo   Finger Scan Notify - โรงพยาบาลเชียงกลาง
echo   MOPH Alert Notification System
echo ================================================
echo.

cd /d "%~dp0"

REM ── ตรวจสอบว่ามี Python หรือไม่ ──
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ไม่พบ Python!
    echo กรุณาดาวน์โหลดที่ https://www.python.org/downloads/
    echo ติดตั้งแล้วเลือก "Add Python to PATH" ด้วย
    pause
    exit /b 1
)

echo [OK] พบ Python:
python --version

echo.
echo [*] กำลังติดตั้ง dependencies...
python -m pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] ติดตั้ง dependencies ไม่สำเร็จ
    pause
    exit /b 1
)

echo [OK] ติดตั้ง dependencies เรียบร้อย
echo.
echo [*] เปิดแอพที่ http://localhost:5050
echo     กด Ctrl+C เพื่อหยุด
echo.

python app.py

pause
