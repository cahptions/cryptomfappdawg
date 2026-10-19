"""The signer accepts only these structured requests (PLAN §16.2). Unknown fields are refused, never ignored."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from desk.contracts.observed import SolAddress, UtcDatetime


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    side: Literal["BUY", "SELL"]
    mint: SolAddress
    amount_raw: int = Field(gt=0, strict=True)            # BUY: lamports in ; SELL: raw token units in
    max_slippage_bps: int = Field(ge=1, le=300, strict=True)
    min_out_raw: int = Field(gt=0, strict=True)
    quote_ref: str = Field(min_length=1, max_length=200)
    expires_at: UtcDatetime


class Withdrawal(BaseModel):
    """The single transfer capability: to the pre-registered address only (operator decision; PLAN §16.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    destination: SolAddress
    lamports: int = Field(gt=0, strict=True)
    confirmation: str
    expires_at: UtcDatetime
