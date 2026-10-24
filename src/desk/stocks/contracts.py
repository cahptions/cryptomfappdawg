"""Typed records shared by the stocks desk's sources, storage and analysis.

Research values are floats (prices, ratios, statistics); the PAPER ledger converts money to integer micro-dollars.
Dates are exchange dates (America/New_York trading days); timestamps are UTC.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Listing:
    symbol: str             # canonical: class shares with a dot (BRK.B), as Nasdaq Trader's ACT symbol
    name: str
    exchange: str           # Nasdaq, NYSE, NYSE American, NYSE Arca, Cboe BZX, IEX
    etf: bool
    test: bool
    kind: str               # common, adr, warrant, unit, right, preferred, note, fund, other


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    trades: int | None = None


@dataclass(frozen=True)
class IntradayBar:
    symbol: str
    at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Split:
    symbol: str
    execution_date: date
    split_from: float
    split_to: float

    @property
    def ratio(self) -> float:
        """New shares per old share: prices before the split are divided by this."""
        return self.split_to / self.split_from


@dataclass(frozen=True)
class Dividend:
    symbol: str
    ex_date: date
    cash_amount: float
    pay_date: date | None
    frequency: int | None
    distribution_type: str | None


@dataclass(frozen=True)
class Fact:
    """One XBRL value as filed: ``start`` is None for an instant (balance sheet) value."""

    concept: str
    unit: str
    start: date | None
    end: date
    value: float
    form: str
    filed: date
    fy: int | None
    fp: str | None
    accn: str

    @property
    def days(self) -> int:
        return 0 if self.start is None else (self.end - self.start).days + 1


@dataclass(frozen=True)
class Filing:
    cik: int
    accession: str
    form: str
    filed: date
    accepted: datetime | None
    report_date: date | None
    items: tuple[str, ...]
    primary_document: str


@dataclass(frozen=True)
class Company:
    cik: int
    name: str
    sic: str | None
    sic_description: str | None
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    fiscal_year_end: str | None
    category: str | None
    filings: tuple[Filing, ...]


@dataclass(frozen=True)
class IndexEntry:
    form: str
    company: str
    cik: int
    filed: date
    path: str

    @property
    def accession(self) -> str:
        return self.path.rsplit("/", 1)[-1].removesuffix(".txt")


@dataclass(frozen=True)
class InsiderTrade:
    accession: str
    issuer_cik: int
    symbol: str
    owner: str
    owner_cik: int
    is_director: bool
    is_officer: bool
    officer_title: str | None
    is_ten_percent: bool
    day: date
    code: str               # P open-market purchase, S open-market sale, A grant, M exercise, F tax withholding ...
    shares: float
    price: float | None
    acquired: bool
    owned_after: float | None
    direct: bool
    planned: bool           # made under a Rule 10b5-1 plan (aff10b5One)

    @property
    def value(self) -> float:
        return self.shares * (self.price or 0.0)


@dataclass(frozen=True)
class ShortInterest:
    symbol: str
    settlement: date
    short_shares: float
    previous_short_shares: float | None
    avg_daily_volume: float | None
    days_to_cover: float | None


@dataclass(frozen=True)
class ShortVolume:
    symbol: str
    day: date
    short_volume: float
    exempt_volume: float
    total_volume: float


@dataclass(frozen=True)
class Fail:
    settlement: date
    cusip: str
    symbol: str
    quantity: float
    price: float | None


@dataclass(frozen=True)
class Mention:
    symbol: str
    name: str
    source: str             # an ApeWisdom filter: wallstreetbets, all-stocks, options, ...
    rank: int
    mentions: int
    upvotes: int
    rank_24h_ago: int | None
    mentions_24h_ago: int | None


@dataclass(frozen=True)
class RatePoint:
    series: str
    day: date
    value: float


@dataclass(frozen=True)
class OptionQuote:
    occ: str                # OCC symbol, e.g. AAPL261120C00250000
    underlying: str
    kind: str               # call | put
    strike: float
    expiration: date
    bid: float | None
    ask: float | None
    last: float | None
    volume: int
    open_interest: int
    average_volume: int | None
    quoted_at: datetime | None

    @property
    def mid(self) -> float | None:
        if (self.bid is None or self.ask is None or not math.isfinite(self.bid) or not math.isfinite(self.ask)
                or self.bid < 0 or self.ask <= 0 or self.bid > self.ask):
            return None
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class StockQuote:
    symbol: str
    last: float | None
    bid: float | None
    ask: float | None
    prev_close: float | None
    volume: float | None
    quoted_at: datetime | None


__all__ = ["Bar", "Company", "Dividend", "Fact", "Fail", "Filing", "IndexEntry", "InsiderTrade", "Listing", "Mention",
           "OptionQuote", "RatePoint", "ShortInterest", "ShortVolume", "Split", "StockQuote"]
