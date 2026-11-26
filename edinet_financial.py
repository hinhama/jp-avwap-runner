import pandas as pd
from pathlib import Path

class Official:
    def __init__(self, folder='edinet_probe_results'):
        self.reports=pd.read_csv(Path(folder)/'reports.csv',dtype=str).set_index('code')
        f=pd.read_csv(Path(folder)/'facts.csv',dtype=str)
        self.groups={c:g for c,g in f[f.unit_id.eq('JPY')].groupby('code')}
    def get(self,code,date,field,asof):
        r=self.reports.loc[code]; end=pd.Timestamp(r.periodEnd); date=pd.Timestamp(date)
        if pd.Timestamp(r.submitDateTime).date()>pd.Timestamp(asof).date(): raise ValueError('report not yet public')
        if date==end: prefix='CurrentYear'
        elif date==end-pd.DateOffset(years=1): prefix='Prior1Year'
        else: raise ValueError('EDINET period mismatch')
        g=self.groups[code]; g=g[g.docID.eq(r.docID)]
        names={'cash':['jppfs_cor:CashAndCashEquivalents','jpigp_cor:CashAndCashEquivalentsIFRS'], 'op':['jppfs_cor:OperatingIncome','jpigp_cor:OperatingProfitLossIFRS'], 'cf':['jppfs_cor:NetCashProvidedByUsedInOperatingActivities','jpigp_cor:NetCashProvidedByUsedInOperatingActivitiesIFRS'], 'equity':['jpigp_cor:EquityAttributableToOwnersOfParentIFRS']}
        # JGAAP shareholders equity excludes accumulated OCI; do not substitute for Yahoo parent equity.
        if field not in names: raise ValueError('unsupported official field')
        suffix='Duration' if field in ('op','cf') else 'Instant'
        # Only consolidated primary contexts; no implicit standalone substitution.
        q=g[g.context.eq(prefix+suffix)&g.consolidation.eq('連結')&g.element.isin(names[field])]
        vals=pd.to_numeric(q.value,errors='coerce').dropna().unique()
        if len(vals)!=1: raise ValueError('missing or conflicting official fact')
        return float(vals[0])

    def debt_candidate(self,code,date,asof):
        r=self.reports.loc[code]; end=pd.Timestamp(r.periodEnd); date=pd.Timestamp(date)
        if pd.Timestamp(r.submitDateTime).date()>pd.Timestamp(asof).date(): raise ValueError('report not yet public')
        prefix='CurrentYear' if date==end else 'Prior1Year' if date==end-pd.DateOffset(years=1) else None
        if prefix is None: raise ValueError('period mismatch')
        g=self.groups[code]; g=g[g.docID.eq(r.docID)&g.context.eq(prefix+'Instant')&g.consolidation.eq('連結')]
        tags=['ShortTermLoansPayable','LongTermLoansPayable','CurrentPortionOfLongTermLoansPayable','BondsPayable','CurrentPortionOfBondsPayable','CommercialPapersLiabilities','LeaseObligationsCL','LeaseObligationsNCL']
        parts={}
        for tag in tags:
            q=g[g.element.eq('jppfs_cor:'+tag)]
            if q.empty: continue
            v=pd.to_numeric(q.value.replace({'－':'0','―':'0'}),errors='coerce').dropna().unique()
            if len(v)!=1 or v[0]<0: raise ValueError('conflicting debt component')
            parts[tag]=float(v[0])
        if not parts: raise ValueError('no verified debt components')
        return sum(parts.values()),parts
