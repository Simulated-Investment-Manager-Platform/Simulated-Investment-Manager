"""Fill simulation for child orders: closing auction, VWAP and arrival-plus-impact.

Price models (s = +1 buy / -1 sell, sigma = daily volatility):

  MOC child          close_auction_price * (1 + s * I_auc)
                     I_auc = auction_eta * sigma * sqrt(Q_auction / V_auction)
  VWAP child on MIC  venue_vwap * (1 + s * (cross * spread/2 + I_cont))
  ARRIVAL_IS child   arrival_price * (1 + s * (cross * spread/2 + I_cont))
                     I_cont = eta * sigma * sqrt(Q_cont / (V_cont * window_volume_share))

where Q is the order's total shares in that mechanism (impact is a property of
the parent, shared by its children) and V is the realised volume. Midpoint/dark
children pay no spread. ``cross`` is the fraction of lit volume taken
aggressively. The square-root law and its coefficients are standard
literature values (eta ~ 0.5-1). They are NOT calibrated to BMLL yet: the
notebook exposes them so they can be fitted to BMLL markouts.

Fees are per share by liquidity type (US access-fee scale).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .market_data import u_shaped_curve
from .order_generator import DARK_MICS
from .schema import make_id, validate


@dataclass
class ImpactModel:
    eta: float = 0.6                  # continuous sqrt-law coefficient
    auction_eta: float = 0.25         # closing-auction coefficient
    cross_fraction: float = 0.5       # share of lit child volume that crosses the spread
    default_auction_share: float = .10  # if BMLL auction volume is missing
    fee_take: float = .0030           # $/share, removing liquidity (lit)
    fee_make: float = -.0020          # rebate when adding liquidity
    fee_auction: float = .0010
    fee_dark: float = .0010

    def as_dict(self):
        return asdict(self)


def _window_share(start, end, n_bins=13):
    """Share of continuous volume between two times (default curve)."""
    day = pd.Timestamp(start).normalize()
    edges = pd.date_range(day + pd.Timedelta("09:30:00"), day + pd.Timedelta("16:00:00"), periods=n_bins + 1)
    curve = u_shaped_curve(n_bins).to_numpy()
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    tot = 0.
    for i in range(n_bins):
        lo, hi = max(edges[i], a), min(edges[i + 1], b)
        if hi > lo:
            tot += curve[i] * (hi - lo) / (edges[i + 1] - edges[i])
    return max(tot, 1e-6)


def simulate_fills(trader, broker, venue_days, stock_days, model=None):
    model = model or ImpactModel()
    s = stock_days.set_index(["date", "permno"])
    vv = venue_days.set_index(["date", "permno", "mic"]).vwap
    t = trader.set_index("trader_order_id")
    b = broker.join(t[["permno", "trade_date", "strategy", "start_ts", "end_ts"]], on="trader_order_id")
    b["date"] = pd.to_datetime(b.trade_date).dt.normalize()
    m = s.reindex(pd.MultiIndex.from_frame(b[["date", "permno"]]))
    m.index = b.index
    sign = np.where(b.side.eq("BUY"), 1., -1.)
    sigma = m.sigma_daily.to_numpy(float)
    auction_vol = m.close_auction_volume.fillna(model.default_auction_share * m.day_volume).to_numpy(float)
    cont_vol = (m.day_volume.to_numpy(float) - auction_vol).clip(min=1)
    # Parent-level quantities in each mechanism (per trader order).
    q_parent = b.trader_order_id.map(t.quantity).to_numpy(float)
    is_moc = b.order_type.eq("MOC").to_numpy()
    window = np.array([_window_share(a, e) for a, e in zip(b.start_ts, b.end_ts)])
    i_auc = model.auction_eta * sigma * np.sqrt(q_parent / np.maximum(auction_vol, 1))
    i_cont = model.eta * sigma * np.sqrt(q_parent / (cont_vol * window))
    dark = b.mic.isin(DARK_MICS).to_numpy()
    half_spread = m.spread_bps.fillna(m.spread_bps.median()).to_numpy(float) / 2e4
    spread_cost = np.where(dark, 0., model.cross_fraction * half_spread)
    venue_vwap = vv.reindex(pd.MultiIndex.from_frame(b[["date", "permno", "mic"]])).to_numpy(float)
    venue_vwap = np.where(np.isfinite(venue_vwap), venue_vwap, m.day_vwap.to_numpy(float))
    base = np.where(is_moc, m.close_price.to_numpy(float),
                    np.where(b.strategy.eq("ARRIVAL_IS"), m.arrival_price.to_numpy(float), venue_vwap))
    impact = np.where(is_moc, i_auc, spread_cost + i_cont)
    price = base * (1 + sign * impact)
    # Respect the protective collar on continuous children (unfilled beyond it).
    lim = b.limit_price.to_numpy(float)
    breach = ~is_moc & np.isfinite(lim) & ((sign > 0) & (price > lim) | (sign < 0) & (price < lim))
    qty = np.where(breach, 0, b.quantity.to_numpy(int))
    fee_ps = np.where(is_moc, model.fee_auction, np.where(
        dark, model.fee_dark,
        model.cross_fraction * model.fee_take + (1 - model.cross_fraction) * model.fee_make))
    liquidity = np.where(is_moc, "AUCTION", np.where(dark, "MIDPOINT", "REMOVE"))
    fills = pd.DataFrame({
        "fill_id": [make_id(c, "F") for c in b.child_order_id], "child_order_id": b.child_order_id.values,
        "mic": b.mic.values, "fill_ts": np.where(is_moc, b.bucket_end, b.bucket_start + (b.bucket_end - b.bucket_start) / 2),
        "side": b.side.values, "quantity": qty, "price": price, "liquidity": liquidity,
        "fee_usd": qty * fee_ps, "impact_bps": 1e4 * impact,
        "trader_order_id": b.trader_order_id.values, "permno": b.permno.values})
    fills["fill_ts"] = pd.to_datetime(fills.fill_ts)
    fills = fills[fills.quantity > 0].reset_index(drop=True)
    validate(fills, "fills")
    return fills
