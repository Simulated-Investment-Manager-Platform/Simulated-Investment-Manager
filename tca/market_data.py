"""Daily market inputs for execution simulation, from BMLL or a synthetic stand-in.

Two canonical tables:

venue_days   date, permno, mic, is_primary, volume, vwap
             Continuous-session volume and VWAP of one stock on one venue (MIC).
stock_days   date, permno, primary_mic, prev_close, arrival_price, close_price,
             close_auction_volume, day_volume, day_vwap, spread_bps, sigma_daily,
             adv_shares, is_index_event
             Consolidated per-stock fields. arrival_price = opening-auction price
             (fallback: first continuous price), close_price = closing-auction price.

``BMLLSource`` builds both from ``bmll.reference`` + ``bmll.time_series`` daily
metrics. Metric names are resolved at run time with the same candidate formats
as bmll_probe_v4, because the catalogue spelling differs by suffix.
``synthetic_market`` builds the same tables when BMLL is not reachable; it is
anchored on the blotter's real decision-day and execution-day closes, so only
the intraday shape, venue split and liquidity are simulated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

US_VENUE_MICS = ["XNAS", "XNYS", "ARCX", "BATS", "BATY", "EDGX", "EDGA", "IEXG",
                 "MEMX", "XCHI", "XBOS", "XPHL", "EPRL", "XASE", "LTSE"]
SIP_MIC = "@SIP"
PRIMARY_MICS = ("XNYS", "XNAS", "ARCX", "BATS", "XASE")

# canonical key -> (catalogue metric, suffix); as in bmll_probe_v4
BMLL_METRICS = {
    "close_price": ("ClosingAuctionPrice", "Executed"),
    "arrival_price": ("OpeningAuctionPrice", "Executed"),
    "close_continuous": ("Close", "Executed"),
    "vwap": ("VWAP", None),
    "spread_bps": ("Spread", "RelTWA"),
    "volume_lit": ("TradeVolume", "Lit"),
    "volume_dark": ("TradeVolume", "Dark"),
    "close_auction_volume": ("ClosingAuctionVolume", None),
}
_FORMATS = [lambda m, s: f"{m}|{s}", lambda m, s: f"{m}{s}", lambda m, s: f"{m}.{s}",
            lambda m, s: f"{m}_{s}", lambda m, s: m]

# Typical share of continuous volume by venue for large US stocks; DARK = off-exchange TRF.
BASE_VENUE_SHARE = {"PRIMARY": .17, "ARCX": .07, "BATS": .06, "EDGX": .07, "MEMX": .04,
                    "IEXG": .03, "BATY": .02, "EDGA": .02, "XCHI": .01, "DARK": .45,
                    "OTHER_LIT": .06}

VENUE_COLS = ["date", "permno", "mic", "is_primary", "volume", "vwap"]
STOCK_COLS = ["date", "permno", "primary_mic", "prev_close", "arrival_price", "close_price",
              "close_auction_volume", "day_volume", "day_vwap", "spread_bps", "sigma_daily",
              "adv_shares", "is_index_event"]


def u_shaped_curve(n_bins=13):
    """Default intraday continuous-volume curve over n equal bins (09:30-16:00)."""
    x = (np.arange(n_bins) + .5) / n_bins
    w = 1 + 2.2 * (x - .5) ** 2 * 4 + 1.2 * np.exp(-((1 - x) / .06))
    return pd.Series(w / w.sum(), index=pd.RangeIndex(n_bins, name="bin"), name="volume_share")


def check_market(venue_days, stock_days):
    for df, cols, name in ((venue_days, VENUE_COLS, "venue_days"), (stock_days, STOCK_COLS, "stock_days")):
        miss = set(cols) - set(df)
        if miss:
            raise ValueError(f"{name} missing {sorted(miss)}")
    if stock_days[["date", "permno"]].duplicated().any():
        raise ValueError("stock_days must be unique per date/permno")
    if venue_days[["date", "permno", "mic"]].duplicated().any():
        raise ValueError("venue_days must be unique per date/permno/mic")
    pos = ["prev_close", "arrival_price", "close_price", "day_vwap", "sigma_daily", "adv_shares"]
    if (stock_days[pos] <= 0).any().any() or stock_days[pos].isna().any().any():
        raise ValueError("stock_days needs positive prices/volatility/ADV")
    return True


# ----------------------------------------------------------------------------
# Synthetic
# ----------------------------------------------------------------------------
def synthetic_market(anchors, *, seed=11, index_event_dates=(), n_steps=78, mics=None,
                     auction_share_normal=.08, auction_share_event=.22,
                     sigma_median=.016, adv_usd_median=6e8, spread_bps_median=3.0):
    """Synthetic venue_days/stock_days anchored on real closes.

    anchors: date, permno, prev_close, close_price, optional market_cap_usd /
    adv_usd / sigma_daily. Typically prev_close = blotter decision price and
    close_price = blotter execution price, so the day's open-to-close path ends at
    the real close. On ``index_event_dates`` the closing auction takes a larger
    share of volume (S&P rebalance days run roughly 2-3x a normal auction).
    """
    rng = np.random.default_rng(seed)
    a = anchors.copy()
    a["date"] = pd.to_datetime(a.date).dt.normalize()
    if a[["date", "permno"]].duplicated().any():
        raise ValueError("anchors must be unique per date/permno")
    n = len(a)
    events = set(pd.to_datetime(list(index_event_dates)).normalize())
    sigma = a["sigma_daily"].to_numpy(float) if "sigma_daily" in a else \
        sigma_median * np.exp(rng.normal(0, .35, n))
    if "adv_usd" in a:
        adv_usd = a.adv_usd.to_numpy(float)
    elif "market_cap_usd" in a:
        adv_usd = .0075 * a.market_cap_usd.to_numpy(float) * np.exp(rng.normal(0, .3, n))
    else:
        adv_usd = adv_usd_median * np.exp(rng.normal(0, 1.0, n))
    spread = spread_bps_median * np.exp(rng.normal(0, .45, n)) * (sigma / sigma_median) ** .5
    is_event = a.date.isin(events).to_numpy()
    prev, close = a.prev_close.to_numpy(float), a.close_price.to_numpy(float)
    curve = u_shaped_curve(n_steps).to_numpy()
    # Brownian bridge in log price from open to the real close.
    gap = rng.normal(0, .3, n) * sigma
    log_open = np.log(prev) + gap
    t = np.linspace(0, 1, n_steps + 1)
    w = rng.normal(0, 1, (n, n_steps)) * np.sqrt(1 / n_steps)
    bm = np.concatenate([np.zeros((n, 1)), np.cumsum(w, axis=1)], axis=1)
    bridge = bm - t * bm[:, [-1]]
    path = log_open[:, None] + t * (np.log(close) - log_open)[:, None] + .9 * sigma[:, None] * bridge
    mid = np.exp(.5 * (path[:, 1:] + path[:, :-1]))
    day_vwap = (mid * curve).sum(axis=1)
    arrival = np.exp(log_open)
    vol_mult = np.where(is_event, 1.8, 1.0) * np.exp(rng.normal(0, .2, n))
    day_volume = adv_usd / prev * vol_mult
    auction_share = np.where(is_event, auction_share_event, auction_share_normal) * np.exp(rng.normal(0, .15, n))
    auction_vol = day_volume * auction_share
    continuous = day_volume - auction_vol
    primary = rng.choice(["XNYS", "XNAS"], n, p=[.55, .45])
    stock = pd.DataFrame({
        "date": a.date.values, "permno": a.permno.astype(int).values, "primary_mic": primary,
        "prev_close": prev, "arrival_price": arrival, "close_price": close,
        "close_auction_volume": np.round(auction_vol), "day_volume": np.round(day_volume),
        "day_vwap": day_vwap, "spread_bps": spread, "sigma_daily": sigma,
        "adv_shares": np.round(adv_usd / prev), "is_index_event": is_event})
    names = list(BASE_VENUE_SHARE)
    base = np.array([BASE_VENUE_SHARE[k] for k in names])
    shares = rng.dirichlet(base * 200, n)
    rows = []
    for i in range(n):
        for j, k in enumerate(names):
            mic = primary[i] if k == "PRIMARY" else ("XADF" if k == "DARK" else
                  ("XBOS" if k == "OTHER_LIT" else k))
            if mics is not None and mic not in mics:
                continue
            noise = rng.normal(0, .15 * spread[i]) / 1e4
            rows.append((stock.date.iat[i], stock.permno.iat[i], mic, k == "PRIMARY",
                         round(continuous[i] * shares[i, j]), day_vwap[i] * (1 + noise)))
    venue = pd.DataFrame(rows, columns=VENUE_COLS)
    venue = venue.groupby(["date", "permno", "mic"], as_index=False).agg(
        is_primary=("is_primary", "max"), volume=("volume", "sum"), vwap=("vwap", "mean"))
    # Consolidated VWAP = volume-weighted venue VWAPs (as in BMLL consolidated data).
    pv = (venue.vwap * venue.volume).groupby([venue.date, venue.permno]).sum() / \
        venue.volume.groupby([venue.date, venue.permno]).sum()
    stock["day_vwap"] = pv.reindex(pd.MultiIndex.from_frame(stock[["date", "permno"]])).to_numpy()
    check_market(venue, stock)
    return venue[VENUE_COLS], stock[STOCK_COLS]


# ----------------------------------------------------------------------------
# BMLL
# ----------------------------------------------------------------------------
class BMLLSource:
    """Pull daily per-venue VWAP/volume and auction prints from BMLL Data Lab.

    ``bmll`` is the imported ``bmll`` package (inject a fake in tests).
    ``ticker_map`` is a point-in-time PERMNO -> ticker table with columns
    permno, ticker, valid_from, valid_to (validated by research_pipeline.
    validate_bmll_mapping upstream). One BMLL listing per (ticker, MIC) is
    selected for each date range; primary-listing metrics give auction prices.
    """

    def __init__(self, bmll, mics=US_VENUE_MICS, metrics=BMLL_METRICS):
        self.bmll, self.mics, self.metrics = bmll, list(mics), dict(metrics)
        self.resolved = {}

    def listings(self, tickers, start, end):
        ref = self.bmll.reference.query(Ticker=list(tickers), MIC=self.mics + [SIP_MIC],
                                        start_date=str(start), end_date=str(end))
        ref = ref[ref.get("IsAlive", True) == True].copy()  # noqa: E712
        ref["Date"] = pd.to_datetime(ref["Date"])
        g = ref.groupby(["Ticker", "MIC", "ListingId"], as_index=False).agg(
            is_primary=("IsPrimary", "max"), valid_from=("Date", "min"), valid_to=("Date", "max"))
        return g.rename(columns={"Ticker": "ticker", "MIC": "mic", "ListingId": "listing_id"})

    def resolve(self, probe_id, start):
        """Find the catalogue spelling of every metric (cached)."""
        end = (pd.Timestamp(start) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        for key, (metric, suffix) in self.metrics.items():
            if key in self.resolved:
                continue
            names = [metric] if suffix is None else list(dict.fromkeys(f(metric, suffix) for f in _FORMATS))
            for name in names:
                try:
                    df = self.bmll.time_series.query(object_ids=[probe_id], metric=[name], frequency="D",
                                                     start_date=str(pd.Timestamp(start).date()), end_date=end)
                except Exception:
                    continue
                cols = [c for c in df.columns if c not in ("ObjectId", "Date")]
                pick = cols[0] if len(cols) == 1 else next(
                    (c for c in cols if suffix and suffix.lower() in c.lower()), None)
                if pick:
                    self.resolved[key] = (name, pick)
                    break
        return self.resolved

    def daily(self, listing_ids, keys, start, end):
        keys = [k for k in keys if k in self.resolved]
        if not keys:
            return pd.DataFrame(columns=["ObjectId", "Date"])
        req = list(dict.fromkeys(self.resolved[k][0] for k in keys))
        df = self.bmll.time_series.query(object_ids=list(map(int, listing_ids)), metric=req, frequency="D",
                                         start_date=str(pd.Timestamp(start).date()),
                                         end_date=str(pd.Timestamp(end).date()))
        df = df.rename(columns={self.resolved[k][1]: k for k in keys})
        df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()
        return df[["ObjectId", "Date"] + [k for k in keys if k in df]]

    def market(self, requests, ticker_map, *, history_days=30, index_event_dates=()):
        """requests: date, permno (one row per stock-day to execute).

        Returns (venue_days, stock_days). sigma_daily and adv_shares come from the
        ``history_days`` calendar days before each date (no look-ahead).
        """
        req = requests[["date", "permno"]].drop_duplicates().copy()
        req["date"] = pd.to_datetime(req.date).dt.normalize()
        tm = ticker_map.copy()
        for c in ("valid_from", "valid_to"):
            tm[c] = pd.to_datetime(tm[c])
        req = req.merge(tm, on="permno", how="left")
        req = req[req.date.between(req.valid_from, req.valid_to.fillna(pd.Timestamp.max))]
        if req[["date", "permno"]].duplicated().any() or req.ticker.isna().any():
            raise ValueError("PERMNO->ticker map missing or ambiguous on a requested date")
        start = req.date.min() - pd.Timedelta(days=history_days)
        end = req.date.max()
        lst = self.listings(req.ticker.unique(), start.date(), end.date())
        venue_ids = lst[lst.mic.ne(SIP_MIC)]
        self.resolve(int(venue_ids.listing_id.iloc[0]), start)
        raw = self.daily(venue_ids.listing_id, list(self.metrics), start, end)
        raw = raw.merge(venue_ids.rename(columns={"listing_id": "ObjectId"}), on="ObjectId")
        raw = raw.merge(req[["ticker", "permno"]].drop_duplicates(), on="ticker")
        vol_cols = [c for c in ("volume_lit", "volume_dark") if c in raw]
        if not vol_cols or "vwap" not in raw:
            raise ValueError(f"BMLL VWAP/volume metrics unresolved: {sorted(self.resolved)}")
        raw["volume"] = raw[vol_cols].sum(axis=1, min_count=1)
        if "close_price" not in raw or raw.close_price.isna().all():
            if "close_continuous" not in raw:
                raise ValueError("Neither closing-auction nor continuous close resolved in BMLL")
            raw["close_price"] = raw.get("close_price", pd.Series(np.nan, index=raw.index))
        if "close_continuous" in raw:   # auction missing on a day -> last continuous price
            raw["close_price"] = raw.close_price.fillna(raw.close_continuous)
        for c in ("arrival_price", "spread_bps", "close_auction_volume"):
            if c not in raw:
                raw[c] = np.nan
        raw = raw.rename(columns={"Date": "date"})
        prim = raw[raw.is_primary.astype(bool)].sort_values("date")
        prim = prim.assign(ret=prim.groupby("permno").close_price.transform(lambda s: np.log(s).diff()))
        cons = raw.groupby(["date", "permno"]).agg(day_volume=("volume", "sum")).reset_index()
        cons = cons.merge(raw.assign(pv=raw.vwap * raw.volume).groupby(["date", "permno"]).pv.sum()
                          .rename("pv").reset_index(), on=["date", "permno"])
        cons["day_vwap"] = cons.pv / cons.day_volume
        hist = prim.merge(cons, on=["date", "permno"])
        hist = hist.sort_values(["permno", "date"])
        grp = hist.groupby("permno")
        hist["prev_close"] = grp.close_price.shift()
        hist["sigma_daily"] = grp.ret.transform(lambda s: s.shift().rolling(20, min_periods=10).std())
        hist["adv_shares"] = grp.day_volume.transform(lambda s: s.shift().rolling(20, min_periods=10).mean())
        events = set(pd.to_datetime(list(index_event_dates)).normalize())
        stock = req[["date", "permno"]].merge(hist.rename(columns={"mic": "primary_mic"}), on=["date", "permno"],
                                              how="left")
        stock["arrival_price"] = stock.arrival_price.fillna(stock.prev_close)
        stock["is_index_event"] = stock.date.isin(events)
        venue = raw.merge(req[["date", "permno"]], on=["date", "permno"])
        venue = venue[VENUE_COLS].dropna(subset=["volume", "vwap"])
        stock = stock[STOCK_COLS]
        check_market(venue, stock)
        return venue.reset_index(drop=True), stock.reset_index(drop=True)


def anchors_from_blotter(blotter, weights=None):
    """Real price anchors for ``synthetic_market`` from a blotter.

    prev_close = decision price (split-adjusted execution basis when present),
    close_price = execution price (fills joined in the handoff file). Market cap
    (CRSP, $000 in weights.csv) from the latest snapshot on/before the trade date
    gives a liquidity scale for ADV.
    """
    b = blotter.copy()
    tcol = next(c for c in ("execution_date", "expected_execution_date") if c in b)
    pcol = "decision_price_execution_basis" if "decision_price_execution_basis" in b else "decision_price"
    a = pd.DataFrame({"date": pd.to_datetime(b[tcol]).dt.normalize(), "permno": b.permno.astype(int),
                      "prev_close": b[pcol].astype(float), "close_price": b.execution_price.astype(float)})
    a = a.drop_duplicates(["date", "permno"])
    if weights is not None:
        w = weights[["date", "permno", "market_cap"]].copy()
        w["date"] = pd.to_datetime(w.date).dt.normalize()
        w = w.sort_values("date")
        a = pd.merge_asof(a.sort_values("date"), w.rename(columns={"date": "snap"}), left_on="date",
                          right_on="snap", by="permno", direction="backward")
        a["market_cap_usd"] = a.market_cap * 1e3
        med = a.market_cap_usd.median()
        a["market_cap_usd"] = a.market_cap_usd.fillna(med)
        a = a.drop(columns=["snap", "market_cap"])
    return a.reset_index(drop=True)


def ticker_map_from_weights(weights):
    """Point-in-time PERMNO -> CRSP ticker from quarterly snapshots.

    Each snapshot's ticker is valid from a week before its reference date (before
    the Friday-auction orders are placed) until the day before the next
    snapshot's window opens, so windows never overlap. BMLL tickers may use a different share-class
    separator (BRK.B vs BRK B); check unmapped names before a BMLL pull.
    """
    w = weights[["date", "permno", "ticker"]].copy()
    w["date"] = pd.to_datetime(w.date).dt.normalize()
    start_col = pd.to_datetime(weights["reference_date"]).dt.normalize() if "reference_date" in weights else w.date
    w["valid_from"] = start_col - pd.Timedelta(days=7)
    window = w.groupby("date").valid_from.min()          # snapshot -> window start
    nxt = dict(zip(window.index[:-1], window.values[1:]))
    # Windows tile: each ends the day before the next snapshot's window opens.
    w["valid_to"] = pd.to_datetime(w.date.map(nxt)) - pd.Timedelta(days=1)
    w = w.sort_values(["permno", "valid_from"])
    return w[["permno", "ticker", "valid_from", "valid_to"]].reset_index(drop=True)
