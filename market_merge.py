import csv
import json
from pathlib import Path

root = Path('market_results')
paths = sorted(root.glob('shard_*/avwap_latest.csv'))
if not paths:
    raise SystemExit('No shard result CSVs found')
rows = []
fields = None
for path in paths:
    with path.open(encoding='utf-8-sig', newline='') as f:
        r = csv.DictReader(f)
        fields = fields or r.fieldnames
        rows.extend(r)
rows.sort(key=lambda x: x.get('code', ''))
Path('market_merged').mkdir(exist_ok=True)
with Path('market_merged/avwap_market_latest.csv').open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows(rows)
summary = {'total': len(rows), 'ok': sum(r['status'] == 'ok' for r in rows), 'errors': sum(r['status'] != 'ok' for r in rows), 'missing_e_avwap': sum(not r.get('e_avwap') for r in rows), 'shards_received': len(paths)}
Path('market_merged/summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False), flush=True)
