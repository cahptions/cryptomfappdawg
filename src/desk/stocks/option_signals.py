"""What a stock's option prices say about the stock (docs/STOCKS_METHOD.md → Options desk).

Each measure below has predicted stock returns in published research; KESTREL measures each from the chains it saves,
per stock and session, and the Options desk (desk.stocks.desks) combines them. Nothing here is assumed to work for
these stocks: the desk starts without a vote and earns one only from its own forward record (desk.stocks.validation).

* **iv_spread** (Cremers & Weinbaum 2010): the open-interest-weighted average of call IV − put IV over call-put pairs
  at the same strike and expiry, 10-60 days out. Calls priced richer than puts has preceded higher returns.
* **skew** (Xing, Zhang & Zhao 2010): the IV of an out-of-the-money put (|delta| nearest 0.225 within 0.15-0.35, else
  strike 4-12% below the price) minus the at-the-money call's, in the expiry nearest 30 days (10-60). Expensive downside
  protection has preceded weaker returns.
* **os_ratio** (Johnson & So 2012): option volume (contracts × 100, expiries within 120 days) over the session's share
  volume, and its log against the stock's own median of the previous ``OS_SESSIONS`` stored sessions. Heavy option
  trading relative to the stock has preceded weaker returns.
* **d_call_iv / d_put_iv** (An, Ang, Bali & Cakici 2014): the change in 30-day at-the-money call (put) IV against the
  row about ``CHANGE_SESSIONS`` sessions earlier. Rising call IV has preceded higher returns, rising put IV lower.
* **term_slope** (Vasquez 2017): 90-day minus 30-day at-the-money IV; with **iv_rv**, 30-day IV over the stock's
  realized volatility of the last 22 sessions (desk.stocks.vol_forecast's realized variance). These predicted option
  (straddle) returns rather than stock returns, so they inform the Learner and the stock page, not the desk.
* **borrow**: Schwab's ``htbRate`` as sent (unit unverified; only its rank across stocks is used) where a quote
  sends it; a stock Schwab says is easy to borrow (``isHardToBorrow`` false, no rate) counts as 0; no quote, no value.
  Much of the skew and IV-spread signal is the cost of shorting (borrow-fee studies), so borrow is measured on its own
  and recorded going forward only.

Quotes count only with a bid above zero, an ask at or above it, (ask − bid) / mid at most ``MAX_SPREAD``, a mid of at
least ``MIN_MID`` and a solvable Black-Scholes IV (the day's 3-month Treasury yield and the stock's trailing dividend
yield). A measure without enough such quotes is missing, with the reason in the row's snapshot; never zero.

Point in time: a row is computed from one session's chain and that session's share volume; the derived measures read
only that row and earlier rows (``read``), so a past day's signals never see a later day.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.contracts import OptionQuote

NAME = "stocks_option_signals"
VERSION = 1
JOB = "option_signals"
MAX_SPREAD = 0.5                 # (ask − bid) / mid
MIN_MID = 0.05
MAX_MONEYNESS = 0.30             # |ln K/S| for the IV spread's pairs
SPREAD_DAYS = (10, 60)
MIN_PAIRS = 3
SKEW_DAYS = (10, 60)
TARGET_DAYS = 30
PUT_DELTA = (0.15, 0.35, 0.225)  # band and target |delta| of the skew's put
PUT_STRIKE = (0.88, 0.96, 0.925)  # fallback band and target strike / price
ATM_BAND = 0.07                  # |ln K/S| of an at-the-money strike
TERM_DAYS = (7, 200)
FAR_DAYS = 90
NEAR_TOLERANCE, FAR_TOLERANCE = 12, 30
VOLUME_DAYS = 120
OS_SESSIONS, OS_MIN = 60, 20
CHANGE_SESSIONS = 5
CHANGE_BAND = (4, 8)             # sessions back the earlier row may be
FRESH_SESSIONS = 3               # a row this many sessions old still counts for a day's desk and page
MIN_LIQUID = 10                  # liquid contracts before a row counts at all
SETTLE = timedelta(minutes=20)
BACKFILL_SECONDS = 60.0

# The Options desk's components: research sign (+1: higher has preceded higher returns).
DESK_SIGNS = {"iv_spread": 1, "skew": -1, "os_ratio": -1, "d_call_iv": 1, "d_put_iv": -1, "borrow": -1}
SIGNALS = ("iv_spread", "skew", "os_ratio", "os_abnormal", "d_call_iv", "d_put_iv", "term_slope", "iv_rv", "borrow")
LABELS = {"iv_spread": "Calls vs puts", "skew": "Price of crash protection", "os_ratio": "Option vs share trading",
          "os_abnormal": "Option trading vs usual", "d_call_iv": "Call IV, past week", "d_put_iv": "Put IV, past week",
          "term_slope": "3-month vs 1-month IV", "iv_rv": "Options vs actual moves", "borrow": "Cost to borrow shares"}
RESEARCH = {"iv_spread": "Cremers & Weinbaum 2010", "skew": "Xing, Zhang & Zhao 2010", "os_ratio": "Johnson & So 2012",
            "os_abnormal": "Johnson & So 2012", "d_call_iv": "An, Ang, Bali & Cakici 2014",
            "d_put_iv": "An, Ang, Bali & Cakici 2014", "term_slope": "Vasquez 2017", "iv_rv": "Vasquez 2017",
            "borrow": "borrow-fee studies"}


def content() -> dict[str, Any]:
    return {"version": VERSION, "max_spread": MAX_SPREAD, "min_mid": MIN_MID, "max_moneyness": MAX_MONEYNESS,
            "spread_days": list(SPREAD_DAYS), "min_pairs": MIN_PAIRS, "pair_weight": "mean open interest of the pair",
            "skew_days": list(SKEW_DAYS), "target_days": TARGET_DAYS, "put_delta": list(PUT_DELTA),
            "put_strike": list(PUT_STRIKE), "atm_band": ATM_BAND, "term_days": list(TERM_DAYS), "far_days": FAR_DAYS,
            "tolerance": [NEAR_TOLERANCE, FAR_TOLERANCE], "interpolation": "total variance",
            "volume_days": VOLUME_DAYS, "os": "contracts x 100 / shares", "os_sessions": [OS_SESSIONS, OS_MIN],
            "change_sessions": CHANGE_SESSIONS, "change_band": list(CHANGE_BAND), "fresh_sessions": FRESH_SESSIONS,
            "min_liquid": MIN_LIQUID, "rv": "vol_forecast 22-session mean realized variance",
            "iv_model": "Black-Scholes at the mid, 3-month Treasury, trailing dividend yield",
            "borrow": "Schwab htbRate; isHardToBorrow false without a rate = 0; else missing",
            "desk_signs": DESK_SIGNS}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def _r(x: float | None, digits: int = 5) -> float | None:
    return None if x is None or not math.isfinite(x) else (x if x == 0 else float(f"{x:.{digits}g}"))


# ── plain English ────────────────────────────────────────────────────────────────────────────────
LEAN = {"iv_spread": 1, "skew": -1, "os_ratio": -1, "os_abnormal": -1, "d_call_iv": 1, "d_put_iv": -1, "borrow": -1}
BULL, BEAR = "historically a mildly bullish sign", "historically a mildly bearish sign"


def _pts(x: float) -> str:
    return f"{abs(x) * 100:.1f} points"


def text(key: str, v: float) -> str:
    """What the number says, in a sentence."""
    if key == "iv_spread":
        return ("Calls and puts priced alike" if abs(v) < 0.0005 else
                f"Calls priced {_pts(v)} of implied volatility richer than puts" if v > 0 else
                f"Puts priced {_pts(v)} of implied volatility richer than calls")
    if key == "skew":
        return f"Crash protection: out-of-the-money puts {_pts(v)} {'above' if v >= 0 else 'below'} at-the-money calls"
    if key == "os_ratio":
        return f"Options traded on {v:.0%} as many shares as the stock itself"
    if key == "os_abnormal":
        return f"Option trading {math.exp(v):.1f}× its usual share of the stock's volume"
    if key in ("d_call_iv", "d_put_iv"):
        side = "Call" if key == "d_call_iv" else "Put"
        return f"{side} implied volatility {'up' if v >= 0 else 'down'} {_pts(v)} over about a week"
    if key == "term_slope":
        return f"3-month options {_pts(v)} {'above' if v >= 0 else 'below'} 1-month options"
    if key == "iv_rv":
        return f"Options price {v:.2f}× the moves the stock made over the last month"
    if key == "borrow":
        return (f"Hard to borrow: Schwab quotes a borrow rate of {v:g} (higher = costlier to sell short)" if v > 0
                else "Easy to borrow: no borrow fee")
    raise KeyError(key)


def hint(key: str, v: float, percentile: float | None = None) -> str:
    """One line on what it has meant historically (``percentile``: among the stocks measured that day)."""
    if key == "term_slope":
        return ("Near-term options pricier: the market expects a bumpy few weeks (predicts option prices more than "
                "the stock's direction)" if v < 0 else "The usual shape: longer-dated options a little pricier")
    if key == "iv_rv":
        return ("Options price bigger moves than the stock has been making" if v > 1.2 else
                "Options price smaller moves than the stock has been making" if v < 0.8 else
                "Options price moves in line with recent ones")
    if key == "iv_spread":
        return "No lean either way" if abs(v) < 0.0005 else f"{'Calls' if v > 0 else 'Puts'} priced richer — " + (
            BULL if v > 0 else BEAR)
    if key in ("d_call_iv", "d_put_iv"):
        side = "call" if key == "d_call_iv" else "put"
        if abs(v) < 0.0005:
            return f"Little change in {side} prices — no lean"
        return f"{'Rising' if v > 0 else 'Falling'} {side} prices — " + (BULL if v * LEAN[key] > 0 else BEAR)
    if key == "borrow" and v == 0:
        return "Easy to borrow — no sign of costly short selling"
    if key == "os_abnormal":
        return ("Heavier option trading than usual for this stock — " + BEAR if v > math.log(1.5) else
                "Lighter option trading than usual — " + BULL if v < -math.log(1.5) else "About its usual option trading")
    higher = {"skew": "Steeper crash protection", "os_ratio": "Heavier option trading", "borrow": "Costlier to short"}[key]
    if percentile is None:
        return f"{higher} has been {BEAR}"
    if percentile >= 2 / 3:
        return f"{higher} than most stocks today — {BEAR}"
    if percentile <= 1 / 3:
        return f"Lower than most stocks today — {BULL}"
    return "About typical today — no lean"


def sentence(key: str, v: float) -> str:
    """A desk reason: the measure and the research behind it."""
    return f"{text(key, v)} ({RESEARCH[key]})"


# ── one session's chain ──────────────────────────────────────────────────────────────────────────
def liquid(c: opt.Contract) -> bool:
    q = c.quote
    return (c.iv is not None and c.mid is not None and c.mid >= MIN_MID and q.bid is not None and q.bid > 0
            and q.ask is not None and q.ask >= q.bid and c.spread_fraction is not None
            and c.spread_fraction <= MAX_SPREAD)


def at_days(points: Sequence[tuple[int, float]], days: int, tolerance: int) -> float | None:
    """IV at a constant ``days``: interpolated in total variance between the expiries either side, else the nearest
    expiry within ``tolerance`` days."""
    if not points:
        return None
    below = [p for p in points if p[0] <= days]
    above = [p for p in points if p[0] >= days]
    if below and above:
        (d0, v0), (d1, v1) = max(below), min(above)
        if d1 == d0:
            return v0
        w = v0 * v0 * d0 + (v1 * v1 * d1 - v0 * v0 * d0) * (days - d0) / (d1 - d0)
        return math.sqrt(w / days) if w > 0 else None
    nearest = min(points, key=lambda p: (abs(p[0] - days), p[0]))
    return nearest[1] if abs(nearest[0] - days) <= tolerance else None


def measure(chains: Mapping[date, Sequence[OptionQuote]], s: float, day: date, r: float, q: float = 0.0
            ) -> dict[str, Any]:
    """The raw measures of one session's chain against the stock price ``s`` (module docstring)."""
    missing: dict[str, str] = {}
    by_expiry: dict[date, list[opt.Contract]] = {}
    contracts = volume = 0
    for expiry, quotes in sorted(chains.items()):
        dte = (expiry - day).days
        contracts += len(quotes)
        if 0 <= dte <= VOLUME_DAYS:
            volume += sum(max(0, int(x.volume)) for x in quotes)
        if dte >= 2 and quotes:
            by_expiry[expiry] = [c for c in opt.analyse(quotes, s, day, r, q) if liquid(c)]
    n_liquid = sum(map(len, by_expiry.values()))

    # call-put IV spread: matched pairs 10-60 days out, weighted by the pair's open interest
    num = den = 0.0
    pairs = 0
    for expiry, cs in by_expiry.items():
        if not SPREAD_DAYS[0] <= (expiry - day).days <= SPREAD_DAYS[1]:
            continue
        calls = {c.quote.strike: c for c in cs if c.quote.kind == "call"}
        puts = {c.quote.strike: c for c in cs if c.quote.kind == "put"}
        for k in set(calls) & set(puts):
            if abs(math.log(k / s)) > MAX_MONEYNESS:
                continue
            w = (calls[k].quote.open_interest + puts[k].quote.open_interest) / 2
            pairs += 1
            if w > 0:
                num += w * ((calls[k].iv or 0.0) - (puts[k].iv or 0.0))
                den += w
    iv_spread = num / den if pairs >= MIN_PAIRS and den > 0 else None
    if iv_spread is None:
        missing["iv_spread"] = (f"{pairs} liquid call-put pairs 10-60 days out (needs {MIN_PAIRS})" if pairs < MIN_PAIRS
                                else "no open interest on the matched pairs")

    # at-the-money call and put IV per expiry, then at 30 and 90 days
    expiries: list[dict[str, Any]] = []
    call_pts: list[tuple[int, float]] = []
    put_pts: list[tuple[int, float]] = []
    for expiry, cs in by_expiry.items():
        dte = (expiry - day).days
        calls = {c.quote.strike: c for c in cs if c.quote.kind == "call"}
        puts = {c.quote.strike: c for c in cs if c.quote.kind == "put"}
        both = [k for k in set(calls) & set(puts) if abs(math.log(k / s)) <= ATM_BAND]
        quotes = chains[expiry]
        item: dict[str, Any] = {"days": dte, "volume": sum(x.volume for x in quotes),
                                "oi": sum(x.open_interest for x in quotes), "call_iv": None, "put_iv": None}
        if both:
            k = min(both, key=lambda x: (abs(x - s), x))
            item.update(strike=k, call_iv=_r(calls[k].iv, 4), put_iv=_r(puts[k].iv, 4))
            if TERM_DAYS[0] <= dte <= TERM_DAYS[1]:
                call_pts.append((dte, calls[k].iv or 0.0))
                put_pts.append((dte, puts[k].iv or 0.0))
        expiries.append(item)
    call30, put30 = at_days(call_pts, TARGET_DAYS, NEAR_TOLERANCE), at_days(put_pts, TARGET_DAYS, NEAR_TOLERANCE)
    atm_pts = [(d, (c + p) / 2) for (d, c), (_, p) in zip(call_pts, put_pts, strict=True)]
    iv30, iv90 = at_days(atm_pts, TARGET_DAYS, NEAR_TOLERANCE), at_days(atm_pts, FAR_DAYS, FAR_TOLERANCE)
    if iv30 is None:
        missing["iv30"] = "no at-the-money call and put near 30 days"
    if iv90 is None:
        missing["iv90"] = "no at-the-money call and put near 90 days"

    # skew: an out-of-the-money put against the at-the-money call, expiry nearest 30 days
    skew, legs = None, None
    for expiry in sorted((e for e in by_expiry if SKEW_DAYS[0] <= (e - day).days <= SKEW_DAYS[1]),
                         key=lambda e: (abs((e - day).days - TARGET_DAYS), e)):
        cs = by_expiry[expiry]
        atm = [c for c in cs if c.quote.kind == "call" and abs(math.log(c.quote.strike / s)) <= ATM_BAND]
        by_delta = [c for c in cs if c.quote.kind == "put" and c.greeks is not None
                    and PUT_DELTA[0] <= abs(c.greeks.delta) <= PUT_DELTA[1]]
        put = (min(by_delta, key=lambda c: (abs(abs(c.greeks.delta) - PUT_DELTA[2]) if c.greeks else 9, c.quote.strike))
               if by_delta else None)
        if put is None:
            by_strike = [c for c in cs if c.quote.kind == "put" and PUT_STRIKE[0] <= c.quote.strike / s <= PUT_STRIKE[1]]
            put = min(by_strike, key=lambda c: (abs(c.quote.strike / s - PUT_STRIKE[2]), c.quote.strike)) if by_strike \
                else None
        if atm and put is not None:
            call = min(atm, key=lambda c: (abs(c.quote.strike - s), c.quote.strike))
            skew = (put.iv or 0.0) - (call.iv or 0.0)
            legs = {"days": (expiry - day).days, "put_strike": put.quote.strike, "call_strike": call.quote.strike,
                    "put_delta": _r(put.greeks.delta, 3) if put.greeks else None}
            break
    if skew is None:
        missing["skew"] = "no liquid out-of-the-money put and at-the-money call 10-60 days out"
    return {"iv_spread": _r(iv_spread), "pairs": pairs, "skew": _r(skew), "call_iv30": _r(call30), "put_iv30": _r(put30),
            "iv30": _r(iv30), "iv90": _r(iv90), "option_volume": volume, "contracts": contracts, "liquid": n_liquid,
            "detail": {"expiries": expiries, "skew_legs": legs, "missing": missing}}


# ── storing ──────────────────────────────────────────────────────────────────────────────────────
def borrow_of(details: Mapping[str, Any] | None) -> tuple[float | None, int | None]:
    """(htbRate, hard-to-borrow flag) from a Schwab quote's reference section (parse_quote_details' keys)."""
    if not details:
        return None, None
    rate, flag = details.get("borrow_rate"), details.get("hard_to_borrow")
    rate = float(rate) if isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(rate) else None
    return rate, (None if not isinstance(flag, bool) else int(flag))


def realized(conn: sqlite3.Connection, symbol: str, day: date) -> float | None:
    """Realized volatility of the 22 sessions up to ``day`` from vol_forecast's realized variance, annualized."""
    from desk.stocks import vol_forecast as vf

    st = vf.state(conn, symbol, day)
    return None if st is None else math.sqrt(st.m * vf.TRADING_YEAR)


def share_volume(conn: sqlite3.Connection, symbol: str, day: date) -> float | None:
    row = conn.execute("SELECT volume FROM stk_bars WHERE symbol=? AND day=?", (symbol, day.toordinal())).fetchone()
    return float(row[0]) if row and row[0] else None


def save(conn: sqlite3.Connection, symbol: str, day: date, source: str, m: Mapping[str, Any], *, price: float,
         stock_volume: float | None, borrow: tuple[float | None, int | None], rv22: float | None, observed_at: datetime,
         now: datetime) -> None:
    conn.execute("INSERT OR REPLACE INTO stk_option_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        symbol, day.toordinal(), source, VERSION, price, m["iv_spread"], m["pairs"], m["skew"], m["call_iv30"],
        m["put_iv30"], m["iv30"], m["iv90"], _r(rv22), m["option_volume"], stock_volume, borrow[0], borrow[1],
        m["contracts"], m["liquid"], canonical_json(m["detail"]), to_iso(observed_at), to_iso(now)))


# ── reading, with the measures that need earlier rows ────────────────────────────────────────────
def _sessions(first: date, last: date) -> dict[int, int]:
    return {d.toordinal(): i for i, d in enumerate(cal.trading_days(first, last))}


def derive(rows: Sequence[Mapping[str, Any]], index: Mapping[int, int]) -> dict[str, Any]:
    """The signals of the newest of one stock's ``rows`` (oldest first, none after it), from it and earlier rows."""
    row = rows[-1]
    at = index.get(int(row["day"]))
    out: dict[str, Any] = {"day": date.fromordinal(int(row["day"])).isoformat(), "source": row["source"],
                           "liquid": row["liquid"], "pairs": row["pairs"], "iv30": row["iv30"],
                           "iv_spread": row["iv_spread"], "skew": row["skew"]}
    os_ratio = (row["option_volume"] * 100 / row["stock_volume"]
                if row["option_volume"] is not None and row["stock_volume"] else None)
    out["os_ratio"] = _r(os_ratio)
    past = [p["option_volume"] * 100 / p["stock_volume"] for p in rows[:-1][-OS_SESSIONS:]
            if p["option_volume"] is not None and p["stock_volume"]]
    median = statistics.median(past) if len(past) >= OS_MIN else None
    out["os_abnormal"] = _r(math.log(os_ratio / median)) if os_ratio and median else None
    earlier = None
    if at is not None:
        candidates = [p for p in rows[:-1] if (j := index.get(int(p["day"]))) is not None
                      and CHANGE_BAND[0] <= at - j <= CHANGE_BAND[1]]
        if candidates:
            earlier = min(candidates, key=lambda p: (abs(at - index[int(p["day"])] - CHANGE_SESSIONS), -int(p["day"])))
    for key, col in (("d_call_iv", "call_iv30"), ("d_put_iv", "put_iv30")):
        out[key] = (_r(row[col] - earlier[col]) if earlier is not None and row[col] is not None
                    and earlier[col] is not None else None)
    out["term_slope"] = _r(row["iv90"] - row["iv30"]) if row["iv90"] is not None and row["iv30"] is not None else None
    out["iv_rv"] = _r(row["iv30"] / row["rv22"]) if row["iv30"] and row["rv22"] else None
    rate, flag = row["borrow_rate"], row["hard_to_borrow"]
    out["borrow"] = rate if rate is not None else (0.0 if flag == 0 else None)
    out["hard_to_borrow"] = None if flag is None else bool(flag)
    detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
    out["missing"] = detail.get("missing", {})
    return out


def read(conn: sqlite3.Connection, asof: date, symbols: Iterable[str] | None = None, *,
         fresh: int = FRESH_SESSIONS, computed_by: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Each stock's signals as of ``asof``: its newest row no more than ``fresh`` sessions old with at least
    ``MIN_LIQUID`` liquid contracts, derived from rows on or before it (``computed_by``: only rows written by then)."""
    wanted = None if symbols is None else set(symbols)
    first = asof - timedelta(days=OS_SESSIONS * 7 // 5 + 30)
    sessions = cal.trading_days(asof - timedelta(days=fresh * 2 + 7), asof)
    oldest = sessions[-min(len(sessions), fresh + 1)] if sessions else asof
    sql = "SELECT * FROM stk_option_signals WHERE day>=? AND day<=? AND computed_at<=?"
    args: list[Any] = [first.toordinal(), asof.toordinal(), to_iso(computed_by) if computed_by else "9999"]
    if wanted is not None and len(wanted) <= 200:
        sql += f" AND symbol IN ({','.join('?' * len(wanted))})"
        args += sorted(wanted)
    try:
        rows = conn.execute(sql + " ORDER BY symbol, day", args).fetchall()
    except sqlite3.OperationalError:          # a database from before the option signals
        return {}
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if wanted is None or r["symbol"] in wanted:
            by[r["symbol"]].append(r)
    index = _sessions(first, asof)
    out = {}
    for symbol, history in by.items():
        usable = [r for r in history if r["liquid"] >= MIN_LIQUID]
        if usable and int(usable[-1]["day"]) >= oldest.toordinal():
            out[symbol] = derive(usable, index)
    return out


def percentiles(signals: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Each stock's percentile (0-1, ties averaged) on each signal among the stocks that have it."""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for key in SIGNALS:
        values = sorted((float(v[key]), s) for s, v in signals.items() if v.get(key) is not None)
        n = len(values)
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[j + 1][0] == values[i][0]:
                j += 1
            for k in range(i, j + 1):
                out[values[k][1]][key] = 0.5 if n == 1 else ((i + j) / 2) / (n - 1)
            i = j + 1
    return dict(out)


# ── backfill from the collected chains ───────────────────────────────────────────────────────────
def pending(conn: sqlite3.Connection, through: date) -> list[tuple[str, int]]:
    """Collected (stock, session) chains up to ``through`` without a row (nor a recorded failure), newest first."""
    return [(r[0], int(r[1])) for r in conn.execute(
        "SELECT DISTINCT c.underlying, c.day FROM stk_option_chains c WHERE c.day<=? AND NOT EXISTS (SELECT 1 FROM "
        "stk_option_signals s WHERE s.symbol=c.underlying AND s.day=c.day) AND NOT EXISTS (SELECT 1 FROM "
        "stk_option_scan f WHERE f.symbol=c.underlying AND f.day=c.day AND f.status='failed') "
        "ORDER BY c.day DESC, c.underlying", (through.toordinal(),))]


class Pricing:
    """The rate and dividend yield Black-Scholes reads on a day (as the IV history solves its IVs)."""

    def __init__(self, market: Any) -> None:
        from desk.stocks.backtest import rate_series
        from desk.stocks.paper.fills import Dividends

        self.rate: Callable[[date], float] = rate_series(market)
        self.dividends = Dividends(market.dividends(), market.splits())

    def yield_on(self, symbol: str, day: date, s: float) -> float:
        return self.dividends.yield_on(symbol, day, s)


def backfill(market: Any, now: datetime, *, budget_seconds: float | None = BACKFILL_SECONDS, limit: int | None = None,
             progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Rows for collected chains of finished sessions, newest first, within ``budget_seconds`` (resumable: the rest
    next pass, ``remaining``)."""
    from desk.storage.db import transaction

    conn = market.conn
    through = cal.last_closed_session(now, SETTLE)
    todo = pending(conn, through)
    started, done, failed = time.monotonic(), 0, 0
    pricing = Pricing(market) if todo else None
    for i, (symbol, n) in enumerate(todo):
        if (budget_seconds is not None and time.monotonic() - started > budget_seconds) or (
                limit is not None and i >= limit):
            break
        assert pricing is not None
        day = date.fromordinal(n)
        got, chains = market.option_chain(symbol, day)
        price = market.chain_underlying_price(symbol, day)
        if price is None:
            row = conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?", (symbol, n)).fetchone()
            price = float(row[0]) if row and row[0] else None
        with transaction(conn):
            if got != day or not price or price <= 0:
                conn.execute("INSERT OR REPLACE INTO stk_option_scan VALUES (?,?,?,?,?)",
                             (n, symbol, "failed", "backfill: no chain or stock price for the session", to_iso(now)))
                failed += 1
                continue
            observed = conn.execute("SELECT MAX(observed_at) FROM stk_option_chains WHERE underlying=? AND day=?",
                                    (symbol, n)).fetchone()[0]
            m = measure(chains, price, day, pricing.rate(day), pricing.yield_on(symbol, day, price))
            save(conn, symbol, day, "collected", m, price=price, stock_volume=share_volume(conn, symbol, day),
                 borrow=(None, None), rv22=realized(conn, symbol, day), observed_at=datetime.fromisoformat(observed),
                 now=now)
        done += 1
        if progress and (i + 1) % 50 == 0:
            progress(f"option signals: {i + 1} of {len(todo)} stored chains")
    return {"computed": done, "failed": failed, "remaining": len(todo) - done - failed, "through": through.isoformat(),
            **coverage(conn)}


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(day), MAX(day) FROM stk_option_signals").fetchone()
    by = dict(conn.execute("SELECT source, COUNT(*) FROM stk_option_signals GROUP BY source").fetchall())
    return {"rows": row[0], "stocks": row[1], "first": date.fromordinal(row[2]).isoformat() if row[2] else None,
            "last": date.fromordinal(row[3]).isoformat() if row[3] else None,
            "rows_by_source": {k: int(v) for k, v in by.items()}}


async def job(market: Any, now: datetime) -> dict[str, Any]:
    """The collection worker's step: signals for newly finished sessions' collected chains (``option_signals``)."""
    try:
        out = backfill(market, now)
    except (sqlite3.Error, ArithmeticError, ValueError) as exc:     # recorded; never stops collection
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
        market.job(JOB, now, error=error)
        return {"ok": False, "error": error}
    market.job(JOB, now, detail=out)
    return {"ok": True, **out}


__all__ = ["DESK_SIGNS", "JOB", "LABELS", "LEAN", "NAME", "RESEARCH", "SIGNALS", "VERSION", "at_days", "backfill",
           "borrow_of", "content", "coverage", "derive", "hint", "job", "liquid", "measure", "pending", "percentiles",
           "read", "realized", "save", "sentence", "share_volume", "text", "version_hash"]
