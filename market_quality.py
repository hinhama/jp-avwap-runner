"""Post-merge data-quality labels and conservative screening export.
No claims of verified disclosures are made for unverified TDnet candidates.
"""
import csv
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('market_merged')
SOURCE = ROOT / 'avwap_market_latest.csv'
TODAY = datetime.now(ZoneInfo('Asia/Tokyo')).date()

def number(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None

def main():
    with SOURCE.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit('No rows in merged result')
    output = []
    counts = Counter()
    for row in rows:
        flags = []
        if row.get('status') != 'ok':
            flags.append('calculation_error')
        else:
            try:
                price_day = date.fromisoformat(row['price_date'])
                # Business days since last price; weekends are not stale.
                business_days = sum(date.fromordinal(n).weekday() < 5 for n in range(price_day.toordinal()+1, TODAY.toordinal()+1))
                if business_days > 1:
                    flags.append('stale_price')
            except (ValueError, KeyError):
                flags.append('invalid_price_date')
            source = row.get('e_source', '')
            if 'awaiting_price' in source:
                flags.append('e_awaiting_next_session')
            elif not row.get('e_avwap'):
                flags.append('e_missing')
            if 'candidate_unverified' in source:
                flags.append('e_disclosure_unverified')
            elif source and source not in ('tdnet_verified_manual', 'earnings_csv_manual'):
                flags.append('e_source_not_verified')
        y, e, s = (number(row.get(f'{key}_gap_pct')) for key in ('y', 'e', 's'))
        ready = row.get('status') == 'ok' and not any(f in flags for f in ('stale_price', 'invalid_price_date', 'e_missing', 'e_awaiting_next_session')) and all(v is not None for v in (y,e,s))
        row['quality_flags'] = '|'.join(flags)
        row['screen_status'] = 'PASS_PRELIMINARY' if ready and y > 0 and e > 0 and s > 0 else ('FAIL' if ready else 'HOLD')
        row['disclosure_verified'] = 'YES' if row.get('e_source') == 'tdnet_verified_manual' else 'NO'
        counts[row['screen_status']] += 1
        for flag in flags:
            counts[flag] += 1
        output.append(row)
    fields = list(output[0])
    for filename, selection in (
        ('avwap_market_quality.csv', output),
        ('avwap_screen_candidates.csv', [r for r in output if r['screen_status'] == 'PASS_PRELIMINARY']),
        ('avwap_hold_review.csv', [r for r in output if r['screen_status'] == 'HOLD']),
    ):
        with (ROOT / filename).open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            w.writeheader()
            w.writerows(selection)
    summary = {'asof_jst': TODAY.isoformat(), 'total': len(output), 'counts': dict(counts), 'disclaimer': 'PASS_PRELIMINARY is an AVWAP-only technical filter, not a buy recommendation; unverified disclosures must be checked before trading.'}
    (ROOT / 'quality_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
