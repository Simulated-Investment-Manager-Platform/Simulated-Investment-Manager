"""Evidence-based review of this project's March–June 2026 cached experiment.

Updates only pending decisions, preserves source data and existing human decisions,
and saves byte-for-byte backups plus a row-level audit. No live queries are made.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_pipeline import (load_handoff, load_helpers, scope_events,
                               review_candidates, approved_events)


# Primary issuer/SEC evidence checked during this review. Values are on the
# actual event-date share basis, not retrospectively split-adjusted amounts.
EVIDENCE = {
    "CRSP:DIST:84769:2026-03-23:1": (
        "WRDS", .25, "2026-04-14",
        "https://s21.q4cdn.com/564806605/files/doc_news/2026/Feb/05/2026_02_05-PR-1Q-2026-Dividend.pdf",
        "Amphenol declared USD 0.25 for record date 2026-03-23, payable 2026-04-14. "
        "Bloomberg's 0.125 is not the event-date share basis; choose WRDS."),
    "CRSP:DIST:84769:2026-06-23:1": (
        "WRDS", .25, "2026-07-15",
        "https://investor.amphenol.com/news-and-events/news-details/2026/Amphenol-Announces-Second-Quarter-2026-Dividend/default.aspx",
        "Amphenol confirms USD 0.25, record date 2026-06-23, payable 2026-07-15; choose WRDS."),
    "CRSP:DIST:46886:2026-05-18:1": (
        "WRDS", 2.30, "2026-06-02",
        "https://ir.kla.com/news-events/press-releases/detail/515/kla-corporation-announces-ten-to-one-stock-split-and",
        "KLA confirms USD 2.30 payable 2026-06-02 before its 10:1 split on 2026-06-12. "
        "Bloomberg's 0.23 is the later share basis; choose WRDS."),
    "CRSP:DIST:16851:2026-05-15:1": (
        "WRDS", .20, "2026-05-29",
        "https://www.dupont.com/news/dupont-announces-regularly-quarterly-dividend-on-common-stock.html",
        "DuPont confirms USD 0.20 payable 2026-05-29, record date 2026-05-15; choose WRDS. "
        "The Bloomberg row uses the later reverse-split CUSIP and 0.60 amount."),
    "CRSP:DIST:10145:2026-05-15:1": (
        "WRDS", 1.19, "2026-06-05",
        "https://investor.honeywell.com/news-releases/news-release-details/honeywell-announces-quarterly-dividend-21",
        "Honeywell confirms USD 1.19 payable 2026-06-05, record date 2026-05-15; choose WRDS. "
        "Bloomberg uses the later reverse-split CUSIP and 2.38 amount."),
    "CRSP:DIST:81774:2026-04-15:1": (
        "BLOOMBERG", .15, "2026-05-01",
        "https://investors.fcx.com/investors/stock-and-dividend-information/dividend-history/default.aspx",
        "Freeport's issuer history confirms USD 0.075 base plus USD 0.075 variable, same record/pay dates. "
        "Select Bloomberg's total 0.15; do not also book the WRDS base 0.075 separately."),
    "CRSP:DIST:25232:2026-05-04:1": (
        "WRDS", 1.07, "2026-05-15",
        "https://ir.oneok.com/stock-information/dividend-history",
        "ONEOK issuer history confirms ex-date 2026-05-04, USD 1.07 payable 2026-05-15. "
        "Use WRDS identity and terms; the Bloomberg CUSIP does not match effective CRSP history."),
    "CRSP:DIST:11850:2026-05-15:1": (
        "WRDS", 1.03, "2026-06-10",
        "https://www.businesswire.com/news/home/20260430770231/en/ExxonMobil-Announces-First-Quarter-2026-Results",
        "ExxonMobil's issuer release confirms USD 1.03 payable 2026-06-10, record date 2026-05-15. "
        "Use WRDS identity and terms; the Bloomberg CUSIP does not match effective CRSP history."),
}

# Match the original announcement placeholders to their subsequent disposition.
# These dates describe related transactions/status, never invented spin-off ex-dates.
SCOPE_EVIDENCE = {
    "BAX": dict(announcement="2023-01-06", outcome="SUPERSEDED_BY_BUSINESS_SALE",
        related_date="2025-01-31", evidence_url="https://www.sec.gov/Archives/edgar/data/10456/000162828025003388/bax-20250131.htm",
        explanation="The proposed Kidney Care spin-off became the Vantive business sale to Carlyle, completed 2025-01-31. No shareholder spin-off entitlement in the replay window."),
    "FIS": dict(announcement="2023-02-13", outcome="SUPERSEDED_BY_BUSINESS_SALES",
        related_date="2026-01-12", evidence_url="https://www.investor.fisglobal.com/news-releases/news-release-details/fis-completes-strategic-acquisition-global-payments-issuer/",
        explanation="The Worldpay spin-off proposal became a majority-stake sale in 2024 and sale of the remaining stake completed 2026-01-12. These are corporate asset disposals, not a distribution of Worldpay shares to FIS holders in this window."),
    "JNJ": dict(announcement="2025-10-14", outcome="PLANNED_FUTURE_SEPARATION",
        related_date="", evidence_url="https://www.jnj.com/media-center/press-releases/johnson-johnson-announces-intent-to-separate-its-orthopaedics-business",
        explanation="The DePuy Synthes separation announcement targeted completion 18–24 months after 2025-10-14. The company continued to describe a planned separation after June 2026; this placeholder is not a March–June 2026 effective event."),
    "KDP": dict(announcement="2025-08-24", outcome="PLANNED_FUTURE_SEPARATION",
        related_date="", evidence_url="https://www.sec.gov/Archives/edgar/data/1418135/000095014226001848/eh260797101_ex9901.htm",
        explanation="Matched to the KDP/JDE Peet's proposed separation publicly announced 2025-08-25 (one day after the workbook announcement date). On 2026-06-23 KDP explicitly targeted the subsequent separation for early 2027. Acquisition closing is not the shareholder separation; no spin-off ex-date is assigned."),
    "KHC": dict(announcement="2025-09-02", outcome="SEPARATION_PAUSED",
        related_date="2026-02-11", evidence_url="https://www.sec.gov/Archives/edgar/data/1637459/000163745926000009/R12.htm",
        explanation="Kraft Heinz's SEC filing links the 2025-09-02 separation plan to the board's 2026-02-11 pause. The proposal is not an effective shareholder distribution in the replay window."),
    "TFX": dict(announcement="2025-02-27", outcome="SUPERSEDED_BY_BUSINESS_SALES",
        related_date="2025-12-09", evidence_url="https://investors.teleflex.com/news/news-details/2025/Teleflex-Announces-Sale-of-Acute-Care-Interventional-Urology-and-OEM-Businesses-for-2-03-Billion/default.aspx",
        explanation="The proposed NewCo spin-off was superseded by agreements announced 2025-12-09 to sell Acute Care/Interventional Urology and OEM businesses. No shareholder NewCo distribution is established by this placeholder."),
}


def main():
    review = ROOT / "data/stage2_review"
    cache = ROOT / "data/stage2_inputs"
    coverage = json.loads((cache / "coverage.json").read_text())
    if (coverage["start"], coverage["end"]) != ("2026-03-23", "2026-06-30"):
        raise ValueError("Issuer-specific review rules apply only to the checked March–June 2026 window")
    _, tables = load_handoff(cache)
    for name, digest in coverage["extra_sha256"].items():
        if hashlib.sha256((cache / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Cached input changed: {name}")
    helpers = load_helpers(ROOT / "BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb")
    wrds_parts = [tables[n] for n in ("wrds_corporate_actions", "wrds_delist_events") if not tables[n].empty]
    wrds_all = pd.concat(wrds_parts, ignore_index=True)
    wrds, uq = scope_events(wrds_all, coverage["universe"], coverage["start"], coverage["end"])
    bbg, bq = scope_events(tables["bloomberg_corporate_actions"], coverage["universe"], coverage["start"], coverage["end"])
    quarantine = pd.concat([uq, bq], ignore_index=True)
    sources = pd.concat([wrds, bbg], ignore_index=True)
    indexed = sources.set_index("source_event_id", drop=False)
    fresh = review_candidates(helpers["reconcile_corporate_actions"](wrds, bbg), sources)
    decisions = pd.read_csv(review / "event_decisions.csv")
    scope = pd.read_csv(review / "scope_decisions.csv", dtype={"raw_cusip": "string"})
    decisions["rationale"] = decisions["rationale"].astype("object")
    scope["rationale"] = scope["rationale"].astype("object")
    for field in ("verified_outcome", "related_transaction_date", "evidence_url"):
        if field not in scope:
            scope[field] = ""
        scope[field] = scope[field].astype("object")
    if decisions.review_key.duplicated().any() or set(fresh.review_key) != set(decisions.review_key):
        raise ValueError("Stale event decisions; regenerate candidates before review")
    if scope.source_event_id.duplicated().any() or set(scope.source_event_id) != set(quarantine.source_event_id):
        raise ValueError("Stale scope decisions")
    # Review visible economics from regenerated candidates, never edited display columns.
    candidates = fresh.set_index("review_key")
    factor = helpers["check_split_factors"](wrds, tables["wrds_adj_factors"])
    factor_good = set(factor.loc[factor.status.eq("AGREE"), "source_event_id"])
    checks = {}
    audit, issues = [], []
    reviewed_at = datetime.now(timezone.utc).isoformat()
    for i, d in decisions.iterrows():
        r = candidates.loc[d.review_key]
        chosen, reason, url = None, None, ""
        if r.match_status == "MATCHED_AGREE" and r.event_type == "CASH_DIVIDEND":
            a, b = indexed.loc[r.wrds_event_id], indexed.loc[r.bloomberg_event_id]
            valid = (
                str(a.currency).upper() == str(b.currency).upper() == "USD"
                and np.isfinite(float(a.cash_per_share)) and float(a.cash_per_share) >= 0
                and np.isclose(float(a.cash_per_share), float(b.cash_per_share), atol=1e-10, rtol=0)
                and pd.notna(a.pay_date) and a.pay_date == b.pay_date and a.pay_date >= a.ex_date
                and not bool(a.requires_review) and not bool(b.requires_review)
            )
            if valid:
                chosen = r.wrds_event_id
                reason = ("AUTO_REVIEW: unique date-effective historical CUSIP match; WRDS and Bloomberg "
                          "agree on ordinary-dividend ex-date, amount (1e-10 tolerance), payment date and USD "
                          "currency; source records have no unresolved normalization flags. Select WRDS.")
                if "currency_check=CHECK" in str(b.source_detail):
                    reason += " Workbook currency flag resolved for this row by both source currency fields explicitly stating USD."
        elif r.event_type == "STOCK_SPLIT" and r.match_status in {"MATCHED_AGREE", "MATCHED_CONFLICT"}:
            a, b = indexed.loc[r.wrds_event_id], indexed.loc[r.bloomberg_event_id]
            only_paydate = pd.isna(r.review_reason) or not str(r.review_reason).strip() or r.review_reason == "pay_date_conflict"
            if (only_paydate and r.wrds_event_id in factor_good
                and np.isfinite(float(a.split_ratio)) and float(a.split_ratio) > 0
                and np.isclose(float(a.split_ratio), float(b.split_ratio), atol=1e-6, rtol=0)
                and a.permno == b.permno and a.ex_date == b.ex_date):
                chosen = r.wrds_event_id
                reason = ("AUTO_REVIEW: historical CUSIP/ex-date identity and share multiplier agree across sources; "
                          "adjacent CRSP cumulative factors independently confirm the ratio. Select WRDS. "
                          "A missing Bloomberg split payment date is immaterial to ex-date share accounting. "
                          "Pure share splits require no cash currency. Approval does not supply fractional cash-in-lieu "
                          "or resolve any separate same-day spin-off.")
        if r.wrds_event_id in EVIDENCE:
            side, amount, payday, url, explanation = EVIDENCE[r.wrds_event_id]
            selected_id = r.wrds_event_id if side == "WRDS" else r.bloomberg_event_id
            e = indexed.loc[selected_id]
            if (e.event_type != "CASH_DIVIDEND" or str(e.currency).upper() != "USD"
                or not np.isclose(float(e.cash_per_share), amount, atol=1e-10, rtol=0)
                or e.pay_date != pd.Timestamp(payday)):
                raise ValueError(f"Cached terms changed for issuer-verified event {selected_id}")
            chosen = selected_id
            reason = "AUTO_REVIEW_PRIMARY_SOURCE: " + explanation + " Evidence: " + url
        if chosen is not None and d.decision == "PENDING":
            decisions.loc[i, "decision"] = "APPROVE"
            decisions.loc[i, "chosen_source_event_id"] = chosen
            decisions.loc[i, "rationale"] = reason
            audit.append(dict(file="event_decisions.csv", key=d.review_key, old_decision=d.decision,
                              new_decision="APPROVE", chosen_source_event_id=chosen, rationale=reason,
                              reviewed_at_utc=reviewed_at, evidence_url=url))
        if chosen is None:
            a = indexed.loc[r.wrds_event_id] if pd.notna(r.wrds_event_id) else indexed.loc[r.bloomberg_event_id]
            names = tables["wrds_id_history"].loc[tables["wrds_id_history"].permno.eq(r.permno), "ticker"].dropna().unique()
            ticker = "/".join(map(str, names))
            need = ("Both sources agree on special cash terms; cash-event support for this event type is not implemented. "
                    "Do not exclude a held-security entitlement to bypass the ledger."
                    if r.event_type == "SPECIAL_CASH_DIVIDEND" else
                    "Complex action affects a constituent held in this experiment. Verify linked source events, "
                    "successor PERMNO/CUSIP, cash/share ratios, dates, sequencing and fractional-share settlement; "
                    "implement accounting before approval. Do not book WRDS/Bloomberg representations twice.")
            issues.append(dict(category="IMPLEMENTATION_AND_TERMS", review_key=d.review_key,
                ticker=ticker, permno=r.permno, event_type=r.event_type, ex_date=r.ex_date,
                wrds_event_id=r.wrds_event_id, bloomberg_event_id=r.bloomberg_event_id,
                wrds_cash_per_share=r.wrds_cash_per_share, bloomberg_cash_per_share=r.bloomberg_cash_per_share,
                successor_permno=a.new_permno, next_action=need))

    history = tables["wrds_id_history"]
    weights = tables["weights"]
    weights = weights[weights.date.between(coverage["start"], coverage["end"])]
    for i, d in scope.iterrows():
        r = quarantine.loc[quarantine.source_event_id.eq(d.source_event_id)].iloc[0]
        cusip = str(r.raw_cusip)[:8]
        history_match = (history.ticker.eq(r.raw_ticker) | history.cusip.eq(cusip)
                         | history.cusip9.eq(str(r.raw_cusip)))
        weight_match = weights.ticker.eq(r.raw_ticker) | weights.cusip.eq(cusip)
        supported_exclusion = (pd.notna(r.ex_date) and "in_index=FALSE" in str(r.source_detail)
                               and not history_match.any() and not weight_match.any())
        official = SCOPE_EVIDENCE.get(r.raw_ticker) if pd.isna(r.ex_date) else None
        if official is not None:
            if r.event_type != "SPINOFF" or r.announce_date != pd.Timestamp(official["announcement"]):
                raise ValueError(f"Announcement placeholder changed: {r.source_event_id}")
            scope.loc[i, "verified_outcome"] = official["outcome"]
            scope.loc[i, "related_transaction_date"] = official["related_date"]
            scope.loc[i, "evidence_url"] = official["evidence_url"]
            if d.decision == "PENDING":
                rationale = ("AUTO_REVIEW_PRIMARY_SOURCE_SCOPE: " + official["explanation"]
                    + " Exclude this announcement-only placeholder from the declared 2026-03-23–2026-06-30 replay; "
                    "retain original missing ex-date and raw record. Evidence: " + official["evidence_url"])
                scope.loc[i, "decision"] = "EXCLUDE"
                scope.loc[i, "rationale"] = rationale
                audit.append(dict(file="scope_decisions.csv", key=d.source_event_id, old_decision=d.decision,
                    new_decision="EXCLUDE", rationale=rationale, reviewed_at_utc=reviewed_at,
                    evidence_url=official["evidence_url"]))
        if supported_exclusion and d.decision == "PENDING":
            rationale = ("AUTO_REVIEW_SCOPE: for the declared 2026-03-23–2026-06-30 index-universe experiment, "
                "Bloomberg explicitly marks in_index=FALSE and neither ticker nor CUSIP appears in "
                "the fetched historical-universe identifier history or selected target snapshots. "
                "Exclude from this reconciliation scope only; retain the raw event. Reassess if successor "
                "holdings or the research universe/window are expanded.")
            scope.loc[i, "decision"] = "EXCLUDE"
            scope.loc[i, "rationale"] = rationale
            audit.append(dict(file="scope_decisions.csv", key=d.source_event_id, old_decision=d.decision,
                              new_decision="EXCLUDE", rationale=rationale, reviewed_at_utc=reviewed_at))
        if not supported_exclusion and official is None:
            issues.append(dict(category="USER_EVIDENCE_NEEDED", source_event_id=d.source_event_id,
                ticker=r.raw_ticker, event_type=r.event_type, announce_date=r.announce_date,
                next_action="Provide actual ex-date and final completed/cancelled/announced status. "
                "The announcement date and near-zero placeholder amount do not establish an effective event. "
                "Keep pending until corrected or demonstrably outside the experiment."))

    # Validate approved rows individually while incomplete candidates remain pending.
    approved_mask = decisions.decision.eq("APPROVE")
    subset = fresh[fresh.review_key.isin(decisions.loc[approved_mask, "review_key"])]
    checked_events, _ = approved_events(subset, decisions.loc[approved_mask], sources)
    evidence_path = review / "complex_action_evidence.csv"
    if evidence_path.exists():
        terms = pd.read_csv(evidence_path).set_index("ticker")
        for field in ("terms_review_status", "verified_terms", "evidence_url", "remaining_blocker"):
            if field not in decisions:
                decisions[field] = ""
            decisions[field] = decisions[field].astype("object")
        for issue in issues:
            ticker = issue.get("ticker")
            if issue.get("category") != "IMPLEMENTATION_AND_TERMS" or ticker not in terms.index:
                continue
            evidence = terms.loc[ticker]
            if str(evidence.evidence_url).startswith("http"):
                issue["category"] = "IMPLEMENTATION_AND_SETTLEMENT"
                issue["evidence_url"] = evidence.evidence_url
                issue["verified_terms"] = evidence.known_terms
                issue["next_action"] = evidence.remaining_work
                key = issue["review_key"]
                mask = decisions.review_key.eq(key) & decisions.decision.eq("PENDING")
                rationale = ("AUTO_REVIEW_TERMS_VERIFIED: " + evidence.known_terms
                    + " Still PENDING for ledger execution: " + evidence.remaining_work
                    + " Evidence: " + evidence.evidence_url)
                if mask.any():
                    old_rationale = decisions.loc[mask, "rationale"].iloc[0]
                    decisions.loc[mask, "terms_review_status"] = "CORE_TERMS_VERIFIED_ACCOUNTING_PENDING"
                    decisions.loc[mask, "verified_terms"] = evidence.known_terms
                    decisions.loc[mask, "evidence_url"] = evidence.evidence_url
                    decisions.loc[mask, "remaining_blocker"] = evidence.remaining_work
                    if pd.isna(old_rationale) or not str(old_rationale).strip() or str(old_rationale).startswith("AUTO_REVIEW_TERMS_VERIFIED:"):
                        decisions.loc[mask, "rationale"] = rationale
                        if pd.isna(old_rationale) or str(old_rationale) != rationale:
                            audit.append(dict(file="event_decisions.csv", key=key,
                                old_decision="PENDING", new_decision="PENDING", rationale=rationale,
                                reviewed_at_utc=reviewed_at, evidence_url=evidence.evidence_url))
    checks.update(event_decisions=decisions.decision.value_counts().to_dict(),
                  scope_decisions=scope.decision.value_counts().to_dict(),
                  split_factor_checks=factor.status.value_counts().to_dict(),
                  validated_approved_events=len(checked_events),
                  unresolved_event_rows=int(decisions.decision.eq("PENDING").sum()),
                  unresolved_scope_rows=int(scope.decision.eq("PENDING").sum()),
                  public_scope_resolutions=int(scope.verified_outcome.fillna("").ne("").sum()),
                  public_terms_verified_issuers=len(terms) if evidence_path.exists() else 0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = review / "backups" / stamp
    backup.mkdir(parents=True)
    for name in ("event_decisions.csv", "scope_decisions.csv"):
        shutil.copy2(review / name, backup / name)
    decisions.to_csv(review / "event_decisions.csv", index=False)
    scope.to_csv(review / "scope_decisions.csv", index=False)
    scope.loc[scope.raw_ticker.isin(SCOPE_EVIDENCE), ["source_event_id", "raw_ticker", "announce_date",
        "ex_date", "verified_outcome", "related_transaction_date", "decision", "rationale", "evidence_url"]].to_csv(
            review / "public_scope_matches.csv", index=False)
    pd.DataFrame(audit).to_csv(backup / "changes.csv", index=False)
    pd.DataFrame(issues).to_csv(review / "remaining_review.csv", index=False)
    # This is deliberately a partial set. The notebook's full review gate stays active.
    checked_events.to_csv(review / "auto_reviewed_approved_subset.csv", index=False)
    prices = pd.read_csv(cache / "daily_prices.csv")
    price_problems = prices.loc[prices.close_price.isna() | prices.close_price.le(0)
                                | prices.price_flag.ne("TR")].copy()
    price_problems["next_action"] = (
        "Delisting/action-day observation is not an executable raw close. Process the linked "
        "merger/settlement event; do not replace zero with a stale price or ordinary fill.")
    price_problems.to_csv(review / "price_review.csv", index=False)
    checks["price_rows"] = len(prices)
    checks["duplicate_price_keys"] = int(prices.duplicated(["date", "permno"]).sum())
    checks["nonpositive_or_nontrade_prices"] = len(price_problems)
    (review / "auto_review_summary.json").write_text(json.dumps(checks, indent=2))
    provenance = {"reviewed_at_utc": reviewed_at, "backup": str(backup.relative_to(ROOT)),
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in [cache / "manifest.json", cache / "coverage.json",
                            review / "event_candidates.csv", review / "event_decisions.csv",
                            review / "scope_decisions.csv", Path(__file__)]
                  + ([evidence_path] if evidence_path.exists() else [])}}
    (review / "auto_review_provenance.json").write_text(json.dumps(provenance, indent=2))
    print(json.dumps(checks, indent=2))
    print("Backup and row-level audit:", backup)


if __name__ == "__main__":
    main()
