"""โหมดเงา (B1) ของ Scan importer — ล้วน ไม่มี I/O ไม่เขียนอะไร
เทียบแถวต้นทาง (เครื่องสแกน) กับแถวที่ hosoffice มีอยู่ แล้วบอกว่า importer ตัวใหม่ "จะ" ทำอะไร ใช้ plan_batch ตัวจริง จึงตรงกับโหมดเขียนทุกกติกา
รายงานมีแต่ตัวเลขกับเลข Device sequence — ไม่มีชื่อหรือรหัสบุคคล
"""
from collections import Counter

import tigersoft

from scan_importer import plan

MAX_LISTED = 20


def build_report(source_rows, hos_rows, now, *, checkpoint):
    existing = {(r.get('machine'), r['seq']) for r in hos_rows}
    p = plan.plan_batch(source_rows, existing, now, checkpoint=checkpoint)
    stored = {}
    for r in hos_rows:
        stored.setdefault((r.get('machine'), r['seq']), []).append(r)
    src_by_key = {plan.row_key(r): r for r in source_rows}

    matches, mismatch = 0, []
    for key, s in src_by_key.items():
        rows = stored.get(key)
        if not rows:
            continue
        if any(tigersoft.same_scan_time(h['time'], s) for h in rows):
            matches += 1
        else:
            mismatch.append(key[1])
    only_hos = sorted(k[1] for k in stored if k not in src_by_key)
    lo = min((r['seq'] for r in source_rows), default=None)
    only_hos = [q for q in only_hos if lo is not None and q >= lo]
    return {
        'needs_baseline': p['needs_baseline'], 'flood': p['flood'], 'source_rows': len(source_rows),
        'would_insert': len(p['insert']), 'would_hold': len(p['held']), 'already_present': len(p['duplicate']),
        'insert_seqs': [r['seq'] for r in p['insert']][:MAX_LISTED], 'held_seqs': [r['seq'] for r in p['held']][:MAX_LISTED],
        'time_matches': matches, 'time_mismatch_seqs': sorted(mismatch)[:MAX_LISTED],
        'duplicated_in_hosoffice': sum(1 for v in stored.values() if len(v) > 1),
        'only_in_hosoffice_seqs': only_hos[:MAX_LISTED],
    }


def fill_profile(rows):
    """คอลัมน์ไหนที่ตัวนำเข้าเดิมเติมเสมอ / ไม่เคยเติม / เติมบางแถว (สัดส่วน) — ใช้ถอดกติกาเติมคอลัมน์ (Q26) ; คืนเฉพาะชื่อคอลัมน์กับสัดส่วน"""
    if not rows:
        return {'always': [], 'never': [], 'sometimes': {}}
    cols = list(rows[0])
    filled = Counter()
    for r in rows:
        for c in cols:
            if r.get(c) not in (None, ''):
                filled[c] += 1
    n = len(rows)
    return {'always': [c for c in cols if filled[c] == n], 'never': [c for c in cols if filled[c] == 0],
            'sometimes': {c: filled[c] / n for c in cols if 0 < filled[c] < n}}


CODE_COLUMNS = ('COM_NAME', 'DAY_TYPE_ID', 'DAY_TYPE_NAME', 'SCAN_TYPE', 'APP_TYPE_SAVE', 'ISCONFIRM', 'ISINPUT')
MAX_DISTINCT = 12


def value_profile(rows, columns=CODE_COLUMNS):
    """การกระจายค่าของคอลัมน์รหัสที่ตัวนำเข้าเดิมใส่ → {คอลัมน์: {ค่า: จำนวน}} ; คอลัมน์ที่มีค่าต่างกันเกิน MAX_DISTINCT
    คืน {'__distinct__': n} เท่านั้น (ไม่แสดงค่า เผื่อเป็นข้อมูลบุคคล) — ใช้เฉพาะคอลัมน์รหัส ไม่ใช่ HR_*"""
    out = {}
    for c in columns:
        counts = Counter(repr(r.get(c)) for r in rows)
        out[c] = dict(counts.most_common()) if len(counts) <= MAX_DISTINCT else {'__distinct__': len(counts)}
    return out


def compare_with_real(sample, sources, consts, templates=None):
    """สร้างแถวด้วย plan.build_row จากแถวต้นทาง แล้วเทียบกับแถวจริงที่ตัวเดิมเขียน (sample) ทีละคอลัมน์
    template ของแต่ละแถว = แถวอื่นล่าสุดของคนเดียวกันใน sample (sample เรียง ID มากไปน้อย) ; PERSON_ID ใช้ของแถวจริง
    templates = {PERSON_ID: แถวล่าสุดจากฐานข้อมูล} ถ้าให้มาจะใช้แทนการหาจาก sample ; คืนแต่จำนวนกับชื่อคอลัมน์ที่ไม่ตรง — ไม่มีค่า"""
    out = {'compared': 0, 'identical': 0, 'differing': {}, 'no_template': 0, 'source_missing': 0}
    for r in sample:
        seq = r.get('MACHIN_SCAN_ID')
        if seq is None:
            continue
        src = sources.get(int(seq))
        if src is None:
            out['source_missing'] += 1
            continue
        template = (templates or {}).get(r.get('PERSON_ID')) or next(
            (o for o in sample if o is not r and o.get('PERSON_ID') == r.get('PERSON_ID')), None)
        built = plan.build_row(src, r.get('PERSON_ID'), template, consts)
        if built is None:
            out['no_template'] += 1
            continue
        out['compared'] += 1
        diff = plan.differing_columns(built, r)
        if not diff:
            out['identical'] += 1
        for c in diff:
            out['differing'][c] = out['differing'].get(c, 0) + 1
    return out
