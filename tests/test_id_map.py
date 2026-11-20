"""Tests for tca.id_map on synthetic fixtures only.

Every PERMNO, CUSIP, ISIN, ticker, listing id and price below is made up. No
CRSP, Bloomberg or BMLL rows are used.
"""
import unittest

import pandas as pd

from tca import id_map as im

OPEN = None  # open-ended CRSP interval / BMLL listing


def cusip9(cusip8):
    return cusip8 + im.cusip_check_digit(cusip8)


# Synthetic securities: permno -> 8-character CUSIP
ALFA, BETA, GAMA, DELT, ECHO, FOXT = 90001, 90002, 90003, 90004, 90005, 90006
CUSIP8 = {ALFA: "99900110", BETA: "99900210", GAMA: "99900310", DELT: "99900410",
          ECHO: "G9990510", FOXT: "99900610"}
ISIN = {p: im.isin_from_cusip9(cusip9(c)) for p, c in CUSIP8.items()}


def history(rows):
    """rows: (permno, start, end, ticker)."""
    return pd.DataFrame([{"permno": p, "secinfostartdt": a, "secinfoenddt": z, "ticker": t,
                          "cusip": CUSIP8[p], "cusip9": cusip9(CUSIP8[p])} for p, a, z, t in rows])


def listing(listing_id, instrument_id, isin, ticker, mic, opol, primary, start, end):
    return {"ListingId": listing_id, "InstrumentId": instrument_id, "ISIN": isin, "Ticker": ticker,
            "MIC": mic, "OPOL": opol, "IsPrimary": primary, "ValidFrom": start, "ValidTo": end}


def instrument(base, isin, ticker, opol, start="2015-01-01", end="2026-12-31"):
    """Primary listing, a second venue and the consolidated @SIP listing."""
    other = "ARCX" if opol != "ARCX" else "XNYS"
    return [listing(base + 1, base, isin, ticker, opol, opol, True, start, end),
            listing(base + 2, base, isin, ticker, other, opol, False, start, end),
            listing(base + 3, base, isin, ticker, "@SIP", opol, False, start, end)]


def closes(permno, listing_id, days, price=50.0, bmll_factor=1.0):
    d = pd.to_datetime(days)
    return (pd.DataFrame({"permno": permno, "date": d, "close_price": price}),
            pd.DataFrame({"ListingId": listing_id, "Date": d, "close": price * bmll_factor}))


class IdentifierTests(unittest.TestCase):
    def test_check_digits(self):
        # Hand-computed: CUSIP "00000001" -> position 8 is doubled -> 2 -> check digit 8.
        self.assertEqual(im.cusip_check_digit("00000000"), "0")
        self.assertEqual(im.cusip_check_digit("00000001"), "8")
        # "US000000000" expands to 3028000000000; Luhn sum 18 -> check digit 2.
        self.assertEqual(im.isin_from_cusip9("000000000"), "US0000000002")
        self.assertTrue(im.is_valid_isin("US0000000002"))
        self.assertFalse(im.is_valid_isin("US0000000003"))
        for isin in ISIN.values():
            self.assertTrue(im.is_valid_isin(isin))

    def test_cusip9_prefers_nine_characters_then_builds_from_eight(self):
        self.assertEqual(im.cusip9_of("999001109", "IGNORED"), "999001109")
        self.assertEqual(im.cusip9_of(None, "99900110"), cusip9("99900110"))
        self.assertIsNone(im.cusip9_of(None, float("nan")))
        with self.assertRaises(ValueError):
            im.isin_from_cusip9("1234")


class BuildTests(unittest.TestCase):
    def test_normal_match_uses_the_primary_listing_on_the_opol(self):
        hist = history([(ALFA, "2010-01-04", OPEN, "ALFA")])
        ref = pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS"))
        res = im.build_id_map(hist, ref, start="2022-07-01", end="2026-06-30")
        self.assertEqual(len(res.id_map), 1)
        row = res.id_map.iloc[0]
        self.assertEqual((row["permno"], row["isin"], row["ticker"], row["listing_id"], row["mic"], row["method"]),
                         (ALFA, ISIN[ALFA], "ALFA", 1001, "XNYS", "ISIN"))
        self.assertEqual(row.sip_listing_id, 1003)
        self.assertEqual(row.valid_from, pd.Timestamp("2022-07-01"))
        self.assertEqual(row.valid_to, pd.Timestamp("2026-06-30"))
        self.assertTrue(res.unmatched.empty)
        self.assertEqual(list(res.id_map.columns), im.MAP_COLS)

    def test_ticker_change_keeps_the_same_listing(self):
        # Same CUSIP, new ticker from 2024-06-03. BMLL shows the new ticker on the same listing.
        hist = history([(BETA, "2015-01-02", "2024-05-31", "BETA"),
                        (BETA, "2024-06-03", OPEN, "BNEW")])
        ref = pd.DataFrame(
            [listing(2001, 2000, ISIN[BETA], "BETA", "XNAS", "XNAS", True, "2015-01-01", "2024-05-31"),
             listing(2001, 2000, ISIN[BETA], "BNEW", "XNAS", "XNAS", True, "2024-06-01", "2026-12-31")])
        res = im.build_id_map(hist, ref, start="2022-07-01", end="2026-06-30")
        self.assertEqual(res.id_map.ticker.tolist(), ["BETA", "BNEW"])
        self.assertEqual(res.id_map.listing_id.tolist(), [2001, 2001])
        self.assertEqual(res.id_map.valid_to.iloc[0], pd.Timestamp("2024-05-31"))
        self.assertEqual(res.id_map.valid_from.iloc[1], pd.Timestamp("2024-06-03"))
        self.assertTrue(res.unmatched.empty)
        requests = pd.DataFrame({"date": ["2024-05-30", "2024-06-03", "2026-03-20"], "permno": BETA})
        matched, missing = im.lookup(res.id_map, requests)
        self.assertEqual(matched.ticker.tolist(), ["BETA", "BNEW", "BNEW"])
        self.assertEqual(matched.listing_id.tolist(), [2001] * 3)
        self.assertTrue(missing.empty)

    def test_delisted_name_ends_and_later_requests_are_reported(self):
        # CRSP history ends 2024-03-15; the monthly BMLL snapshot runs to month end.
        hist = history([(GAMA, "2012-05-01", "2024-03-15", "GAMA"),
                        (ALFA, "2010-01-04", OPEN, "ALFA")])
        ref = pd.DataFrame(instrument(3000, ISIN[GAMA], "GAMA", "XNYS", end="2024-03-31")
                           + instrument(1000, ISIN[ALFA], "ALFA", "XNYS"))
        res = im.build_id_map(hist, ref, start="2022-07-01", end="2026-06-30")
        gama = res.id_map[res.id_map.permno.eq(GAMA)]
        self.assertEqual(gama.valid_to.tolist(), [pd.Timestamp("2024-03-15")])
        self.assertTrue(res.unmatched.empty)       # mapped while it traded: not "unmatched"
        requests = pd.DataFrame({"date": ["2024-03-15", "2024-06-21", "2024-06-21"],
                                 "permno": [GAMA, GAMA, ALFA]})
        matched, missing = im.lookup(res.id_map, requests)
        self.assertEqual(matched[["permno", "listing_id"]].values.tolist(), [[GAMA, 3001], [ALFA, 1001]])
        self.assertEqual(missing[["permno", "n_matches"]].values.tolist(), [[GAMA, 0]])
        self.assertEqual(missing.date.iloc[0], pd.Timestamp("2024-06-21"))

    def test_name_that_left_the_index_keeps_its_mapping(self):
        # The map follows CRSP identifier history, not index membership.
        hist = history([(ALFA, "2010-01-04", OPEN, "ALFA")])
        ref = pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS"))
        res = im.build_id_map(hist, ref, universe={ALFA}, start="2022-07-01", end="2026-06-30")
        left_index_on = "2023-09-15"
        matched, missing = im.lookup(res.id_map, pd.DataFrame(
            {"date": [left_index_on, "2025-12-19"], "permno": ALFA}))
        self.assertEqual(matched.listing_id.tolist(), [1001, 1001])
        self.assertTrue(missing.empty)

    def test_permno_without_a_bmll_match_is_reported_not_dropped(self):
        hist = history([(ALFA, "2010-01-04", OPEN, "ALFA"), (DELT, "2018-02-01", OPEN, "DELT")])
        ref = pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS"))
        res = im.build_id_map(hist, ref, start="2022-07-01", end="2026-06-30")
        self.assertEqual(res.id_map.permno.tolist(), [ALFA])
        self.assertEqual(res.unmatched.permno.tolist(), [DELT])
        row = res.unmatched.iloc[0]
        self.assertEqual((row["ticker"], row["isin"], row["reason"]),
                         ("DELT", ISIN[DELT], im.NOT_IN_BMLL_REFERENCE))
        self.assertEqual(list(res.unmatched.columns), im.UNMATCHED_COLS)
        matched, missing = im.lookup(res.id_map, pd.DataFrame(
            {"date": ["2024-06-21"] * 2, "permno": [ALFA, DELT]}))
        self.assertEqual(matched.permno.tolist(), [ALFA])
        self.assertEqual(missing.permno.tolist(), [DELT])
        # Every input PERMNO is in exactly one of the two outputs.
        self.assertEqual(set(res.id_map.permno) | set(res.unmatched.permno), {ALFA, DELT})

    def test_ticker_fallback_needs_agreeing_closes(self):
        # Foreign issuer: BMLL carries a non-US ISIN, so "US" + CUSIP finds nothing.
        hist = history([(ECHO, "2019-01-02", OPEN, "ECHO")])
        ref = pd.DataFrame(instrument(5000, "XX0000000001", "ECHO", "XNYS"))
        days = pd.bdate_range("2023-01-02", periods=30)
        kw = dict(start="2022-07-01", end="2026-06-30")

        crsp, bmll = closes(ECHO, 5001, days)
        ok = im.build_id_map(hist, ref, crsp, bmll, **kw)
        self.assertEqual(ok.id_map.method.tolist(), [im.TICKER_METHOD])
        self.assertEqual(ok.id_map.listing_id.tolist(), [5001])
        self.assertTrue(ok.unmatched.empty)

        crsp, bmll = closes(ECHO, 5001, days, bmll_factor=1.01)       # 100 bp apart
        bad = im.build_id_map(hist, ref, crsp, bmll, **kw)
        self.assertTrue(bad.id_map.empty)
        self.assertEqual(bad.unmatched.reason.tolist(), [im.TICKER_PRICE_CHECK_FAILED])
        self.assertTrue(bad.unmatched.foreign_cusip.iloc[0])

        none = im.build_id_map(hist, ref, **kw)                       # no closes: never accept
        self.assertTrue(none.id_map.empty)
        self.assertEqual(none.unmatched.reason.tolist(), [im.TICKER_NOT_PRICE_CHECKED])

    def test_isin_found_but_listing_outside_the_crsp_interval(self):
        hist = history([(FOXT, "2025-01-02", OPEN, "FOXT")])
        ref = pd.DataFrame(instrument(6000, ISIN[FOXT], "FOXT", "XNAS", end="2023-12-29"))
        res = im.build_id_map(hist, ref, start="2022-07-01", end="2026-06-30")
        self.assertTrue(res.id_map.empty)
        self.assertEqual(res.unmatched.reason.tolist(), [im.ISIN_LISTING_OUTSIDE_INTERVAL])

    def test_venue_move_uses_the_crsp_venue_when_closes_agree(self):
        # CRSP: NYSE until 2023-11-06, Nasdaq after. BMLL flags only the latest venue as primary.
        hist = history([(ALFA, "2010-01-04", "2023-11-06", "ALFA"), (ALFA, "2023-11-07", OPEN, "ALFA")])
        hist["primaryexch"] = ["N", "Q"]
        ref = pd.DataFrame(
            [listing(1001, 1000, ISIN[ALFA], "ALFA", "XNAS", "XNAS", True, "2015-01-01", "2026-12-31"),
             listing(1002, 1000, ISIN[ALFA], "ALFA", "XNYS", "XNAS", False, "2015-01-01", "2023-11-30")])
        crsp, bmll = closes(ALFA, 1002, pd.bdate_range("2022-07-01", "2023-11-06"))
        res = im.build_id_map(hist, ref, crsp, bmll, start="2022-07-01", end="2026-06-30")
        got = res.id_map[["listing_id", "mic", "method", "valid_from", "valid_to"]].values.tolist()
        self.assertEqual(got, [
            [1002, "XNYS", "ISIN_CRSP_VENUE", pd.Timestamp("2022-07-01"), pd.Timestamp("2023-11-06")],
            [1001, "XNAS", "ISIN", pd.Timestamp("2023-11-07"), pd.Timestamp("2026-06-30")]])
        self.assertTrue(res.unmatched.empty)

    def test_exactly_one_listing_per_permno_per_day(self):
        hist = history([(BETA, "2015-01-02", "2024-05-31", "BETA"), (BETA, "2024-06-03", OPEN, "BNEW"),
                        (ALFA, "2010-01-04", OPEN, "ALFA")])
        ref = pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS")
                           + instrument(2000, ISIN[BETA], "BETA", "XNAS"))
        res = im.build_id_map(hist, ref, start="2022-07-01", end="2026-06-30")
        days = pd.bdate_range("2022-07-01", "2026-06-30", freq="W-FRI")
        requests = pd.DataFrame([(d, p) for d in days for p in (ALFA, BETA)], columns=["date", "permno"])
        matched, missing = im.lookup(res.id_map, requests)
        self.assertEqual(len(matched), len(requests))
        self.assertTrue(missing.empty)

    def test_dated_snapshots_are_collapsed_to_intervals(self):
        snaps = pd.to_datetime(["2023-01-31", "2023-02-28", "2023-03-31"])
        ref = pd.DataFrame([{"ListingId": 1001, "ISIN": ISIN[ALFA], "Ticker": "ALFA", "MIC": "XNYS",
                             "OPOL": "XNYS", "IsPrimary": True, "Date": d} for d in snaps])
        out = im.reference_intervals(ref)
        self.assertEqual(len(out), 1)
        self.assertEqual((out.ValidFrom.iloc[0], out.ValidTo.iloc[0]),
                         (pd.Timestamp("2023-01-31"), pd.Timestamp("2023-03-31")))
        res = im.build_id_map(history([(ALFA, "2010-01-04", OPEN, "ALFA")]), ref)
        self.assertEqual(res.id_map.listing_id.tolist(), [1001])
        with self.assertRaises(ValueError):
            im.reference_intervals(ref.drop(columns="Date"))

    def test_open_ended_listing_is_stored_as_nat_and_matches_any_later_date(self):
        hist = history([(ALFA, "2010-01-04", OPEN, "ALFA")])
        ref = pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS", end=None))
        res = im.build_id_map(hist, ref, start="2022-07-01")
        self.assertTrue(pd.isna(res.id_map.valid_to.iloc[0]))
        matched, missing = im.lookup(res.id_map, pd.DataFrame({"date": ["2030-01-02"], "permno": [ALFA]}))
        self.assertEqual(len(matched), 1)
        self.assertTrue(missing.empty)

    def test_input_validation(self):
        ref = pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS"))
        hist = history([(ALFA, "2010-01-04", OPEN, "ALFA")])
        with self.assertRaises(ValueError):
            im.build_id_map(hist.drop(columns="ticker"), ref)
        with self.assertRaises(ValueError):
            im.build_id_map(hist.drop(columns=["cusip", "cusip9"]), ref)
        with self.assertRaises(ValueError):
            im.build_id_map(hist, ref.drop(columns="OPOL"))
        with self.assertRaises(ValueError):
            im.build_id_map(hist, ref, start="2026-01-01", end="2025-01-01")


class CoverageTests(unittest.TestCase):
    def test_coverage_summary_counts_each_permno_once(self):
        hist = history([
            (ALFA, "2010-01-04", OPEN, "ALFA"),                 # ISIN match
            (BETA, "2015-01-02", OPEN, "BETA"),                 # ISIN match
            (ECHO, "2019-01-02", OPEN, "ECHO"),                 # ticker fallback
            (DELT, "2018-02-01", OPEN, "DELT"),                 # not in BMLL at all
            (FOXT, "2025-01-02", OPEN, "FOXT"),                 # ticker found, closes disagree
            (GAMA, "2012-05-01", "2023-06-30", "GAMA"),         # matched ...
            (GAMA, "2023-07-03", OPEN, "GNEW"),                 # ... then a gap: listing ended
        ])
        ref = pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS")
                           + instrument(2000, ISIN[BETA], "BETA", "XNAS")
                           + instrument(5000, "XX0000000001", "ECHO", "XNYS")
                           + instrument(6000, "XX0000000002", "FOXT", "XNAS")
                           + instrument(3000, ISIN[GAMA], "GAMA", "XNYS", end="2023-06-30"))
        days = pd.bdate_range("2025-02-03", periods=20)
        c1, b1 = closes(ECHO, 5001, days)
        c2, b2 = closes(FOXT, 6001, days, bmll_factor=1.05)
        res = im.build_id_map(hist, ref, pd.concat([c1, c2]), pd.concat([b1, b2]),
                              start="2022-07-01", end="2026-06-30")
        s = im.coverage_summary(res.id_map, res.unmatched)
        got = {(r.status, r.reason): r.permnos for r in s.itertuples()}
        self.assertEqual(got, {
            ("MATCHED_ISIN", ""): 3, ("MATCHED_TICKER_FALLBACK", ""): 1,
            ("UNMATCHED", ""): 2,
            ("UNMATCHED", im.NOT_IN_BMLL_REFERENCE): 1,
            ("UNMATCHED", im.TICKER_PRICE_CHECK_FAILED): 1,
            ("MATCHED_WITH_GAPS", ""): 1,
            ("MATCHED_WITH_GAPS", im.ISIN_LISTING_OUTSIDE_INTERVAL): 1,
            ("TOTAL", ""): 6})
        top = s[s.reason.eq("") & s.status.isin(["MATCHED_ISIN", "MATCHED_TICKER_FALLBACK", "UNMATCHED"])]
        self.assertEqual(top.permnos.sum(), 6)
        self.assertAlmostEqual(top.share.sum(), 1.0)

    def test_coverage_summary_on_empty_inputs(self):
        res = im.build_id_map(history([(ALFA, "2010-01-04", "2011-01-03", "ALFA")]),
                              pd.DataFrame(instrument(1000, ISIN[ALFA], "ALFA", "XNYS")),
                              start="2022-07-01", end="2026-06-30")   # history ends before the window
        self.assertTrue(res.id_map.empty and res.unmatched.empty)
        s = im.coverage_summary(res.id_map, res.unmatched)
        self.assertEqual(s.loc[s.status.eq("TOTAL"), "permnos"].iloc[0], 0)


if __name__ == "__main__":
    unittest.main()
