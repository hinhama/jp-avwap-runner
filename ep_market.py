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

def debt(frame, date):
    try: return val(frame, ['Total Debt'], date)
    except ValueError: pass
    # Match lease-inclusive or lease-exclusive definitions; never mix buckets.
    for current, long in [('Current Debt And Capital Lease Obligation','Long Term Debt And Capital Lease Obligation'), ('Current Debt','Long Term Debt')]:
        try: return val(frame,[current],date)+val(frame,[long],date)
        except ValueError: pass
    # Explicit zero total debt is already accepted above; zero total liabilities also proves no debt.
    try:
        if val(frame,['Total Liabilities Net Minority Interest'],date)==0: return 0.0
    except ValueError: pass
    # A complete component breakdown is acceptable, not a missing bucket.
    try:
        return sum(val(frame,[n],date) for n in ['Current Debt','Long Term Debt','Current Capital Lease Obligation','Long Term Capital Lease Obligation'])
    except ValueError: raise ValueError('missing verified debt breakdown')

def equity(frame,date):
    try: return val(frame,['Stockholders Equity','Common Stock Equity'],date)
    except ValueError:
        return val(frame,['Total Equity Gross Minority Interest'],date)-val(frame,['Minority Interest'],date)

def dated_shares(t,bs,asof):
    ref=pd.Timestamp(asof).date()
    try:
        shares=t.get_shares_full(start=(pd.Timestamp(asof)-pd.Timedelta(days=365)).strftime('%Y-%m-%d'),end=(pd.Timestamp(asof)+pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        if isinstance(shares,pd.Series):
            shares=shares[shares.index.date<=ref].dropna().sort_index()
            shares=shares[shares>0]
            if not shares.empty: return float(shares.iloc[-1]),str(shares.index[-1].date()),'dated_share_series'
    except Exception: pass
    # Annual statement fallback is a proxy: date is fiscal period end, not publication date.
    for d in sorted(bs.columns,reverse=True):
        if d.date()>ref: continue
        try:
            n=val(bs,['Ordinary Shares Number'],d)
            if n<=0: continue
            h=t.history(start=str(d.date()),end=str(pd.Timestamp(asof).date()+pd.Timedelta(days=1)),auto_adjust=False)
            if h.empty or 'Stock Splits' not in h or str(h.index[-1].date())!=asof: continue
            splits=h.loc[h.index.date>d.date(),'Stock Splits']
            if splits.isna().any(): continue
            n*=float(splits[splits>0].prod())
            return n,str(d.date()),'annual_shares_proxy'
        except Exception: continue
    raise ValueError('missing verified shares outstanding')

def calculate(nopat, capital, ev, wacc):
    if capital<=0 or ev<=0: raise ValueError('nonpositive capital/EV')
    ep=nopat-capital*wacc
    return ep, ep/ev, nopat/capital-wacc

def inspect(code, asof, official=None):
    t=yf.Ticker(code+'.T'); info=t.get_info()
    if info.get('sector')=='Financial Services': return {'status':'excluded_financial'}
    if not info.get('sector'): raise ValueError('missing sector')
    inc=t.income_stmt; bs=t.balance_sheet; cf=t.cashflow
    dates=sorted(set(inc.columns)&set(bs.columns),reverse=True)
    if len(dates)<4: raise ValueError('need four annual balance sheets')
    sources=[]
    def read(field, d, fn):
        try: return fn()
        except ValueError:
            if official is None: raise
            try: value=official.get(code,d,field,asof)
            except (ValueError,KeyError): raise ValueError('missing '+field+'; EDINET unavailable') from None
            sources.append(field+':'+str(d.date()))
            return value
    def cash(d): return read('cash',d,lambda: val(bs,['Cash And Cash Equivalents'],d))
    def eq(d): return read('equity',d,lambda: equity(bs,d))
    def cap(d): return eq(d)+debt(bs,d)-cash(d)
    eps=[]; nops=[]; caps=[]
    for i,d in enumerate(dates[:3]):
        n=read('op',d,lambda: val(inc,['Operating Income'],d))*.7; c=(cap(d)+cap(dates[i+1]))/2
        if c<=0: raise ValueError('nonpositive capital')
        eps.append(n-.06*c); nops.append(n); caps.append(c)
    hist=t.history(start=(pd.Timestamp(asof)-pd.Timedelta(days=90)).strftime('%Y-%m-%d'), end=(pd.Timestamp(asof)+pd.Timedelta(days=1)).strftime('%Y-%m-%d'),auto_adjust=False)
    if hist.empty or str(hist.index[-1].date()) != asof: raise ValueError('missing reference-date close')
    if len(hist)<20: raise ValueError('missing prices')
    close=float(hist.Close.iloc[-1]); turnover=float((hist.Close*hist.Volume).tail(20).mean())
    shares,shares_date,shares_source=dated_shares(t,bs,asof)
    ev=close*shares+debt(bs,dates[0])-cash(dates[0])
    ep,ratio,spread=calculate(nops[0],caps[0],ev,.06)
    growth=ep/eps[1]-1 if eps[1]>0 else None
    cf_values=[]
    for d in dates[:3]:
        try: cf_values.append(read('cf',d,lambda: val(cf,['Operating Cash Flow','Cash Flow From Continuing Operating Activities','Total Cash From Operating Activities'],d)))
        except ValueError: cf_values.append(None)
    cf_ok=all(v is not None and v>0 for v in cf_values)
    liquid=close>=300 and turnover>=1e8
    robust=nops[0]-.08*caps[0]>0
    base=liquid and ep>0 and spread>=.03 and robust and cf_ok and growth is not None and growth>=.10 and eps[0]>eps[1]>eps[2]
    return dict(edinet_fields=';'.join(sorted(set(sources))),status='proxy_shares' if shares_source=='annual_shares_proxy' else ('ok' if None not in cf_values else 'partial_cf'),nopat=nops[0],capital=caps[0],shares=shares,shares_date=shares_date,shares_source=shares_source,cf_complete=None not in cf_values,sector=info['sector'],financial_date=str(dates[0].date()),price_date=str(hist.index[-1].date()),close=close,turnover20=turnover,ep=ep,ep_previous=eps[1],ep_two_years_ago=eps[2],ep_growth=growth,ev=ev,ep_ev=ratio,spread=spread,ep_wacc8=nops[0]-.08*caps[0],base_pass=base,classification='turnaround_watch' if ep>0 and eps[1]<=0 and liquid and robust and cf_ok else 'review',basis='annual; fixed tax30%; fixed WACC6%; sensitivity8%')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=10); p.add_argument('--shard',type=int,default=0); p.add_argument('--shards',type=int,default=1); a=p.parse_args(); assert 0<=a.shard<a.shards
    from market_prepare import download_listing
    df,_=download_listing(); df=df[df['市場・商品区分'].str.contains('内国株式',na=False)]
    av=pd.read_csv('https://raw.githubusercontent.com/hinhama/jp-avwap-data/main/data/avwap_market_latest.csv',dtype={'code':str})
    asof=str(av.loc[av.status.eq('ok'),'price_date'].dropna().max())
    if asof=='nan': raise ValueError('no valid AVWAP reference date')
    from edinet_financial import Official
    official=Official()
    rows=[]; target=df if a.limit==0 else df.head(a.limit)
    if a.limit==100 and Path('ep_pilot_codes.csv').exists():
        target=pd.read_csv('ep_pilot_codes.csv',dtype={'code':str}).rename(columns={'code':'コード','name':'銘柄名'})
    target=target.sort_values('コード').iloc[a.shard::a.shards]
    for _,r in target.iterrows():
        code=str(r['コード']); row={'code':code,'name':r['銘柄名']}
        try: row.update(inspect(code,asof,official))
        except Exception as e:
            msg=str(e)
            status='special_capital' if 'nonpositive capital' in msg else ('short_history' if 'four annual' in msg else 'unavailable')
            row.update(status=status,error=msg)
        try:
            d=official.reports.loc[code,'periodEnd']; amount,parts=official.debt_candidate(code,d,asof)
            row.update(edinet_debt_candidate=amount,edinet_debt_parts=json.dumps(parts),edinet_debt_date=d)
        except (ValueError,KeyError,AttributeError): pass
        rows.append(row); print(code,row['status'],flush=True); time.sleep(1)
    out=pd.DataFrame(rows); dest=Path('ep_results'); dest.mkdir(exist_ok=True)
    if 'base_pass' in out:
        mask=out['base_pass'].eq(True)&out['ep_ev'].ge(.03)&out.status.eq('ok')
        out.loc[out.status.eq('proxy_shares'),'classification']='shares_proxy_review'
        out.loc[mask,'classification']='provisional_pass'
    out.to_csv(dest/'ep_latest.csv',index=False,encoding='utf-8-sig')
    try:
        merged=out.merge(av,on='code',how='left',suffixes=('','_avwap'),validate='one_to_one')
        merged['avwap_aligned']=merged.get('price_date',pd.Series(index=merged.index,dtype=str)).eq(merged['price_date_avwap']) & merged['status_avwap'].eq('ok')
        merged.to_csv(dest/'ep_avwap.csv',index=False,encoding='utf-8-sig')
    except Exception as e: print('AVWAP merge unavailable:',e)
    (dest/'summary.json').write_text(json.dumps({'asof':asof,'universe':len(df),'attempted':len(out),'status':out.status.value_counts().to_dict(),'basis':'annual fixed WACC proxy; not TTM or final screening'},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
