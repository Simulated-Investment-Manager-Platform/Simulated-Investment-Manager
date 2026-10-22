"""Research-only reconciliation and temporal exclusion policy.

No rule turns economic source conflicts into human approvals. Resolutions derived
from WRDS are provisional for a restricted scenario; a signed release remains blocked.
"""
from collections import defaultdict
import numpy as np
import pandas as pd


def provisional_wrds_choice(candidate, split_factors):
    """Return (WRDS id, rule label) or (None, reason) for narrow audited cases.

    split_factors: dictionary keyed by WRDS source_event_id, with status.
    """
    wid = candidate.get('wrds_event_id')
    if pd.isna(wid) or not str(wid).startswith('CRSP:'):
        return None, 'no_wrds_event'
    status = str(candidate.get('match_status', ''))
    typ = str(candidate.get('event_type', ''))
    ex = pd.to_datetime(candidate.get('ex_date'), errors='coerce')
    if pd.isna(ex): return None, 'undated'
    if status == 'MATCHED_AGREE':
        return str(wid), 'CONCORDANT'
    if typ == 'CASH_DIVIDEND' and status == 'MATCHED_CONFLICT':
        a = pd.to_numeric(candidate.get('wrds_cash_per_share'), errors='coerce')
        b = pd.to_numeric(candidate.get('bloomberg_cash_per_share'), errors='coerce')
        p1, p2 = pd.to_datetime(candidate.get('wrds_pay_date'),errors='coerce'), pd.to_datetime(candidate.get('bloomberg_pay_date'),errors='coerce')
        if not (np.isfinite(a) and np.isfinite(b) and a >= 0 and b >= 0 and pd.notna(p1) and p1 == p2):
            return None, 'cash_economics_or_paydate_unverified'
        if abs(a-b) <= max(1e-8,1e-7*abs(a)):
            return str(wid), 'SAME_ECONOMICS_TICKER_MATCH_RESEARCH_ONLY'
        if a > 0 and b > 0:
            ratio = a / b
            # Verified future CRSP split must explain a raw pre-split dividend
            # vs Bloomberg's back-adjusted per-share amount.
            matches = []
            for x in candidate.get('_verified_later_splits', []):
                split_date, split_ratio = x
                if split_date > ex and np.isclose(ratio,split_ratio,atol=1e-5,rtol=1e-5):
                    matches.append(split_ratio)
            if matches:
                return str(wid), 'FUTURE_VERIFIED_SPLIT_BASIS_RESEARCH_ONLY'
        return None, 'unexplained_dividend_amount'
    if typ == 'STOCK_SPLIT' and status == 'MATCHED_CONFLICT':
        a = pd.to_numeric(candidate.get('wrds_split_ratio'),errors='coerce')
        b = pd.to_numeric(candidate.get('bloomberg_split_ratio'),errors='coerce')
        if np.isfinite(a) and np.isfinite(b) and a>=1 and np.isclose(a, b, rtol=0, atol=1e-8) and split_factors.get(str(wid)) == 'AGREE':
            return str(wid), 'SPLIT_RATIO_FACTOR_AGREE_PAYDATE_METADATA_ONLY'
        return None,'unverified_split'
    return None,'requires_economic_review'


def suggest_provisional_events(candidates, split_checks):
    """Classify candidate rows; NEVER fill APPROVE/EXCLUDE review decisions."""
    c=candidates.copy()
    fs = dict(zip(split_checks.source_event_id.astype(str),split_checks.status.astype(str)))
    split_dates=defaultdict(list)
    for r in c[c.event_type.eq('STOCK_SPLIT')].itertuples(index=False):
        if pd.isna(r.permno) or pd.isna(r.wrds_event_id): continue
        wid = str(r.wrds_event_id)
        ratio = pd.to_numeric(r.wrds_split_ratio,errors='coerce')
        if fs.get(wid)=='AGREE' and np.isfinite(ratio) and ratio >= 1:
            split_dates[int(r.permno)].append((pd.Timestamp(r.ex_date),float(ratio)))
    choices, rules=[] , []
    for _,r in c.iterrows():
        r=r.copy()
        r['_verified_later_splits']=split_dates.get(int(r.permno),[]) if pd.notna(r.permno) else []
        choice, rule = provisional_wrds_choice(r,fs)
        choices.append(choice or '')
        rules.append(rule)
    c['provisional_wrds_source']=choices
    c['provisional_rule']=rules
    c['human_approval_required']=True
    return c


def temporal_exclusion_snapshots(weights, calendar, event_blocks, prices):
    """Exclude per snapshot/holding interval rather than excluding PERMNO for all years.

    event_blocks: dict PERMNO -> [(ex_date or None, reason), ...].
    Price validation includes decision day and every day until next snapshot's close fill.
    This remains an *explicit restricted scenario*, not true index replication.
    """
    w=weights.copy()
    w['date']=pd.to_datetime(w.date).dt.normalize()
    px=prices.copy()
    px['date']=pd.to_datetime(px.date).dt.normalize()
    if px[['date','permno']].duplicated().any():
        raise ValueError('Duplicate price key')
    cal=pd.DatetimeIndex(pd.to_datetime(calendar)).normalize().sort_values().unique()
    snaps=sorted(w.date.unique())
    if not all(d in cal for d in snaps):raise ValueError('Snapshot without trading day')
    by_price={int(pid):f.set_index('date') for pid,f in px.groupby('permno')}
    exclusions=[]
    for j,d in enumerate(snaps):
        left=cal.get_loc(d)
        right=min(len(cal)-1,cal.get_loc(snaps[j+1])+1) if j<len(snaps)-1 else len(cal)-1
        required=cal[left:right+1]
        # An event after order execution is relevant while the position is held.
        effective_start=cal[min(left+1,len(cal)-1)]
        for row in w.loc[w.date.eq(d)].itertuples(index=False):
            pid=int(row.permno)
            problems=[]
            for dt,reason in event_blocks.get(pid,[]):
                if dt is None or pd.isna(dt):
                    problems.append('undated_'+reason)
                elif effective_start <= pd.Timestamp(dt).normalize() <= required[-1]:
                    problems.append(reason)
            quotes=by_price.get(pid)
            if quotes is None:
                problems.append('missing_price_series')
            else:
                part=quotes.reindex(required)
                val=pd.to_numeric(part.close_price,errors='coerce')
                if val.isna().any() or (~np.isfinite(val)).any() or val.le(0).any():
                    problems.append('missing_or_invalid_active_interval_close')
                # Important: non-TR flag does NOT invalidate a valuation close.
                # It blocks potentially required execution closes only.
                if 'price_flag' in part:
                    test_dates=[d,effective_start]
                    if j<len(snaps)-1:
                        test_dates.extend([snaps[j+1],required[-1]])
                    if part.loc[list(dict.fromkeys(test_dates)),'price_flag'].astype(str).str.upper().ne('TR').any():
                        problems.append('nontradeable_snapshot_or_execution_close')
            if problems:
                exclusions.append({'date':d,'permno':pid,'exclusion_reason':';'.join(sorted(set(problems)))})
    e=pd.DataFrame(exclusions,columns=['date','permno','exclusion_reason'])
    cut=w.merge(e,on=['date','permno'],how='inner',validate='one_to_one')
    kept=w.merge(e[['date','permno']] if not e.empty else pd.DataFrame(columns=['date','permno']),on=['date','permno'],how='left',indicator=True)
    kept=kept.loc[kept._merge=='left_only'].drop(columns=['_merge'])
    if set(kept.date)!=set(w.date):raise ValueError('Removed entire snapshot')
    totals=kept.groupby('date').cap_weight_proxy.transform('sum')
    kept['original_cap_weight_proxy']=kept.cap_weight_proxy
    kept['cap_weight_proxy']=kept.cap_weight_proxy/totals
    if 'cap_weight_proxy_pct' in kept:kept['cap_weight_proxy_pct']=100*kept.cap_weight_proxy
    return kept,cut
