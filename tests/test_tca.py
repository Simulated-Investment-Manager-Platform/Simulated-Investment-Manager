import unittest

import numpy as np
import pandas as pd

from tca.schema import pm_orders_from_blotter, validate, validate_links
from tca.market_data import synthetic_market, BMLLSource, u_shaped_curve
from tca.order_generator import generate, _alloc
from tca.execution import ImpactModel, simulate_fills
from tca.analysis import run_strategies, rebalance_costs, execution_tracking, weighted, COST_COLS


def blotter(n=40, seed=0, dates=("2025-03-21", "2025-06-20")):
    rng = np.random.default_rng(seed)
    rows = []
    for d in dates:
        trade = pd.Timestamp(d)
        for p in range(1, n + 1):
            px = float(rng.uniform(20, 400))
            rows.append({"order_id": f"{d}-{p}", "decision_date": trade - pd.offsets.BDay(1),
                         "expected_execution_date": trade, "permno": p,
                         "signed_quantity": int(rng.choice([-1, 1]) * rng.integers(100, 20_000)),
                         "decision_price": px, "execution_price": px * np.exp(rng.normal(0, .015)),
                         "reason": "REBALANCE"})
    return pd.DataFrame(rows)


def market_for(b, seed=1):
    anchors = b.rename(columns={"expected_execution_date": "date", "decision_price": "prev_close",
                                "execution_price": "close_price"})[["date", "permno", "prev_close", "close_price"]]
    return synthetic_market(anchors, seed=seed, index_event_dates=b.expected_execution_date.unique())


class SchemaTests(unittest.TestCase):
    def test_blotter_to_pm_orders(self):
        pm = pm_orders_from_blotter(blotter())
        self.assertTrue(validate(pm, "pm_orders"))
        self.assertTrue(pm.benchmark.eq("CLOSE").all())
        bad = pm.copy(); bad.loc[0, "side"] = "SHORT"
        with self.assertRaisesRegex(ValueError, "side invalid"):
            validate(bad, "pm_orders")

    def test_links_catch_over_allocation_and_orphans(self):
        b = blotter(5)
        pm = pm_orders_from_blotter(b)
        v, s = market_for(b)
        trader, broker = generate(pm, v, s, "VWAP")
        self.assertTrue(validate_links(pm, trader, broker))
        over = broker.copy(); over.loc[over.index[0], "quantity"] += 10**7
        with self.assertRaisesRegex(ValueError, "over-allocated"):
            validate_links(pm, trader, over)
        orphan = trader.copy(); orphan.loc[orphan.index[0], "pm_order_id"] = "nope"
        with self.assertRaisesRegex(ValueError, "orphan"):
            validate_links(pm, orphan, broker)


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.b = blotter()
        self.pm = pm_orders_from_blotter(self.b)
        self.v, self.s = market_for(self.b)

    def test_alloc_is_exact(self):
        self.assertEqual(_alloc(1001, [1, 1, 1]).sum(), 1001)
        self.assertEqual(_alloc(0, [1, 2]).tolist(), [0, 0])

    def test_children_conserve_quantity_for_every_strategy(self):
        for strat in ("CLOSE_AUCTION", "VWAP", "ARRIVAL_IS"):
            trader, broker = generate(self.pm, self.v, self.s, strat)
            validate_links(self.pm, trader, broker)
            per_pm = trader.groupby("pm_order_id").quantity.sum()
            self.assertTrue(per_pm.eq(self.pm.set_index("pm_order_id").quantity).all(), strat)
            per_t = broker.groupby("trader_order_id").quantity.sum()
            self.assertTrue(per_t.eq(trader.set_index("trader_order_id").quantity).all(), strat)

    def test_vwap_children_span_mics_and_follow_volume(self):
        trader, broker = generate(self.pm, self.v, self.s, "VWAP")
        self.assertGreater(broker.mic.nunique(), 5)
        dark = broker[broker.mic.eq("XADF")]
        self.assertTrue(dark.order_type.eq("MIDPOINT_PEG").all())
        share = broker[broker.order_type.ne("MOC")].groupby("mic").quantity.sum()
        share /= share.sum()
        vol = self.v.groupby("mic").volume.sum() / self.v.volume.sum()
        self.assertLess((share - vol.reindex(share.index)).abs().max(), .05)

    def test_large_orders_spill_to_close(self):
        pm = self.pm.copy()
        big = pm.index[0]
        adv = self.s.set_index(["date", "permno"]).adv_shares
        pm.loc[big, "quantity"] = int(adv.loc[(pm.trade_date[big], pm.permno[big])] * .5)
        trader, _ = generate(pm, self.v, self.s, "VWAP", max_participation=.1)
        legs = trader[trader.pm_order_id.eq(pm.pm_order_id[big])]
        self.assertEqual(set(legs.strategy), {"VWAP", "CLOSE_AUCTION"})
        self.assertEqual(legs.quantity.sum(), pm.quantity[big])


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.b = blotter()
        self.pm = pm_orders_from_blotter(self.b)
        self.v, self.s = market_for(self.b)

    def test_auction_fills_near_close_with_positive_cost(self):
        r = run_strategies(self.pm, self.v, self.s, ["CLOSE_AUCTION"])["CLOSE_AUCTION"]["orders"]
        pure = r[r.spill_shares.eq(0)]
        self.assertGreater(len(pure), 10)
        self.assertTrue((pure.vs_close_bps > 0).all())   # impact + fees only
        self.assertLess(np.average(r.vs_close_bps, weights=r.notional_usd), 20)

    def test_zero_impact_reduces_to_reference_prices(self):
        m = ImpactModel(eta=0, auction_eta=0, cross_fraction=0, fee_take=0, fee_make=0,
                        fee_auction=0, fee_dark=0)
        out = run_strategies(self.pm, self.v, self.s, model=m, max_participation=1.,
                             max_auction_participation=10., is_participation=1.)
        auc = out["CLOSE_AUCTION"]["orders"]
        self.assertLess(auc.vs_close_bps.abs().max(), 1e-8)
        isr = out["ARRIVAL_IS"]["orders"]
        main = out["ARRIVAL_IS"]["trader"]
        no_spill = isr.pm_order_id.isin(main.loc[~main.is_spill.astype(bool)].pm_order_id) & isr.spill_shares.eq(0)
        self.assertLess(isr.loc[no_spill, "vs_arrival_bps"].abs().max(), 1e-8)
        vw = out["VWAP"]["orders"]
        ok = vw.spill_shares.eq(0)
        self.assertLess(vw.loc[ok, "vs_vwap_bps"].abs().max(), .2)   # share rounding only

    def test_impact_grows_with_size(self):
        small = self.pm.copy(); big = self.pm.copy(); big["quantity"] *= 4; big["notional_usd"] *= 4
        a = run_strategies(small, self.v, self.s, ["CLOSE_AUCTION"])["CLOSE_AUCTION"]["orders"]
        c = run_strategies(big, self.v, self.s, ["CLOSE_AUCTION"])["CLOSE_AUCTION"]["orders"]
        self.assertGreater(c.vs_close_bps.mean(), a.vs_close_bps.mean())


class AnalysisTests(unittest.TestCase):
    def test_tracking_error_identity_and_weighting(self):
        b = blotter()
        pm = pm_orders_from_blotter(b)
        v, s = market_for(b)
        out = run_strategies(pm, v, s)
        orders = pd.concat([o["orders"] for o in out.values()])
        nav = pd.DataFrame({"date": pm.decision_date.unique(), "nav": 1e10})
        reb = rebalance_costs(orders, nav)
        te = execution_tracking(reb, years=.5)
        c = reb[reb.strategy.eq("VWAP")].cost_vs_close_bps_nav
        self.assertAlmostEqual(te.loc["VWAP", "tracking_error_bps_pa"], np.sqrt((c ** 2).sum() / .5))
        summ = weighted(orders, COST_COLS, "strategy")
        g = orders[orders.strategy.eq("VWAP")]
        self.assertAlmostEqual(summ.loc["VWAP", "vs_close_bps"],
                               (g.vs_close_bps * g.notional_usd).sum() / g.notional_usd.sum())
        # The auction tracks the close benchmark far more tightly order by order.
        # (At NAV level VWAP drift partly nets across a rebalance's buys and sells,
        # so the portfolio-level comparison is an empirical question, not a test.)
        disp = orders.groupby("strategy").apply(
            lambda g: np.average(g.vs_close_bps.abs(), weights=g.notional_usd), include_groups=False)
        self.assertLess(disp["CLOSE_AUCTION"] * 3, disp["VWAP"])


class FakeBMLL:
    """Minimal stand-in for bmll.reference / bmll.time_series."""
    def __init__(self):
        self.reference, self.time_series = self, self
        self.names = {"ClosingAuctionPrice|Executed": "ClosingAuctionPrice", "OpeningAuctionPrice|Executed":
                      "OpeningAuctionPrice", "VWAP": "VWAP", "Spread|RelTWA": "Spread",
                      "TradeVolume|Lit": "TradeVolumeLit", "TradeVolume|Dark": "TradeVolumeDark",
                      "Close|Executed": "Close"}

    def query(self, **kw):
        if "Ticker" in kw:   # reference
            days = pd.bdate_range(kw["start_date"], kw["end_date"])
            rows = [{"Ticker": "AAA", "MIC": mic, "ListingId": lid, "IsPrimary": mic == "XNYS",
                     "IsAlive": True, "Date": d}
                    for mic, lid in (("XNYS", 1), ("ARCX", 2), ("BATS", 3), ("@SIP", 9)) for d in days]
            return pd.DataFrame(rows)
        name = kw["metric"]
        if any(n not in self.names for n in name):
            raise KeyError(name)
        days = pd.bdate_range(kw["start_date"], kw["end_date"])
        rng = np.random.default_rng(0)
        rows = []
        for lid in kw["object_ids"]:
            px = 100 * np.exp(np.cumsum(rng.normal(0, .01, len(days))))
            for d, p in zip(days, px):
                r = {"ObjectId": lid, "Date": d}
                for n in name:
                    col = self.names[n]
                    r[col] = {"ClosingAuctionPrice": p, "OpeningAuctionPrice": p * .999, "VWAP": p * 1.0005,
                              "Spread": 2.5, "TradeVolumeLit": 1e6 / lid, "TradeVolumeDark": 5e5 / lid,
                              "Close": p}[col]
                rows.append(r)
        return pd.DataFrame(rows)


class BMLLAdapterTests(unittest.TestCase):
    def test_market_tables_from_fake_bmll(self):
        src = BMLLSource(FakeBMLL(), mics=["XNYS", "ARCX", "BATS"])
        tm = pd.DataFrame({"permno": [7], "ticker": ["AAA"], "valid_from": ["2020-01-01"], "valid_to": [None]})
        venue, stock = src.market(pd.DataFrame({"date": ["2025-03-21"], "permno": [7]}), tm,
                                  index_event_dates=["2025-03-21"])
        self.assertEqual(sorted(venue.mic), ["ARCX", "BATS", "XNYS"])
        self.assertEqual(stock.primary_mic.iloc[0], "XNYS")
        self.assertTrue(stock.is_index_event.iloc[0])
        self.assertGreater(stock.sigma_daily.iloc[0], 0)
        self.assertNotIn("close_auction_volume", src.resolved)   # absent metric tolerated
        self.assertEqual(src.resolved["volume_lit"][0], "TradeVolume|Lit")


if __name__ == "__main__":
    unittest.main()


class LoaderTests(unittest.TestCase):
    def test_ticker_windows_tile_and_join_fills(self):
        from tca.market_data import ticker_map_from_weights
        from tca.schema import join_orders_fills
        w = pd.DataFrame({"date": ["2025-03-24", "2025-03-24", "2025-06-23"], "permno": [1, 2, 1],
                          "ticker": ["AAA", "BBB", "AAB"], "reference_date": ["2025-03-21", "2025-03-21", "2025-06-20"]})
        m = ticker_map_from_weights(w)
        a = m[m.permno.eq(1)].reset_index(drop=True)
        self.assertEqual(a.valid_to[0] + pd.Timedelta(days=1), a.valid_from[1])
        self.assertTrue(pd.isna(a.valid_to[1]))
        o = pd.DataFrame({"order_id": ["x", "y"], "decision_date": ["2025-03-20"] * 2, "permno": [1, 2]})
        f = pd.DataFrame({"order_id": ["x", "y"], "execution_date": ["2025-03-21"] * 2, "execution_price": [10., 20.]})
        self.assertEqual(join_orders_fills(o, f).execution_price.tolist(), [10., 20.])
        with self.assertRaisesRegex(ValueError, "without a fill"):
            join_orders_fills(o, f.iloc[:1])
