"""Order-level TCA, notional-weighted summaries and execution tracking error.

Sign convention: positive bps = cost to the fund (buy above / sell below the
reference). For an index fund the key reference is the *closing-auction price
of the rebalance day*, because that is where the index rebalances; cost vs close
is therefore the tracking difference created by execution.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .execution import ImpactModel, simulate_fills
from .order_generator import generate

STRATEGIES = ("CLOSE_AUCTION", "VWAP", "ARRIVAL_IS")


def order_tca(pm, trader, fills, stock_days, label):
    """One row per PM order. Unfilled shares are charged at the close (opportunity cost)."""
    f = fills.merge(trader[["trader_order_id", "pm_order_id", "strategy", "is_spill"]], on="trader_order_id")
    f["value"] = f.quantity * f.price
    g = f.groupby("pm_order_id").agg(filled=("quantity", "sum"), value=("value", "sum"),
                                     fees_usd=("fee_usd", "sum"))
    spill = f[f.is_spill].groupby("pm_order_id").quantity.sum()
    auction = f[f.liquidity.eq("AUCTION")].groupby("pm_order_id").quantity.sum()
    x = pm.set_index("pm_order_id").join(g).join(spill.rename("spill_shares")).join(
        auction.rename("auction_shares"))
    x[["filled", "value", "fees_usd", "spill_shares", "auction_shares"]] = x[
        ["filled", "value", "fees_usd", "spill_shares", "auction_shares"]].fillna(0)
    m = stock_days.set_index(["date", "permno"])
    ref = m.reindex(pd.MultiIndex.from_arrays([x.trade_date, x.permno])).reset_index(drop=True)
    ref.index = x.index
    x = x.join(ref[["prev_close", "arrival_price", "close_price", "day_vwap", "sigma_daily",
                    "adv_shares", "day_volume", "close_auction_volume"]])
    s = np.where(x.side.eq("BUY"), 1., -1.)
    x["avg_price"] = np.where(x.filled > 0, x.value / x.filled.replace(0, np.nan), np.nan)
    x["unfilled"] = x.quantity - x.filled
    x["notional_usd"] = x.quantity * x.decision_price
    # Executed cost + opportunity cost on unfilled shares, both vs reference.
    def cost_bps(refcol):
        ex = s * x.filled * (x.avg_price.fillna(x[refcol]) - x[refcol])
        opp = s * x.unfilled * (x.close_price - x[refcol])
        return 1e4 * (ex + opp) / (x.quantity * x[refcol])
    x["is_vs_decision_bps"] = cost_bps("decision_price")
    x["vs_arrival_bps"] = cost_bps("arrival_price")
    x["vs_close_bps"] = cost_bps("close_price")
    x["vs_vwap_bps"] = cost_bps("day_vwap")
    x["fees_bps"] = 1e4 * x.fees_usd / x.notional_usd
    x["cost_vs_close_usd"] = x.vs_close_bps / 1e4 * x.quantity * x.close_price + x.fees_usd
    x["is_usd"] = x.is_vs_decision_bps / 1e4 * x.notional_usd + x.fees_usd
    x["pct_adv"] = x.quantity / x.adv_shares
    x["strategy"] = label
    return x.reset_index()


def weighted(df, value_cols, by, weight="notional_usd"):
    """Notional-weighted means of ``value_cols`` per group, plus totals."""
    def agg(g):
        w = g[weight]
        out = {c: np.average(g[c], weights=w) for c in value_cols}
        out.update(orders=len(g), notional_usd=w.sum(),
                   cost_vs_close_usd=g.cost_vs_close_usd.sum(), fees_usd=g.fees_usd.sum())
        return pd.Series(out)
    return df.groupby(by).apply(agg, include_groups=False)


COST_COLS = ["vs_close_bps", "is_vs_decision_bps", "vs_arrival_bps", "vs_vwap_bps", "fees_bps"]


def run_strategies(pm, venue_days, stock_days, strategies=STRATEGIES, model=None, **gen_kw):
    """Generate, fill and measure each strategy on the same PM orders."""
    model = model or ImpactModel()
    out = {}
    for strat in strategies:
        trader, broker = generate(pm, venue_days, stock_days, strat, **dict(gen_kw))
        fills = simulate_fills(trader, broker, venue_days, stock_days, model)
        out[strat] = {"trader": trader, "broker": broker, "fills": fills,
                      "orders": order_tca(pm, trader, fills, stock_days, strat)}
    return out


def rebalance_costs(orders, nav):
    """Per strategy x trade date: $ cost vs close and as bps of fund NAV.

    nav: date, nav (fund NAV, taken on the decision date, i.e. known before trading).
    """
    n = nav[["date", "nav"]].copy()
    n["date"] = pd.to_datetime(n.date).dt.normalize()
    g = orders.groupby(["strategy", "trade_date", "decision_date"]).agg(
        orders=("pm_order_id", "size"), notional_usd=("notional_usd", "sum"),
        cost_vs_close_usd=("cost_vs_close_usd", "sum"), is_usd=("is_usd", "sum"),
        fees_usd=("fees_usd", "sum")).reset_index()
    g = g.merge(n.rename(columns={"date": "decision_date"}), on="decision_date", how="left")
    if g.nav.isna().any():
        raise ValueError("NAV missing on a decision date")
    g["turnover_pct_nav"] = 100 * g.notional_usd / g.nav
    g["cost_vs_close_bps_nav"] = 1e4 * g.cost_vs_close_usd / g.nav
    g["is_bps_nav"] = 1e4 * g.is_usd / g.nav
    g["cost_vs_close_bps_traded"] = 1e4 * g.cost_vs_close_usd / g.notional_usd
    return g


def execution_tracking(reb, years):
    """Annualised tracking difference and tracking error caused by execution.

    Execution adds an active return of -c_r (c_r = cost vs close in NAV bps) on
    each rebalance day and ~0 otherwise. Daily TE x sqrt(252) is then
    sqrt(sum c_r^2 / years); tracking difference is -sum c_r / years.
    ``te_dispersion_bps`` (std of c_r x sqrt(rebalances/yr)) isolates the
    unpredictable part, since a constant cost is drag, not error.
    """
    rows = []
    for strat, g in reb.groupby("strategy"):
        c = g.cost_vs_close_bps_nav.to_numpy(float)
        per_year = len(c) / years
        rows.append({"strategy": strat, "rebalances": len(c),
                     "mean_cost_bps_nav": c.mean(),
                     "tracking_difference_bps_pa": -c.sum() / years,
                     "tracking_error_bps_pa": np.sqrt((c ** 2).sum() / years),
                     "te_dispersion_bps_pa": c.std(ddof=1) * np.sqrt(per_year) if len(c) > 1 else np.nan,
                     "worst_rebalance_bps_nav": c.max(),
                     "notional_weighted_vs_close_bps":
                         1e4 * g.cost_vs_close_usd.sum() / g.notional_usd.sum(),
                     "notional_weighted_is_bps": 1e4 * g.is_usd.sum() / g.notional_usd.sum()})
    return pd.DataFrame(rows).set_index("strategy")
