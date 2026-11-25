import argparse, time, json, math
from pathlib import Path
import pandas as pd
import yfinance as yf
import requests

def val(frame, names, date):
    for n in names:
        if n in frame.index and date in frame.columns:
            v=frame.loc[n,date]
            if pd.notna(v): return float(v)
    raise ValueError('missing '+ '/'.join(names))

def calculate(nopat, capital, ev, wacc):
    if capital<=0 or ev<=0: raise ValueError('nonpositive capital/EV')
    ep=nopat-capital*wacc
    return ep, ep/ev, nopat/capital-wacc

def inspect(code):
    t=yf.Ticker(code+'.T'); info=t.get_info()
    if info.get('sector')=='Financial Services': return {'status':'excluded_financial'}
    if not info.get('sector'): raise ValueError('missing sector')
    inc=t.income_stmt; bs=t.balance_sheet; cf=t.cashflow
    dates=sorted(set(inc.columns)&set(bs.columns),reverse=True)
    if len(dates)<4: raise ValueError('need four annual balance sheets')
    def cap(d): return val(bs,['Stockholders Equity'],d)+val(bs,['Total Debt'],d)-val(bs,['Cash And Cash Equivalents'],d)
    eps=[]; nops=[]; caps=[]
    for i,d in enumerate(dates[:3]):
        n=val(inc,['Operating Income'],d)*.7; c=(cap(d)+cap(dates[i+1]))/2
        if c<=0: raise ValueError('nonpositive capital')
        eps.append(n-.06*c); nops.append(n); caps.append(c)
    hist=t.history(period='2mo',auto_adjust=False)
    if len(hist)<20: raise ValueError('missing prices')
    close=float(hist.Close.iloc[-1]); turnover=float((hist.Close*hist.Volume).tail(20).mean())
    ev=float(info['marketCap'])+val(bs,['Total Debt'],dates[0])-val(bs,['Cash And Cash Equivalents'],dates[0])
    ep,ratio,spread=calculate(nops[0],caps[0],ev,.06)
    growth=ep/eps[1]-1 if eps[1]>0 else None
    cf_ok=all(val(cf,['Operating Cash Flow'],d)>0 for d in dates[:3])
    liquid=close>=300 and turnover>=1e8
    robust=nops[0]-.08*caps[0]>0
    base=liquid and ep>0 and spread>=.03 and robust and cf_ok and growth is not None and growth>=.10 and eps[0]>eps[1]>eps[2]
    return dict(status='ok',sector=info['sector'],financial_date=str(dates[0].date()),price_date=str(hist.index[-1].date()),close=close,turnover20=turnover,ep=ep,ep_previous=eps[1],ep_two_years_ago=eps[2],ep_growth=growth,ev=ev,ep_ev=ratio,spread=spread,ep_wacc8=nops[0]-.08*caps[0],base_pass=base,classification='turnaround_watch' if ep>0 and eps[1]<=0 and liquid and robust and cf_ok else 'review',basis='annual; fixed tax30%; fixed WACC6%; sensitivity8%')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=10); a=p.parse_args()
    from market_prepare import download_listing
    df,_=download_listing(); df=df[df['市場・商品区分'].str.contains('内国株式',na=False)]
    rows=[]; target=df if a.limit==0 else df.head(a.limit)
    for _,r in target.iterrows():
        code=str(r['コード']); row={'code':code,'name':r['銘柄名']}
        try: row.update(inspect(code))
        except Exception as e: row.update(status='unavailable',error=str(e))
        rows.append(row); print(code,row['status'],flush=True); time.sleep(1)
    out=pd.DataFrame(rows); dest=Path('ep_results'); dest.mkdir(exist_ok=True)
    if 'base_pass' in out:
        mask=out['base_pass'].eq(True)&out['ep_ev'].ge(.03)
        out.loc[mask,'classification']='provisional_pass'
    out.to_csv(dest/'ep_latest.csv',index=False,encoding='utf-8-sig')
    try:
        av=pd.read_csv('https://raw.githubusercontent.com/hinhama/jp-avwap-data/main/data/avwap_market_latest.csv',dtype={'code':str}); out.merge(av,on='code',how='left',suffixes=('','_avwap')).to_csv(dest/'ep_avwap.csv',index=False,encoding='utf-8-sig')
    except Exception as e: print('AVWAP merge unavailable:',e)
    (dest/'summary.json').write_text(json.dumps({'universe':len(df),'attempted':len(out),'status':out.status.value_counts().to_dict(),'basis':'annual fixed WACC proxy; not TTM or final screening'},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
