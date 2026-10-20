"""Parsed Robinhood and Coinbase observations. Every number is a Decimal parsed from the response text."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

Route = Literal["MM", "EX"]          # MM: v1 market-maker routing (spread, no commission); EX: v2 exchange routing (fee)
Side = Literal["BUY", "SELL"]
STABLECOINS = frozenset({"USDC", "USDT", "PYUSD", "USD1", "DAI", "USDG", "FDUSD", "TUSD", "USDP", "GUSD", "RLUSD"})


@dataclass(frozen=True)
class Pair:
    symbol: str                       # e.g. "BTC-USD"
    asset_code: str
    quote_code: str
    asset_increment: Decimal
    quote_increment: Decimal
    max_order_size: Decimal
    min_order_amount: Decimal | None  # USD, fee-tier (v2) orders
    status: str
    is_api_tradable: bool

    @property
    def tradable(self) -> bool:
        return (self.status == "tradable" and self.is_api_tradable and self.quote_code == "USD"
                and self.asset_code not in STABLECOINS)


@dataclass(frozen=True)
class BidAsk:
    symbol: str
    bid: Decimal
    ask: Decimal
    received_at: datetime
    raw_sha256: str

    @property
    def crossed(self) -> bool:
        """Reported aggregate bid exceeds ask; this does not establish executable profit."""
        return self.bid > self.ask

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_fraction(self) -> Decimal:
        """Signed reported spread, preserving a crossed observation instead of inventing new prices."""
        return (self.ask - self.bid) / self.mid


@dataclass(frozen=True)
class Estimate:
    """Robinhood's own estimate of filling ``quantity`` on one side, fee or spread included."""

    symbol: str
    route: Route
    side: Side
    quantity: Decimal
    unit_price: Decimal                # price per unit before any fee (spread already inside for MM)
    fee: Decimal                       # USD; 0 for MM
    total: Decimal                     # BUY: USD paid including fee; SELL: USD received after fee
    fee_ratio: Decimal | None
    provider_at: datetime | None
    received_at: datetime
    raw_sha256: str


@dataclass(frozen=True)
class Candle:
    product: str                       # Coinbase product id, e.g. "BTC-USD"
    granularity: int                   # seconds
    start: datetime                    # bucket START (Coinbase docs; measured)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal                    # base units


__all__ = ["STABLECOINS", "BidAsk", "Candle", "Estimate", "Pair", "Route", "Side"]
