"""Announced earnings dates, stored daily, for Plays' earnings sections and the stock page.

Of the sources the app can use, only Finnhub's free plan (the optional ``FINNHUB_API_KEY`` the stock page already
reads) publishes a forward earnings calendar: Massive's Stocks Basic has none (its earnings data is a paid partner
add-on), Schwab's market data has the last report date but no next one, and the SEC publishes filings after the fact.
Nasdaq's calendar pages refuse programs, so they are not used. With the key, once a day the next four weeks' calendar
for every stock is read (one request a week of dates) and kept per stock and date with when it was first and last
listed, so a moved date drops out and a past day only sees what was announced by then. Without it, report dates stay
estimated from past 8-K results filings (a ±7-day window around the median gap; desk.stocks.signals.events), and the
pages say which one they show.

Point in time (``known``): a calendar row counts at a decision time only if it was first seen by then and was still
listed in the newest calendar read by then, or kept being listed up to its report day (a moved or withdrawn date drops
out; a new one never shows before it was first seen). Without one, the report is estimated from the results filings
(8-K item 2.02) accepted by then. Confirmed and estimated reports stay apart (``confirmed``), and a results filing's
acceptance time in New York gives its timing: before the open, during market hours or after the close.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from typing import Any

from desk.core.clock import to_iso
from desk.stocks import calendar as cal
from desk.stocks.collect import _job
from desk.stocks.repo import StocksMarket, date_of, day_of
from desk.stocks.suite import StocksSuite
from desk.stocks.universe import stored_symbols
from desk.storage.db import transaction

JOB = "earnings_calendar"
SOURCE = "finnhub"
LABEL = "Finnhub earnings calendar"
WEEKS = 4
GRACE = timedelta(days=3)            # a date still listed this close to its report day counts as kept
SEC_LABEL = "Estimated from past results filings (8-K item 2.02)"
BEFORE, DURING, AFTER = "before the open", "during market hours", "after the close"
OPEN = time(9, 30)
SAME_REPORT = 5                      # results filings this many days apart are one report (an amendment, an exhibit)
WINDOW = 7                           # ± days around the estimated date


def save(conn: sqlite3.Connection, rows: list[dict[str, Any]], keep: set[str], now: datetime) -> int:
    """Upsert this run's listed dates (first seen kept); every date listed in the run gets the run's time."""
    stamp = to_iso(now)
    kept = [r for r in rows if r.get("symbol") in keep and r.get("date")]
    with transaction(conn):
        conn.executemany(
            "INSERT INTO stk_earnings_dates(symbol, report_date, hour, source, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(symbol, report_date) DO UPDATE SET hour = excluded.hour, "
            "last_seen = excluded.last_seen",
            [(r["symbol"], day_of(date.fromisoformat(r["date"])), r.get("when"), SOURCE, stamp, stamp) for r in kept])
    return len(kept)


def announced(conn: sqlite3.Connection, session: date) -> dict[str, dict[str, Any]]:
    """Each stock's next announced report on or after ``session``, from the newest calendar read (a date that has
    dropped out of it was moved or withdrawn)."""
    try:
        latest = conn.execute("SELECT MAX(last_seen) FROM stk_earnings_dates").fetchone()[0]
    except sqlite3.OperationalError:
        return {}
    if latest is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for symbol, day, hour in conn.execute(
            "SELECT symbol, report_date, hour FROM stk_earnings_dates WHERE last_seen = ? AND report_date >= ? "
            "ORDER BY report_date", (latest, day_of(session))):
        out.setdefault(symbol, {"date": date_of(day).isoformat(), "when": hour, "source": LABEL, "seen": latest})
    return out


# ── point in time ────────────────────────────────────────────────────────────────────────────────
def _ny_day(stamp: str) -> date:
    return cal.new_york(datetime.fromisoformat(stamp)).date()


def timing_of(accepted: datetime | None, filed: date) -> tuple[date, str | None]:
    """(report day, timing) of a results filing from its acceptance time in New York: before 9:30 → before the open,
    before the close → during market hours, else after the close; a non-trading day → before the next session's open.
    Without an acceptance time, the filing day with the timing unknown."""
    if accepted is None:
        return filed, None
    local = cal.new_york(accepted)
    d = local.date()
    if not cal.is_trading_day(d):
        return cal.next_trading_day(d), BEFORE
    if local.time() < OPEN:
        return d, BEFORE
    return (d, DURING) if local.time() < cal.close_time(d) else (d, AFTER)


def sessions(report: date, timing: str | None) -> tuple[date, date]:
    """(entry, exit): the last session whose close came before the results and the first whose close reflects them.
    Before the open or during the session: the session before → that day; after the close: that day → the next;
    unknown (or a non-trading day): the session before → the session after."""
    if not cal.is_trading_day(report):
        return cal.previous_trading_day(report), cal.next_trading_day(report)
    if timing in (BEFORE, DURING):
        return cal.previous_trading_day(report), report
    if timing == AFTER:
        return report, cal.next_trading_day(report)
    return cal.previous_trading_day(report), cal.next_trading_day(report)


def _calendar(conn: sqlite3.Connection, since: date, as_of: datetime | None) -> dict[str, dict[str, Any]]:
    """Each stock's next calendar date on or after ``since`` as the calendar stood at ``as_of`` (None: now)."""
    stamp = to_iso(as_of) if as_of else "9999"
    try:
        newest = conn.execute("SELECT MAX(last_seen) FROM stk_earnings_dates WHERE last_seen <= ?", (stamp,)).fetchone()[0]
        rows = conn.execute("SELECT symbol, report_date, hour, first_seen, last_seen FROM stk_earnings_dates "
                            "WHERE first_seen <= ? AND report_date >= ?", (stamp, day_of(since))).fetchall()
    except sqlite3.OperationalError:
        return {}
    best: dict[str, tuple[str, int, str | None]] = {}
    for symbol, day, hour, _first, last in rows:
        report = date_of(day)
        if not ((newest is not None and last >= newest) or _ny_day(last) >= report - GRACE):
            continue                                       # dropped out of the calendar: moved or withdrawn
        key = (last, -day)
        if symbol not in best or key > (best[symbol][0], -best[symbol][1]):
            best[symbol] = (last, day, hour)
    out = {}
    for symbol, (last, day, hour) in best.items():
        report = date_of(day)
        entry, exit_ = sessions(report, hour)
        out[symbol] = {"date": report.isoformat(), "to": report.isoformat(), "confirmed": True, "timing": hour,
                       "source": LABEL, "seen": last, "entry": entry.isoformat(), "exit": exit_.isoformat()}
    return out


def filed_reports(conn: sqlite3.Connection, as_of: datetime | None = None,
                  symbols: set[str] | None = None) -> dict[str, list[tuple[date, str | None]]]:
    """Each listed stock's past results reports from its 8-K item 2.02 filings accepted by ``as_of`` (None: all),
    oldest first, as (report day, timing); filings a few days apart count once (the first)."""
    stamp = to_iso(as_of) if as_of else "9999"
    cut = day_of(cal.new_york(as_of).date()) if as_of else 2**31
    out: dict[str, list[tuple[date, str | None]]] = defaultdict(list)
    for symbol, filed, accepted in conn.execute(
            "SELECT l.symbol, f.filed, f.accepted_at FROM stk_filings f JOIN stk_listings l ON l.cik = f.cik "
            "WHERE f.form IN ('8-K', '8-K/A') AND (',' || f.items || ',') LIKE '%,2.02,%' "
            "AND (f.accepted_at <= ? OR (f.accepted_at IS NULL AND f.filed < ?)) ORDER BY f.filed", (stamp, cut)):
        if symbols is not None and symbol not in symbols:
            continue
        report = timing_of(datetime.fromisoformat(accepted) if accepted else None, date_of(filed))
        reports = out[symbol]
        if reports and (report[0] - reports[-1][0]).days <= SAME_REPORT:
            continue
        reports.append(report)
    return out


def estimated(reports: list[tuple[date, str | None]], since: date) -> dict[str, Any] | None:
    """The next report estimated from past ones: the last plus the median gap between reports (50-130 days), ± a
    week, if that window has not ended before ``since``."""
    gaps = [(b[0] - a[0]).days for a, b in pairwise(reports)]
    gaps = [g for g in gaps if 50 <= g <= 130]
    if not gaps:
        return None
    centre = reports[-1][0] + timedelta(days=int(statistics.median(gaps)))
    start, end = max(centre - timedelta(days=WINDOW), since), centre + timedelta(days=WINDOW)
    if end < since:
        return None
    entry = max(since, cal.previous_trading_day(start))
    exit_ = end if cal.is_trading_day(end) else cal.next_trading_day(end)
    return {"date": start.isoformat(), "to": end.isoformat(), "confirmed": False, "timing": None,
            "source": SEC_LABEL, "seen": None, "entry": entry.isoformat(), "exit": exit_.isoformat()}


def known(conn: sqlite3.Connection, session: date, as_of: datetime | None = None,
          symbols: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """The point-in-time calendar: each stock's next report on or after ``session`` as known at ``as_of`` (None:
    everything stored). A confirmed date from Finnhub's calendar beats an estimate from results filings; each says which
    (``confirmed``), its timing (None when unknown) and its entry and exit sessions (``sessions``)."""
    out = {s: r for s, r in _calendar(conn, session, as_of).items() if symbols is None or s in symbols}
    for symbol, reports in filed_reports(conn, as_of, symbols).items():
        if symbol not in out and (guess := estimated(reports, session)) is not None:
            out[symbol] = guess
    return out


def listed_past(conn: sqlite3.Connection, before: date) -> dict[str, list[tuple[date, str | None]]]:
    """Past calendar dates (before ``before``) that stayed listed up to their report day, per stock, as (day, timing)."""
    out: dict[str, list[tuple[date, str | None]]] = defaultdict(list)
    try:
        rows = conn.execute("SELECT symbol, report_date, hour, last_seen FROM stk_earnings_dates WHERE report_date < ? "
                            "ORDER BY report_date", (day_of(before),)).fetchall()
    except sqlite3.OperationalError:
        return out
    for symbol, day, hour, last in rows:
        if _ny_day(last) >= date_of(day) - GRACE:
            out[symbol].append((date_of(day), hour))
    return out


async def job(suite: StocksSuite, market: StocksMarket, now: datetime) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        assert suite.finnhub is not None
        today = cal.new_york(now).date()
        rows: list[dict[str, Any]] = []
        for week in range(WEEKS):
            start = today + timedelta(days=7 * week)
            rows += await suite.finnhub.market_calendar(start, start + timedelta(days=6))
        stored = save(market.conn, rows, stored_symbols(market, delisted=False), now)
        return {"listed": len(rows), "stored": stored, "through": (today + timedelta(days=7 * WEEKS - 1)).isoformat()}
    return await _job(market, JOB, now, work)


__all__ = ["AFTER", "BEFORE", "DURING", "JOB", "LABEL", "SEC_LABEL", "announced", "estimated", "filed_reports", "job",
           "known", "listed_past", "save", "sessions", "timing_of"]
