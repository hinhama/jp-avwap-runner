import pandas as pd
from pathlib import Path
import json
root=Path('parts'); dest=Path('ep_results'); dest.mkdir(exist_ok=True)
files=list(root.glob('*/summary.json')); assert len(files)==20, 'missing shards'
summaries=[json.loads(p.read_text()) for p in files]; assert len({s['asof'] for s in summaries})==1, 'mixed dates'
for name in ['ep_latest.csv','ep_avwap.csv']:
    out=pd.concat([pd.read_csv(p.parent/name,dtype={'code':str}) for p in files],ignore_index=True)
    assert not out.code.duplicated().any(), 'duplicate codes'
    assert len(out)==summaries[0]['universe'], 'incomplete universe'
    out.sort_values('code').to_csv(dest/name,index=False,encoding='utf-8-sig')
(dest/'summary.json').write_text(json.dumps({'asof':summaries[0]['asof'],'attempted':len(out),'status':out.status.value_counts().to_dict()},ensure_ascii=False,indent=2))
