"""Earnings: the move options price for a results report against the move KESTREL expects (docs/STOCKS_PLAYS.md
"Earnings: priced vs expected move").

* **When.** The report's date and timing come from the point-in-time calendar (``earnings_dates.known``): a date
  announced in Finnhub's free calendar (confirmed) else one estimated from past results filings, kept apart. The
  timing gives the *entry* session (the last close before the results) and the *exit* session (the first close that
  reflects them): before the open or during the session, the session before → that day; after the close, that day →
  the next; unknown, the session before → the session after.
* **Priced move** (Dubinsky & Johannes 2006). From one chain, each expiry's at-the-money implied volatility gives its
  total variance w = IV² × years, over n trading sessions to expiry. A normal session's variance v is w₀/n₀ from the
  latest expiry before the results, else (w₂ − w₁)/(n₂ − n₁) from the first two expiries after them. The results
  session's variance is σ² = w₁ − v (n₁ − 1) at the first expiry after the results, and the priced move is the
  expected absolute move of a normal with that variance, σ √(2/π). The at-the-money straddle of that expiry over the
  stock price is kept beside it (it also prices the ordinary sessions to expiry).
* **Past moves.** Each past report's close-to-close move from the entry to the exit session, split-adjusted, from
  the saved daily bars; stored with the move priced by the last chain observed by 4:15 PM New York on the entry
  session, when one was stored (``stk_earnings_moves``, filled by the daily job ``earnings_moves``, resumable).
* **Expected move.** The mean absolute move of the stock's last ``HISTORY`` reports, shrunk by n / (n + ``SHRINK``)
  toward the priced move × the realized-to-priced ratio of past reports in its sector (at least ``SECTOR_MIN``
  reports, else all stocks, none 1.0), all measured before the decision session.
* **Verdict.** expected ÷ priced ≥ ``CHEAP`` → options look cheap; ≤ ``EXPENSIVE`` → expensive; else fair. Fewer
  than ``MIN_REPORTS`` past reports → no verdict. Confidence says how clearly the stock's own record departs from
  the priced move (standard errors) and how long that record is.

Point in time: a decision on session S reads S's chain, reports known by then, and past moves whose exit session is
before S. Missing pieces are missing with a reason, never filled in.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
import time as clock
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import earnings_dates as ed
from desk.stocks import options as opt
from desk.stocks.contracts import OptionQuote
from desk.stocks.repo import date_of, day_of

NAME = "stocks_earnings_model"
VERSION = 1
JOB = "earnings_moves"
HISTORY = 12                     # past reports in the typical move
SHRINK = 4                       # n / (n + SHRINK) on the stock's own record
MIN_REPORTS = 4
CHEAP, EXPENSIVE = 1.15, 0.85    # expected ÷ priced
SECTOR_MIN = 20
RATIO_BOUNDS = (0.5, 2.0)        # the sector's realized-to-priced ratio, clipped
ATM_BAND = 0.10                  # |ln K/S| of an at-the-money strike
MAX_SPREAD = 0.5                 # (ask − bid) / mid of an at-the-money quote
MIN_MID = 0.05
CHAIN_BY = timedelta(minutes=15)  # history: the last chain observed by 15 minutes after the entry session's close
LOOKAHEAD = 150                  # days of expiries read from a chain
PASS_LIMIT = 400                 # past reports a pass
BUDGET_SECONDS = 60.0
FRESH_DAYS = 5                   # the stock page shows a verdict only from a chain at most this many days old
VERDICTS = {"cheap": "Options look cheap for this report", "expensive": "Options look expensive for this report",
            "fair": "Options look fairly priced for this report"}


def content() -> dict[str, Any]:
    return {"version": VERSION, "history": HISTORY, "shrink": SHRINK, "min_reports": MIN_REPORTS,
            "cheap": CHEAP, "expensive": EXPENSIVE, "sector_min": SECTOR_MIN, "ratio_bounds": list(RATIO_BOUNDS),
            "atm_band": ATM_BAND, "max_spread": MAX_SPREAD, "min_mid": MIN_MID,
            "chain_by_minutes_after_close": CHAIN_BY.total_seconds() / 60, "lookahead_days": LOOKAHEAD,
            "grace_days": ed.GRACE.days, "window_days": ed.WINDOW,
            "implied": "Dubinsky-Johannes: w = IV^2 years, v = w0/n0 else (w2-w1)/(n2-n1), s2 = w1 - v(n1-1), "
                       "move = s sqrt(2/pi)",
            "realized": "split-adjusted close entry -> exit (before/during: prev -> day; after: day -> next; "
                        "unknown: prev -> next)",
            "expected": "mean |move| of the last reports, n/(n+shrink) toward implied x sector ratio",
            "sector": "Fama-French 12 from SIC", "iv_model": "Black-Scholes at the mid, 3-month Treasury, trailing "
                                                              "dividend yield (Plays: its own rate, no dividend)"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def _r(x: float | None, digits: int = 5) -> float | None:
    return None if x is None or not math.isfinite(x) else (x if x == 0 else float(f"{x:.{digits}g}"))


# ── the priced move ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Atm:
    expiry: date
    sessions: int        # trading sessions after the chain's day up to and including the expiry
    years: float
    iv: float            # mean of the at-the-money call's and put's implied volatility
    straddle: float      # their mids added
    strike: float

    @property
    def w(self) -> float:
        return self.iv * self.iv * self.years


def sessions_to(day: date, expiry: date) -> int:
    return len(cal.trading_days(day + timedelta(days=1), expiry))


def _usable(c: opt.Contract) -> bool:
    q = c.quote
    return (c.iv is not None and c.mid is not None and c.mid >= MIN_MID and q.bid is not None and q.bid > 0
            and q.ask is not None and q.ask >= q.bid and c.spread_fraction is not None
            and c.spread_fraction <= MAX_SPREAD)


def atm_points(chains: Mapping[date, Sequence[OptionQuote]], s: float, day: date, r: float, q: float = 0.0
               ) -> list[Atm]:
    """Each expiry's at-the-money point: the strike nearest the price with a usable call and put (within
    ``ATM_BAND``), their mean implied volatility and their straddle. Only near-the-money quotes are solved."""
    out = []
    for expiry, quotes in sorted(chains.items()):
        if not day < expiry <= day + timedelta(days=LOOKAHEAD):
            continue
        near = [x for x in quotes if x.strike > 0 and abs(math.log(x.strike / s)) <= ATM_BAND]
        cs = [c for c in opt.analyse(near, s, day, r, q) if _usable(c)]
        calls = {c.quote.strike: c for c in cs if c.quote.kind == "call"}
        puts = {c.quote.strike: c for c in cs if c.quote.kind == "put"}
        both = set(calls) & set(puts)
        if not both:
            continue
        k = min(both, key=lambda x: (abs(x - s), x))
        call, put = calls[k], puts[k]
        n = sessions_to(day, expiry)
        if n < 1 or call.iv is None or put.iv is None or call.mid is None or put.mid is None:
            continue
        out.append(Atm(expiry, n, opt.year_fraction(day, expiry), (call.iv + put.iv) / 2, call.mid + put.mid, k))
    return out


def implied(points: Sequence[Atm], s: float, entry: date, exit_: date) -> dict[str, Any]:
    """The results-day move priced by one chain (module docstring), with the straddle of the first expiry after the
    results; each missing piece says why."""
    pre = [p for p in points if p.expiry <= entry]
    post = [p for p in points if p.expiry >= exit_]
    out: dict[str, Any] = {"move": None, "straddle": None, "expiry": None, "basis": None, "note": None,
                           "sigma": None, "normal": None}
    if not post:
        out["note"] = "No expiry after the results with an at-the-money call and put"
        return out
    p1 = post[0]
    out.update(straddle=_r(p1.straddle / s), expiry=p1.expiry.isoformat())
    v = None
    if pre:
        p0 = pre[-1]
        v, out["basis"] = p0.w / p0.sessions, "pre_expiry"
    elif len(post) >= 2 and post[1].sessions > p1.sessions:
        p2 = post[1]
        v, out["basis"] = (p2.w - p1.w) / (p2.sessions - p1.sessions), "post_expiries"
    if v is None:
        out["note"] = "Needs an expiry before the results, or two after them, to separate an ordinary day's move"
        return out
    if v <= 0:
        out["note"] = "The later expiry prices less variance than the earlier one: no ordinary-day variance to read"
        return out
    sigma2 = p1.w - v * (p1.sessions - 1)
    out["normal"] = _r(math.sqrt(v))
    if sigma2 <= 0:
        out["note"] = "The expiries price no extra move for the results day"
        return out
    out.update(sigma=_r(math.sqrt(sigma2)), move=_r(math.sqrt(sigma2) * math.sqrt(2 / math.pi)))
    return out


# ── past moves ───────────────────────────────────────────────────────────────────────────────────
def realized_move(conn: sqlite3.Connection, symbol: str, entry: date, exit_: date) -> tuple[float | None, str | None]:
    """Close-to-close move from ``entry`` to ``exit_``, adjusted for any split between them."""
    closes = dict(conn.execute("SELECT day, close FROM stk_bars WHERE symbol=? AND day IN (?, ?)",
                               (symbol, day_of(entry), day_of(exit_))).fetchall())
    a, b = closes.get(day_of(entry)), closes.get(day_of(exit_))
    if not a or not b or a <= 0 or b <= 0:
        missing = entry if not a or a <= 0 else exit_
        return None, f"No stored close for {missing.isoformat()}"
    ratio = 1.0
    for frm, to in conn.execute("SELECT split_from, split_to FROM stk_splits WHERE symbol=? AND execution_date>? "
                                "AND execution_date<=?", (symbol, day_of(entry), day_of(exit_))):
        if frm and to and frm > 0 and to > 0:
            ratio *= to / frm
    return b / (a / ratio) - 1, None


def history(conn: sqlite3.Connection, symbol: str, before: date, limit: int = HISTORY) -> list[dict[str, Any]]:
    """The stock's newest stored past reports whose exit session is before ``before``, newest first."""
    try:
        cur = conn.execute("SELECT * FROM stk_earnings_moves WHERE symbol=? AND exit_day<? AND realized IS NOT NULL "
                           "ORDER BY report_date DESC LIMIT ?", (symbol, day_of(before), limit))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    except sqlite3.OperationalError:          # a database from before the earnings model
        return []


def sectors(conn: sqlite3.Connection) -> dict[str, str]:
    from desk.stocks.sectors import sector

    return {s: sector(sic) for s, sic in conn.execute(
        "SELECT l.symbol, c.sic FROM stk_listings l JOIN stk_companies c ON c.cik = l.cik")}


class Priors:
    """The realized-to-priced ratio of past reports (exit before ``before``) per sector, and for all stocks."""

    def __init__(self, conn: sqlite3.Connection, before: date) -> None:
        self.sector_of = sectors(conn)
        sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        try:
            rows = conn.execute("SELECT symbol, realized, implied FROM stk_earnings_moves WHERE exit_day<? AND "
                                "realized IS NOT NULL AND implied > 0", (day_of(before),)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for symbol, realized, priced in rows:
            for key in (self.sector_of.get(symbol, "Other"), "*"):
                sums[key][0] += abs(realized)
                sums[key][1] += priced
                sums[key][2] += 1
        self.sums = dict(sums)

    def ratio(self, symbol: str) -> tuple[float, str]:
        sector = self.sector_of.get(symbol, "Other")
        for key, label in ((sector, f"{sector} sector"), ("*", "all stocks")):
            got = self.sums.get(key)
            if got and got[1] > 0 and (got[2] >= SECTOR_MIN or key == "*"):
                value = min(RATIO_BOUNDS[1], max(RATIO_BOUNDS[0], got[0] / got[1]))
                return value, f"{label}: {int(got[2])} past reports moved {value:.2f}× what options priced"
        return 1.0, "no past reports with a priced move yet: assumes options price the move fairly"


# ── the edge ─────────────────────────────────────────────────────────────────────────────────────
LEVELS = ("low", "medium", "high")


def edge(moves: Sequence[float], priced: float | None, prior: float) -> dict[str, Any]:
    """Expected move, its ratio to the priced move, the verdict and its confidence (module docstring)."""
    absolute = [abs(m) for m in moves]
    n = len(absolute)
    typical = statistics.fmean(absolute) if n else None
    out: dict[str, Any] = {"reports": n, "typical": _r(typical), "expected": None, "ratio": None, "weight": None,
                           "verdict": None, "confidence": None, "note": None}
    if priced is None or priced <= 0:
        out["note"] = "No priced move: the chain could not separate the results day"
        return out
    w = n / (n + SHRINK)
    expected = (w * typical if typical is not None else 0.0) + (1 - w) * priced * prior
    ratio = expected / priced
    out.update(expected=_r(expected), ratio=_r(ratio), weight=_r(w))
    if n < MIN_REPORTS:
        out["note"] = f"{n} past reports with a stored move (needs {MIN_REPORTS})"
        return out
    out["verdict"] = "cheap" if ratio >= CHEAP else "expensive" if ratio <= EXPENSIVE else "fair"
    by_n = 2 if n >= 8 else 1 if n >= 6 else 0
    if out["verdict"] == "fair" or typical is None:
        level = by_n
    else:
        sd = statistics.pstdev(absolute)
        z = math.inf if sd == 0 else abs(typical - priced) / (sd / math.sqrt(n))
        level = min(by_n, 2 if z >= 2 else 1 if z >= 1 else 0)
    out["confidence"] = LEVELS[level]
    return out


def outlook(conn: sqlite3.Connection, symbol: str, day: date, report: Mapping[str, Any],
            points: Sequence[Atm], price: float, priors: Priors) -> dict[str, Any] | None:
    """Everything about the stock's next report as seen from ``day``'s chain: when, the priced move, the past moves,
    the expected move and the verdict. None when the report is not ahead of the chain (its entry session passed)."""
    entry, exit_ = date.fromisoformat(report["entry"]), date.fromisoformat(report["exit"])
    if entry < day or exit_ <= day:
        return None
    priced = implied(points, price, entry, exit_)
    moves = [float(m["realized"]) for m in history(conn, symbol, day)]
    prior, prior_basis = priors.ratio(symbol)
    e = edge(moves, priced["move"], prior)
    return {"version": VERSION, "chain_day": day.isoformat(), "price": price,
            "report": {k: report.get(k) for k in ("date", "to", "confirmed", "timing", "source")},
            "entry": entry.isoformat(), "exit": exit_.isoformat(), "implied": priced["move"],
            "straddle": priced["straddle"], "expiry": priced["expiry"], "basis": priced["basis"],
            "prior": _r(prior), "prior_basis": prior_basis, **{k: v for k, v in e.items() if k != "note"},
            "note": priced["note"] or e["note"], "recent": [_r(m, 4) for m in moves[:HISTORY]]}


def _pct(x: float) -> str:
    return f"{x:.1%}"


def text(o: Mapping[str, Any]) -> str:
    """The card's sentence."""
    if o.get("implied") is None:
        return f"KESTREL can't read the results-day move from this chain: {o.get('note') or 'missing'}"
    out = f"Options price a ±{_pct(o['implied'])} results-day move"
    if o.get("expected") is not None:
        out += f"; KESTREL expects ±{_pct(o['expected'])}"
    if o.get("typical") is not None:
        out += f" (its last {o['reports']} reports moved ±{_pct(o['typical'])} on average)"
    return out


def when_text(report: Mapping[str, Any]) -> str:
    d = date.fromisoformat(report["date"])
    if report.get("confirmed"):
        return f"Results on {d:%b %-d}, {report.get('timing') or 'time not given'} (announced)"
    return (f"Results expected {d:%b %-d}–{date.fromisoformat(report['to']):%b %-d} "
            "(estimated from past results filings)")


# ── the stock page ───────────────────────────────────────────────────────────────────────────────
def view(conn: sqlite3.Connection, symbol: str, now: datetime) -> dict[str, Any] | None:
    """The stock page's earnings block: the next report's priced, typical and expected moves from the newest stored
    chain, only when that chain is at most ``FRESH_DAYS`` old and a report lies ahead of it."""
    from desk.stocks import option_signals as osg
    from desk.stocks.repo import StocksMarket

    market = StocksMarket(conn)
    day, chains = market.option_chain(symbol)
    today = cal.new_york(now).date()
    if day is None or (today - day).days > FRESH_DAYS or not chains:
        return None
    report = ed.known(conn, day, now, {symbol}).get(symbol)
    price = market.chain_underlying_price(symbol, day)
    if report is None or not price or price <= 0:
        return None
    pricing = osg.Pricing(market)
    points = atm_points(chains, price, day, pricing.rate(day), pricing.yield_on(symbol, day, price))
    o = outlook(conn, symbol, day, report, points, price, Priors(conn, day))
    if o is None:
        return None
    return {**o, "title": VERDICTS.get(o["verdict"] or "", "Not enough past reports to judge this one"),
            "text": text(o), "when": when_text(report)}


# ── the daily job: past reports' realized and priced moves ───────────────────────────────────────
def past_reports(conn: sqlite3.Connection, through: date) -> list[tuple[str, date, str | None, str]]:
    """Every listed stock's past reports whose exit session is on or before ``through``: announced dates that stayed
    listed (Finnhub), else results filings (8-K item 2.02) not within a few days of one; (symbol, day, timing, source)."""
    listed = ed.listed_past(conn, through)
    filed = ed.filed_reports(conn)
    out = []
    for symbol in sorted(set(listed) | set(filed)):
        announced = listed.get(symbol, [])
        reports = [(d, t, "finnhub") for d, t in announced]
        reports += [(d, t, "sec_8k") for d, t in filed.get(symbol, [])
                    if all(abs((d - a).days) > ed.SAME_REPORT for a, _ in announced)]
        for d, t, source in reports:
            if ed.sessions(d, t)[1] <= through:
                out.append((symbol, d, t, source))
    return out


def pending(conn: sqlite3.Connection, through: date) -> list[tuple[str, date, str | None, str]]:
    """Past reports without a stored row, newest first."""
    done = {(s, int(d)) for s, d in conn.execute("SELECT symbol, report_date FROM stk_earnings_moves")}
    todo = [r for r in past_reports(conn, through) if (r[0], day_of(r[1])) not in done]
    return sorted(todo, key=lambda r: (-day_of(r[1]), r[0]))


def measure(market: Any, symbol: str, report: date, timing: str | None, rate: Callable[[date], float],
            yield_on: Callable[[str, date, float], float]) -> dict[str, Any]:
    """One past report's row: its realized move and the move priced by the last chain observed by 4:15 PM (15
    minutes after the close) on its entry session."""
    conn = market.conn
    entry, exit_ = ed.sessions(report, timing)
    realized, note = realized_move(conn, symbol, entry, exit_)
    row: dict[str, Any] = {"entry": entry, "exit": exit_, "realized": realized, "implied": None, "straddle": None,
                           "basis": None, "expiry": None, "observed": None, "note": note}
    cutoff = cal.session_close(entry) + CHAIN_BY
    got, chains = market.option_chain(symbol, entry, observed_by=cutoff)
    if got != entry or not chains:
        row["note"] = "; ".join(x for x in (note, "No chain stored for the session before the results") if x)
        return row
    price = market.chain_underlying_price(symbol, entry)
    if not price or price <= 0:
        row["note"] = "; ".join(x for x in (note, "No stock price stored with that chain") if x)
        return row
    priced = implied(atm_points(chains, price, entry, rate(entry), yield_on(symbol, entry, price)), price, entry, exit_)
    observed = conn.execute("SELECT MAX(observed_at) FROM stk_option_chains WHERE underlying=? AND day=? AND "
                            "observed_at<=?", (symbol, day_of(entry), to_iso(cutoff))).fetchone()[0]
    row.update(implied=priced["move"], straddle=priced["straddle"], basis=priced["basis"], observed=observed,
               expiry=date.fromisoformat(priced["expiry"]) if priced["expiry"] else None,
               note="; ".join(x for x in (note, priced["note"]) if x) or None)
    return row


def backfill(market: Any, now: datetime, *, limit: int = PASS_LIMIT, budget_seconds: float | None = BUDGET_SECONDS,
             progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Rows for past reports, newest first, at most ``limit`` a pass (resumable: ``remaining`` next pass)."""
    from desk.stocks import option_signals as osg
    from desk.storage.db import transaction

    conn = market.conn
    bars = conn.execute("SELECT MAX(day) FROM stk_bars").fetchone()[0]
    through = min(cal.last_closed_session(now), date_of(bars)) if bars else None
    if through is None:
        return {"computed": 0, "remaining": 0, "through": None, **coverage(conn)}
    todo = pending(conn, through)
    pricing = osg.Pricing(market) if todo else None
    started, done = clock.monotonic(), 0
    for i, (symbol, report, timing, source) in enumerate(todo[:limit]):
        if budget_seconds is not None and clock.monotonic() - started > budget_seconds:
            break
        assert pricing is not None
        row = measure(market, symbol, report, timing, pricing.rate, pricing.yield_on)
        with transaction(conn):
            conn.execute("INSERT OR IGNORE INTO stk_earnings_moves VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                symbol, day_of(report), timing, source, day_of(row["entry"]), day_of(row["exit"]),
                _r(row["realized"]), row["implied"], row["straddle"], row["basis"],
                day_of(row["expiry"]) if row["expiry"] else None, row["observed"], VERSION, row["note"], to_iso(now)))
        done += 1
        if progress and (i + 1) % 50 == 0:
            progress(f"earnings moves: {i + 1} of {len(todo)} past reports")
    return {"computed": done, "remaining": len(todo) - done, "through": through.isoformat(), **coverage(conn)}


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), COUNT(implied), MIN(report_date), MAX(report_date) "
                       "FROM stk_earnings_moves").fetchone()
    return {"rows": row[0], "stocks": row[1], "with_implied": row[2],
            "first": date_of(row[3]).isoformat() if row[3] else None,
            "last": date_of(row[4]).isoformat() if row[4] else None}


async def job(market: Any, now: datetime) -> dict[str, Any]:
    """The collection worker's step (``earnings_moves``): past reports' realized and priced moves."""
    try:
        out = backfill(market, now)
    except (sqlite3.Error, ArithmeticError, ValueError) as exc:     # recorded; never stops collection
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
        market.job(JOB, now, error=error)
        return {"ok": False, "error": error}
    market.job(JOB, now, detail=out)
    return {"ok": True, **out}


__all__ = ["CHEAP", "EXPENSIVE", "HISTORY", "JOB", "MIN_REPORTS", "NAME", "SHRINK", "VERDICTS", "VERSION", "Atm",
           "Priors", "atm_points", "backfill", "content", "coverage", "edge", "history", "implied", "job", "measure",
           "outlook", "past_reports", "pending", "realized_move", "sessions_to", "text", "version_hash", "view",
           "when_text"]
