#!/usr/bin/env python3
"""Build the PERMNO -> BMLL listing map on real data and report coverage.

Run where the ``bmll`` SDK is authenticated (BMLL Data Lab, or a machine with
BMLL_KEY_PATH / BMLL_USERNAME set):

    python scripts/run_id_map.py --orderbook <orderbook.csv> \
        --id-history data/stage2_inputs/wrds_id_history.csv \
        [--crsp-prices data/stage2_inputs/daily_prices.csv]

Steps
  1. Universe = every PERMNO in the order book.
  2. BMLL reference listings for the universe's ISINs, as monthly snapshots on
     the primary venues plus @SIP (same approach as App-SIMP's puller). Names
     whose ISIN BMLL does not know are looked up again by ticker; with
     ``--crsp-prices`` their BMLL closes are pulled for the 1 bp price check.
  3. ``tca.id_map.build_id_map`` for the window, then coverage by PERMNO and by
     order-book notional, and the largest unmatched names.

Outputs go to ``outputs/id_map/`` (must be gitignored; checked before anything
is written). They contain licensed identifiers and must never be committed.

BMLL errors. Each snapshot is saved as soon as it is pulled. On any BMLL error
(including a quota error) the script stops, says how far it got and exits with
status 2. It never retries. Running it again resumes from the saved snapshots.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tca import id_map as im                                    # noqa: E402
from tca.market_data import BMLL_METRICS, BMLLSource, PRIMARY_MICS, SIP_MIC  # noqa: E402

REF_MICS = list(PRIMARY_MICS) + [SIP_MIC]
CLOSE_KEYS = ("close_price", "close_continuous")
NO_CRSP_ID_HISTORY = "NO_CRSP_ID_HISTORY"
NO_LISTING_ON_TRADE_DATE = "NO_LISTING_ON_TRADE_DATE"
QUOTA_HINT = re.compile(r"quota|limit|429|throttl|exceed", re.I)


class PullStopped(RuntimeError):
    """A BMLL call failed. Carries how far the pull got."""


# ----------------------------------------------------------------------------
# Safety: never write licensed data where git could pick it up
# ----------------------------------------------------------------------------
def require_gitignored(folder: Path) -> None:
    probe = str((folder / "probe.csv").resolve().relative_to(ROOT))
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", probe]).returncode == 0
    if not ignored:
        sys.exit(f"{folder} is not gitignored; refusing to write licensed data there")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", str(folder)],
                             capture_output=True, text=True).stdout.strip()
    if tracked:
        sys.exit(f"{folder} already has files tracked by git; choose another --out")


# ----------------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------------
def load_orderbook(path, notional_col, reasons):
    ob = pd.read_csv(path)
    if "permno" not in ob:
        sys.exit(f"{path} has no permno column")
    if reasons and "reason" in ob:
        ob = ob[ob.reason.isin(reasons)].copy()
        scope = f"reason in {sorted(reasons)}"
    else:
        scope = "all rows (no reason column)" if reasons else "all rows"
    if notional_col in ob:
        notional, basis = pd.to_numeric(ob[notional_col], errors="raise").abs(), notional_col
    elif {"signed_filled", "execution_price"} <= set(ob):
        notional, basis = (ob.signed_filled * ob.execution_price).abs(), "|signed_filled| x execution_price"
    elif {"signed_quantity", "decision_price"} <= set(ob):
        notional, basis = (ob.signed_quantity * ob.decision_price).abs(), "|signed_quantity| x decision_price"
    else:
        sys.exit(f"{path}: no {notional_col} column and nothing to compute a notional from")
    date_col = next((c for c in ("execution_date", "expected_execution_date", "trade_date",
                                 "decision_date") if c in ob), None)
    if date_col is None:
        sys.exit(f"{path} has no trade/decision date column")
    out = pd.DataFrame({"date": pd.to_datetime(ob[date_col]).dt.normalize(),
                        "permno": ob.permno.astype(int), "notional_usd": notional.astype(float)})
    if out.empty:
        sys.exit(f"{path}: no orders left after filtering ({scope})")
    return out, {"notional_basis": basis, "date_column": date_col, "scope": scope}


def snapshot_days(start, end, freq):
    days = list(pd.bdate_range(start, end, freq=freq))
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if not days or days[0] != start:
        days = [start, *days]
    if days[-1] != end:
        days.append(end)
    return days


# ----------------------------------------------------------------------------
# BMLL
# ----------------------------------------------------------------------------
def connect():
    try:
        import bmll
    except ImportError:
        sys.exit("The bmll package is not installed here. Run this in BMLL Data Lab, "
                 "or install the SDK and set BMLL_KEY_PATH / BMLL_USERNAME.")
    key = os.environ.get("BMLL_KEY_PATH")
    if key:   # outside Data Lab: log in with the registered key
        bmll.login(username=os.environ.get("BMLL_USERNAME"), key_path=str(Path(key).expanduser()),
                   passphrase=os.environ.get("BMLL_KEY_PASSPHRASE"),
                   lab_org_id=os.environ.get("BMLL_LAB_ORG_ID"))
    return BMLLSource(bmll, mics=REF_MICS, metrics={k: BMLL_METRICS[k] for k in CLOSE_KEYS})


def _stop(what, done, total, exc):
    kind = "This looks like a quota/rate limit." if QUOTA_HINT.search(str(exc)) else "Not obviously a quota error."
    raise PullStopped(f"BMLL {what} stopped after {done} of {total} snapshots. {kind}\n"
                      f"  {type(exc).__name__}: {exc}\n"
                      "  Completed snapshots are saved; nothing was retried.") from exc


def pull_reference(src, field, values, days, cache, batch=200):
    """One reference snapshot per day in ``days``; each is saved before the next is pulled."""
    if not values:
        return pd.DataFrame()
    tag = hashlib.md5(repr((field, sorted(values), src.mics)).encode()).hexdigest()[:8]
    frames = []
    for k, day in enumerate(days):
        path = cache / f"reference_{field.lower()}_{tag}_{day:%Y%m%d}.csv"
        if path.exists():
            try:
                frames.append(pd.read_csv(path))
            except pd.errors.EmptyDataError:
                pass
            continue
        parts = []
        for i in range(0, len(values), batch):
            try:
                parts.append(src.bmll.reference.query(
                    start_date=day.date(), end_date=day.date(), object_type="Listing",
                    MIC=src.mics, **{field: values[i:i + batch]}))
            except Exception as exc:                      # stop; never retry
                _stop(f"reference pull by {field}", k, len(days), exc)
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if len(df):
            df["Date"] = day
        df.to_csv(path, index=False)
        if len(df):
            frames.append(df)
        print(f"  reference by {field}: snapshot {k + 1}/{len(days)} ({day.date()}), {len(df)} rows", flush=True)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def snapshot_intervals(raw, days):
    """Collapse dated snapshots to ValidFrom/ValidTo per listing and identifier set.

    A listing first seen at snapshot k started some time after snapshot k-1,
    and one last seen at snapshot k ended some time before snapshot k+1, so the
    interval is widened to those bounds (open-ended at the last snapshot). The
    exact dates then come from the CRSP interval it is intersected with.
    """
    alias = {"ObjectId": "ListingId", "Opol": "OPOL"}
    raw = raw.rename(columns={k: v for k, v in alias.items() if k in raw and v not in raw})
    need = {"ListingId", "ISIN", "Ticker", "MIC", "OPOL", "IsPrimary"}
    if need - set(raw):
        sys.exit(f"BMLL reference is missing {sorted(need - set(raw))}; got {sorted(raw.columns)}")
    raw["Date"] = pd.to_datetime(raw["Date"]).dt.normalize()
    keys = [c for c in ("ListingId", "InstrumentId", "ISIN", "Ticker", "MIC", "OPOL", "IsPrimary") if c in raw]
    agg = raw.groupby(keys, dropna=False, as_index=False).agg(first=("Date", "min"), last=("Date", "max"))
    days = [pd.Timestamp(d).normalize() for d in days]
    prev, nxt = dict(zip(days[1:], days[:-1])), dict(zip(days[:-1], days[1:]))
    agg["ValidFrom"] = [im.OPEN_START if d == days[0] else prev[d] + im.DAY for d in agg["first"]]
    agg["ValidTo"] = [pd.NaT if d == days[-1] else nxt[d] - im.DAY for d in agg["last"]]
    return agg.drop(columns=["first", "last"])


def pull_closes(src, listing_ids, start, end):
    """BMLL daily closes (closing auction, else last continuous) for the price check."""
    ids = sorted({int(x) for x in listing_ids})
    if not ids:
        return None
    probe_from = pd.Timestamp(end) - pd.Timedelta(days=10)     # a recent week, where listings have data
    for tried, lid in enumerate(ids[:5], 1):
        src.resolve(lid, probe_from)
        if src.resolved:
            break
    if not src.resolved:
        raise PullStopped(f"BMLL close metrics could not be resolved ({tried} listings probed). "
                          "BMLLSource.resolve hides the underlying error, so this may be a quota or "
                          "entitlement problem. The reference pull is complete and saved: rerun "
                          "without --crsp-prices to get coverage without the ticker price check.")
    try:
        df = src.daily(ids, list(CLOSE_KEYS), start, end)
    except Exception as exc:
        _stop("daily close pull", 0, 1, exc)
    close = df[[c for c in CLOSE_KEYS if c in df]].bfill(axis=1).iloc[:, 0]
    return pd.DataFrame({"ListingId": df["ObjectId"].astype(int), "Date": df["Date"], "close": close})


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--orderbook", required=True, help="fund order book CSV (permno, date, notional)")
    ap.add_argument("--id-history", default=str(ROOT / "data/stage2_inputs/wrds_id_history.csv"))
    ap.add_argument("--crsp-prices", help="CRSP daily closes (permno, date, close_price) for the ticker fallback")
    ap.add_argument("--start", default="2022-06-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--out", default=str(ROOT / "outputs/id_map"))
    ap.add_argument("--notional-col", default="executed_notional_usd")
    ap.add_argument("--reasons", nargs="*", default=["REBALANCE"], help="order reasons to count; none = all")
    ap.add_argument("--snapshot-freq", default="BMS", help="pandas offset for reference snapshots (BMS = monthly)")
    ap.add_argument("--reference-csv", help="reuse a saved reference_listing.csv instead of querying BMLL")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args(argv)

    out = Path(a.out).resolve()
    require_gitignored(out)
    cache = out / "_snapshots"
    cache.mkdir(parents=True, exist_ok=True)

    orders, info = load_orderbook(a.orderbook, a.notional_col, set(a.reasons))
    universe = sorted(orders.permno.unique())
    hist = pd.read_csv(a.id_history)
    hist = hist[hist.permno.isin(universe)].copy()
    hist["isin"] = [im.isin_from_cusip9(c) if c else None
                    for c in map(im.cusip9_of, hist.get("cusip9", [None] * len(hist)), hist.get("cusip", [None] * len(hist)))]
    print(f"Order book: {len(orders):,} orders, {len(universe)} PERMNOs, ${orders.notional_usd.sum():,.0f} "
          f"({info['scope']}; notional = {info['notional_basis']}; dates = {info['date_column']})")
    print(f"Identifier history: {hist.permno.nunique()} of {len(universe)} PERMNOs, {len(hist)} intervals")

    crsp_close = bmll_close = None
    if a.reference_csv:
        reference = pd.read_csv(a.reference_csv)
        print(f"Reference: {len(reference)} rows from {a.reference_csv} (BMLL not queried)")
    else:
        src = connect()
        days = snapshot_days(a.start, a.end, a.snapshot_freq)
        isins = sorted(hist["isin"].dropna().unique())
        print(f"BMLL reference: {len(isins)} ISINs x {len(days)} snapshots on {src.mics}")
        try:
            raw = pull_reference(src, "ISIN", isins, days, cache)
            known = set(raw["ISIN"].dropna().astype(str)) if len(raw) else set()
            by_permno = hist.groupby("permno")["isin"].agg(lambda s: set(s.dropna()))
            lost = [p for p, s in by_permno.items() if not s & known]
            tickers = sorted({str(t).replace(".", " ") for t in hist[hist.permno.isin(lost)].ticker.dropna()})
            print(f"  ticker lookup for {len(tickers)} tickers ({len(lost)} PERMNOs with no ISIN known to BMLL)")
            raw_t = pull_reference(src, "Ticker", tickers, days, cache)
            raw = pd.concat([raw, raw_t], ignore_index=True)
            if raw.empty:
                sys.exit("BMLL returned no reference rows; check entitlements and the MIC list")
            reference = snapshot_intervals(raw, days)
            if a.crsp_prices and len(raw_t):
                t = snapshot_intervals(raw_t, days)
                ids = t.loc[t.IsPrimary.astype(bool) & t.MIC.eq(t.OPOL), "ListingId"]
                bmll_close = pull_closes(src, ids, a.start, a.end)
        except PullStopped as stop:
            print(f"\nSTOPPED: {stop}\nSaved snapshots: {cache}", file=sys.stderr)
            return 2
        reference.to_csv(out / "reference_listing.csv", index=False)
    if a.crsp_prices and bmll_close is not None:
        crsp_close = pd.read_csv(a.crsp_prices, usecols=["permno", "date", "close_price"])
        crsp_close = crsp_close[crsp_close.permno.isin(universe)]

    res = im.build_id_map(hist.drop(columns="isin"), reference, crsp_close, bmll_close,
                          universe=universe, start=a.start, end=a.end)
    # Order-book PERMNOs with no CRSP identifier history never reach build_id_map: report them too.
    seen = set(res.id_map.permno) | set(res.unmatched.permno)
    absent = pd.DataFrame([{"permno": p, "reason": NO_CRSP_ID_HISTORY, "foreign_cusip": False,
                            "valid_from": pd.Timestamp(a.start), "valid_to": pd.Timestamp(a.end)}
                           for p in universe if p not in seen], columns=im.UNMATCHED_COLS)
    unmatched = pd.concat([res.unmatched, absent.astype({"permno": "Int64"})], ignore_index=True)
    coverage = im.coverage_summary(res.id_map, unmatched)

    matched, _missing = im.lookup(res.id_map, orders)
    o = orders.merge(matched[["date", "permno", "method"]], on=["date", "permno"], how="left")
    total = o.notional_usd.sum()
    o["status"] = o.method.map(lambda m: "UNMATCHED" if pd.isna(m) else
                               ("MATCHED_ISIN" if m in im.ISIN_METHODS else "MATCHED_TICKER_FALLBACK"))
    notional = o.groupby("status").agg(orders=("permno", "size"), permnos=("permno", "nunique"),
                                       notional_usd=("notional_usd", "sum")).reset_index()
    notional["share"] = notional.notional_usd / total

    last = unmatched.sort_values(["permno", "valid_from"]).groupby("permno").agg(
        ticker=("ticker", "last"), reason=("reason", "last"))
    names = hist.sort_values("secinfostartdt").groupby("permno").ticker.last()
    top = o[o.status.eq("UNMATCHED")].groupby("permno").agg(
        orders=("permno", "size"), notional_usd=("notional_usd", "sum")).join(last)
    top["ticker"] = top.ticker.fillna(top.index.to_series().map(names))
    top["reason"] = top.reason.fillna(NO_LISTING_ON_TRADE_DATE)   # mapped, but not on the order's date
    top["share_of_total"] = top.notional_usd / total
    top = top.sort_values("notional_usd", ascending=False).reset_index()

    res.id_map.to_csv(out / "id_map.csv", index=False)
    unmatched.to_csv(out / "unmatched.csv", index=False)
    coverage.to_csv(out / "coverage.csv", index=False)
    notional.to_csv(out / "notional_coverage.csv", index=False)
    top.to_csv(out / "unmatched_by_notional.csv", index=False)

    pd.set_option("display.width", 160)
    print("\nCoverage by PERMNO")
    print(coverage.to_string(index=False, formatters={"share": "{:.1%}".format}))
    print("\nCoverage by order-book notional (an order counts as matched only if its PERMNO "
          "has exactly one listing on the order's date)")
    print(notional.to_string(index=False, formatters={"share": "{:.2%}".format, "notional_usd": "${:,.0f}".format}))
    covered = notional.loc[notional.status.ne("UNMATCHED"), "notional_usd"].sum()
    print(f"\nMatched notional: ${covered:,.0f} of ${total:,.0f} = {covered / total:.2%}")
    print(f"\nTop {min(a.top, len(top))} unmatched names by notional ({len(top)} in total)")
    if len(top):
        print(top.head(a.top).to_string(index=False, formatters={
            "share_of_total": "{:.3%}".format, "notional_usd": "${:,.0f}".format}))
    else:
        print("  none")
    print(f"\nWritten to {out} (gitignored; do not commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
