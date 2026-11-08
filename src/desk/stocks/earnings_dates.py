"""Announced earnings dates, stored daily, for Plays' earnings sections and the stock page.

Of the sources the app can use, only Finnhub's free plan (the optional ``FINNHUB_API_KEY`` the stock page already
reads) publishes a forward earnings calendar: Massive's Stocks Basic has none (its earnings data is a paid partner
add-on), Schwab's market data has the last report date but no next one, and the SEC publishes filings after the fact.
Nasdaq's calendar pages refuse programs, so they are not used. With the key, once a day the next four weeks' calendar
for every stock is read (one request a week of dates) and kept per stock and date with when it was first and last
listed, so a moved date drops out and a past day only sees what was announced by then. Without it, report dates stay
estimated from past 8-K results filings (a ±7-day window around the median gap; desk.stocks.signals.events), and the
pages say which one they show.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
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


__all__ = ["JOB", "LABEL", "announced", "job", "save"]
