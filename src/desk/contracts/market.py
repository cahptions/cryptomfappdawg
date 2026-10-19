"""Market observations: pool snapshots and OHLCV bars."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from desk.contracts.observed import Observed, SolAddress, UtcDatetime

WINDOWS = ("m5", "m15", "m30", "h1", "h6", "h24")


class WindowMetrics(BaseModel):
    """Activity over one rolling window. ``None`` means the provider does not report it (never zero)."""

    model_config = ConfigDict(frozen=True)

    volume_usd: Decimal | None = None
    price_change_pct: Decimal | None = None
    buys: int | None = Field(default=None, ge=0)
    sells: int | None = Field(default=None, ge=0)
    buyers: int | None = Field(default=None, ge=0)    # distinct addresses (GeckoTerminal only)
    sellers: int | None = Field(default=None, ge=0)   # distinct addresses (GeckoTerminal only)


class PoolSnapshot(BaseModel):
    """One provider's view of one pool at one moment."""

    model_config = ConfigDict(frozen=True)

    source: str
    pool_address: SolAddress
    base_mint: SolAddress
    quote_mint: SolAddress | None
    venue: str                      # canonical venue id, or "unverified:<provider id>"
    venue_raw: str
    name: str | None                # sanitised untrusted text
    base_symbol: str | None         # sanitised untrusted text
    pool_created_at: UtcDatetime | None
    price_usd: Observed[Decimal]
    price_native: Observed[Decimal]
    liquidity_usd: Observed[Decimal]
    fdv_usd: Observed[Decimal]
    market_cap_usd: Observed[Decimal]
    locked_liquidity_pct: Decimal | None = None   # provider claim (GeckoTerminal pools/multi)
    liquidity_base_amount: Decimal | None = None  # reserve of the base token, as reported (DexScreener)
    liquidity_quote_amount: Decimal | None = None # reserve of the quote token, as reported (DexScreener)
    windows: dict[str, WindowMetrics]
    flags: tuple[str, ...] = ()
    observed_at: UtcDatetime
    raw_sha256: str | None = None

    @property
    def is_bonding_curve(self) -> bool:
        return self.venue in {"pumpfun_bonding_curve", "meteora_dbc"}


class OhlcvBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    pool_address: SolAddress
    timeframe: str          # e.g. "minute:5"
    ts: UtcDatetime         # bar timestamp as returned by the provider (believed to be bar start; TO VERIFY)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_usd: Decimal
    source: str
    observed_at: UtcDatetime
