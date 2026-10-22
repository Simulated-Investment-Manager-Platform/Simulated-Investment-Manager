"""Point-in-time active-window coverage checks for multi-year BMLL simulations.

Does not waive review decisions, synthesize prices, or approve actions.
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import json
import pandas as pd
import numpy as np


def required_quote_days(weights, calendar):
    """PERMNO -> required valuation and decision/fill sessions.

    Holdings from snapshot t must remain valuated until the execution session
    following snapshot t+1, when they can be sold. The next snapshot itself
    may first trade on t+1's next session. Original simulation never trades
    before a snapshot and has no forced intraquarter delisting resolution.
    """
    w = weights.copy()
    w['date'] = pd.to_datetime(w.date).dt.normalize()
    if w[['date','permno']].duplicated().any():
        raise ValueError('Duplicate security in quarterly snapshot')
    cal = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize().sort_values().unique()
    if not len(cal) or w.empty:
        raise ValueError('Missing snapshot or trading calendar')
    dates = sorted(pd.to_datetime(w.date.unique()))
    if dates[0] != cal[0]:
        raise ValueError('Replay calendar must start at its initial portfolio decision snapshot')
    if not set(dates).issubset(set(cal)):
        raise ValueError('Snapshot without a trading session')
    need = defaultdict(set)
    for i, d in enumerate(dates):
        next_snapshot = dates[i+1] if i+1 < len(dates) else None
        if next_snapshot is None:
            right = len(cal)-1
        else:
            next_loc = cal.get_loc(next_snapshot)
            right = min(len(cal)-1, next_loc+1)
        left = cal.get_loc(d)
        active_days = set(cal[left:right+1])
        for pid in w.loc[w.date.eq(d),'permno']:
            need[int(pid)].update(active_days)
    return {p: frozenset(days) for p, days in need.items()}


def price_coverage_report(required, prices):
    q=prices.copy()
    q['date']=pd.to_datetime(q.date).dt.normalize()
    if q[['date','permno']].duplicated().any():
        raise ValueError('Duplicate CRSP date/PERMNO price keys')
    q['permno']=pd.to_numeric(q.permno,errors='raise').astype(int)
    by_id={int(p):f.set_index('date') for p,f in q.groupby('permno')}
    out=[]
    for p, days in sorted(required.items()):
        have=by_id.get(p)
        required_days=sorted(days)
        if have is None:
            miss=len(days); invalid=trade=0
        else:
            part=have.reindex(required_days)
            miss=int(part['close_price'].isna().sum())
            vals=pd.to_numeric(part.close_price,errors='coerce')
            invalid=int((vals.notna() & (~np.isfinite(vals) | (vals<=0))).sum())
            if 'price_flag' in part:
                trade=int((vals.notna() & part.price_flag.astype(str).str.upper().ne('TR')).sum())
            else:
                trade=0
        out.append({'permno':p,'first_required':required_days[0],
            'last_required':required_days[-1], 'required_sessions':len(days),
            'missing_sessions':miss, 'bad_price_sessions':invalid,
            'untradeable_sessions':trade, 'ready':not(miss or invalid or trade)})
    return pd.DataFrame(out)


def audit(root, *, evaluation_start='2022-07-01', evaluation_end='2026-06-30'):
    from research_pipeline import load_handoff, read_table
    root=Path(root)
    folder=root/'data/stage2_inputs'
    _, data=load_handoff(folder)
    w=data['weights'].copy()
    px=read_table(folder/'daily_prices.csv')
    cal=read_table(folder/'calendar.csv').date
    w['date']=pd.to_datetime(w.date)
    required=required_quote_days(w[w.date.between(cal.min(),cal.max())],cal)
    details=price_coverage_report(required,px)
    summary={
      'evaluation_start':evaluation_start,'evaluation_end':evaluation_end,
      'replay_start':str(pd.Timestamp(cal.min()).date()),
      'replay_end':str(pd.Timestamp(cal.max()).date()),
      'trading_sessions':len(cal),
      'snapshot_count':int(w[w.date.between(cal.min(),cal.max())].date.nunique()),
      'snapshot_company_counts':{},
      'securities_requiring_prices':len(details),
      'securities_with_missing_active_prices':int((details.missing_sessions>0).sum()),
      'securities_with_invalid_active_prices':int((details.bad_price_sessions>0).sum()),
      'securities_with_nonTR_flags':int((details.untradeable_sessions>0).sum()),
      'unmapped_bloomberg_events':int(data['bloomberg_corporate_actions'].permno.isna().sum()),
    }
    subset=w[w.date.between(cal.min(),cal.max())]
    if 'permco' in subset:
        summary['snapshot_company_counts']={str(pd.Timestamp(d).date()):int(n) for d,n in subset.groupby('date').permco.nunique().items()}
    else:
        summary['snapshot_company_counts']={'warning':'No PERMCO: cannot verify 500 companies'}
    snapshots=sorted(subset.date.unique())
    srows=[]
    for i,d in enumerate(snapshots):
        right=snapshots[i+1] if i+1<len(snapshots) else pd.Timestamp(cal.max())+pd.Timedelta(days=1)
        frame=subset.loc[subset.date.eq(d)]
        ps=set(frame.permno.astype(int))
        exceptions=details[details.permno.isin(ps) & ~details.ready]
        srows.append({'decision_date':d,'next_decision':right if i+1<len(snapshots) else pd.NaT,
         'security_count':len(ps),'company_count':int(frame.permco.nunique()) if 'permco' in frame else np.nan,
         'weight_sum':frame.cap_weight_proxy.sum(),
         'securities_with_any_active_price_issue':len(exceptions),
         'original_weight_in_problem_securities':frame.loc[frame.permno.isin(exceptions.permno),'cap_weight_proxy'].sum()})
    report_dir=root/'outputs/four_year_preflight'
    report_dir.mkdir(parents=True,exist_ok=True)
    details.to_csv(report_dir/'active_window_price_coverage.csv',index=False)
    pd.DataFrame(srows).to_csv(report_dir/'quarterly_universe_audit.csv',index=False)
    annual=[]
    for source, table in [
       ('WRDS_DISTRIBUTION',data['wrds_corporate_actions']),
       ('WRDS_DELIST',data['wrds_delist_events']),
       ('BLOOMBERG',data['bloomberg_corporate_actions'])]:
        if len(table) and 'ex_date' in table:
            temp=table.copy()
            temp['event_year']=pd.to_datetime(temp.ex_date,errors='coerce').dt.year
            for year, part in temp.groupby('event_year',dropna=False):
                annual.append({'source':source,'event_year':str(int(year)) if pd.notna(year) else 'MISSING_DATE',
                   'event_records':int(len(part)),
                   'unmatched_permnos':int(part.permno.isna().sum()) if 'permno' in part else None})
    pd.DataFrame(annual,columns=['source','event_year','event_records','unmatched_permnos']).to_csv(
       report_dir/'annual_corporate_action_coverage.csv',index=False)
    (report_dir/'preflight_summary.json').write_text(json.dumps(summary,indent=2))
    return summary,details,pd.DataFrame(srows)
