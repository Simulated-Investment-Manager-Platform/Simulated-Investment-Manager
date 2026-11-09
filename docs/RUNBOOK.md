# Runbook: Friday-auction rebalance, benchmark, flows and phase-2 TCA

This branch responds to the supervisor's handoff comments. It contains **code only**. WRDS/CRSP, Bloomberg and BMLL data, and every generated output (`data/`, `outputs/`, `*.xlsx`), are gitignored and must stay in licensed project storage.

## What changed

| Supervisor item | Where |
|---|---|
| Rebalance in the reference-Friday closing auction | `fund_simulator.py` (`execution_timing='reference_close'`), used by `scripts/complete_release.py` and `scripts/multiyear_preflight.py` |
| S&P 500 total-return benchmark | `scripts/benchmark.py` (CRSP `dsp500` `vwretd`), notebook sections 5A and 8B |
| Synthetic inflows/outflows | `fund_simulator.synthetic_flows`, notebook section 5B |
| Code clean-up in the 4Y notebook | `BMLL_4Y_Corrected_Portfolio_Orderbook.ipynb` (config validation, evaluation-return base, imports) |
| (1) PM → Trader → Broker order schema | `tca/schema.py`, `docs/order_schema.md` |
| (2) Synthetic order generator across MICs | `tca/order_generator.py`, `tca/market_data.py` (BMLL adapter and synthetic stand-in) |
| (3) One date: auction vs VWAP vs arrival + impact | `BMLL_TCA_Execution_Comparison.ipynb` sections 2–4 |
| (4) All rebalances: notional-weighted cost and tracking error | `tca/analysis.py`, TCA notebook section 5 |

**Timing.** The S&P reference date is the third Friday of Mar/Jun/Sep/Dec, or the Thursday when that Friday is a holiday (e.g. 18 June 2026). The fund decides at the close of the session before it, sizing orders from that day's NAV and prices, and fills at the reference close. The CRSP snapshot (the following Monday) is used only for the weights. `research_pipeline.simulate` is unchanged; `EXECUTION_TIMING='next_session'` reproduces the old Monday-decision / Tuesday-fill run exactly.

## 0. Offline tests (any machine)

```bash
python3 -m pip install -r requirements.txt matplotlib
python3 -m unittest discover -s tests -v
```

All 46 tests run offline on synthetic data; they do not need WRDS, BMLL or the missing data-prep scripts.

## 1. Re-run the four-year fund on WRDS (Colab)

Prerequisite: `scripts/build_wrds_handoff.py`, `extract_bloomberg.py`, `freeze_data.py` and `review_triage.py` from `main` (they are not in this branch).

1. Open `BMLL_4Y_Corrected_Portfolio_Orderbook.ipynb` from the repo checkout and add the `WRDS_USERNAME` / `WRDS_PASSWORD` Colab secrets.
2. Keep the defaults in the configuration cell: `EXECUTION_TIMING='reference_close'`, `USE_SYNTHETIC_FLOWS=True` (set it to `False` for a no-flow baseline), `RUN_BENCHMARK_EXTRACTION=True`.
3. Run all cells. `REPLAY_START` becomes the session before the first reference Friday (16 June 2022 for the June 2022 rebalance).
4. **Preflight.** Under Friday timing, index additions need CRSP closes on the Thursday and Friday *before* their Monday snapshot. If the preflight reports missing active prices, widen the per-security price window in `build_wrds_handoff.py` by one week and re-extract.
5. **Check.** The schedule assertion requires every rebalance to fill on its reference date. Section 8B reports the fund against the S&P 500 TR (tracking difference, tracking error, by year).
6. If `freeze_data.derive` rejects the new `INFLOW`/`OUTFLOW` rows or the new order columns (`order_id`, `reason`, `expected_execution_date`, `reference_date`, `timing`), extend its schema rather than dropping them.

Outputs go to `outputs/daily_fund/` (including `rebalance_schedule.csv`) and `data/stage2_inputs/benchmark_sp500_tr.csv`. Keep them in project storage.

## 2. Run the TCA (BMLL Data Lab)

1. Upload the repo code and the new run's `orders.csv`, `fills.csv` and `daily_nav.csv` (not the old Tuesday-timed handoff).
2. In `BMLL_TCA_Execution_Comparison.ipynb` set `DATA_SOURCE='bmll'`, `ORDERS_CSV`, `FILLS_CSV` and `NAV_CSV`. Set `REASONS=['REBALANCE','INFLOW','OUTFLOW']` to include flow trades.
3. Run all. Section 1 should show every rebalance trading on a Friday (Thursday for holidays). Where BMLL lacks a metric, `BMLLSource` falls back (for example, the first continuous price for the open), so check `stock_days` for gaps, especially closing-auction volume.

`DATA_SOURCE='synthetic'` runs anywhere but **shows mechanics only**. Do not quote its numbers.

## 3. Before quoting results

- **Calibrate impact.** `ImpactModel(eta=0.6, auction_eta=0.25, cross_fraction=0.5)` uses textbook defaults. Fit them to BMLL markouts or auction imbalance data and record the fitted values in the notebook.
- **Weights.** CRSP cap weights are a proxy, not official float-adjusted S&P weights. Part of the fund–benchmark tracking error comes from this.
- **Where the cost sits.** Most cost comes from a small number of index add/delete orders above about 3% of ADV, so check those names' BMLL auction volumes by hand.

## Data rules

Never commit WRDS/CRSP, Bloomberg or BMLL extracts, holdings, NAVs, blotters, fills or executed notebooks. Commit notebooks with outputs cleared. Keep credentials only in Colab secrets or environment variables.
