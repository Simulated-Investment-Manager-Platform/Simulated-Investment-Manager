"""BMLL reproducible portfolio/order candidate, conservative with independent audit.

A generated research snapshot is NOT a certified full-index fund. Economic actions
and security exclusions remain explicit; no event is silently auto-approved.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research_pipeline import (load_helpers, load_handoff, read_table, scope_events,
                               review_candidates, approved_events)
from fund_simulator import simulate_fund, rebalance_schedule
from scripts.freeze_data import derive
from scripts.coverage_recovery import suggest_provisional_events, temporal_exclusion_snapshots

SIMPLE = {'CASH_DIVIDEND', 'SPECIAL_CASH_DIVIDEND', 'STOCK_SPLIT'}

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def _txt(x):
    return '' if pd.isna(x) else str(x)

def _eligible_source(e):
    if e.event_type not in SIMPLE or pd.isna(e.ex_date) or pd.isna(e.permno):
        return False, 'unsupported_type_or_missing_identity_date'
    if e.event_type in {'CASH_DIVIDEND', 'SPECIAL_CASH_DIVIDEND'}:
        amount = pd.to_numeric(pd.Series([e.cash_per_share]), errors='coerce').iloc[0]
        if not (np.isfinite(amount) and amount >= 0 and _txt(e.currency).upper() == 'USD'):
            return False, 'unverified_dividend_economics_or_currency'
        if pd.isna(e.pay_date) or pd.Timestamp(e.pay_date) < pd.Timestamp(e.ex_date):
            return False, 'invalid_payment_date'
    if e.event_type == 'STOCK_SPLIT':
        ratio = pd.to_numeric(pd.Series([e.split_ratio]), errors='coerce').iloc[0]
        if not (np.isfinite(ratio) and ratio >= 1 and float(ratio).is_integer()):
            return False, 'fractional_or_reverse_split_unsupported'
    return True, ''

def _read_review(path, key):
    if not path.exists():
        return None
    d = pd.read_csv(path, dtype={'review_key':'string', 'source_event_id':'string'})
    if key not in d or d[key].duplicated().any():
        raise ValueError(f'{path.name}: missing/duplicate {key}')
    return d

def generate(root, start, end, *, initial_cash=1e10, cash_buffer_bps=10.0,
             fee_bps=0.0, asof=None, mode='restricted', workbook=None,
             benchmark=None, mapping=None, qc=None, resolutions=None,
             provisional_wrds_research=True, execution_timing='reference_close',
             flows=None, flow_band_bps=25.0):
    """Build the research candidate.

    execution_timing='reference_close' trades the quarterly rebalance in the
    closing auction of the S&P reference Friday (decided the session before);
    'next_session' reproduces the original Monday-decision / Tuesday-fill run.
    ``start`` is the first decision session for the chosen timing. ``flows``
    is an optional DataFrame of dealing-date investor flows (see
    fund_simulator.simulate_fund).
    """
    root = Path(root).resolve()
    stage = root/'data/stage2_inputs'
    review = root/'data/stage2_review'
    output = root/'outputs/daily_fund'
    candidate = root/f'outputs/release_{start}_{end}_{mode}_candidate'
    for p in (review,output,candidate): p.mkdir(parents=True,exist_ok=True)
    asof = asof or datetime.now(timezone.utc).date().isoformat()
    if pd.Timestamp(asof) < pd.Timestamp(end):
        raise ValueError('As-of must not precede the last replay session')
    _, upstream = load_handoff(stage)
    coverage = json.loads((stage/'coverage.json').read_text())
    if (coverage['start'],coverage['end']) != (start,end):
        raise ValueError('WRDS coverage does not match configured start/end; extract again')
    for name, d in coverage.get('extra_sha256',{}).items():
        if digest(stage/name)!=d: raise ValueError(f'Cached market data changed: {name}')
    weights = upstream['weights']
    weights = weights.loc[weights.date.between(start,end)].copy()
    prices = read_table(stage/'daily_prices.csv')
    calendar = read_table(stage/'calendar.csv').date
    if calendar.duplicated().any() or not calendar.is_monotonic_increasing:
        raise ValueError('Trading calendar is unsorted or duplicated')
    if calendar.empty or calendar.min()!=pd.Timestamp(start) or calendar.max()>pd.Timestamp(end):
        raise ValueError('Trading calendar coverage/date mismatch')
    if weights.empty:
        raise ValueError('No quarterly snapshot inside the replay window')
    schedule = rebalance_schedule(weights, calendar, execution_timing)
    if schedule.decision_date.iloc[0] != pd.Timestamp(start):
        raise ValueError(f'Start must be the first {execution_timing} decision session: '
                         f'{schedule.decision_date.iloc[0].date()}')
    universe = {int(p) for p in coverage['universe']}
    action_parts = [upstream[n] for n in ('wrds_corporate_actions', 'wrds_delist_events') if not upstream[n].empty]
    wrds_all = pd.concat(action_parts, ignore_index=True) if action_parts else upstream['wrds_corporate_actions'].copy()
    bloomberg_all = upstream['bloomberg_corporate_actions']
    wrds, wqu = scope_events(wrds_all, universe, start, end)
    bbg, bqu = scope_events(bloomberg_all, universe, start, end)
    quarantine = pd.concat([wqu,bqu],ignore_index=True)
    sources = pd.concat([wrds,bbg],ignore_index=True)
    if sources.source_event_id.duplicated().any():
        raise ValueError('Duplicate source event IDs in WRDS/Bloomberg')
    helpers = load_helpers(root/'BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb')
    unified = helpers['reconcile_corporate_actions'](wrds, bbg)
    candidates = review_candidates(unified, sources)
    # Review state is durable only if the hash-derived keys still match.
    manual = _read_review(review/'event_decisions.csv','review_key')
    if manual is not None and set(manual.review_key.astype(str))!=set(candidates.review_key.astype(str)):
        path = review/'event_decisions.stale.csv'
        (review/'event_decisions.csv').rename(path)
        manual = None
    if manual is None:
        candidates.to_csv(review/'event_decisions.csv',index=False)
        manual = pd.read_csv(review/'event_decisions.csv')
    qdec = _read_review(review/'scope_decisions.csv','source_event_id')
    if qdec is not None and set(qdec.source_event_id.astype(str))!=set(quarantine.source_event_id.astype(str)):
        (review/'scope_decisions.csv').rename(review/'scope_decisions.stale.csv')
        qdec = None
    if qdec is None:
        qdec = quarantine.copy()
        qdec['decision']='PENDING';qdec['rationale']=''
        qdec.to_csv(review/'scope_decisions.csv',index=False)
    candidates.to_csv(review/'event_candidates.csv',index=False)
    quarantine.to_csv(review/'scope_quarantine.csv',index=False)
    split_checks = helpers['check_split_factors'](wrds,upstream['wrds_adj_factors'])
    split_checks.to_csv(review/'split_factor_checks.csv',index=False)
    factor_status = (dict(zip(split_checks.source_event_id, split_checks.status))
                     if len(split_checks) else {})
    suggestions = suggest_provisional_events(candidates, split_checks)
    suggestions.to_csv(review/'provisional_event_suggestions.csv',index=False)
    suggestion_idx = suggestions.set_index('review_key')
    src_idx = sources.set_index('source_event_id',drop=False)
    dec_idx = manual.set_index('review_key')
    blocked = {}  # audit reasons per security, not blanket multi-year exclusion
    timed_blocks = {}  # securities only excluded during affected holding intervals
    chosen_events, decisions_audit = [], []
    def block(p, reason, when=None):
        if pd.notna(p):
            blocked.setdefault(int(p),set()).add(reason)
            timed_blocks.setdefault(int(p), []).append((pd.to_datetime(when,errors='coerce'),reason))
    for r in candidates.itertuples(index=False):
        p = int(r.permno) if pd.notna(r.permno) else None
        d = dec_idx.loc[r.review_key]
        decision = _txt(d.decision).strip().upper()
        human_rationale = _txt(d.rationale).strip()
        human_chosen = _txt(d.chosen_source_event_id).strip()
        choice, disposition, rationale = None, 'PENDING', ''
        suggestion = suggestion_idx.loc[r.review_key]
        if decision == 'EXCLUDE' and human_rationale:
            block(p,'human_excluded_economic_action',r.ex_date)
            disposition, rationale = 'EXCLUDE', human_rationale
        elif decision == 'APPROVE' and human_rationale:
            allowed = {_txt(r.wrds_event_id),_txt(r.bloomberg_event_id)} - {''}
            if human_chosen not in allowed:
                block(p,'invalid_human_source_choice',r.ex_date)
                rationale = 'Human decision source ID is not one of the reconciled records'
            else:
                choice = human_chosen
                disposition, rationale = 'HUMAN_APPROVED', human_rationale
        elif r.match_status == 'MATCHED_AGREE' and pd.notna(r.wrds_event_id):
            choice = r.wrds_event_id
            disposition = 'AUTO_CONCORDANT_RESEARCH_ONLY'
            rationale = 'Sources match, provisional only; no human approval is implied'
        elif provisional_wrds_research and suggestion.provisional_rule in {
                'FUTURE_VERIFIED_SPLIT_BASIS_RESEARCH_ONLY',
                'SAME_ECONOMICS_TICKER_MATCH_RESEARCH_ONLY',
                'SPLIT_RATIO_FACTOR_AGREE_PAYDATE_METADATA_ONLY'} and suggestion.provisional_wrds_source:
            choice = suggestion.provisional_wrds_source
            disposition = 'AUTO_WRDS_RESEARCH_ONLY'
            rationale = 'Research only, unresolved human decision: ' + suggestion.provisional_rule
        else:
            block(p, 'unresolved_'+str(r.match_status).lower(),r.ex_date)
            rationale = f'Unreviewed or conflicting event ({r.match_status})'
        e = None
        if choice:
            if choice not in src_idx.index:
                block(p, 'chosen_event_missing_in_sources',r.ex_date)
                disposition='PENDING';rationale='Chosen event not found in source table'
            else:
                e = src_idx.loc[choice]
                okay, why = _eligible_source(e)
                if okay and e.event_type=='STOCK_SPLIT':
                    fac = factor_status.get(choice)
                    if fac is not None and fac!='AGREE':
                        okay=False;why='unreconciled_split_adjustment_factor_'+str(fac)
                if not okay:
                    block(p, why,r.ex_date);disposition='PENDING';rationale=why
                    e=None
        if e is not None and disposition in {'HUMAN_APPROVED','AUTO_CONCORDANT_RESEARCH_ONLY','AUTO_WRDS_RESEARCH_ONLY'}:
            event_record = e.copy()
            # A special cash dividend uses the same dollars-per-held-share ledger as
            # an ordinary cash dividend, but only after a human verifies its terms.
            # Preserve its original classification for subsequent audit.
            if event_record.event_type == 'SPECIAL_CASH_DIVIDEND':
                if disposition != 'HUMAN_APPROVED':
                    block(p, 'special_cash_dividend_requires_human_review',r.ex_date)
                    disposition = 'PENDING'
                    rationale = 'Special cash dividend needs explicit human economic review'
                else:
                    event_record['original_event_type'] = 'SPECIAL_CASH_DIVIDEND'
                    event_record['event_type'] = 'CASH_DIVIDEND'
            if disposition in {'HUMAN_APPROVED','AUTO_CONCORDANT_RESEARCH_ONLY','AUTO_WRDS_RESEARCH_ONLY'}:
                chosen_events.append(event_record)
        decisions_audit.append({'review_key':r.review_key, 'permno':p, 'event_type':r.event_type,
          'ex_date':r.ex_date,'match_status':r.match_status,'decision':disposition,
          'chosen_source_event_id':choice or '', 'rationale':rationale})
    audit = pd.DataFrame(decisions_audit)
    audit.to_csv(review/'portfolio_scenario_event_audit.csv',index=False)
    # The immutable human decision file is never overwritten by automatic research selection.
    human_complete = (manual.decision.fillna('PENDING').isin(['APPROVE','EXCLUDE']).all()
        and manual.rationale.fillna('').astype(str).str.strip().ne('').all()
        and qdec.decision.fillna('PENDING').eq('EXCLUDE').all()
        and qdec.rationale.fillna('').astype(str).str.strip().ne('').all())
    # Check price coverage ONLY when a security is in the simulated portfolio
    # universe. Across four years many legitimate constituents join/leave; requiring
    # historical prices outside their holdings interval would be lookback bias.
    from scripts.multiyear_preflight import required_quote_days, price_coverage_report
    px = prices.copy()
    px['date']=pd.to_datetime(px['date']).dt.normalize()
    px['permno']=pd.to_numeric(px['permno'],errors='raise').astype('int64')
    if px[['date','permno']].duplicated().any(): raise ValueError('Duplicated daily price key')
    required = required_quote_days(weights, calendar, execution_timing)
    coverage_details = price_coverage_report(required, px)
    coverage_details.to_csv(review/'required_price_coverage.csv', index=False)
    for r in coverage_details.itertuples(index=False):
        # Diagnostics retained. Actual exclusions are per holding interval below,
        # not blanket exclusions because of a single bad historical date.
        pass
    # A replay must never hold a security with an unaccounted event. Reverse splits,
    # issuer actions with fractional economic terms, and overlapping split/dividends
    # are excluded in the explicit restricted scenario.
    chosen = pd.DataFrame(chosen_events,columns=sources.columns)
    if not chosen.empty:
        chosen['ex_date']=pd.to_datetime(chosen.ex_date)
        groups=chosen.groupby(['permno','ex_date']).event_type.agg(lambda s:set(s))
        same_day_mixed=[]
        for (p,event_day),types in groups.items():
            if len(types)>1:
                block(p,'ambiguous_same_day_split_dividend_sequence',event_day)
                same_day_mixed.append((p,event_day))
        for p,event_day in same_day_mixed:
            chosen=chosen.loc[~(chosen.permno.eq(p) & chosen.ex_date.eq(event_day))]
        if chosen.source_event_id.duplicated().any():
            raise ValueError('Selected events duplicated')
    original = weights.groupby('date').cap_weight_proxy.sum()
    if not np.allclose(original,1.0,atol=1e-8,rtol=0):
        raise ValueError('Original snapshot weights do not sum to 1')
    # The old version dropped a security for EVERY quarter if ONE event in four
    # years was unresolved. This version excludes only snapshots whose holding
    # interval overlaps the event and checks required prices in that same interval.
    keep, excluded = temporal_exclusion_snapshots(weights, calendar, timed_blocks, prices,
                                                  execution_timing)
    excluded.to_csv(output/'excluded_security_snapshots.csv',index=False)
    if not chosen.empty:
        chosen['requires_review'] = False  # simulator input only; not human approval
        chosen['review_reason'] = ''
    # Fills use the CRSP close of the execution session (reference-Friday closing
    # auction under reference_close). No L2/L3 order book is reconstructed here.
    results=simulate_fund(keep, prices, calendar, chosen, start, end,
       initial_cash=initial_cash,cash_buffer_bps=cash_buffer_bps,fee_bps=fee_bps,
       flows=flows, execution_timing=execution_timing, flow_band_bps=flow_band_bps)
    results['schedule'].to_csv(output/'rebalance_schedule.csv',index=False)
    for name in ('daily_nav','daily_holdings','orders','fills','action_journal'):
        df=results[name]
        if df.empty and not len(df.columns):
            # enforce schemas for zero-event cases
            cols={'action_journal':['date','source_event_id','permno','share_change','accrued','paid'],
                  'orders':['decision_date','permno','signed_quantity','decision_price'],
                  'fills':['decision_date','execution_date','permno','signed_requested','signed_filled','unfilled_quantity','execution_price','decision_price','fee']} .get(name)
            if cols: df=pd.DataFrame(columns=cols)
        df.to_csv(output/f'{name}.csv',index=False)
    # Preserve an explicit scenario source audit; do not disguise automatic decisions as human approvals.
    audit.rename(columns={'rationale':'scenario_rationale'}).to_csv(output/'scenario_action_selection.csv',index=False)
    # Existing validator consumes approval_audit and scope_decisions, and must block incomplete reviews.
    audit[['review_key','permno','event_type','decision','chosen_source_event_id']].assign(
      rationale=audit.rationale).to_csv(review/'approval_audit.csv',index=False)
    review_summary={'human_review_complete':bool(human_complete), 'events_matched_concordant':int((audit.decision=='AUTO_CONCORDANT_RESEARCH_ONLY').sum()),
      'human_approved_events':int((audit.decision=='HUMAN_APPROVED').sum()),
      'provisional_wrds_disagreements':int((audit.decision=='AUTO_WRDS_RESEARCH_ONLY').sum()),
      'pending_or_blocked_events':int(audit.decision.eq('PENDING').sum()),
      'scope_quarantine':int(len(quarantine)), 'scope_pending':int((qdec.decision!='EXCLUDE').sum()),
      'excluded_permnos':int(excluded.permno.nunique()) if len(excluded) else 0,
      'event_blocked_permnos':len(blocked),
      'excluded_snapshot_rows':len(excluded),
      'provisional_wrds_research_enabled':bool(provisional_wrds_research),
      'original_snapshot_permnos':int(weights.permno.nunique()),'kept_snapshot_permnos':int(keep.permno.nunique()),
      'simulated_actions':len(chosen),'provisional':True}
    (review/'scenario_review_summary.json').write_text(json.dumps(review_summary,indent=2))
    prov={'start':start,'end':end,'aum':initial_cash,'execution_timing':execution_timing,
      'synthetic_flows':flows is not None,'flow_band_bps':flow_band_bps,
      'provisional_wrds_research_enabled':bool(provisional_wrds_research),'cash_buffer_bps':cash_buffer_bps,
      'fee_bps':fee_bps, 'status':'RESEARCH_CANDIDATE_NOT_HUMAN_APPROVED',
      'files':{str(p.relative_to(root)):digest(p) for p in [stage/'coverage.json',stage/'manifest.json',
        review/'portfolio_scenario_event_audit.csv',root/'research_pipeline.py']}}
    (output/'run_provenance.json').write_text(json.dumps(prov,indent=2))
    # Existing finance/accounting/release validator writes the five export CSVs even
    # if the review gate fails, and reports any arithmetic inconsistencies.
    report=derive(output,stage,review,candidate,mode,asof,initial_cash,False,None,
      bloomberg=str(workbook) if workbook and Path(workbook).exists() else None,
      qc=str(qc) if qc and Path(qc).exists() else None,
      resolutions=str(resolutions) if resolutions and Path(resolutions).exists() else None,
      benchmark=str(benchmark) if benchmark and Path(benchmark).exists() else None,
      bmll_mapping=str(mapping) if mapping and Path(mapping).exists() else None)
    # Archive research-only snapshots without declaring a signed or certified freeze.
    report['scenario_review_summary']=review_summary
    report['economic_review_complete']=bool(human_complete)
    report['provisional_concordant_events']=review_summary['events_matched_concordant']
    # A signed version requires every action to be human reviewed. Label accurately
    # even if arithmetic checks pass; do not silently mark as fully frozen.
    report['research_candidate_only']=True
    # An accounting-valid provisional run is NOT a human-approved data freeze.
    report['arithmetic_checks_passed']=not any(
        item.get('code') in {'RECONCILIATION','NONFINITE','INVALID_NAV','FILLS',
        'HOLDING_DATES','POSITIONS','DUPLICATE','TIMING','DATES',
        'ORPHAN_FILLS','ORDER_RECONCILIATION','UNSETTLED_JOURNAL'}
        for item in report.get('issues',[]))
    report['checks_passed']=False
    report['status']='RESEARCH_CANDIDATE_NOT_CERTIFIED'
    report.setdefault('issues',[]).append({
        'severity':'ERROR', 'code':'CERTIFICATION_REQUIRES_HUMAN_REVIEW',
        'detail':'Provisional source selections and/or restricted universe are not a certified full-index freeze'})
    (candidate/'validation_report.json').write_text(json.dumps(report,indent=2,default=str))
    manifest_path=candidate/'release_manifest.json'
    manifest=json.loads(manifest_path.read_text())
    manifest['status']='RESEARCH_CANDIDATE_NOT_CERTIFIED'
    manifest['validation_report_sha256']=digest(candidate/'validation_report.json')
    manifest['scenario_review_summary']=review_summary
    manifest_path.write_text(json.dumps(manifest,indent=2,default=str))
    # Additional independent math checks surface fatal errors; don't hide behind review blockers.
    fatal_codes={'RECONCILIATION','NONFINITE','INVALID_NAV','FILLS','HOLDING_DATES','POSITIONS',
                 'DUPLICATE','TIMING','DATES','ORPHAN_FILLS','ORDER_RECONCILIATION','UNSETTLED_JOURNAL'}
    failures=[r for r in report.get('issues',[]) if r.get('code') in fatal_codes]
    if failures:
        raise RuntimeError(f'Portfolio/order arithmetic validation FAILED: {failures[:5]}')
    return {'candidate_dir':str(candidate),'review_dir':str(review),'report':report,
            'review_summary':review_summary,'results':results}
