"""Swap quotes. A quote is evidence of a *possible* trade at one moment — never proof of a fill."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from desk.contracts.observed import SolAddress, UtcDatetime


class QuoteObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str                       # "jupiter.order_v2" | "jupiter.quote_v1"
    input_mint: SolAddress
    output_mint: SolAddress
    in_amount_raw: int = Field(ge=0)
    out_amount_raw: int = Field(ge=0)
    min_out_raw: int | None = Field(default=None, ge=0)
    swap_mode: str
    slippage_bps: int | None = None
    price_impact_pct: Decimal | None = None      # percentage points, sign as reported
    router: str | None = None
    route_labels: tuple[str, ...] = ()
    fee_bps: int | None = None
    fee_mint: SolAddress | None = None
    signature_fee_lamports: int | None = Field(default=None, ge=0)
    prioritization_fee_lamports: int | None = Field(default=None, ge=0)
    rent_fee_lamports: int | None = Field(default=None, ge=0)
    gasless: bool | None = None
    transaction_included: bool = False
    request_id: str | None = None
    context_slot: int | None = None
    taker: SolAddress | None = None
    provider_time_ms: Decimal | None = None
    error_code: str | None = None
    error_message: str | None = None
    requested_at: UtcDatetime
    received_at: UtcDatetime
    latency_ms: Decimal
    raw_sha256: str | None = None


class QuoteUnavailable(BaseModel):
    """The provider answered, but no route/quote exists (e.g. TOKEN_NOT_TRADABLE). Not a transport failure."""

    model_config = ConfigDict(frozen=True)

    source: str
    input_mint: str
    output_mint: str
    in_amount_raw: int
    error_code: str | None
    error_message: str | None
    http_status: int
    received_at: UtcDatetime
    raw_sha256: str | None = None
