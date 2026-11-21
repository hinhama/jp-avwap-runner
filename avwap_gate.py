"""Produce a shared, conservative AVWAP input for other screening systems.

This exports signals; it does NOT execute or integrate proprietary stock screeners.
Nikkei regime and strategy-specific fundamental rules must be evaluated separately.
"""
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path('market_merged')
SOURCE = BASE / 'avwap_market_quality.csv'
FIELDS = ['code','name','price_date','close_adjusted','y_gap_pct','e_gap_pct','s_gap_pct',
          'e_source','disclosure_verified','quality_flags','screen_status','avwap_grade',
          'avwap_gate','nikkei_regime','final_trade_permission','reason']

def number(value):
    try:
        v = float(value)
        return v if v == v and abs(v) != float('inf') else None
    except (TypeError, ValueError):
        return None

def classify(row):
    if row.get('status') != 'ok':
        return 'HOLD','HOLD','calculation_error'
    if row.get('screen_status') == 'HOLD':
        return 'HOLD','HOLD',row.get('quality_flags') or 'data_incomplete'
    gaps = [number(row.get(k+'_gap_pct')) for k in ('y','e','s')]
    if any(v is None for v in gaps):
        return 'HOLD','HOLD','missing_avwap_gap'
    positive = sum(v > 0 for v in gaps)
    # The grade is a transparent, provisional position-based proxy, not a validated trading score.
    if positive == 3:
        grade = 'A+' if min(gaps) >= 3 else 'A'
        gate = 'PASS_PRELIMINARY'
    elif positive == 2:
        grade,gate = 'B','WATCH'
    else:
        grade,gate = 'C','AVOID_NEW_BUY'
    if row.get('disclosure_verified') != 'YES':
        return grade,gate,'earnings_disclosure_unverified;verify_before_trade'
    return grade,gate,'earnings_disclosure_verified;other_checks_required'

def main():
    with SOURCE.open(encoding='utf-8-sig', newline='') as f:
        source = list(csv.DictReader(f))
    if not source:
        raise SystemExit('No quality rows')
    output=[]
    counts=Counter()
    for row in source:
        grade,gate,reason=classify(row)
        result={key:row.get(key,'') for key in FIELDS}
        result.update(avwap_grade=grade,avwap_gate=gate,nikkei_regime='NOT_EVALUATED',
                      final_trade_permission='NOT_AUTHORIZED',reason=reason)
        output.append(result)
        counts[gate]+=1
    for name, rows in [('avwap_common_gate.csv',output),
                       ('avwap_common_pass.csv',[r for r in output if r['avwap_gate']=='PASS_PRELIMINARY']),
                       ('avwap_common_watch.csv',[r for r in output if r['avwap_gate']=='WATCH'])]:
        with (BASE/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    (BASE/'avwap_gate_summary.json').write_text(json.dumps({
        'asof_jst':datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),
        'total':len(output),'gate_counts':dict(counts),
        'notes':'AVWAP-only technical gate. Nikkei regime, fundamentals, liquidity and earnings disclosure verification are not incorporated; no trade is authorized.'
    },ensure_ascii=False,indent=2),encoding='utf-8')
    print('AVWAP common gate:',len(output),dict(counts),flush=True)

if __name__=='__main__':
    main()
