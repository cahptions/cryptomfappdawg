"""Who the desk researches: listed US common stock and ADRs (no ETFs, funds, warrants, units, rights, preferreds or
test issues), with a SEC CIK when one exists. Liquidity is applied from prices, per day (desk.stocks.engine)."""

from __future__ import annotations

import sqlite3

from desk.stocks.repo import StocksMarket

RESEARCH_KINDS = frozenset({"common", "adr"})
# benchmarks and sector funds the desk compares against (stored with the universe's bars)
BENCHMARKS = ("SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
              "SMH", "TLT", "HYG", "GLD")
SECTOR_FUNDS = {"BusEq": "XLK", "Money": "XLF", "Enrgy": "XLE", "Hlth": "XLV", "Manuf": "XLI", "Durbl": "XLY",
                "Shops": "XLY", "NoDur": "XLP", "Utils": "XLU", "Chems": "XLB", "Telcm": "XLC", "Other": "SPY"}


def research_listings(market: StocksMarket) -> list[sqlite3.Row]:
    return [r for r in market.listings(active=True) if r["kind"] in RESEARCH_KINDS and not r["etf"] and not r["test"]]


def stored_symbols(market: StocksMarket) -> set[str]:
    """Symbols whose bars are kept: the research universe and the benchmarks."""
    return {r["symbol"] for r in research_listings(market)} | set(BENCHMARKS)


def symbol_ciks(market: StocksMarket) -> dict[str, int]:
    return {r["symbol"]: int(r["cik"]) for r in research_listings(market) if r["cik"]}


__all__ = ["BENCHMARKS", "RESEARCH_KINDS", "SECTOR_FUNDS", "research_listings", "stored_symbols", "symbol_ciks"]
