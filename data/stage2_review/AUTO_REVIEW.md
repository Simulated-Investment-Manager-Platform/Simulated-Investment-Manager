# Automatic review of cached March 23–June 30, 2026 inputs

Updated event_decisions.csv: 453 APPROVE, 13 PENDING (466 total).
Updated scope_decisions.csv: 32 EXCLUDE, 0 PENDING (32 total). All six undated placeholders have now been matched to primary-source transaction histories.
All five split-factor checks agree. Source input files and original Bloomberg export are unchanged.

## Resolved decisions
440 ordinary-dividend candidates agree on date-effective historical identity, ex-date, USD amount and payment date (cash tolerance 1e-10).
Five ordinary splits agree across vendors and adjacent cumulative adjustment factors. Pure share splits do not require a cash currency field; the ledger checks were corrected accordingly. Approval does not implement fractional-share settlement or any linked spin-off.
Eight dividend/identity conflicts were resolved by primary issuer/SEC evidence and a selected existing source record. URLs and explanations are in each decision rationale. Examples include APH 0.25, DD 0.20, HON 1.19 and KLAC 2.30 on their actual event-date share bases; FCX uses the combined 0.15 base-plus-variable entitlement. ONEOK and ExxonMobil use WRDS identity/terms rather than their mismatched Bloomberg CUSIPs.
26 scoped-out records have valid ex-dates, explicit Bloomberg in_index=FALSE annotations, and no matching ticker/CUSIP in the selected snapshots or fetched universe identifier history. These exclusions apply only to the declared research universe; revisit for expanded history or successor portfolios.

## Still pending
remaining_review.csv lists 13 pending candidate rows covering seven constituent issuers. The six undated Bloomberg spin-off placeholders are resolved in public_scope_matches.csv. WRDS security distributions and Bloomberg SPINOFF labels can describe the same underlying economic event; do not apply both.
complex_action_evidence.csv records publicly verified terms for APTV, FDX, HON, CTRA and HOLX, plus issuer-verified special dividends for WRB and HST. Core terms are publicly verified for all seven issuers; event_decisions.csv now records terms_review_status, verified_terms, evidence_url and remaining_blocker separately from execution approval. These records need additional accounting/data rather than arbitrary exclusions. Successor PERMNOs come from the cached CRSP source records; issuer announcements confirm names/ratios rather than CRSP identifiers.

Public issuer/SEC evidence now resolves all six placeholders: BAX's proposed spin-off became the Vantive sale completed January 31, 2025; FIS sold Worldpay stakes rather than distributing shares, with the remaining-stake sale completed January 12, 2026; JNJ's DePuy Synthes separation remained a future plan; KDP explicitly targeted early 2027 as of June 23, 2026; KHC paused the separation February 11, 2026; TFX replaced its proposed spin-off with December 9, 2025 business-sale agreements. These are scoped exclusions, not fabricated effective distributions. Original ex-dates remain missing. The KDP workbook announcement date precedes the issuer's public announcement by one day, which is documented in the rationale.

For the seven complex-action issuers, actual fractional cash settlement amounts/dates and a defensible daily HOLX CVR valuation were not established from the checked primary sources. Cash-in-lieu provisions and a maximum conditional payout are not actual proceeds or fair values. These remain explicit data/model gaps. Public evidence is retrospective audit evidence; later announcements must not be treated as information available to earlier trading decisions.

## Input-quality observations
34,912 daily price records have unique date/PERMNO keys. Two zero-price DA observations (HOLX on April 7 and CTRA on May 7) are delisting-action records, not executable closes. They are retained in source data and listed in price_review.csv; merger accounting must replace the old security economically, not fill trades at zero or carry stale quotes.
Both selected quarterly snapshots contain 503 securities and weights summing to one.

## Files and next execution
Backups and row-level change audits are under backups/. auto_reviewed_approved_subset.csv is a partial reviewed subset, not permission to omit pending actions from a full backtest.
The original upstream match_status/review_reason columns retain source findings; the decision and rationale columns record resolutions.
Use INPUT_MODE="cache" and rerun section 2's approval cell. READY is computed by validation, not a switch to force True. It will correctly remain False until pending rows and required accounting are resolved.

## User-requested exclusions

The user instructed "Exclude the pending now". All 13 remaining candidate rows were changed to EXCLUDE with this rationale. Event decisions now contain 453 APPROVE, 13 EXCLUDE and no PENDING rows; scope decisions contain 32 EXCLUDE. Full decision validation passes and approved_events.csv/approval_audit.csv have been regenerated. Earlier pending counts above describe the prior review state.

This does not implement or economically settle the omitted actions. The current ledger can still stop at HOLX/CTRA zero-price delisting records or fractional reverse-split holdings. No prices, holdings, cash entitlements or source records were altered to bypass those stops. A complete historical-fund interpretation remains inappropriate for this exclusion scenario.
