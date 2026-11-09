"""Rank unresolved corporate actions by observed as-of portfolio weight.

This is a *review prioritization* tool, never an event approver.
"""
from pathlib import Path
import pandas as pd
import numpy as np


def prioritize(root):
    root=Path(root)
    r=root/'data/stage2_review'
    dpath=r/'event_decisions.csv'
    spath=r/'scope_decisions.csv'
    wpath=root/'data/stage2_inputs/weights.csv'
    if not dpath.exists() or not wpath.exists():
        raise FileNotFoundError('Need event_decisions.csv and weight snapshots first')
    events=pd.read_csv(dpath,dtype={'review_key':'string','chosen_source_event_id':'string'})
    weights=pd.read_csv(wpath)
    events['ex_date']=pd.to_datetime(events.ex_date,errors='coerce')
    weights['date']=pd.to_datetime(weights.date)
    weights=weights.sort_values(['date','permno'])
    events['permno']=pd.to_numeric(events.permno,errors='coerce')
    weight_lkp={(int(r.permno),pd.Timestamp(r.date)):float(r.cap_weight_proxy)
                for r in weights.itertuples(index=False)}
    by_security={int(p):part[['date','cap_weight_proxy']].sort_values('date')
                  for p,part in weights.groupby('permno')}
    observed=[]
    for row in events.itertuples(index=False):
        p=row.permno
        d=row.ex_date
        if pd.isna(p) or pd.isna(d) or int(p) not in by_security:
            observed.append(np.nan);continue
        part=by_security[int(p)]
        eligible=part[part.date.le(d)]
        if eligible.empty: observed.append(np.nan)
        else: observed.append(float(eligible.iloc[-1].cap_weight_proxy))
    events['last_observed_target_weight']=observed
    events['review_attention']=np.where(events.decision.fillna('PENDING').eq('PENDING'),
        np.where(events.last_observed_target_weight.fillna(0).ge(.002),'P0',
          np.where(events.event_type.isin(['SPINOFF','MERGER','DELIST','STOCK_SPLIT','SPECIAL_CASH_DIVIDEND']), 'P0','P1')),'REVIEWED')
    events.sort_values(['review_attention','last_observed_target_weight'],ascending=[True,False]).to_csv(
        r/'prioritized_event_decisions_4y.csv',index=False)
    if spath.exists():
        scope=pd.read_csv(spath)
        scope['priority']=np.where(scope.event_type.isin(['SPINOFF','MERGER','DELIST','STOCK_SPLIT']), 'P0','P1')
        scope.to_csv(r/'prioritized_scope_decisions_4y.csv',index=False)
    return events
