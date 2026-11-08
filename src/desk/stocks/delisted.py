"""Companies delisted during Massive's two years, so those years' point-in-time universe is survivorship-free.

The listing files only show what is listed today, so companies delisted before collection began used to be missing,
and Massive's grouped daily bars for them were thrown away at ingest. This background job, once, reads Massive's
reference list of inactive common stocks and ADRs (``stk_delisted``: name, CIK, delisting day), keeps those delisted
on or after the first stored Massive day, and then asks Massive's grouped daily again for the stored days before the
list existed, keeping only those companies' bars up to their delisting day (``PER_PASS`` days a pass at Massive's
shared 5 requests a minute), and their splits and dividends a month a pass. Days fetched after the list exists keep
them anyway (``universe.stored_symbols``). A ticker listed again today (reused by another company) is left out: the
two companies' bars couldn't be told apart.

From the first stored Massive day on, the rebuilt and replayed universe then includes companies later delisted
(``universe.listed_on``). Earlier years come from Schwab's history of today's names only (desk.stocks.schwab_history):
there is no free source for companies delisted before Massive's window, so those years are not survivorship-free, and
evaluations report the two periods separately (``survivorship_free_from``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.config import StocksSection
from desk.core.errors import ProviderError
from desk.stocks import calendar as cal
from desk.stocks.collect import _job, missing_price_days
from desk.stocks.repo import StocksMarket, date_of, day_of
from desk.stocks.schwab_history import OFFICIAL, boundary
from desk.stocks.suite import StocksSuite
from desk.storage.db import transaction

JOB = "delisted"
SOURCE = "massive_reference"
KINDS = {"CS": "common", "ADRC": "adr"}
PER_PASS = 4                 # grouped-daily days a pass (under a minute of Massive's shared 5 a minute)


def _detail(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT detail_json FROM stk_jobs WHERE name = ?", (JOB,)).fetchone()
    value = json.loads(row[0]) if row and row[0] else {}
    return value if isinstance(value, dict) else {}


def survivorship_free_from(conn: sqlite3.Connection) -> date | None:
    """The first day whose stored universe includes companies later delisted: the first Massive day once this job has
    finished, else the day collection began (listing files record delistings from then on)."""
    if _detail(conn).get("complete"):
        return boundary(conn)
    began = conn.execute("SELECT MIN(first_seen) FROM stk_listings").fetchone()[0]
    return datetime.fromisoformat(began).date() if began else None


def save_list(conn: sqlite3.Connection, rows: list[dict[str, Any]], edge: date, now: datetime) -> dict[str, int]:
    """Keep research kinds delisted on or after ``edge``; skip tickers listed again today."""
    listed = {r[0] for r in conn.execute("SELECT symbol FROM stk_listings WHERE active = 1")}
    kept = [r for r in rows if r["type"] in KINDS and r["delisted"] is not None and r["delisted"] >= edge
            and not r["active"]]
    reused = [r for r in kept if r["symbol"] in listed]
    with transaction(conn):
        conn.executemany("INSERT OR IGNORE INTO stk_delisted(symbol, name, kind, cik, delisted, source, received_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(r["symbol"], r["name"], KINDS[r["type"]], r["cik"], day_of(r["delisted"]), SOURCE, to_iso(now))
                          for r in kept if r["symbol"] not in listed])
    return {"delisted": len(kept) - len(reused), "reused_tickers": len(reused)}


def save_bars(conn: sqlite3.Connection, day: date, bars: list[Any], until: dict[str, int]) -> int:
    """Bars of delisted companies on a stored Massive day, up to each one's delisting day; never rewrites a row."""
    stored = 0
    with transaction(conn):
        for b in bars:
            if b.day != day or until.get(b.symbol, -1) < day_of(day):
                continue
            stored += conn.execute(
                "INSERT OR IGNORE INTO stk_bars(symbol, day, open, high, low, close, volume, vwap, trades) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (b.symbol, day_of(b.day), b.open, b.high, b.low, b.close, b.volume, b.vwap, b.trades)).rowcount
        if stored:
            conn.execute("UPDATE stk_bar_days SET rows = rows + ? WHERE day = ? AND source = ?",
                         (stored, day_of(day), OFFICIAL))
    return stored


async def job(suite: StocksSuite, market: StocksMarket, now: datetime, settings: StocksSection) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        state = _detail(market.conn)
        edge = boundary(market.conn)
        missing = missing_price_days(market, now, settings.history_days,
                                     timedelta(minutes=settings.refresh_after_close_minutes))
        if edge is None or any((now.date() - d).days > 10 for d in missing):
            # the list is kept from the first Massive day, so it waits until Massive's two years are in
            return {**state, "waiting": "Waiting for Massive's two years of daily bars", "remaining": 1}
        if not state.get("listed_at"):
            rows = [r for kind in KINDS for r in await suite.massive.delisted_tickers(kind)]
            counts = save_list(market.conn, rows, edge, now)
            return {**counts, "listed_at": cal.new_york(now).date().isoformat(), "edge": edge.isoformat(), "remaining": 1}
        listed_at = date.fromisoformat(state["listed_at"])
        until = {r[0]: int(r[1]) for r in market.conn.execute("SELECT symbol, delisted FROM stk_delisted")}
        last = max(until.values(), default=0)
        days = [date_of(r[0]) for r in market.conn.execute(
            "SELECT day FROM stk_bar_days WHERE source = ? AND rows > 0 AND day < ? AND day <= ? AND day < ? "
            "ORDER BY day DESC", (OFFICIAL, day_of(listed_at), last, int(state.get("next_before") or day_of(listed_at))))]
        stored = 0
        for d in days[:PER_PASS]:
            stored += save_bars(market.conn, d, await suite.massive.grouped_daily(d), until)
        out: dict[str, Any] = {**state, "bars": int(state.get("bars", 0)) + stored}
        if days:
            out["next_before"] = day_of(days[:PER_PASS][-1])
        # then their splits and dividends over the window, a month a pass, newest first
        actions = date.fromisoformat(state["actions_before"]) if state.get("actions_before") else listed_at
        if not days[PER_PASS:] and actions > edge:
            start = max(edge, (actions - timedelta(days=1)).replace(day=1))
            keep = set(until)
            try:
                splits = await suite.massive.splits(start, actions - timedelta(days=1))
                dividends = await suite.massive.dividends(start, actions - timedelta(days=1))
            except ProviderError as exc:
                if exc.status_code not in (401, 403):
                    raise
                splits, dividends, start = [], [], edge
                out["refused"] = f"Massive refused splits and dividends (HTTP {exc.status_code})"
            market.save_splits([s for s in splits if s.symbol in keep], now)
            market.save_dividends([x for x in dividends if x.symbol in keep], now)
            out["actions_before"] = start.isoformat()
            actions = start
        out["remaining"] = int(bool(days[PER_PASS:]) or actions > edge)
        out["complete"] = not out["remaining"]
        return out
    return await _job(market, JOB, now, work)


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    free = survivorship_free_from(conn)
    return {"survivorship_free_from": free.isoformat() if free else None,
            "delisted_companies": conn.execute("SELECT COUNT(*) FROM stk_delisted").fetchone()[0],
            "complete": bool(_detail(conn).get("complete"))}


__all__ = ["JOB", "coverage", "job", "save_bars", "save_list", "survivorship_free_from"]
