"""Point-in-time CRSP PERMNO -> BMLL listing map.

Ported from App-SIMP so both repos map identifiers the same way:

    App-SIMP/backend/simp/data/mapping/isin.py      (copied; pure Python)
    App-SIMP/backend/simp/data/mapping/builder.py   (polars -> pandas, same rules)

at App-SIMP commit 062ac89. Keep the two in step when either changes.

Rules (unchanged from App-SIMP)
  1. ISIN per CRSP identifier-history interval: "US" + CUSIP9 + check digit.
  2. Match the ISIN to BMLL's primary listing (``IsPrimary`` and ``MIC == OPOL``).
     The mapping is valid where the CRSP interval and the listing's life overlap.
  3. Only when BMLL has no primary listing for the ISIN: fall back on the ticker,
     and accept it only if BMLL and CRSP closes agree within 1 bp on up to five
     sample days.
  4. One listing per PERMNO per day; overlaps are resolved in favour of the
     later listing.
  5. Every CRSP interval without a listing is returned in ``unmatched`` with a
     reason. Nothing is dropped silently.

Why ``IsPrimary and MIC == OPOL``
  A US stock has one BMLL listing per venue it trades on (XNYS, XNAS, ARCX,
  BATS, ..., plus the consolidated "@SIP" listing), all sharing one ISIN. Opening
  and closing auctions run on the listing exchange only, so TCA needs the listing
  on that exchange. BMLL's public Daily Reference page describes OPOL as the
  "primary market" code, so the row whose own MIC equals its OPOL is the listing
  on the primary market. ``IsPrimary`` is applied as well because App-SIMP does,
  and its tests cover references where only one of several rows is flagged.

  What is NOT settled, and should be confirmed with BMLL before relying on it:
  * BMLL's public Daily Analytics schema describes OPOL as the place where the
    instrument was *originally* listed. For a stock that has moved exchange,
    "original" and "current primary" differ, and the public pages do not say
    which one the reference data holds on a given date.
  * No public definition of ``IsPrimary`` was found. App-SIMP's notes say the
    reference keeps only a listing's latest primary flag, so after a venue move
    the old venue is no longer flagged for any date.
  Because of this, when the CRSP history carries ``primaryexch`` the listing on
  the venue CRSP names for that interval is preferred, if its closes agree with
  CRSP (method ``ISIN_CRSP_VENUE``). Without ``primaryexch`` or closes, venue
  moves are not corrected and the BMLL-flagged primary is used throughout.

Differences from App-SIMP
  * ``primaryexch`` is optional (the team's wrds_id_history.csv does not have it).
  * Close prices are optional. Without them the ticker fallback is never
    accepted; those names are reported as ``TICKER_NOT_PRICE_CHECKED``.
  * ``reference_intervals`` collapses dated reference rows (one row per ``Date``,
    as returned by ``BMLLSource.listings``) into ValidFrom/ValidTo intervals.
  * ``start``/``end`` clip the CRSP history to the study window, so decades of
    pre-BMLL history are not reported as unmatched.
  * Unmatched reasons are short codes so they can be counted.

Inputs
  id_history      permno, secinfostartdt, secinfoenddt, ticker, cusip9 and/or
                  cusip (8 characters), optional primaryexch
                  (CRSP stksecurityinfohist)
  bmll_reference  ListingId, ISIN, Ticker, MIC, OPOL, IsPrimary, optional
                  InstrumentId, and either ValidFrom/ValidTo or Date
  crsp_close      permno, date, close_price            (optional)
  bmll_close      ListingId, Date, close               (optional)

No licensed data belongs in this repository: pass the tables in at run time.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

OPEN_START = pd.Timestamp("1900-01-01")
OPEN_END = pd.Timestamp.max.normalize()
DAY = pd.Timedelta(days=1)
SIP_MIC = "@SIP"
PRICE_TOLERANCE = 1e-4          # 1 bp
PRICE_SAMPLE_DAYS = 5

ISIN_METHODS = ("ISIN", "ISIN_CRSP_VENUE")
TICKER_METHOD = "TICKER_PRICE_CHECK"

MAP_COLS = ["permno", "isin", "ticker", "listing_id", "valid_from", "valid_to",
            "instrument_id", "sip_listing_id", "mic", "method"]
UNMATCHED_COLS = ["permno", "ticker", "isin", "valid_from", "valid_to", "reason",
                  "foreign_cusip"]

# Unmatched reasons
NOT_IN_BMLL_REFERENCE = "NOT_IN_BMLL_REFERENCE"            # neither ISIN nor ticker found
ISIN_LISTING_OUTSIDE_INTERVAL = "ISIN_LISTING_OUTSIDE_INTERVAL"  # ISIN found, dates do not overlap
TICKER_PRICE_CHECK_FAILED = "TICKER_PRICE_CHECK_FAILED"    # ticker found, closes disagree or absent
TICKER_NOT_PRICE_CHECKED = "TICKER_NOT_PRICE_CHECKED"      # ticker found, no closes supplied


# ----------------------------------------------------------------------------
# ISIN / CUSIP helpers (copied from App-SIMP isin.py)
# ----------------------------------------------------------------------------
def isin_check_digit(body: str) -> str:
    """ISO 6166 check digit: Luhn over the letter-expanded string."""
    digits = "".join(str(int(c, 36)) for c in body.upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        v = int(ch)
        if i % 2 == 0:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return str((10 - total % 10) % 10)


def isin_from_cusip9(cusip9: str, country: str = "US") -> str:
    if len(cusip9) != 9:
        raise ValueError(f"CUSIP9 must have 9 characters, got {cusip9!r}")
    body = country + cusip9.upper()
    return body + isin_check_digit(body)


def cusip_check_digit(cusip8: str) -> str:
    total = 0
    for i, ch in enumerate(cusip8.upper()):
        if ch.isdigit():
            v = int(ch)
        elif ch.isalpha():
            v = ord(ch) - 55
        else:
            v = {"*": 36, "@": 37, "#": 38}.get(ch, 0)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def cusip9_of(cusip9, cusip) -> str | None:
    """The 9-character CUSIP from CRSP's cusip9, else cusip (8 chars + check digit), else None."""
    for c in (cusip9, cusip):
        c = "" if c is None or pd.isna(c) else str(c).strip().upper()
        if len(c) == 9:
            return c
        if len(c) == 8:
            return c + cusip_check_digit(c)
    return None


def is_valid_isin(isin: str) -> bool:
    return len(isin) == 12 and isin_check_digit(isin[:11]) == isin[11]


# CRSP primary exchange codes -> MIC of the listing exchange. MICs pass through unchanged.
EXCH_TO_MIC = {"N": "XNYS", "Q": "XNAS", "A": "XASE", "P": "ARCX", "R": "ARCX",
               "Z": "BATS", "B": "BATS", "I": "IEXG"}


def exch_to_mic(code) -> str | None:
    if code is None or pd.isna(code) or not str(code).strip():
        return None
    code = str(code).strip().upper()
    return EXCH_TO_MIC.get(code, code)


# ----------------------------------------------------------------------------
# Interval helpers (ported from App-SIMP builder.py)
# ----------------------------------------------------------------------------
def _day(x):
    return None if x is None or pd.isna(x) else pd.Timestamp(x).normalize()


def _overlap(a0, a1, b0, b1):
    lo, hi = max(a0, b0), min(a1, b1)
    return (lo, hi) if lo <= hi else None


def _minus(ov, cut):
    """The parts of ``ov`` outside ``cut``."""
    if ov is None:
        return []
    out = []
    if ov[0] < cut[0]:
        out.append((ov[0], min(ov[1], cut[0] - DAY)))
    if ov[1] > cut[1]:
        out.append((max(ov[0], cut[1] + DAY), ov[1]))
    return out


def _one_listing_per_day(placed):
    """Resolve overlapping listings matched to one CRSP interval.

    A later listing wins from its start. On equal starts the first candidate
    (ISIN before ticker) is kept.
    """
    out = []
    for x in sorted((dict(r) for r in placed), key=lambda r: r["valid_from"]):
        if out and x["valid_from"] == out[-1]["valid_from"]:
            continue
        if out and x["valid_from"] <= out[-1]["valid_to"]:
            prev = out[-1]
            if x["valid_to"] < prev["valid_to"]:   # x sits inside prev: prev resumes after it
                out.append({**prev, "valid_from": x["valid_to"] + DAY})
                out.sort(key=lambda r: r["valid_from"])
            prev["valid_to"] = x["valid_from"] - DAY
        out.append(x)
        out.sort(key=lambda r: r["valid_from"])
    return out


def reference_intervals(reference: pd.DataFrame) -> pd.DataFrame:
    """BMLL reference rows with ValidFrom/ValidTo per listing and identifier set.

    Accepts a table that already has ValidFrom/ValidTo, or dated snapshot rows
    (one per ``Date``), which are collapsed to the first and last date each
    (ListingId, ISIN, Ticker, MIC, OPOL, IsPrimary) combination was seen. A
    snapshot-based end date is only as precise as the snapshot frequency.
    """
    ref = reference.copy()
    alias = {"ObjectId": "ListingId", "Opol": "OPOL", "StartDate": "ValidFrom", "EndDate": "ValidTo"}
    ref = ref.rename(columns={k: v for k, v in alias.items() if k in ref and v not in ref})
    need = {"ListingId", "ISIN", "Ticker", "MIC", "OPOL", "IsPrimary"}
    if need - set(ref):
        raise ValueError(f"BMLL reference missing {sorted(need - set(ref))}")
    if "InstrumentId" not in ref:
        # Without InstrumentId, sibling listings (other venues, @SIP) cannot be linked.
        ref["InstrumentId"] = ref["ListingId"]
    ref["ISIN"] = ref["ISIN"].astype(object).where(ref["ISIN"].notna(), None)
    ref["ISIN"] = [None if v is None or not str(v).strip() else str(v).strip() for v in ref["ISIN"]]
    ref["IsPrimary"] = ref["IsPrimary"].fillna(False).astype(bool)
    keys = ["ListingId", "InstrumentId", "ISIN", "Ticker", "MIC", "OPOL", "IsPrimary"]
    if {"ValidFrom", "ValidTo"} <= set(ref):
        ref["ValidFrom"] = pd.to_datetime(ref["ValidFrom"]).dt.normalize()
        ref["ValidTo"] = pd.to_datetime(ref["ValidTo"]).dt.normalize()
        if ref["ValidFrom"].isna().any():
            raise ValueError("BMLL reference has a missing ValidFrom")
        out = ref[keys + ["ValidFrom", "ValidTo"]].copy()
    elif "Date" in ref:
        ref["Date"] = pd.to_datetime(ref["Date"]).dt.normalize()
        out = ref.groupby(keys, dropna=False, as_index=False).agg(
            ValidFrom=("Date", "min"), ValidTo=("Date", "max"))
    else:
        raise ValueError("BMLL reference needs ValidFrom/ValidTo or Date")
    out["ValidFrom"] = [_day(v) for v in out["ValidFrom"]]
    out["ValidTo"] = [_day(v) or OPEN_END for v in out["ValidTo"]]
    return out.astype({"ValidFrom": object, "ValidTo": object}).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Builder
# ----------------------------------------------------------------------------
@dataclass
class IdMapResult:
    id_map: pd.DataFrame       # MAP_COLS; valid_to is NaT when open-ended
    unmatched: pd.DataFrame    # UNMATCHED_COLS; one row per CRSP interval without a listing


def build_id_map(id_history, bmll_reference, crsp_close=None, bmll_close=None, *,
                 universe=None, start=None, end=None) -> IdMapResult:
    """Build the point-in-time PERMNO -> BMLL listing map.

    ``universe`` restricts the PERMNOs. ``start``/``end`` clip the CRSP
    identifier history to the study window. The map follows CRSP identifier
    history, not index membership, so a name keeps its mapping after it leaves
    the index and stops only when CRSP or BMLL stops carrying it.
    """
    need = {"permno", "secinfostartdt", "secinfoenddt", "ticker"}
    if need - set(id_history):
        raise ValueError(f"id_history missing {sorted(need - set(id_history))}")
    if not {"cusip9", "cusip"} & set(id_history):
        raise ValueError("id_history needs cusip9 or cusip")
    w0, w1 = _day(start) or OPEN_START, _day(end) or OPEN_END
    if w0 > w1:
        raise ValueError("start must not be after end")
    wanted = None if universe is None else {int(p) for p in universe}

    recs = reference_intervals(bmll_reference).to_dict("records")
    for r in recs:
        r["ListingId"], r["InstrumentId"] = int(r["ListingId"]), int(r["InstrumentId"])
    prim = [r for r in recs if r["IsPrimary"] and r["MIC"] == r["OPOL"]]

    # Listing life: reference interval, widened to every date BMLL has daily data for it.
    traded, bc = {}, {}
    if bmll_close is not None and len(bmll_close):
        b = bmll_close[["ListingId", "Date", "close"]].copy()
        b["Date"] = pd.to_datetime(b["Date"]).dt.normalize()
        for lid, g in b.groupby("ListingId"):
            traded[int(lid)] = (g["Date"].min(), g["Date"].max())
        b = b.dropna(subset=["close"])
        bc = {(int(l), d): float(x) for l, d, x in zip(b["ListingId"], b["Date"], b["close"])}
    life = {}
    for r in recs:
        a, z = life.get(r["ListingId"], (r["ValidFrom"], r["ValidTo"]))
        life[r["ListingId"]] = (min(a, r["ValidFrom"]), max(z, r["ValidTo"]))
    for lid, (a, z) in traded.items():
        if lid in life:
            life[lid] = (min(life[lid][0], a), max(life[lid][1], z))

    by_isin, by_ticker, by_inst_mic, sip_by_inst = {}, {}, {}, {}
    for r in prim:
        if r["ISIN"]:
            by_isin.setdefault(r["ISIN"], []).append(r)
        by_ticker.setdefault(str(r["Ticker"]).replace(" ", "."), []).append(r)
    for r in sorted(recs, key=lambda r: r["ValidTo"]):      # latest row per (instrument, venue) wins
        by_inst_mic[(r["InstrumentId"], str(r["MIC"]))] = r
        if r["MIC"] == SIP_MIC:
            sip_by_inst.setdefault(r["InstrumentId"], []).append(r)

    cc, crsp_dates = {}, {}
    if crsp_close is not None and len(crsp_close):
        c = crsp_close[["permno", "date", "close_price"]].dropna().copy()
        c["date"] = pd.to_datetime(c["date"]).dt.normalize()
        c = c.sort_values(["permno", "date"])
        for p, d, x in zip(c["permno"], c["date"], c["close_price"]):
            cc[(int(p), d)] = float(x)
            crsp_dates.setdefault(int(p), []).append(d)
    have_prices = bool(cc) and bool(bc)
    has_exch = "primaryexch" in id_history

    hist = id_history.copy()
    hist["secinfostartdt"] = pd.to_datetime(hist["secinfostartdt"]).dt.normalize()
    hist["secinfoenddt"] = pd.to_datetime(hist["secinfoenddt"]).dt.normalize()
    if hist["permno"].isna().any() or hist["secinfostartdt"].isna().any():
        raise ValueError("id_history has a missing permno or start date")
    hist = hist.sort_values(["permno", "secinfostartdt"])

    rows, queue = [], []
    for s in hist.to_dict("records"):
        permno = int(s["permno"])
        if wanted is not None and permno not in wanted:
            continue
        clipped = _overlap(s["secinfostartdt"], _day(s["secinfoenddt"]) or OPEN_END, w0, w1)
        if clipped is None:
            continue
        s0, s1 = clipped
        ticker = None if pd.isna(s["ticker"]) else str(s["ticker"]).strip()
        c9 = cusip9_of(s.get("cusip9"), s.get("cusip"))
        isin = isin_from_cusip9(c9) if c9 else None
        mic = exch_to_mic(s["primaryexch"]) if has_exch else None

        def span(r):
            # the listing's life inside this CRSP interval
            lf, lt = life.get(r["ListingId"], (r["ValidFrom"], r["ValidTo"]))
            return _overlap(s0, s1, lf, lt)

        def price_ok(r, ov):
            # BMLL close = CRSP close within 1 bp on up to 5 sample days inside the overlap
            lid = r["ListingId"]
            days = [d for d in crsp_dates.get(permno, []) if ov[0] <= d <= ov[1]]
            sample = days[:: max(1, len(days) // PRICE_SAMPLE_DAYS)][:PRICE_SAMPLE_DAYS]
            ok = [abs(bc[(lid, d)] / cc[(permno, d)] - 1) <= PRICE_TOLERANCE
                  for d in sample if (lid, d) in bc and (permno, d) in cc]
            return len(ok) >= min(PRICE_SAMPLE_DAYS, len(sample)) and bool(ok) and all(ok)

        def venue(r):
            # the instrument's listing on the venue CRSP names as primary for this interval
            if mic is None or r["MIC"] == mic:
                return r
            return by_inst_mic.get((r["InstrumentId"], mic))

        cands = []
        isin_rows = by_isin.get(isin, []) if isin else []
        for r in isin_rows:
            ov = span(r)
            v = venue(r)
            vo = span(v) if v is not None and v is not r else None
            if v is not None and vo is not None and price_ok(v, vo):
                cands.append((v, "ISIN_CRSP_VENUE", vo))
                cands += [(r, "ISIN", seg) for seg in _minus(ov, vo)]
            elif ov is not None:
                cands.append((r, "ISIN", ov))
        ticker_rows = by_ticker.get(ticker, []) if ticker else []
        if not isin_rows:
            for r in ticker_rows:
                v = venue(r)
                vo = span(v) if v is not None else None
                if v is not None and vo is not None and price_ok(v, vo):
                    cands.append((v, TICKER_METHOD, vo))

        mine = []
        for r, method, ov in cands:
            sips = [x for x in sip_by_inst.get(r["InstrumentId"], [])
                    if _overlap(ov[0], ov[1], x["ValidFrom"], x["ValidTo"])]
            mine.append({"permno": permno, "isin": r["ISIN"] or isin, "ticker": ticker,
                         "listing_id": r["ListingId"], "valid_from": ov[0], "valid_to": ov[1],
                         "instrument_id": r["InstrumentId"],
                         "sip_listing_id": sips[0]["ListingId"] if sips else None,
                         "mic": r["MIC"], "method": method})
        rows.extend(_one_listing_per_day(mine))
        if not mine:
            if isin_rows:
                reason = ISIN_LISTING_OUTSIDE_INTERVAL
            elif ticker_rows:
                reason = TICKER_PRICE_CHECK_FAILED if have_prices else TICKER_NOT_PRICE_CHECKED
            else:
                reason = NOT_IN_BMLL_REFERENCE
            queue.append({"permno": permno, "ticker": ticker, "isin": isin, "valid_from": s0,
                          "valid_to": s1, "reason": reason,
                          # CINS CUSIP: foreign-domiciled issuer whose ISIN is not "US" + CUSIP
                          "foreign_cusip": bool(c9 and c9[0].isalpha())})

    return IdMapResult(_frame(rows, MAP_COLS, ["permno", "valid_from"], int_cols=(
        "permno", "listing_id", "instrument_id", "sip_listing_id")),
        _frame(queue, UNMATCHED_COLS, None, int_cols=("permno",)))


def _frame(rows, cols, unique, int_cols=()):
    df = pd.DataFrame(rows, columns=cols)
    for c in ("valid_from", "valid_to"):
        # open-ended intervals are stored as NaT
        df[c] = pd.to_datetime([None if v is None or v >= OPEN_END else v for v in df[c]])
    for c in int_cols:
        df[c] = df[c].astype("Int64")
    if unique:
        df = df.drop_duplicates(unique, keep="first")
    return df.sort_values(["permno", "valid_from"], kind="stable").reset_index(drop=True)


# ----------------------------------------------------------------------------
# Using the map
# ----------------------------------------------------------------------------
def lookup(id_map, requests):
    """Listing for each requested (date, permno).

    Returns ``(matched, missing)``. ``matched`` has one row per request with
    exactly one listing. ``missing`` lists every other request with
    ``n_matches`` (0 = no listing on that date, >1 = ambiguous), so callers can
    report gaps instead of losing rows in a join.
    """
    r = requests[["date", "permno"]].drop_duplicates().copy()
    r["date"] = pd.to_datetime(r["date"]).dt.normalize()
    r["permno"] = r["permno"].astype("Int64")
    m = r.merge(id_map, on="permno", how="left")
    hit = m.valid_from.notna() & m.date.ge(m.valid_from) & (m.valid_to.isna() | m.date.le(m.valid_to))
    hits = m[hit]
    n = hits.groupby(["date", "permno"]).size().rename("n_matches").reset_index()
    counts = r.merge(n, on=["date", "permno"], how="left")
    counts["n_matches"] = counts["n_matches"].fillna(0).astype(int)
    one = counts.loc[counts.n_matches.eq(1), ["date", "permno"]]
    matched = hits.merge(one, on=["date", "permno"]).sort_values(["date", "permno"]).reset_index(drop=True)
    missing = counts[counts.n_matches.ne(1)].sort_values(["date", "permno"]).reset_index(drop=True)
    return matched, missing


def coverage_summary(id_map, unmatched):
    """PERMNO counts by mapping outcome, for reporting coverage.

    Each PERMNO is counted once in the first three statuses:

    MATCHED_ISIN             at least one interval matched by ISIN
    MATCHED_TICKER_FALLBACK  matched only through the ticker + price check
    UNMATCHED                no listing for any interval; broken down by the
                             reason on the PERMNO's latest unmatched interval

    MATCHED_WITH_GAPS is informational and overlaps the matched statuses: PERMNOs
    that are mapped for part of the window but have at least one unmatched
    interval, by the reason on the latest such interval.
    """
    methods = id_map.groupby("permno").method.agg(set) if len(id_map) else pd.Series(dtype=object)
    by_isin = {p for p, m in methods.items() if m & set(ISIN_METHODS)}
    by_ticker = set(methods.index) - by_isin
    last = (unmatched.sort_values(["permno", "valid_from"]).groupby("permno").reason.last()
            if len(unmatched) else pd.Series(dtype=object))
    none = last[~last.index.isin(methods.index)]
    gaps = last[last.index.isin(methods.index)]
    total = len(methods) + len(none)
    rows = [("MATCHED_ISIN", "", len(by_isin)), ("MATCHED_TICKER_FALLBACK", "", len(by_ticker)),
            ("UNMATCHED", "", len(none))]
    rows += [("UNMATCHED", reason, int(k)) for reason, k in none.value_counts().sort_index().items()]
    rows += [("MATCHED_WITH_GAPS", "", len(gaps))]
    rows += [("MATCHED_WITH_GAPS", reason, int(k)) for reason, k in gaps.value_counts().sort_index().items()]
    rows += [("TOTAL", "", total)]
    out = pd.DataFrame(rows, columns=["status", "reason", "permnos"])
    out["share"] = out.permnos / total if total else float("nan")
    return out
