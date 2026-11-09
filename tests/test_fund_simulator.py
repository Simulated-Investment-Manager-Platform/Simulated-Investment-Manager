import unittest

import numpy as np
import pandas as pd

from research_pipeline import simulate
from fund_simulator import rebalance_schedule, simulate_fund, synthetic_flows


def third_friday(year, month):
    d = pd.Timestamp(year, month, 15)
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


def world(seed=0, n=6, months=((2026, 3), (2026, 6))):
    """Random multi-stock world with quarterly snapshots on the Monday after the
    third Friday, an index add and a delete, and one split."""
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range("2026-03-02", "2026-07-31")
    px = []
    for p in range(1, n + 1):
        path = 50 * np.exp(np.cumsum(rng.normal(0, .015, len(cal))))
        px.append(pd.DataFrame({"date": cal, "permno": p, "close_price": path.round(2),
                                "price_flag": "TR"}))
    prices = pd.concat(px, ignore_index=True)
    rows = []
    for i, (y, m) in enumerate(months):
        ref = third_friday(y, m)
        snap = cal[cal.get_loc(ref) + 1]
        members = list(range(1, n)) if i == 0 else list(range(2, n + 1))  # drop 1, add n
        caps = rng.uniform(1, 10, len(members))
        for p, c in zip(members, caps):
            rows.append({"date": snap, "reference_date": ref, "permno": p,
                         "cap_weight_proxy": c / caps.sum()})
    weights = pd.DataFrame(rows)
    events = pd.DataFrame(columns=["source_event_id", "permno", "event_type", "ex_date",
                                   "pay_date", "split_ratio", "cash_per_share", "currency",
                                   "requires_review"])
    return weights, prices, cal, events


class RegressionTests(unittest.TestCase):
    def test_next_session_matches_original_simulate(self):
        for seed in range(3):
            w, p, c, e = world(seed)
            start = w.date.min()
            old = simulate(w, p, c, e, start, c[-1], initial_cash=1e7, cash_buffer_bps=10)
            new = simulate_fund(w, p, c, e, start, c[-1], initial_cash=1e7, cash_buffer_bps=10,
                                execution_timing="next_session")
            pd.testing.assert_frame_equal(old["daily_nav"], new["daily_nav"])
            pd.testing.assert_frame_equal(old["daily_holdings"], new["daily_holdings"])
            cols = list(old["fills"].columns)
            pd.testing.assert_frame_equal(old["fills"], new["fills"][cols])
            cols = list(old["orders"].columns)
            pd.testing.assert_frame_equal(old["orders"], new["orders"][cols])


class ReferenceCloseTests(unittest.TestCase):
    def test_schedule_is_thursday_decision_friday_fill(self):
        w, _, c, _ = world()
        s = rebalance_schedule(w, c, "reference_close")
        self.assertTrue((s.execution_date == s.reference_date).all())
        self.assertTrue((s.execution_date.dt.day_name() == "Friday").all())
        self.assertTrue((s.decision_date.dt.day_name() == "Thursday").all())
        self.assertTrue((s.snapshot_date.dt.day_name() == "Monday").all())

    def test_fills_at_reference_close_track_index_shares(self):
        w, p, c, e = world(1)
        s = rebalance_schedule(w, c, "reference_close")
        r = simulate_fund(w, p, c, e, s.decision_date.iloc[0], c[-1], initial_cash=1e9,
                          cash_buffer_bps=10)
        fills = r["fills"]
        self.assertEqual(set(fills.execution_date), set(s.reference_date))
        self.assertEqual(set(r["orders"].reason), {"INITIAL", "REBALANCE"})
        # The deleted name is fully sold and the added name bought at the Friday close.
        second = s.reference_date.iloc[1]
        h = r["daily_holdings"]
        held = set(h.loc[h.date.eq(second), "permno"])
        self.assertNotIn(1, held)
        self.assertIn(6, held)
        # At the Friday close the equity weights equal index weights up to share
        # rounding and buy scaling from Thursday->Friday drift; the old Monday-
        # decision/Tuesday-fill timing still holds the pre-rebalance book then.
        snap = w[w.date.eq(s.snapshot_date.iloc[1])].set_index("permno").cap_weight_proxy

        def gap(result):
            hh = result["daily_holdings"]
            day = hh[hh.date.eq(second)].set_index("permno")
            mv = day.shares * day.close_price
            return (mv / mv.sum()).sub(snap, fill_value=0).abs().sum()
        old = simulate_fund(w, p, c, e, w.date.min(), c[-1], initial_cash=1e9,
                            execution_timing="next_session")
        self.assertLess(gap(r), 5e-4)
        self.assertGreater(gap(old), 50 * gap(r))

    def test_split_between_decision_and_reference(self):
        w, p, c, e = world(2)
        s = rebalance_schedule(w, c, "reference_close")
        ref = s.reference_date.iloc[1]
        p = p.copy()
        p.loc[p.permno.eq(3) & p.date.ge(ref), "close_price"] /= 2
        e = pd.DataFrame([{"source_event_id": "s", "permno": 3, "event_type": "STOCK_SPLIT",
                           "ex_date": ref, "pay_date": pd.NaT, "split_ratio": 2.,
                           "cash_per_share": np.nan, "currency": "USD", "requires_review": False}])
        r = simulate_fund(w, p, c, e, s.decision_date.iloc[0], c[-1], initial_cash=1e9)
        h = r["daily_holdings"]
        snap = w[w.date.eq(s.snapshot_date.iloc[1])].set_index("permno").cap_weight_proxy
        day = h[h.date.eq(ref)].set_index("permno")
        mv = day.shares * day.close_price
        self.assertLess(((mv / mv.sum()) - snap).abs().max(), 5e-4)

    def test_rejects_start_after_first_decision(self):
        w, p, c, e = world()
        with self.assertRaisesRegex(ValueError, "Reference date .* not a calendar session"):
            simulate_fund(w, p, c, e, w.date.min(), c[-1])


class FlowTests(unittest.TestCase):
    def run_flows(self, flows, **kw):
        w, p, c, e = world(3)
        s = rebalance_schedule(w, c, "reference_close")
        return simulate_fund(w, p, c, e, s.decision_date.iloc[0], c[-1], initial_cash=1e9,
                             cash_buffer_bps=10, flows=flows, **kw)

    def test_flows_do_not_create_returns(self):
        _, _, c, _ = world(3)
        flows = synthetic_flows(c[c >= pd.Timestamp("2026-03-19")], seed=1,
                                daily_sigma_bps=20, lumpy_prob=.05)
        r = self.run_flows(flows)
        nav = r["daily_nav"]
        # Units are issued/cancelled at pre-flow NAV: unit NAV is unchanged by the flow itself.
        self.assertTrue(np.allclose(nav.nav, nav.fund_units * nav.unit_nav))
        self.assertGreater(nav.external_flow.ne(0).sum(), 50)
        self.assertTrue((nav.cash >= 0).all())
        self.assertTrue({"INFLOW", "OUTFLOW"} & set(r["orders"].reason))

    def test_large_redemption_is_funded_by_sells_on_dealing_day(self):
        _, _, c, _ = world(3)
        deal = pd.Timestamp("2026-04-15")
        r = self.run_flows(pd.DataFrame({"date": [deal], "flow_bps": [-500.]}))
        nav = r["daily_nav"].set_index("date")
        self.assertLess(nav.loc[deal, "external_flow"], 0)
        sells = r["fills"][r["fills"].reason.eq("OUTFLOW")]
        self.assertTrue(sells.execution_date.eq(deal).all())
        self.assertGreater(-(sells.signed_filled * sells.execution_price).sum(),
                           -nav.loc[deal, "external_flow"] * .95)
        self.assertGreaterEqual(nav.loc[deal, "cash"], 0)

    def test_small_flows_stay_in_cash(self):
        _, _, c, _ = world(3)
        r = self.run_flows(pd.DataFrame({"date": [pd.Timestamp("2026-04-15")], "flow_bps": [5.]}),
                           flow_band_bps=25)
        self.assertFalse(r["orders"].reason.isin(["INFLOW", "OUTFLOW"]).any())

    def test_first_session_flow_rejected(self):
        w, p, c, e = world(3)
        s = rebalance_schedule(w, c, "reference_close")
        with self.assertRaisesRegex(ValueError, "first session"):
            simulate_fund(w, p, c, e, s.decision_date.iloc[0], c[-1],
                          flows=pd.DataFrame({"date": [s.decision_date.iloc[0]], "amount": [1e6]}))


if __name__ == "__main__":
    unittest.main()


class ReferenceClosePreflightTests(unittest.TestCase):
    def test_index_add_needs_thursday_and_friday_closes(self):
        from scripts.multiyear_preflight import required_quote_days, price_coverage_report
        from scripts.coverage_recovery import temporal_exclusion_snapshots
        w, p, c, _ = world(4)
        s = rebalance_schedule(w, c, "reference_close")
        cal = c[c >= s.decision_date.iloc[0]]
        need = required_quote_days(w, cal, "reference_close")
        thu, fri = s.decision_date.iloc[1], s.reference_date.iloc[1]
        self.assertIn(thu, need[6])          # added name, decided before the Monday snapshot
        self.assertIn(fri, need[6])
        self.assertIn(fri, need[1])          # deleted name sold in the Friday auction
        self.assertNotIn(s.snapshot_date.iloc[1], need[1])
        old = required_quote_days(w[w.date.ge(cal[0])], c[c >= w.date.min()])
        self.assertNotIn(fri, old[6])
        gap = p[~(p.permno.eq(6) & p.date.eq(fri))]
        rep = price_coverage_report(need, gap).set_index("permno")
        self.assertFalse(rep.loc[6, "ready"])
        kept, cut = temporal_exclusion_snapshots(w, cal, {}, gap, "reference_close")
        self.assertEqual(cut.permno.tolist(), [6])
        self.assertTrue(np.allclose(kept.groupby("date").cap_weight_proxy.sum(), 1))
        self.assertIn("reference_date", kept)
