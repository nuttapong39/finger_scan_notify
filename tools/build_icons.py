"""สร้าง finger_scan_notify/static/icons.svg (sprite) จากไอคอนที่ template เรียกใช้จริง

    npm pack lucide-static simple-icons          # ดาวน์โหลดแล้วแตก .tgz ไว้ที่ใดก็ได้
    python tools/build_icons.py --lucide <แตกแล้ว>/lucide/package/icons --simple-icons <แตกแล้ว>/si/package/icons

ชื่อไอคอนมาจาก `icon('ชื่อ')` ใน finger_scan_notify/templates/*.html
  - ชื่อธรรมดา (log-in, sun, ...)  = Lucide  (ISC)
  - brand-<slug> (brand-line, ...) = Simple Icons (CC0), slug = ชื่อไฟล์ใน simple-icons/icons
เพิ่มไอคอนใหม่: เรียก icon('ชื่อ') ใน template แล้วรันสคริปต์นี้ซ้ำ (sprite ใช้แค่ที่เรียก จึงเล็ก)
"""
import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'finger_scan_notify')
CALL = re.compile(r"""\bicon\(\s*['"]([a-z0-9-]+)['"]""")
INNER = re.compile(r'<svg\b[^>]*>(.*)</svg>', re.S)
COMMENT = re.compile(r'<!--.*?-->', re.S)
TITLE = re.compile(r'<title>.*?</title>', re.S)


def used_names():
    names = set()
    for path in glob.glob(os.path.join(APP, 'templates', '*.html')):
        with open(path, encoding='utf-8') as f:
            names.update(CALL.findall(f.read()))
    return sorted(names)


def package_version(icons_dir):
    try:
        with open(os.path.join(os.path.dirname(icons_dir), 'package.json'), encoding='utf-8') as f:
            return json.load(f).get('version', '?')
    except OSError:
        return '?'


def read_inner(path):
    with open(path, encoding='utf-8') as f:
        svg = f.read()
    inner = INNER.search(svg).group(1)
    return re.sub(r'\s*\n\s*', '', TITLE.sub('', COMMENT.sub('', inner))).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lucide', required=True, help='โฟลเดอร์ icons/ ของ lucide-static')
    ap.add_argument('--simple-icons', required=True, help='โฟลเดอร์ icons/ ของ simple-icons')
    ap.add_argument('--out', default=os.path.join(APP, 'static', 'icons.svg'))
    args = ap.parse_args()

    symbols, missing = [], []
    for name in used_names():
        brand = name.startswith('brand-')
        path = os.path.join(args.simple_icons if brand else args.lucide, (name[6:] if brand else name) + '.svg')
        if not os.path.isfile(path):
            missing.append(name)
            continue
        symbols.append(f'<symbol id="{name}" viewBox="0 0 24 24">{read_inner(path)}</symbol>')
    if missing:
        print('ไม่พบไอคอน:', ', '.join(missing), file=sys.stderr)
        return 1

    header = (f'<!-- สร้างด้วย tools/build_icons.py อย่าแก้มือ | Lucide {package_version(args.lucide)} (ISC) | '
              f'Simple Icons {package_version(args.simple_icons)} (CC0) | {len(symbols)} ไอคอน -->')
    with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg">\n' + header + '\n' + '\n'.join(symbols) + '\n</svg>\n')
    print(f'เขียน {args.out}: {len(symbols)} ไอคอน')
    return 0


if __name__ == '__main__':
    sys.exit(main())
