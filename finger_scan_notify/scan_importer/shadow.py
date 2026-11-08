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
