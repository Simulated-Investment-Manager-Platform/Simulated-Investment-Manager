# Repo review: team repos vs SIMP, sponsor checklist, and next steps for execution/TCA

Author: Aaditya Pai. Review date: 20 November 2026.

This is a read-only review of every repo visible in the `Simulated-Investment-Manager-Platform` GitHub org. No code was changed in any repo. It describes what exists in the code at the commits listed below; it does not describe intent.

## 1. Summary

1. **The org has three visible repos, all public.** `Simulated-Investment-Manager` (team research repo), `SIMP` (Edward's pipeline) and `App-SIMP` (a full web application). No `sim-im` repo, `exec_model` folder or fill-probability code is visible. If one exists it is private and was not reviewed.
2. **Licensed-looking data is committed to a public repo.** `Simulated-Investment-Manager` tracks about 19 MB under `data/` and `outputs/` (CRSP-derived weights, prices and identifiers, Bloomberg corporate actions, holdings, fills), although `.gitignore` and the README say this data is never committed. `App-SIMP` tracks `backend/isins.csv` (CRSP-derived). This needs a team decision.
3. **The execution/TCA code exists three times and the copies disagree.** The team `tca/` package, SIMP's `src/tca_*.py` and App-SIMP's `execution/` + `analytics/tca.py` use different column names, slicing, impact formulas and decision timing. Numbers from the three are not comparable today.
4. **SIMP is a port of the team repo as of 9 November, simplified.** It adds a Phase 1 random order generator, liquidity buckets and a dividend-timing sensitivity. It drops the BMLL client, the Bloomberg cross-check, the old timing option and all tests. It contains nothing from the toxicity track or App-SIMP.
5. **No TCA result on real BMLL data is in any repo.** The team TCA notebook has no saved outputs and defaults to synthetic data. SIMP has no BMLL code in the tree. App-SIMP has a BMLL puller but no committed run output.
6. **Sponsor items: the fund fixes (c) are done in code; the order model (a), Phase 1 generator (b), housekeeping (d) and the one-date and all-date TCA (e, f) are partly done; fill-probability and pre-trade models (g) and the BMLL quota issue (h) are not started.**
7. **Recommended next step for my track:** one rebalance date on real BMLL data from the team `tca/` package, with a shared ISIN-based PERMNO to listing map and a batched, cached BMLL pull.

### What I could not check

- Access was unauthenticated, so only public repos, branches and commits were visible. Pull-request discussions were not read.
- I had no WRDS, Bloomberg or BMLL access, so nothing was run against real data.
- I ran the team repo's offline tests: the 45 tests that could be loaded all pass (pandas 3, Python 3.15). `tests/test_followon_notebook.py` (1 test) did not load because the `wrds` package was not installed in my environment.
- SIMP and App-SIMP were read but not executed. Parts of App-SIMP (`runs/engine.py`, `runs/orchestrator.py`, the API routers, the frontend) were outlined rather than read line by line.

## 2. Repos and branches

| Repo | Default branch | Head commit | Last commit | Tracked files | What it is |
|---|---|---|---|---|---|
| `Simulated-Investment-Manager` | `main` | `e209960` | 2026-11-09 | 103 | Notebooks plus Python modules: portfolio construction, WRDS/Bloomberg corporate actions, daily fund ledger, `tca/` package |
| `SIMP` | `main` | `fe06bcc` | 2026-11-12 | 39 | Script pipeline (`main_0` to `main_3`) with logic in `src/` |
| `App-SIMP` | `claude/upbeat-johnson-fk7zqf` | `062ac89` | 2026-11-07 | 157 | FastAPI + Postgres backend, Next.js frontend, BMLL puller |

Remote branches (last commit date, author, state relative to the default branch):

| Repo | Branch | Last commit | Author | State |
|---|---|---|---|---|
| Simulated-Investment-Manager | `main` | 2026-11-09 | Tanish Patel (merge of PR #5) | default |
| Simulated-Investment-Manager | `feat/friday-auction-tca` | 2026-11-09 | neatonajj | merged, 0 commits ahead |
| Simulated-Investment-Manager | `feat/2022-2026-coverage-recovery` | 2026-10-22 | Rishit Belani | merged, 0 ahead |
| Simulated-Investment-Manager | `tanish/development` | 2026-10-21 | Tanish | merged, 0 ahead |
| Simulated-Investment-Manager | `feat/wrds-bloomberg-corporate-actions` | 2026-10-20 | Rishit Belani | merged, 0 ahead |
| SIMP | `main` | 2026-11-12 | Etlevn | only branch |
| App-SIMP | `claude/upbeat-johnson-fk7zqf` | 2026-11-07 | Tanish | only branch |

There is no unmerged work on any remote branch. App-SIMP's default branch has a feature-style name and the repo has no `main`.

## 3. Simulated-Investment-Manager (team research repo)

### Purpose and structure

| Path | Role |
|---|---|
| `BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb` | 60 cells. S&P 500 constituents and cap weights from CRSP, single-event fund prototype, WRDS/Bloomberg corporate-action extraction and reconciliation. Other modules load its functions (see below) |
| `BMLL_Stage3_Daily_Fund_Research.ipynb` | 18 cells. Daily ledger for 2026-03-23 to 2026-06-30, saved outputs present |
| `BMLL_4Y_Corrected_Portfolio_Orderbook.ipynb` | 32 cells, no saved outputs. Four-year run (2022-07-01 to 2026-06-30): Bloomberg import, WRDS extraction, benchmark, flows, preflight, replay, export |
| `BMLL_TCA_Execution_Comparison.ipynb` | 19 cells, no saved outputs. PM orders to trader/broker/fills, three strategies, one date then all rebalances |
| `research_pipeline.py` | Handoff I/O, event review, original `simulate` ledger (Monday decision, Tuesday fill), `seeded_flows`, `benchmark_report`, `validate_bmll_mapping`, `execution_comparison` |
| `fund_simulator.py` | `rebalance_schedule`, `synthetic_flows`, `simulate_fund` (reference-Friday close timing, flows, order ids) |
| `scripts/` | `benchmark.py`, `complete_release.py`, `coverage_recovery.py`, `multiyear_preflight.py`, `review_priority_4year.py`, `review_stage2_csvs.py` |
| `tca/` | `schema.py`, `market_data.py`, `order_generator.py`, `execution.py`, `analysis.py` |
| `tests/` | 8 modules, 46 test functions, all on synthetic data |
| `docs/` | `RUNBOOK.md`, `order_schema.md` |
| `data/`, `outputs/` | Committed extracts and run outputs (see "Broken, stubbed, hard-coded") |

### What it does end to end

1. Stage 1/2 notebook builds quarterly S&P 500 membership and cap-weight proxies from CRSP and reconciles WRDS and Bloomberg corporate actions into a review queue.
2. `scripts/complete_release.generate` applies review decisions, runs `fund_simulator.simulate_fund` and writes NAV, holdings, orders, fills, an action journal and a validation report under `outputs/`.
3. `scripts/benchmark.performance_report` compares unit-NAV returns with the CRSP S&P 500 total return.
4. `tca/` turns the blotter into PM orders (`schema.pm_orders_from_blotter`), builds trader and child orders per strategy (`order_generator.generate`), prices fills (`execution.simulate_fills`) and summarises costs (`analysis.order_tca`, `weighted`, `rebalance_costs`, `execution_tracking`).

### Data sources and identifier mapping

| Source | Use | Where |
|---|---|---|
| WRDS/CRSP (`crsp_q_stock`, `crsp_q_indexes`) | Membership, market caps, raw closes, distributions, delistings, adjustment factors, identifier history, `dsp500` `vwretd` | Stage 1/2 notebook, `research_pipeline.fetch_daily_prices`, `scripts/benchmark.py` |
| Bloomberg | Corporate actions from a manually exported workbook; no API | `scripts/extract_bloomberg.py` (file missing), Stage 1/2 notebook section 5 |
| BMLL | Daily per-listing metrics through `bmll.reference.query` and `bmll.time_series.query` | `tca/market_data.py`, class `BMLLSource` |

PERMNO to BMLL listing: `tca/market_data.ticker_map_from_weights` builds a point-in-time PERMNO to CRSP ticker table from the quarterly `weights.csv` snapshots. `BMLLSource.listings` then queries BMLL reference data by `Ticker` and `MIC` and takes one `ListingId` per (ticker, MIC). This is a ticker match. The docstring itself warns about share-class separators (BRK.B vs BRK B). `research_pipeline.validate_bmll_mapping` validates an uploaded `permno, bmll_id, valid_from, valid_to` table, but nothing in this repo builds such a table.

### Contributors (git log, all branches)

| Author (as in git) | Commits | Lines +/- excluding `data/`, `outputs/`, notebooks | Lines +/- all files | Active |
|---|---|---|---|---|
| neatonajj | 8 | +2,199 / -30 | +2,779 / -72 | 2026-11-09 |
| Rishit Belani (two git identities) | 3 commits, plus 2 merges and the initial commit | +989 / -2 | +3,601 / -2 | 2026-10-12 to 2026-10-22 |
| Tanish / Tanish Patel | 3 commits, plus 2 merges | +1,053 / -6 | +241,412 / -592 | 2026-10-21, merge on 2026-11-09 |

- All of `tca/`, `fund_simulator.py`, `scripts/benchmark.py` and both docs came in the eight commits by neatonajj on 2026-11-09.
- Tanish's large line count is the committed data and notebooks.
- There are no commits by Aaditya, Aryamaan, Chuan Bin or Edward in this repo. Git authorship shows who committed, not who did the work.

### Code quality

| Aspect | State |
|---|---|
| Tests | 46 test functions, `unittest`, offline. 45 ran and passed for me; 1 needs the `wrds` package |
| Dependencies | `requirements.txt` uses ranges (`pandas>=2.0,<3`), not pins. The 4Y notebook installs `psycopg2-binary`, `openpyxl`, `nbformat` on top. No lock file. Tests passed on pandas 3, outside the declared range |
| README | Detailed, but the "Repository contents" tree lists 5 files, and one status row ("Daily multi-quarter fund ledger: Not yet integrated") contradicts the rows describing `fund_simulator.py` |
| Config | Constants in notebook cells; no config file |
| Error handling | Mostly explicit `raise ValueError`. Exceptions noted below |
| Reproducibility | SHA-256 provenance files for inputs; the four-year notebook cannot run from a clean checkout (missing scripts) |
| CI | None |

### Broken, stubbed, hard-coded, duplicated

| Item | Detail |
|---|---|
| Missing scripts | `scripts/build_wrds_handoff.py`, `extract_bloomberg.py`, `freeze_data.py`, `review_triage.py` are in no branch and no commit. `scripts/complete_release.py` line 18 imports `scripts.freeze_data`, so it cannot be imported. The 4Y notebook imports `extract_bloomberg` (cell 8) and `build_wrds_handoff` (cell 12). `README_CODE_PUSH.md` and `docs/RUNBOOK.md` both state the gap |
| Committed data in a public repo | `data/` and `outputs/` are tracked (commits `05d27e5`, `224b1d6`) though `.gitignore` lists both and the README says no WRDS or Bloomberg data is included. Files include `data/stage2_inputs/daily_prices.csv`, `weights.csv`, `wrds_id_history.csv`, `bloomberg_corporate_actions.csv` |
| Committed outputs are the old run | `outputs/daily_fund/` covers 2026-03-23 to 2026-06-30, two decisions, Monday decision and Tuesday fill, and has no `order_id` or `reason` columns. No four-year or Friday-timed output is in the repo. `outputs/daily_fund/restricted_scenario/fills.csv` is byte-identical to `outputs/daily_fund/fills.csv` |
| Notebook code loaded by cell index | `research_pipeline.load_helpers` executes 18 hard-coded cell indices of the Stage 1/2 notebook. Inserting a cell in that notebook breaks every script that calls it |
| Replay cell hides failures | 4Y notebook cell 20 wraps `generate(...)` in `try/except Exception`, writes `outputs/replay_error.txt`, prints a message and continues |
| TCA notebook defaults | `DATA_SOURCE='synthetic'`; the blotter path `../BMLL_TCA_Supervisor_Handoff/...` is outside the repo and is the old Tuesday-timed handoff |
| Impact model is uncalibrated | `tca/execution.ImpactModel(eta=0.6, auction_eta=0.25, cross_fraction=0.5)` are literature defaults, stated as such |
| BMLL metric resolution hides errors | `BMLLSource.resolve` tries name spellings inside `try/except Exception: continue`, so a quota or auth error looks the same as a wrong metric name |
| One unbatched BMLL query | `BMLLSource.market` requests every listing from 30 days before the first trade date to the last trade date in one `time_series.query`. For the four-year blotter that is four years of daily data for every listing on 15 MICs, when only about 20 sessions before each rebalance are needed |
| Arrival-price fallback differs from the docs | The module docstring and RUNBOOK say a missing opening-auction price falls back to the first continuous price. `market_data.py` line 268 falls back to `prev_close` |
| Volume definition to verify | `day_volume` is `TradeVolume` lit plus dark. `simulate_fills` then computes continuous volume as `day_volume - close_auction_volume`. Whether BMLL lit volume includes auction volume needs checking against the metric definitions, otherwise auction volume is subtracted twice |
| Fills are always complete | Every child fills in full unless its modelled price breaches the 3-sigma collar. All lit fills are labelled `REMOVE` even though the fee blends maker and taker |
| Duplicates inside the repo | `research_pipeline.simulate` and `fund_simulator.simulate_fund`; `research_pipeline.benchmark_report` and `scripts/benchmark.performance_report`; `research_pipeline.seeded_flows` and `fund_simulator.synthetic_flows`; `research_pipeline.execution_comparison` and `tca/analysis.order_tca`. The older versions are kept on purpose for regression tests |

## 4. SIMP (Edward's repo)

### Purpose and structure

| Path | Role |
|---|---|
| `config.py` | All run settings (window, flows, generator, TCA, tape mode) |
| `fetch_data.py` | Validates `data/`; refetches from WRDS if invalid; ensures the benchmark file |
| `main_0_schema.py` | Writes a worked PM/trader/broker/fill example |
| `main_1_generator.py` | Phase 1 random order chain on the market tape |
| `main_2_fund.py` | Fund daybook with flows; writes `daily_nav.csv`, `fills.csv`, `pm_orders.csv` |
| `main_3_tca.py` | Three-strategy TCA on Phase 2 PM orders |
| `src/order_schema.py`, `schema_example.py` | Schema, validators, worked example |
| `src/fund_simulator.py`, `ledger.py`, `pipeline.py` | Fund simulation, exclusions, WRDS fetch, dividend sensitivity |
| `src/market_tape.py` | `MarketTape` protocol, `SyntheticMarketTape`, plugin loader |
| `src/tca_desk.py`, `tca_execution.py`, `tca_report.py`, `order_generator.py` | Desk, fills, TCA summaries, Phase 1 generator |
| `src/corporate_actions.py`, `review.py`, `auto_review.py` | WRDS/Bloomberg reconciliation code carried over from the team notebook |
| `docs/` | `blueprint.md`, `dataset.md`, `data_reality.md`, `order_schema.md`, `tape_plugin.md` |

Outputs go to `output/0_schema/` to `output/3_tca/`. `data/` and `output/` are gitignored and empty in the repo. No notebooks.

### Data sources and identifier mapping

- **WRDS/CRSP only** on the main path: weights, closes, calendar, `stkdistributions` events, `dsp500` total return (`src/pipeline.fetch_wrds`, `src/benchmark.py`).
- **Bloomberg:** not used on the main path. `src/corporate_actions.py` keeps the reconcile functions, and `src/auto_review.py` is headed "LEGACY". `auto_review.py` imports `cache_io.load_cache`, which now raises `RuntimeError`, so it cannot run.
- **BMLL:** no BMLL code in the tree. Market data for Phase 1 and Phase 3 comes from `SyntheticMarketTape` (real CRSP closes as anchors, simulated intraday path, venue split, ADV, spread and auction share). A real feed is meant to be supplied through `TAPE_MODE=plugin`, which loads `$SIMP_TAPE_PLUGIN_DIR/tape_plugin.py`. The plugin directory is gitignored and no plugin is in the repo.
- **PERMNO to BMLL listing:** none. `ledger.validate_vendor_mapping` is a renamed copy of the team's `validate_bmll_mapping`.

### Contributors

Five commits, all by `Etlevn`: `e2ff824` "INIT" on 2026-10-21 and four commits on 2026-11-12. Totals: +11,700 / -4,095 lines.

### Code quality

| Aspect | State |
|---|---|
| Tests | None in the repo. `pyproject.toml` lists `pytest` as a dev dependency. `docs/blueprint.md` says "Use disposable checks ... (delete temp tests after they pass)" |
| Dependencies | `pandas`, `numpy`, `matplotlib`, `wrds` with no version bounds. No lock file |
| Packaging | `[tool.setuptools] packages = []`, so `pip install -e .` installs no modules. Scripts work by adding `src/` to `sys.path` |
| README and docs | Clear and consistent with the code. `docs/data_reality.md` states plainly which inputs are real and which are stand-ins |
| Config | One `config.py`. `configs/default.yaml` is a deprecated stub |
| Error handling | Mostly explicit. `main_3_tca._fund_tracking_scalars` returns `(None, None)` on any exception |
| Reproducibility | Seeds in config; optional `run_manifest.json` with file hashes and git commit |

### Broken, stubbed, hard-coded, duplicated

- Default window in `config.py` is 2026-03-23 to 2026-06-30 (one quarter), not the four-year window.
- `SyntheticMarketTape` is the only data path that can run from the repo, so every Phase 1 and Phase 3 number is synthetic. The docs say so.
- Unused code: `src/auto_review.py`, `src/review.py`, the Bloomberg half of `src/corporate_actions.py`, `pipeline.run_ledger`, `tca_market.build_synthetic_stock_days`.
- `market_tape.load_market_tape` has a branch for synthetic mode with a plugin present that does nothing (`pass`).
- `order_generator._pick_trade_date` falls back to the hard-coded date 2024-06-21 and `market_tape._FALLBACK_PERMNOS` to six hard-coded PERMNOs when no local prices exist. The generator's decision date skips weekends only, not holidays.
- `main_3_tca._pick_trade_date` picks the latest trade date, while the comment in `config.py` says "largest REBALANCE trade_date".
- `tca_execution._window_share` uses a different volume curve (`0.15 + 0.85 x^2`) from `market_tape.u_shaped_curve` in the same repo.
- `fund_simulator.pad_reference_close_coverage` fills a missing Thursday or Friday close with the stock's next available close (usually Monday's). This uses a later price for an earlier date. The rows are written to `padded_quotes.csv` and the docs disclose it.

## 5. App-SIMP

The brief described this as a Voila app. It is not: there are no notebooks and no Voila code in the repo.

### Purpose and structure

- **Backend:** Python 3.12 package `backend/simp/` with FastAPI (`simp.api.app`), a Typer CLI (`simp.cli.main`), Dramatiq/Redis workers, SQLAlchemy and Alembic on Postgres 16, and a Parquet data lake.
- **Frontend:** Next.js 15 in `frontend/src/app/` with pages for blotter, runs, TCA, one-date TCA, market, studies, data, settings and funds; `components/Lineage.tsx` and `Blotter.tsx`.
- **BMLL puller:** `backend/connectors/bmll_puller/puller.py`, a separate project because the `bmll` package pins NumPy below 2.
- **Run:** `docker-compose.yml` (db, redis, api, worker, web).

### What it does end to end

Data pull (synthetic or live) into a hashed lake snapshot, then `Engine.run` (`runs/engine.py`) loops over sessions: corporate actions and settlement, strategy decisions, desk, execution, ledger and NAV. `runs/orchestrator.py` then persists, audits lineage, builds TCA and hashes the output. Database tables include `decision`, `pm_order`, `program`, `trader_order`, `child_order`, `fill`, `ledger_entry`, `flow`, `nav_daily`, `position_daily`, `benchmark_daily`, `tca_order`, `reprice`, `corporate_action`, `mapping_review`.

### Data sources and identifier mapping

- **WRDS/CRSP:** direct Postgres connection in `data/crsp/wrds_loader.py`.
- **BMLL:** `bmll.reference.query`, daily `bmll.time_series.query` (VWAP, TWAP, opening and closing auction price, spreads, volatility, `AuctionOrderImbalance`), `time_series.classified_trades`, and `bmll.data.query("trades_plus", MIC="@SIP")` reduced to minute bars. Batches are saved to Parquet and resumed by input hash (`_batched`).
- **Bloomberg:** none.
- **PERMNO to BMLL listing** (`data/mapping/builder.build_security_map`): PERMNO to CUSIP9 to ISIN to the BMLL primary listing (and the `@SIP` listing), with fallbacks for venue moves and a ticker-plus-price check for foreign issuers. Output has `permno, listing_id, isin, valid_from, valid_to, method, confidence`; unmapped names go to a review queue. The last commit message claims 585 of 585 names mapped; no data is in the repo to confirm it.
- The default data source is a synthetic generator of about 580 invented securities. No lake data or run output is committed, so the repo does not show whether a live run has completed.

### Contributors

26 commits between 2026-11-05 and 2026-11-07, about +22,600 / -2,100 lines excluding lock files and generated files.

### Code quality

- 58 backend test functions and 9 frontend tests; CI runs lint, strict type checks, tests against Postgres and an OpenAPI diff.
- `uv.lock` and `pnpm-lock.yaml` pin dependencies.
- Typed errors; three broad `except Exception` handlers, each commented.
- Hard-coded: brokers `BRK_A` to `BRK_E`, venue fees, users (`tanish`, `andrew`, `admin`), AUM, cost-model parameters (`Y=0.7, beta=0.5, kappa=0.5, lam=0.5`).
- Stubbed: tick-level ("T3") execution raises `ExecutionModelUnavailable`; the auction-imbalance dataset is zero when absent.
- `estimate_trades_plus` exists in the puller but nothing calls it.
- README line 171 documents a `--listings` flag; the puller only accepts `--isins`.

## 6. SIMP compared with the team repos

### Team features and where they are in SIMP

| Feature | Team repo | SIMP | Status in SIMP |
|---|---|---|---|
| CRSP membership and cap weights | Stage 1/2 notebook | `src/universe.py` | Reproduced as a module |
| WRDS distributions to events | Stage 1/2 notebook | `src/corporate_actions.py`, `pipeline.fetch_wrds` | Reproduced |
| Bloomberg import and WRDS/Bloomberg reconciliation with review decisions | Notebook, `scripts/complete_release.py`, `data/stage2_review/` | Code present, not called | Missing from the main path |
| Delistings, adjustment factors, identifier history | Notebook 5B | Not fetched (`fetch_wrds` docstring says so) | Missing |
| Temporal exclusions and provisional WRDS choice | `scripts/coverage_recovery.py` | `ledger.temporal_price_exclusions`; reverse-split names always dropped | Partly; no provisional rule |
| Friday-close timing, flows, order ids | `fund_simulator.py` | `src/fund_simulator.py` | Reproduced, near copy |
| Old Monday/Tuesday timing for comparison | `execution_timing='next_session'` | Removed from `simulate_fund`; `ledger.simulate` kept | Partly |
| Preflight price coverage report | `scripts/multiyear_preflight.py` | Replaced by padding | Different design |
| S&P 500 TR benchmark, TD and TE | `scripts/benchmark.py` | `src/benchmark.py` | Reproduced |
| Order schema and validators | `tca/schema.py` | `src/order_schema.py` | Reproduced with renamed columns |
| Trader and child order generation | `tca/order_generator.py` | `src/tca_desk.py` | Reproduced, simplified |
| Fill pricing | `tca/execution.py` | `src/tca_execution.py` | Reproduced, simplified |
| Order TCA and notional weighting | `tca/analysis.order_tca`, `weighted` | `tca_report.order_tca`, `strategy_summary` | Reproduced |
| Cost in bps of NAV and execution tracking error | `tca/analysis.rebalance_costs`, `execution_tracking` | None | Missing |
| BMLL client | `tca/market_data.BMLLSource` | None (plugin contract only) | Missing |
| Tests | 46 | 0 | Missing |
| Four-year run | 4Y notebook | Config defaults to one quarter | Not shown |

### What SIMP has that the team repo does not

| Feature | Where |
|---|---|
| Phase 1 random order generator (random names, sides and sizes priced near day VWAP) | `src/order_generator.generate_pm_orders`, `main_1_generator.py` |
| Worked schema example, field dictionary, conservation report, schema version | `src/schema_example.py`, `order_schema.field_dictionary`, `conservation_report` |
| `MarketTape` interface with a plugin loader | `src/market_tape.py`, `docs/tape_plugin.md` |
| Liquidity buckets by % of ADV across all rebalances | `tca_report.liquidity_bucket_summary` |
| Dividend-timing sensitivity (accrual vs cash at ex-date vs no dividends) | `fund_simulator.simulate_fund(dividend_mode=...)`, `pipeline._dividend_sensitivity_table` |
| One config file, script entry points, run manifest | `config.py`, `main_*.py` |
| Documentation of real vs stand-in data | `docs/data_reality.md` |

### Where the implementations disagree

| Topic | Team repo | SIMP | App-SIMP | Which looks more correct |
|---|---|---|---|---|
| PM order columns | `pm_order_id`, `portfolio_id`, `quantity` | `order_id`, `fund_id`, `shares` | `pm_order_id`, `fund_id`, `quantity` | Naming only, but tables cannot be exchanged without a rename. SIMP's doc says it is "not a column-for-column copy of the team `tca/schema`" |
| Fund id, id length | `BMLL_SP500_10B`, 20 hex | `SIMP_SP500_10B`, 24 hex | DB keys | Naming only |
| Reason values | `INITIAL/REBALANCE/INFLOW/OUTFLOW` | same | `INDEX_REBALANCE/INDEX_ADD/INDEX_DELETE/SUBSCRIPTION/REDEMPTION/...` | App-SIMP separates adds and deletes, which the TCA needs since those orders carry most of the cost |
| Strategy values | `CLOSE_AUCTION/VWAP/ARRIVAL_IS` | same | `HIGH_TOUCH/ALGO_*/MOC/PORTFOLIO_TRADE/...` | App-SIMP matches the sponsor's wording |
| Broker on a fill | No broker field | No broker field | `broker_id` | App-SIMP matches the sponsor's wording |
| Continuous slicing | 13 half-hour buckets weighted by a volume curve; arrival strategy front-loaded | 2 buckets, 55% / 45% | Fixed volume curve | Team: closer to how VWAP and arrival orders trade |
| Child limit price | 3-sigma collar; breach leaves the child unfilled | No limit; always filled | Minute-bar mode caps at 10% of volume | Team or App-SIMP: SIMP cannot produce an unfilled share |
| Base price for continuous fills | That venue's VWAP, falling back to day VWAP | Day VWAP for every venue | Mid price | Team uses the per-venue data it pulls |
| Missing spread | Median of the sample | Fixed 5 bps | n/a | Team |
| Dark fill label | `MIDPOINT` | `DARK` | `DARK` | Naming only |
| Continuous impact | `eta * sigma * sqrt(Q / (continuous volume * window share))`, eta 0.6 | Same formula, different window curve | `Y * sigma * sqrt(Q / ADV)`, Y 0.7 | None is calibrated. They give different costs for the same order |
| Auction impact | `0.25 * sigma * sqrt(Q / auction volume)` | same | `kappa * sigma * (Q / auction volume)^beta * (1 + lam * same-side imbalance)`, kappa 0.5 | App-SIMP uses imbalance, which the sponsor asked for |
| Rebalance decision time | Close of the session before the reference Friday | same | 5 sessions before by default (`decision_offset_sessions`) | A team decision; it changes decision price and therefore implementation shortfall |
| Missing closes before the reference date | Preflight fails and asks for re-extraction | Pads with a later close | n/a | Team: no later price enters an earlier date |
| Corporate actions | WRDS and Bloomberg reconciled, review decisions applied | WRDS distributions only | WRDS only, with a review queue | Team is the only one doing the cross-check the project describes |
| Rebalance cost roll-up | bps of NAV, execution TD and TE | Fund TD and TE attached to the strategy table | TD net and gross, implementation shortfall split | Team and App-SIMP separate execution cost from fund tracking |
| Liquidity bins | `<0.01%` to `>3%` of ADV, one date only | `<1%`, `1-5%`, `5-10%`, `>=10%`, all dates | Size and auction buckets | SIMP covers all dates; the bins need agreeing |
| Flow seed | 7 | 42 | AR(1) with jumps | Different blotters, so costs do not line up across repos |
| PERMNO to BMLL | Ticker match | None | ISIN match with fallbacks | App-SIMP |

### Duplication and currency

- `SIMP/src/fund_simulator.py` is a near copy of the team `fund_simulator.py`: same docstring text and arithmetic, with the `next_session` branch removed and `dividend_mode` and padding added. The team file was committed on 2026-11-09 and SIMP's restructure on 2026-11-12, so SIMP is based on team `main` as of 9 November, which is still the latest team commit.
- `src/order_schema.py`, `tca_desk.py`, `tca_execution.py`, `tca_report.py`, `benchmark.py` and `market_tape.synthetic_market` are also ports of the matching team modules, with the renames and simplifications listed above.
- The two code bases are already diverging: any fix made in one `simulate_fills` or `order_tca` must be made by hand in the other, and SIMP has no tests to show whether a port changed results.
- SIMP contains no toxicity code and nothing from App-SIMP. `docs/blueprint.md` lists toxicity as an "optional side track ... separate interface only". No toxicity metric exists in any of the three repos; the closest is App-SIMP's markouts and classified-trades pull.
- `docs/blueprint.md` describes SIMP as a "living personal research plan" and names "delivery export for the org remote" as a next step.

## 7. Sponsor checklist

Status describes code in the repos, not results. "Team" is `Simulated-Investment-Manager`.

| Item | Status | Evidence | Gap |
|---|---|---|---|
| **a. Three-level order model** | Partly done (team, SIMP); done in App-SIMP | Team `tca/schema.py` (`PM_ORDER`, `TRADER_ORDER`, `BROKER_ORDER`, `FILL`, `validate_links`); SIMP `src/order_schema.py`; App-SIMP `db/schema.py` (`pm_order`, `trader_order`, `child_order`, `fill`) | Team and SIMP have four levels, no `broker` on fills, and strategies named by benchmark rather than high touch / algo / portfolio trade. PM orders key on PERMNO with ticker optional |
| **b. Phase 1 random generator priced around BMLL daily VWAP** | Partly done | SIMP `src/order_generator.generate_pm_orders` (random, multi-MIC, synthetic VWAP); App-SIMP `strategies/synthetic_generator.py` with `Executor._generator` (BMLL daily VWAP) | Team repo has no random generator: `tca/order_generator.py` only splits the fund's own orders. SIMP's VWAP is synthetic. App-SIMP's is written for BMLL but no run is in the repo |
| **c1. Trade in the Friday closing auction** | Done in code | Team `fund_simulator.rebalance_schedule` and `simulate_fund(execution_timing='reference_close')`; 4Y notebook cell 22 asserts every fill is on its reference date; SIMP same | Fill price is the CRSP close, not the BMLL auction print. Committed outputs are still the Monday/Tuesday run |
| **c2. S&P 500 TR benchmark, tracking difference and error** | Done | Team `scripts/benchmark.fetch_sp500_total_return`, `performance_report`; 4Y notebook 5A and 8B | Benchmark is CRSP `vwretd`, not S&P's SPXT (stated in the notebook) |
| **c3. Synthetic daily flows generating cash-flow orders** | Done | Team `fund_simulator.synthetic_flows`, flow branch of `simulate_fund` (`INFLOW`/`OUTFLOW` orders) | Flow scenario is not calibrated (stated) |
| **c4. Notional weighting** | Done for TCA | Team `tca/analysis.weighted`, `rebalance_costs`; SIMP `tca_report.strategy_summary` | Not checked for every figure in the older Stage 3 notebook |
| **d1. Include `research_pipeline.py` and `scripts/`** | Partly done | Both are in the repo | Four scripts are still missing, so the notebook does not rerun (section 3) |
| **d2. Pin package versions** | Not started (team, SIMP) | `requirements.txt` ranges; SIMP unbounded | App-SIMP has lock files |
| **d3. Fix the config assert** | Done | Original at commit `3d0e9ef`: `assert A == ... and B == ... or A < B`. Now 4Y notebook cell 2: `if not _reb < _start < _end: raise ValueError` | |
| **d4. Stop the replay cell swallowing errors** | Not started | 4Y notebook cell 20 still catches `Exception` and continues | It now saves the traceback to a file, but the cell still succeeds |
| **d5. Measure the provisional dividend rule** | Not started | `scripts/complete_release.generate(provisional_wrds_research=...)` applies the rule and counts the events (`provisional_wrds_disagreements`) | No run with and without the rule is compared. SIMP's `dividend_sensitivity.csv` measures dividend timing, which is a different question |
| **e. One rebalance date end to end on BMLL** | Partly done | Team `BMLLSource` pulls closing-auction price and volume, opening-auction price, per-venue daily VWAP, lit and dark volume, spread; computes 20-day ADV and volatility. `tca/execution.simulate_fills` prices MOC, VWAP and arrival plus spread and impact. `order_tca` gives `pct_adv` | No auction imbalance. No intraday VWAP (daily only). No % of auction volume column. No saved output from a BMLL run. App-SIMP has `pct_auction` and pulls imbalance |
| **f. All rebalances, notional-weighted, by size vs liquidity, with tracking difference** | Partly done | Team TCA notebook section 4, `rebalance_costs`, `execution_tracking`; SIMP `TCA_ALL_REBALANCES`, `liquidity_bucket_summary` | Synthetic data only. Team size buckets exist for one date only (cell 14) |
| **g. Fill-probability and pre-trade cost models; adaptive execution** | Not started | No ridge, gradient boosting or fill-probability code, and no `scikit-learn` dependency, in any visible repo | |
| **h. BMLL quota error near session 31 of 50** | Not started | No quota, retry or backoff handling in any repo. App-SIMP's puller has resumable batches and an unused `estimate_trades_plus`. Team `BMLLSource` makes one unbatched daily query and hides query errors in `resolve` | The code that hit the quota (tick-level trades plus bars) is not in any visible repo |

## 8. Recommended next steps (execution/TCA track)

### Priorities

| # | Build | Size | Sponsor item | Repo |
|---|---|---|---|---|
| 1 | **Shared PERMNO to BMLL listing map.** Export App-SIMP's ISIN-based map (`permno, listing_id, sip_listing_id, mic, valid_from, valid_to, method`) to a file, and make `BMLLSource` read it instead of matching tickers | Small | e | Team repo; map produced by App-SIMP |
| 2 | **One rebalance date on real BMLL data.** Run the TCA notebook in Data Lab with `DATA_SOURCE='bmll'` on the Friday-timed blotter. Add auction imbalance, % of auction volume and an intraday (interval) VWAP. Save a results table without licensed raw data | Medium | e | Team repo, `tca/` |
| 3 | **Quota-safe BMLL data layer.** Pull only the sessions needed around each rebalance, batch by date and listing, cache to Parquet, resume, estimate size before a tick pull, and surface errors instead of hiding them. Use minute bars rather than tick data wherever the analysis allows | Medium | h | Team repo, `tca/market_data.py`; reuse App-SIMP's `_batched` pattern |
| 4 | **Reproducible Friday blotter.** Add the four missing scripts, pin versions with a lock file, let the replay cell fail, rerun the four-year notebook | Small | d | Team repo |
| 5 | **All rebalances on BMLL.** Notional-weighted costs by % of ADV and % of auction volume, with separate rows for index adds and deletes, next to fund tracking difference | Medium | f | Team repo |
| 6 | **Calibrate impact.** Fit `eta` and `auction_eta` to BMLL auction and intraday data; record fitted values and fit quality | Medium | e, f | Team repo |
| 7 | **Schema alignment.** Add `broker`, a strategy category (high touch / algo / portfolio trade) and add/delete reasons; agree one set of column names with Edward and Tanish | Small | a | Team repo first, then SIMP and App-SIMP |
| 8 | **Fill-probability and pre-trade cost models.** Build the training table from BMLL data, ridge baseline, then gradient boosting; compare against VWAP and market-on-close | Large | g | Team repo, new `exec_model/` package |
| 9 | **Adaptive execution** (passive vs market vs adaptive by market state) | Large | g | After 8 |
| 10 | **Provisional dividend rule effect.** Run the fund with and without `provisional_wrds_research` and report the NAV and tracking difference change | Small | d | Team repo |

Items 1 to 3 unblock everything else on this track. Item 4 is outside my track but item 2 needs its output.

### Where the code should live

I recommend keeping execution and TCA work in the team repo's `tca/` package for now, for four reasons that come from the code:

1. It is the only copy with tests (12 in `tests/test_tca.py`) and the only one with a BMLL client.
2. SIMP's copy has renamed columns and a simplified desk and has no tests, so porting new work into it today cannot be verified.
3. SIMP has no place for BMLL code in its public tree by design; a real feed goes through a private plugin.
4. `stock_days` and `venue_days` have the same columns in both repos (`STOCK_COLS`, `VENUE_COLS`). That shared table shape is a workable interface.

To avoid duplicating Edward's work:

- Treat the `stock_days` / `venue_days` tables as the contract. A BMLL data layer that produces them serves the team `tca/` package directly and can be wrapped as SIMP's `build_tape(...)` plugin without copying logic.
- Agree which copy of `simulate_fills`, `order_tca` and the schema is the source of truth. If SIMP is to become the shared package, the port should come with the team tests and a check that both give the same numbers on the same blotter.
- Put new models (item 8) in their own package that takes order and market tables as inputs, so it does not depend on either repo's column names beyond a small adapter.

### Integration risks

1. **Three cost models.** The same order gets a different cost in each repo (section 6). Any number shown to the sponsor needs its source repo and parameters stated.
2. **Column and enum mismatches.** `quantity` vs `shares`, `pm_order_id` vs `order_id`, and different reason and strategy values mean tables cannot pass between repos without an adapter.
3. **Different decision timing.** One session before the reference date (team, SIMP) vs five (App-SIMP default) changes decision price and implementation shortfall.
4. **Different blotters.** Different flow seeds, exclusions and corporate-action sources mean the three repos trade different orders.
5. **Different identifier mapping.** Ticker matching and ISIN matching can select different listings, or miss names, for the same PERMNO.
6. **Padded prices in SIMP.** A later close used for an earlier date changes order sizes on affected names.
7. **No tests in SIMP.** A change in ported logic would not be noticed.
8. **Missing scripts.** The Friday-timed four-year blotter that TCA needs cannot be regenerated from the repo.
9. **Data in public repos.** Committed CRSP and Bloomberg-derived files, and any BMLL-derived output added later, are visible to anyone.
10. **Unbatched BMLL pulls.** Scaling the current `BMLLSource` query to four years risks the same quota problem as item h.

### Questions to raise

For the team:

1. Is there a private `sim-im` repo (with `exec_model` and a daily order generator)? Who can grant access?
2. Which repo is the source of truth for the order schema and the TCA code, and who signs off changes to it?
3. Should `data/`, `outputs/` and `backend/isins.csv` be public? If not, who removes them from history and makes the repos private?
4. Where are the four missing scripts, and who has the Friday-timed four-year `orders.csv`, `fills.csv` and `daily_nav.csv`?
5. Edward: is SIMP meant to replace the team repo, and will it take a BMLL data layer in the tree or only through the private plugin?
6. Tanish: can App-SIMP export its PERMNO to listing map as a file the other repos read? Has a live run completed?
7. What decision time do we use for rebalance orders: one session before or five?
8. Which code hit the BMLL quota error, and where is it?

For the sponsor (Andrew):

1. Is a four-level model (PM order, trader order, child order, fill) acceptable, with broker and strategy category added, or should it be exactly three levels?
2. Which BMLL metrics should we use for closing-auction imbalance and for intraday VWAP, and does lit `TradeVolume` include auction volume?
3. What is our Data Lab quota, and is minute-bar data enough for the TCA, or is tick data required?
4. Should impact be calibrated on BMLL data before any cost figure is presented, or are literature defaults acceptable for a first pass?
5. For the benchmark, is CRSP `vwretd` acceptable in place of the official S&P 500 total return index?
6. What may be committed to the repos: derived summary tables only, or nothing from BMLL at all?
