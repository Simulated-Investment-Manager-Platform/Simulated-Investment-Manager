"""S&P 500 total-return benchmark from WRDS/CRSP and fund-vs-benchmark reporting.

Source: CRSP's daily S&P 500 index file (``dsp500`` / CIZ ``dsp500_v2``).
``vwretd`` is CRSP's value-weighted return of S&P 500 constituents *including
distributions*. That is the total-return series used as ``benchmark_return``.
It is computed from CRSP market caps, the same caps behind our
``cap_weight_proxy``, so it is the like-for-like target for this fund. It is close
to, but not identical with, S&P's float-adjusted SPXT. ``sprtrn`` (S&P's own
price-index return) is kept for a dividend sanity check when present.

Table and column names differ between the legacy and CIZ releases, so they are
discovered from information_schema rather than assumed. Nothing is substituted
silently: missing sessions raise.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

TABLE_CANDIDATES = [
    ("crsp_q_indexes", "dsp500_v2"), ("crsp_a_indexes", "dsp500_v2"),
    ("crsp_q_indexes", "dsp500"), ("crsp_a_indexes", "dsp500"),
    ("crsp", "dsp500_v2"), ("crsp", "dsp500"),
]
DATE_COLUMNS = ("caldt", "dlycaldt", "date")
TR_COLUMNS = ("vwretd",)
OPTIONAL = {"price_return": ("vwretx",), "sp_price_return": ("sprtrn",),
            "sp_index_level": ("spindx",)}
BENCHMARK_FILE = "benchmark_sp500_tr.csv"


def _columns(db, schema, table):
    df = db.raw_sql("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema=%(s)s AND table_name=%(t)s""",
                    params={"s": schema, "t": table})
    return {c.lower() for c in df.column_name}


def discover_table(db, candidates=TABLE_CANDIDATES):
    """First accessible (schema, table, date_col, tr_col, optional) with a TR column."""
    tried = []
    for schema, table in candidates:
        cols = _columns(db, schema, table)
        tried.append(f"{schema}.{table}: {'missing' if not cols else sorted(cols)[:12]}")
        date_col = next((c for c in DATE_COLUMNS if c in cols), None)
        tr_col = next((c for c in TR_COLUMNS if c in cols), None)
        if date_col and tr_col:
            opt = {k: next((c for c in v if c in cols), None) for k, v in OPTIONAL.items()}
            return schema, table, date_col, tr_col, {k: c for k, c in opt.items() if c}
    raise LookupError("No CRSP S&P 500 daily total-return table is accessible:\n  "
                      + "\n  ".join(tried))


def fetch_sp500_total_return(db, start, end, calendar=None, candidates=TABLE_CANDIDATES):
    """Daily S&P 500 total return for [start, end]; validated against ``calendar``."""
    schema, table, dcol, rcol, opt = discover_table(db, candidates)
    select = [f"{dcol} AS date", f"{rcol} AS benchmark_return"] + [
        f"{c} AS {k}" for k, c in opt.items()]
    df = db.raw_sql(f"""SELECT {', '.join(select)} FROM {schema}.{table}
                        WHERE {dcol} BETWEEN %(a)s AND %(b)s ORDER BY {dcol}""",
                    params={"a": str(pd.Timestamp(start).date()), "b": str(pd.Timestamp(end).date())})
    df["date"] = pd.to_datetime(df.date).dt.normalize()
    df["source"] = f"{schema}.{table}.{rcol}"
    return validate_benchmark(df, calendar, start, end)


def validate_benchmark(df, calendar=None, start=None, end=None):
    b = df.copy()
    b["date"] = pd.to_datetime(b.date).dt.normalize()
    b["benchmark_return"] = pd.to_numeric(b.benchmark_return, errors="coerce")
    if b.date.duplicated().any():
        raise ValueError("Duplicate benchmark dates")
    if b.benchmark_return.isna().any() or (b.benchmark_return <= -1).any():
        raise ValueError("Missing/invalid benchmark returns")
    if calendar is not None:
        cal = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize()
        if start is not None:
            cal = cal[(cal >= pd.Timestamp(start)) & (cal <= pd.Timestamp(end))]
        missing = cal.difference(pd.DatetimeIndex(b.date))
        if len(missing):
            raise ValueError(f"Benchmark lacks {len(missing)} sessions, first {missing[0].date()}; "
                             "the CRSP release may end before the replay. Do not fill gaps.")
    return b.sort_values("date").reset_index(drop=True)


def save_benchmark(df, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / BENCHMARK_FILE
    df.to_csv(path, index=False)
    meta = {"rows": len(df), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source": sorted(df.source.unique().tolist()) if "source" in df else None,
            "first": str(df.date.min().date()), "last": str(df.date.max().date())}
    (folder / "benchmark_sp500_tr.json").write_text(json.dumps(meta, indent=2))
    return path


def load_benchmark(folder):
    folder = Path(folder)
    path = folder / BENCHMARK_FILE
    meta = json.loads((folder / "benchmark_sp500_tr.json").read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
        raise ValueError("Benchmark file changed since extraction; extract again")
    return validate_benchmark(pd.read_csv(path))


def performance_report(navs, benchmark, evaluation_start=None, evaluation_end=None):
    """Fund vs S&P 500 TR over the evaluation window (bootstrap sessions excluded).

    Returns (daily, summary, by_year). Daily fund returns are unit-NAV returns,
    so investor flows do not create performance.
    """
    n = navs[["date", "unit_nav", "fund_return", "external_flow"]].copy()
    n["date"] = pd.to_datetime(n.date).dt.normalize()
    if evaluation_start is not None:
        n = n[n.date.ge(pd.Timestamp(evaluation_start))]
    if evaluation_end is not None:
        n = n[n.date.le(pd.Timestamp(evaluation_end))]
    b = validate_benchmark(benchmark)
    x = n.merge(b, on="date", how="left", validate="one_to_one")
    if x.benchmark_return.isna().any():
        raise ValueError("Benchmark does not cover every evaluation session")
    x["active_return"] = x.fund_return - x.benchmark_return
    x["fund_index"] = (1 + x.fund_return).cumprod()
    x["benchmark_index"] = (1 + x.benchmark_return).cumprod()
    x["cumulative_active"] = x.fund_index / x.benchmark_index - 1
    x["rolling_63d_te_bps"] = x.active_return.rolling(63).std(ddof=1) * np.sqrt(252) * 1e4
    years = len(x) / 252
    fund_tr = float(x.fund_index.iloc[-1] - 1)
    bench_tr = float(x.benchmark_index.iloc[-1] - 1)
    te = float(x.active_return.std(ddof=1) * np.sqrt(252))
    cov = np.cov(x.fund_return, x.benchmark_return, ddof=1)
    summary = {
        "sessions": len(x), "first": str(x.date.iloc[0].date()), "last": str(x.date.iloc[-1].date()),
        "fund_total_return": fund_tr, "benchmark_total_return": bench_tr,
        "fund_annualized": (1 + fund_tr) ** (1 / years) - 1,
        "benchmark_annualized": (1 + bench_tr) ** (1 / years) - 1,
        "tracking_difference_annualized_bps":
            1e4 * (((1 + fund_tr) ** (1 / years)) - ((1 + bench_tr) ** (1 / years))),
        "tracking_error_annualized_bps": 1e4 * te,
        "information_ratio": float(x.active_return.mean() * 252 / te) if te else np.nan,
        "beta": float(cov[0, 1] / cov[1, 1]),
        "correlation": float(np.corrcoef(x.fund_return, x.benchmark_return)[0, 1]),
        "worst_active_day_bps": float(1e4 * x.active_return.min()),
        "best_active_day_bps": float(1e4 * x.active_return.max()),
        "benchmark_source": sorted(b.source.unique().tolist()) if "source" in b else None,
    }
    by_year = (x.assign(year=x.date.dt.year).groupby("year").agg(
        sessions=("date", "size"),
        fund_return=("fund_return", lambda r: (1 + r).prod() - 1),
        benchmark_return=("benchmark_return", lambda r: (1 + r).prod() - 1),
        te_bps=("active_return", lambda r: r.std(ddof=1) * np.sqrt(252) * 1e4),
        net_flows=("external_flow", "sum")))
    by_year["tracking_difference_bps"] = 1e4 * (by_year.fund_return - by_year.benchmark_return)
    return x, summary, by_year
