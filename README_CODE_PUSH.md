# BMLL four-year coverage recovery (code-only pull request)

This directory intentionally includes only the newly introduced 2022–2026 coverage fix and its associated tests. It contains **no** licensed Bloomberg/WRDS data and no credentials.

## Upstream dependencies required in the repository root

- `research_pipeline.py` from Tanish's daily fund implementation.
- `BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb` from the existing WRDS/Bloomberg notebook.
- `scripts/build_wrds_handoff.py`, `scripts/extract_bloomberg.py`, `scripts/freeze_data.py`, `scripts/review_triage.py`, `scripts/review_stage2_csvs.py` from the existing data prep kit.
- `requirements.txt`, including `pandas`, `numpy`, `wrds`, `openpyxl`, `psycopg2-binary` and `nbformat`.

**Do not replace Tanish's `research_pipeline.py`, Chuan's notebooks, or the existing repository README blindly.** Compare against the latest `main` and integrate changes in a pull request.

## Included files

- `BMLL_4Y_Corrected_Portfolio_Orderbook.ipynb`: uploaded-code workflow; detects a checked-out repo and only asks for Bloomberg workbook, or falls back to supporting ZIP upload in standalone Colab.
- `scripts/complete_release.py`: four-year candidate output, event decisions, ledger handoff, validation.
- `scripts/coverage_recovery.py`: corrects global-vs-temporal exclusions, documents provisional split-basis reconciliation.
- `scripts/multiyear_preflight.py`: active-window price validation and quarterly checks.
- `scripts/review_priority_4year.py`: ranks unresolved corporate actions for human review.
- Three new tests: coverage recovery, multiyear preflight and review prioritization.

## Smoke test

From repository root (once upstream dependencies are present):

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

## Boundaries

Output is a research candidate, not an independently approved full-index fund. No vendor data, source reconciliation decisions, daily holdings CSVs, or simulated fills should be committed to Git. Store them in licensed project storage. Exclusions must be reported; provisional WRDS selection is NOT a human approval.

The `orderbook.csv` is a *fund order blotter*, not a level 2/3 exchange order book.
