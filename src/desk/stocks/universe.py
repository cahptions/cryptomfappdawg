"""Who the desk researches: listed US common stock and ADRs (no ETFs, funds, warrants, units, rights, preferreds or
test issues), with a SEC CIK when one exists. Liquidity is applied from prices, per day (desk.stocks.engine)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from itertools import zip_longest

from desk.core.clock import to_iso
from desk.stocks import calendar as cal
from desk.stocks.repo import StocksMarket

RESEARCH_KINDS = frozenset({"common", "adr"})
FIRST_FILE_DAYS = 7          # listings first seen this soon after collection began were already listed before it
# benchmarks and sector funds the desk compares against (stored with the universe's bars)
BENCHMARKS = ("SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
              "SMH", "TLT", "HYG", "GLD")
SECTOR_FUNDS = {"BusEq": "XLK", "Money": "XLF", "Enrgy": "XLE", "Hlth": "XLV", "Manuf": "XLI", "Durbl": "XLY",
                "Shops": "XLY", "NoDur": "XLP", "Utils": "XLU", "Chems": "XLB", "Telcm": "XLC", "Other": "SPY"}


def research_listings(market: StocksMarket) -> list[sqlite3.Row]:
    return [r for r in market.listings(active=True) if r["kind"] in RESEARCH_KINDS and not r["etf"] and not r["test"]]


def listed_on(market: StocksMarket, asof: date) -> list[sqlite3.Row]:
    """The research universe as it stood on a rebuilt or replayed day, delisted companies included.

    A listing counts from the day it first appeared in the listing files (an IPO or a new ticker) until the last file
    that still had it. Listings in the first files this app collected were already listed before collection began, so
    they count on any earlier day; their stored prices decide when they actually traded. Companies delisted before
    collection began come from Massive's reference list (``stk_delisted``, desk.stocks.delisted) and count until their
    delisting day; companies delisted before Massive's two years have no free source and are missing (the remaining
    survivorship bias, in Schwab's earlier years only)."""
    began = market.conn.execute("SELECT MIN(first_seen) FROM stk_listings").fetchone()[0]
    if began is None:
        return []
    already = to_iso(datetime.fromisoformat(began) + timedelta(days=FIRST_FILE_DAYS))
    start, end = (to_iso(datetime.combine(asof, t, cal.NEW_YORK)) for t in (time(0, 0), time(23, 59, 59)))
    rows = [r for r in market.conn.execute(
        "SELECT symbol, name, exchange, kind, etf, test, cik, active, first_seen, last_seen FROM stk_listings "
        "WHERE etf = 0 AND test = 0 AND (first_seen <= ? OR first_seen <= ?) AND (active = 1 OR last_seen >= ?) "
        "UNION ALL SELECT d.symbol, d.name, '', d.kind, 0, 0, d.cik, 0, '', '' FROM stk_delisted d "
        "WHERE d.delisted >= ? AND d.symbol NOT IN (SELECT symbol FROM stk_listings) ORDER BY symbol",
        (end, already, start, asof.toordinal())) if r["kind"] in RESEARCH_KINDS]
    return rows


def stored_symbols(market: StocksMarket, *, delisted: bool = True) -> set[str]:
    """Symbols whose bars are kept: the research universe, the benchmarks and (unless ``delisted`` is False, for
    requests about today) the companies delisted during Massive's window."""
    names = {r["symbol"] for r in research_listings(market)} | set(BENCHMARKS)
    if delisted:
        names |= {r[0] for r in market.conn.execute("SELECT symbol FROM stk_delisted")}
    return names


def symbol_ciks(market: StocksMarket) -> dict[str, int]:
    return {r["symbol"]: int(r["cik"]) for r in research_listings(market) if r["cik"]}


def option_focus(research: sqlite3.Connection, limit: int, min_price: float, min_dollar_volume: float) -> list[str]:
    """Interleave liquid lower-priced stocks and dollar-volume leaders within the SAME bounded collection budget.

    Stock price is a coverage-diversity heuristic, never an option-quality or affordable-premium guarantee.
    """
    if limit<=0:
        return []
    leaders = [r[0] for r in research.execute(
        "SELECT symbol FROM stk_scores_latest "
        "ORDER BY json_extract(detail_json,'$.price.dollar_volume_20d') DESC,symbol LIMIT ?",(limit,))]
    retail = [r[0] for r in research.execute(
        "SELECT symbol FROM stk_scores_latest WHERE json_extract(detail_json,'$.price.close') BETWEEN ? AND 100 "
        "AND json_extract(detail_json,'$.price.dollar_volume_20d')>=? "
        "ORDER BY json_extract(detail_json,'$.price.dollar_volume_20d') DESC,symbol LIMIT ?",
        (max(5.,min_price),max(25_000_000.,min_dollar_volume),limit))]
    mixed = dict.fromkeys(s for pair in zip_longest(leaders,retail) for s in pair if s is not None)
    return list(mixed)[:limit]


__all__ = ["BENCHMARKS", "RESEARCH_KINDS", "SECTOR_FUNDS", "listed_on", "option_focus", "research_listings", "stored_symbols",
           "symbol_ciks"]
