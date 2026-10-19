"""The signer accepts only these structured requests (PLAN §16.2). Unknown fields are refused, never ignored."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # A BUY names the approved strategy version and the candidate it decided on; the signer re-establishes every
    # LIVE gate for exactly these before signing (PLAN §16.5). Exits never need a strategy's permission.
    strategy_version: str | None = Field(default=None, pattern=r"^[A-Z][0-9]:[0-9]+:[0-9a-f]{64}$")
    candidate_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")

    @model_validator(mode="after")
    def _buy_names_its_decision(self) -> Self:
        if self.side == "BUY" and (self.strategy_version is None or self.candidate_id is None):
            raise ValueError("a BUY intent must name its strategy_version and candidate_id")
        return self


class Withdrawal(BaseModel):
    """The single transfer capability: to the pre-registered address only (operator decision; PLAN §16.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    destination: SolAddress
    lamports: int = Field(gt=0, strict=True)
    confirmation: str
    expires_at: UtcDatetime
