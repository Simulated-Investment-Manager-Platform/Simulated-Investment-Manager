"""Synthetic trader and broker (child) orders from PM orders.

Strategies (trader level)
  CLOSE_AUCTION  One MOC on the primary listing. If the order exceeds
                 ``max_auction_participation`` of the *expected* closing auction
                 (ADV x historical auction share; known ex ante), the excess is
                 worked as a VWAP order over the day.
  VWAP           Continuous trading 09:30-15:50 following the intraday volume
                 curve, capped at ``max_participation`` of expected continuous
                 volume; any excess spills to the closing auction.
  ARRIVAL_IS     Front-loaded (exponential decay, Almgren-Chriss style) from the
                 open over a horizon set by ``is_participation``; excess spills
                 to the close.

Broker level: each continuous trader order is split into time buckets (curve
weights) and, within each bucket, across MICs in proportion to each venue's share
of continuous volume in ``venue_days`` (BMLL per-listing volume). Off-exchange
volume (XADF/TRF) is routed as MIDPOINT_PEG; lit venues get collared LIMIT
orders. Integer shares are allocated by largest remainder, so children always
sum exactly to their parent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .market_data import u_shaped_curve
from .schema import make_id, validate

OPEN, CLOSE, MOC_CUTOFF = "09:30", "16:00", "15:50"
DARK_MICS = {"XADF", "TRF", "DARK"}


def _alloc(total, weights):
    """Integer allocation of ``total`` proportional to ``weights`` (largest remainder)."""
    w = np.asarray(weights, float)
    if total <= 0 or w.sum() <= 0:
        return np.zeros(len(w), int)
    raw = total * w / w.sum()
    base = np.floor(raw).astype(int)
    rem = int(total - base.sum())
    if rem:
        base[np.argsort(-(raw - base), kind="stable")[:rem]] += 1
    return base


def _ts(day, hhmm):
    return pd.Timestamp(day) + pd.Timedelta(hhmm + ":00")


def trader_orders(pm, stock_days, strategy, *, max_auction_participation=.25,
                  max_participation=.10, is_participation=.15, expected_auction_share=.10,
                  expected_event_auction_share=.20):
    """One or two trader orders per PM order (main strategy + spill)."""
    s = stock_days.set_index(["date", "permno"])
    rows = []
    for o in pm.itertuples(index=False):
        key = (pd.Timestamp(o.trade_date), int(o.permno))
        if key not in s.index:
            raise ValueError(f"No market data for {key[0].date()} PERMNO={key[1]}")
        m = s.loc[key]
        share = expected_event_auction_share if m.is_index_event else expected_auction_share
        exp_auction = m.adv_shares * share
        exp_cont = m.adv_shares * (1 - share)
        q = int(o.quantity)
        day = key[0]
        if strategy == "CLOSE_AUCTION":
            main = min(q, int(np.floor(max_auction_participation * exp_auction)))
            legs = [("CLOSE_AUCTION", main, MOC_CUTOFF, CLOSE, 1.0), ("VWAP", q - main, OPEN, MOC_CUTOFF, max_participation)]
        elif strategy == "VWAP":
            main = min(q, int(np.floor(max_participation * exp_cont)))
            legs = [("VWAP", main, OPEN, MOC_CUTOFF, max_participation), ("CLOSE_AUCTION", q - main, MOC_CUTOFF, CLOSE, 1.0)]
        elif strategy == "ARRIVAL_IS":
            main = min(q, int(np.floor(is_participation * exp_cont)))
            frac = min(1., max(30 / 390, main / max(is_participation * exp_cont, 1)))
            end = (_ts(day, OPEN) + pd.Timedelta(minutes=int(np.ceil(frac * 380)))).strftime("%H:%M")
            legs = [("ARRIVAL_IS", main, OPEN, min(end, MOC_CUTOFF), is_participation),
                    ("CLOSE_AUCTION", q - main, MOC_CUTOFF, CLOSE, 1.0)]
        else:
            raise ValueError(f"Unknown strategy {strategy}")
        for leg, (strat, qty, a, b, cap) in enumerate(legs):
            if qty <= 0:
                continue
            rows.append({"trader_order_id": make_id(o.pm_order_id, strategy, leg),
                         "pm_order_id": o.pm_order_id, "permno": int(o.permno),
                         "strategy": strat, "side": o.side,
                         "quantity": int(qty), "trade_date": day, "start_ts": _ts(day, a),
                         "end_ts": _ts(day, b), "max_participation": float(cap),
                         "limit_price": np.nan, "primary_mic": m.primary_mic,
                         "parent_strategy": strategy, "is_spill": leg > 0})
    out = pd.DataFrame(rows)
    validate(out, "trader_orders")
    return out


def _buckets(start, end, n_bins=13, kind="VWAP", decay=3.0):
    """Half-hour buckets inside [start, end) with schedule weights."""
    day = pd.Timestamp(start).normalize()
    edges = pd.date_range(_ts(day, OPEN), _ts(day, CLOSE), periods=n_bins + 1)
    curve = u_shaped_curve(n_bins).to_numpy()
    out = []
    for i in range(n_bins):
        a, b = max(edges[i], pd.Timestamp(start)), min(edges[i + 1], pd.Timestamp(end))
        if b <= a:
            continue
        frac = (b - a) / (edges[i + 1] - edges[i])
        out.append((a, b, curve[i] * frac))
    if not out:
        return []
    if kind == "ARRIVAL_IS":
        t = np.arange(len(out)) / max(len(out), 1)
        out = [(a, b, w * np.exp(-decay * x)) for (a, b, w), x in zip(out, t)]
    return out


def broker_orders(trader, venue_days, stock_days, *, n_bins=13, collar_sigmas=3.0, is_decay=3.0):
    """Child orders for every trader order."""
    v = venue_days.copy()
    v = v[v.volume > 0]
    venues = {k: g for k, g in v.groupby(["date", "permno"])}
    s = stock_days.set_index(["date", "permno"])
    rows = []
    for t in trader.itertuples(index=False):
        permno = int(t.permno)
        day = pd.Timestamp(t.trade_date)
        m = s.loc[(day, permno)]
        sign = 1 if t.side == "BUY" else -1
        collar = float(m.prev_close * (1 + sign * collar_sigmas * m.sigma_daily))
        if t.strategy == "CLOSE_AUCTION":
            rows.append({"child_order_id": make_id(t.trader_order_id, "MOC"),
                         "trader_order_id": t.trader_order_id, "mic": t.primary_mic,
                         "order_type": "MOC", "tif": "AT_CLOSE", "side": t.side,
                         "quantity": int(t.quantity), "limit_price": np.nan,
                         "submit_ts": t.start_ts, "bucket_start": t.start_ts, "bucket_end": t.end_ts})
            continue
        bk = _buckets(t.start_ts, t.end_ts, n_bins, t.strategy, is_decay)
        per_bucket = _alloc(t.quantity, [w for _, _, w in bk])
        g = venues.get((day, permno))
        if g is None or g.volume.sum() <= 0:
            raise ValueError(f"No venue volume for {day.date()} PERMNO={permno}")
        mics, vol = g.mic.to_numpy(), g.volume.to_numpy(float)
        for (a, b, _), q in zip(bk, per_bucket):
            for mic, cq in zip(mics, _alloc(q, vol)):
                if cq <= 0:
                    continue
                dark = mic in DARK_MICS
                rows.append({"child_order_id": make_id(t.trader_order_id, a, mic),
                             "trader_order_id": t.trader_order_id, "mic": mic,
                             "order_type": "MIDPOINT_PEG" if dark else "LIMIT", "tif": "GTT",
                             "side": t.side, "quantity": int(cq), "limit_price": collar,
                             "submit_ts": a, "bucket_start": a, "bucket_end": b})
    out = pd.DataFrame(rows)
    validate(out, "broker_orders")
    return out


def generate(pm, venue_days, stock_days, strategy, **kw):
    """PM orders -> (trader_orders, broker_orders) for one strategy."""
    tkw = {k: kw.pop(k) for k in list(kw) if k in (
        "max_auction_participation", "max_participation", "is_participation",
        "expected_auction_share", "expected_event_auction_share")}
    trader = trader_orders(pm, stock_days, strategy, **tkw)
    broker = broker_orders(trader, venue_days, stock_days, **kw)
    return trader, broker
