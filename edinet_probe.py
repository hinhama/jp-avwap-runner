import os, io, zipfile, datetime as dt, time, json
from pathlib import Path
import requests, pandas as pd
BASE='https://api.edinet-fsa.go.jp/api/v2'
KEY=os.environ['EDINET_API_KEY']
s=requests.Session()
def get(path, **params):
    for attempt in range(3):
        try:
            r=s.get(BASE+path,params={**params,'Subscription-Key':KEY},timeout=45)
            if r.status_code==200: return r.content
        except requests.RequestException: pass
        time.sleep(2**attempt)
    raise RuntimeError('EDINET request failed (key omitted)')
def main():
    dest=Path('edinet_probe_results'); dest.mkdir(exist_ok=True)
    codes=pd.read_csv('ep_pilot_codes.csv',dtype={'code':str})
    targets=set(codes.code); found={}; errors=[]
    today=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    for i in range(370):
        day=today-dt.timedelta(days=i)
        try:
            data=json.loads(get('/documents.json',date=str(day),type=2))
            if str(data.get('metadata',{}).get('status'))!='200': raise ValueError('metadata status')
            for r in data.get('results',[]):
                code=str(r.get('secCode') or '')[:4]
                if code in targets and code not in found and r.get('docTypeCode')=='120' and r.get('csvFlag')=='1' and r.get('withdrawalStatus')=='0' and r.get('disclosureStatus')=='0': found[code]=r
        except Exception: errors.append(str(day))
        if len(found)==len(targets): break
        time.sleep(.15)
    rows=[]; facts=[]
    terms=('Borrowings','LoansPayable','BondsPayable','LeaseLiabilities','LeaseObligations','Equity','NetAssets','CashAndCashEquivalents','OperatingIncome','OperatingProfit','OperatingActivities','ShortTermLoans','LongTermLoans','CommercialPapers')
    for code,r in sorted(found.items()):
        row={'code':code,'docID':r['docID'],'periodEnd':r.get('periodEnd'),'submitDateTime':r.get('submitDateTime')}
        try:
            z=zipfile.ZipFile(io.BytesIO(get('/documents/'+r['docID'],type=5)))
            count=0
            for name in z.namelist():
                if not name.lower().endswith('.csv') or 'asr' not in name.lower(): continue
                frame=pd.read_csv(io.BytesIO(z.read(name)),encoding='utf-16',sep='\t',dtype=str)
                if len(frame.columns)<9: continue
                for rec in frame.itertuples(index=False,name=None):
                    if any(t.lower() in str(rec[0]).lower() for t in terms):
                        facts.append([code,r['docID'],name,*rec[:9]]); count+=1
            row.update(status='ok' if count else 'no_matching_facts',facts=count)
        except Exception as e: row.update(status='error',error=type(e).__name__)
        rows.append(row); print(code,row['status'],flush=True); time.sleep(.2)
    for code in sorted(targets-set(found)): rows.append({'code':code,'status':'report_not_found'})
    pd.DataFrame(rows).to_csv(dest/'reports.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(facts,columns=['code','docID','file','element','label','context','relative_period','consolidation','period_type','unit_id','unit','value']).to_csv(dest/'facts.csv',index=False,encoding='utf-8-sig')
    (dest/'summary.json').write_text(json.dumps({'targets':len(targets),'reports_found':len(found),'list_error_dates':errors,'facts':len(facts)},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
