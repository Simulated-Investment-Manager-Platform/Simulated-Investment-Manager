# Simulated Investment Manager | BMLL Research Project

A research prototype for a **simulated $10 billion S&P 500 index fund** using historical US equity data. The project is being developed in stages:

**Historical index universe → portfolio construction → order generation → execution simulation → transaction cost analysis (TCA).**

A related corporate-actions workstream combines **WRDS/CRSP CIZ** records with separately exported, authorized **Bloomberg** event data. This repository is a *work in progress*, not a production backtester or investment strategy.

## Current implementation

| Component | Status | Notes |
| --- | --- | --- |
| Historical S&P 500 constituents | Prototype implemented | WRDS/CRSP index membership; `PERMNO`/`PERMCO` identifiers |
| Capitalization-weight proxies | Prototype implemented | Computed from CRSP market cap, **not official float-adjusted index weights** |
| Quarterly snapshots | Prototype implemented | Post-reference-date snapshots; timestamp alignment matters |
| $10B portfolio targets | Prototype implemented | Whole-share target quantities and configurable cash buffer |
| Initial/rebalance orders | Prototype implemented | Target-minus-current holdings |
| Close-price execution baseline | Prototype implemented | Next-session CRSP close, **not** an actual fill or calibrated execution model |
| WRDS corporate-action discovery/extraction | Code implemented, live validation pending | Distributions, delistings, cumulative factors, historical security IDs |
| Bloomberg CSV normalization | Code implemented, real export validation pending | Historical identifier mapping; no Bloomberg API connection |
| WRDS–Bloomberg event reconciliation | Synthetic tests available | Agreement/conflict/review queue |
| Split/dividend accounting | Synthetic prototype | Handles approved ordinary splits and cash-dividend accrual/payment |
| Daily multi-quarter fund ledger | **Not yet integrated** | Corporate actions must be applied across successive valuation dates |
| Auction/VWAP/TWAP execution and TCA | **Not yet implemented** | Requires BMLL coverage and order/fill integration |

**Validation caveat:** Synthetic tests exercise the logic, but do not establish correctness on licensed live WRDS/Bloomberg data. The notebook's historical WRDS demonstrations require your own account and entitlements.

## Repository contents

```text
Simulated-Investment-Manager/
├── README.md
├── .gitignore
├── requirements.txt
├── BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb
└── BMLL_Bloomberg_Corporate_Actions_Template.csv
```

- **Notebook:** Main research notebook. Sections 1–3 build the CRSP S&P 500 universe; section 4 prototypes the fund/initial orders; section 5 covers corporate actions and source reconciliation.
- **CSV template:** *Empty schema only*, to be populated locally with a suitably licensed Bloomberg export.
- **`.gitignore`:** Prevents common credentials, proprietary downloads, and generated output files from entering Git.
- **`requirements.txt`:** Minimal packages for local Jupyter runs. Google Colab includes many of these already.

## Quick start (Google Colab)

1. Open the `.ipynb` file in [Google Colab](https://colab.research.google.com/) or upload it via **File → Upload notebook**.
2. Run the package-installation cell and definition/setup cells in order. The original notebook includes live WRDS examples in sections 3 and 4F, so **do not blindly choose “Run all” without credentials**.
3. In Colab's left sidebar, open **Secrets** (key icon). Create `WRDS_USERNAME` and `WRDS_PASSWORD`, and enable **Notebook access** for both.
4. Run the historical CRSP queries in section 3 only after login works.
5. Run **4A–4E** to test target holdings, rebalancing orders, and the illustrative close-price executor. Section **4F** connects to WRDS; skip it until ready.
6. Run **5A–5G** for corporate-action functions and synthetic tests. These do **not** require a live WRDS session or Bloomberg export.
7. To pull a bounded, authorized WRDS corporate-action sample, set `RUN_LIVE_WRDS = True` in **5A**, choose your sample/date range, and run **5H**.
8. To reconcile Bloomberg data, prepare a local CSV matching the template, set `BLOOMBERG_EXPORT_CSV` and `RUN_BLOOMBERG_IMPORT = True` in **5A**, and run **5I**. Historical identifier matching generally needs the WRDS identifier-history data from **5H**; otherwise records may be unresolved for manual review.
9. Review reconciliation flags and quality checks in **5J** before using any event in a historical fund ledger.

### Local Jupyter alternative

Use Python 3.10+ and install packages:

```bash
python -m venv .venv
source .venv/bin/activate             # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
jupyter notebook BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb
```

The notebook has Google Colab credential support. Outside Colab, provide credentials securely to `wrds_session(...)` using your own local secret-management method. **Never paste a password into committed code.**

## Corporate-actions pipeline

```text
WRDS / CRSP CIZ                              Bloomberg (authorized CSV)
  stkdistributions                             manually normalized columns
  stkdelists                                                 |
  stkdlycumulativeadjfactor                                 |
  stksecurityinfohist                                        |
             |                                               |
             +--------------- normalization -----------------+
                                      |
                          Historical PERMNO/CUSIP mapping
                                      |
                          Event/date/value reconciliation
                                      |
                          Agreed events + manual review queue
                                      |
                    Approved splits/dividends → fund accounting
```

### CRSP tables explored

- `stkdistributions`: dividends, stock splits, distributions, related dates and factors.
- `stkdelists`: security delisting records and related metadata.
- `stkdlycumulativeadjfactor`: daily cumulative adjustment factors for checking splits.
- `stksecurityinfohist`: historical CUSIPs/tickers and their effective-date ranges.

The notebook checks schemas and required columns using WRDS metadata instead of assuming access to a particular subscription. Extracts are batched by `PERMNO` and date range.

### Bloomberg CSV schema

Use the included empty template:

```text
event_id,permno,ticker,cusip,event_type,ex_date,pay_date,announce_date,record_date,cash_per_share,split_ratio,currency,notes
```

Supply a stable security identifier (`permno`, historical `cusip`, or `ticker`), plus an `event_type` and `ex_date`. Prefer historical identifiers over ticker-only matches. For cash distributions, provide amount and payment date when available; for splits, provide the share multiplier. The importer *does not* call Bloomberg or automatically discover Bloomberg-specific field mnemonics.

### Reconciliation policy

- Candidate event key: security (`PERMNO`), normalized event type, and **ex-date**.
- Preserve both sources rather than overwriting an inconsistency.
- Flag disagreements in split ratio, dividend value, date, or identifier for human review.
- WRDS-only/Bloomberg-only records are *not automatically errors*, but are not cross-source confirmed.
- Only reviewed, approved ordinary splits and cash dividends are candidates for the prototype accounting step.
- Merger, delisting, spin-off, rights, special distributions, fractional shares, and other complex events require additional handling.

**Avoid double counting:** If you apply explicit corporate-action cash/share entries, validate price adjustments and dividend treatment separately. Using total-return-adjusted prices together with cash dividends can count the same economics twice.

## Research conventions and limitations

1. **Point-in-time:** Weights based on a closing observation cannot be traded at an *earlier* close. The one-event prototype uses a decision close followed by next-session execution. This is not equivalent to same-close rebalancing.
2. **Index tracking:** CRSP capitalization weights approximate holdings but are not official S&P 500 float-adjusted weights. Interpret benchmark tracking results accordingly.
3. **Execution:** CRSP daily close is an illustrative price benchmark, not a fill simulator incorporating auction liquidity, spreads, impact, or market participation constraints.
4. **Corporate actions:** The code currently has a *single-day event-step prototype*; it is not yet integrated into a multi-quarter daily holdings/NAV replay.
5. **Data rights:** Access and redistribution depend on institutional WRDS/CRSP and Bloomberg agreements. A private GitHub repo does **not** grant redistribution rights. Keep licensed data out of Git unless expressly permitted.

## Work remaining

- [ ] Confirm live WRDS table entitlements and complete historical membership coverage.
- [ ] Build and audit a dated CRSP ↔ BMLL ↔ Bloomberg security-ID mapping.
- [ ] Validate live CRSP/Bloomberg corporate-action matches and adjudicate conflicts.
- [ ] Integrate approved corporate actions with daily cash, shares, dividend receivables, NAV, and order accounting.
- [ ] Model quarterly rebalances and seeded investor inflows/outflows across many dates.
- [ ] Connect auction and intraday market data; implement feasible execution assumptions and TCA.
- [ ] Add reproducible integration tests, automated checks, and documented research outputs.

## Collaboration workflow

This is a **7-person research project**. Use feature branches and pull requests rather than committing directly to `main`:

```bash
git switch -c feat/corporate-actions
# make and test your changes
git add README.md .gitignore requirements.txt \
  BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb \
  BMLL_Bloomberg_Corporate_Actions_Template.csv
git commit -m "Add WRDS Bloomberg corporate actions research notebook"
git push -u origin feat/corporate-actions
```

Open a GitHub pull request targeting `main`, request review, and merge after tests pass. Keep large/proprietary extracts outside Git. Commit notebooks with **outputs cleared** so the repo contains reproducible code, not cached licensed data.

## Data access and intended use

Academic research prototype. Neither WRDS/CRSP nor Bloomberg data is included in the repository. Consult Columbia/WRDS and Bloomberg license terms before sharing or publishing derived datasets.
