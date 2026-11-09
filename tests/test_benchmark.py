import tempfile
import unittest

import numpy as np
import pandas as pd

from scripts.benchmark import (fetch_sp500_total_return, performance_report,
                               save_benchmark, load_benchmark)


class FakeDB:
    """Only crsp_q_indexes.dsp500_v2 exists, with CIZ-style column names."""
    def __init__(self, data):
        self.data = data

    def raw_sql(self, sql, params=None):
        if "information_schema" in sql:
            if (params["s"], params["t"]) == ("crsp_q_indexes", "dsp500_v2"):
                return pd.DataFrame({"column_name": ["caldt", "vwretd", "vwretx", "sprtrn"]})
            return pd.DataFrame({"column_name": []})
        assert "crsp_q_indexes.dsp500_v2" in sql and "vwretd AS benchmark_return" in sql
        d = self.data
        return d[(d.caldt >= params["a"]) & (d.caldt <= params["b"])].rename(columns={
            "caldt": "date", "vwretd": "benchmark_return", "vwretx": "price_return",
            "sprtrn": "sp_price_return"})


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.cal = pd.bdate_range("2024-01-02", "2024-12-31")
        rng = np.random.default_rng(0)
        r = rng.normal(4e-4, .01, len(self.cal))
        self.raw = pd.DataFrame({"caldt": self.cal.strftime("%Y-%m-%d"), "vwretd": r,
                                 "vwretx": r - 6e-5, "sprtrn": r - 6e-5})

    def test_discovers_ciz_table_and_validates_calendar(self):
        b = fetch_sp500_total_return(FakeDB(self.raw), self.cal[0], self.cal[-1], self.cal)
        self.assertEqual(len(b), len(self.cal))
        self.assertEqual(b.source.iloc[0], "crsp_q_indexes.dsp500_v2.vwretd")
        with self.assertRaisesRegex(ValueError, "lacks 1 sessions"):
            fetch_sp500_total_return(FakeDB(self.raw.drop(index=10)), self.cal[0], self.cal[-1], self.cal)
        with tempfile.TemporaryDirectory() as t:
            save_benchmark(b, t)
            pd.testing.assert_frame_equal(load_benchmark(t)[["date", "benchmark_return"]],
                                          b[["date", "benchmark_return"]])

    def test_report_tracking_metrics(self):
        b = fetch_sp500_total_return(FakeDB(self.raw), self.cal[0], self.cal[-1], self.cal)
        active = np.random.default_rng(1).normal(-2e-6, 1e-4, len(self.cal))
        navs = pd.DataFrame({"date": self.cal, "fund_return": b.benchmark_return + active,
                             "external_flow": 0.})
        navs["unit_nav"] = 100 * (1 + navs.fund_return).cumprod()
        daily, s, by_year = performance_report(navs, b, "2024-02-01")
        self.assertEqual(daily.date.min(), pd.Timestamp("2024-02-01"))
        self.assertAlmostEqual(s["tracking_error_annualized_bps"],
                               1e4 * active[len(active) - len(daily):].std(ddof=1) * np.sqrt(252), places=6)
        self.assertGreater(s["correlation"], .99)
        self.assertEqual(list(by_year.index), [2024])
        with self.assertRaisesRegex(ValueError, "cover every"):
            performance_report(navs, b.iloc[:-1])


if __name__ == "__main__":
    unittest.main()
