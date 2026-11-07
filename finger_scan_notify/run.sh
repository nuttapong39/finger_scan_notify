#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  Finger Scan Notify — โรงพยาบาลเชียงกลาง
#  Startup Script
# ─────────────────────────────────────────────────────────────

cd "$(dirname "$0")"

echo ""
echo "═══════════════════════════════════════════════════════"
echo "   🏥  Finger Scan Notify — โรงพยาบาลเชียงกลาง"
echo "   📡  MOPH Alert Notification System"
echo "═══════════════════════════════════════════════════════"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ ไม่พบ Python3 กรุณาติดตั้ง Python 3.8+"
    exit 1
fi

# Install dependencies if needed
echo "📦 ตรวจสอบ dependencies..."
pip3 install -r requirements.txt -q

echo ""
echo "🌐 เปิดแอพที่: http://localhost:5050"
echo "   กด Ctrl+C เพื่อหยุด"
echo ""

python3 app.py
