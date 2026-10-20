"""Research-only council advisory (research/council/advisory.schema.json, schema ``kestrel-advisory-1``).

An advisory records what one of the two council advisors would say about a prospective long at the moment of an
original primary decision. It has no trading authority: mode SHADOW, influence NONE. Beyond the JSON schema this
model enforces the semantic rules of COUNCIL.md: every input was over and received by the decision time, the
calculation is not earlier than the decision, SUPPORT needs fresh qualified exact-ticket economics, diagnostics are
plain decimal strings (never a probability or an expected return), and quantities are exact integer strings.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Literal["kestrel-advisory-1"] = "kestrel-advisory-1"
ADVISORS = ("C1_BRC", "C2_ECDR")
STANCES = ("SUPPORT", "CAUTION", "OPPOSE", "ABSTAIN")
DIAGNOSTICS = ("residual_z", "beta", "alpha", "residual_scale", "shock_z", "depth_ratio", "baseline_cost",
               "event_cost", "recovery_cost1", "recovery_cost2", "target_distance")
_DECIMAL = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def zulu(value: datetime) -> str:
    """UTC with a ``Z`` suffix and microseconds: the advisory time format."""
    if value.tzinfo is None:
        raise ValueError("timezone-aware time required")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _time(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("times are UTC strings ending in Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssetRef(_Frozen):
    profile: Literal["equity_daily", "liquid_crypto_1h", "solana_meme_15m"]
    instrument_id: str = Field(min_length=1)
    pool_or_venue: str = Field(min_length=1)
    source: str = Field(min_length=1)
    price_currency: str = Field(min_length=1)
    account_numeraire: str = Field(min_length=1)
    membership_id: str = Field(min_length=1)


class InputRef(_Frozen):
    """One immutable source row or a contiguous range of them (``row_id`` names the range)."""

    store: str = Field(min_length=1)
    row_id: str = Field(min_length=1)
    raw_sha256: str | None
    event_end: str
    first_available_at: str

    @field_validator("raw_sha256")
    @classmethod
    def _raw(cls, value: str | None) -> str | None:
        if value is not None and not _HASH.match(value):
            raise ValueError("raw_sha256 must be a SHA-256 hex digest")
        return value


class Horizon(_Frozen):
    maximum: int = Field(ge=1)
    unit: Literal["SECONDS", "SESSIONS"]


class Freshness(_Frozen):
    status: Literal["OK", "MISSING", "STALE", "CONFLICT", "UNVERIFIED"]
    missing: tuple[str, ...]
    stale: tuple[str, ...]


class Evidence(_Frozen):
    training_bars: int = Field(ge=0)
    signal_bars: int = Field(ge=0)
    quote_pairs: int = Field(ge=0)
    forward_closed_trades: int = Field(ge=0)
    effective_observations: float | None = Field(default=None, ge=0)


class Uncertainty(_Frozen):
    calibrated: Literal[False] = False
    interval: None = None
    reason: str = Field(min_length=1)


class RegimeRef(_Frozen):
    status: str
    label: str | None


class Economics(_Frozen):
    status: Literal["UNVALIDATED", "UNMEASURED", "CURRENT_COST_QUALIFIED_ONLY", "FAILED_CURRENT_COST"]
    ticket_raw: str | None
    roundtrip_cost_fraction: str | None
    expected_net_return: None = None

    @field_validator("ticket_raw")
    @classmethod
    def _ticket(cls, value: str | None) -> str | None:
        if value is not None and not value.isdigit():
            raise ValueError("ticket_raw is an exact integer string")
        return value

    @field_validator("roundtrip_cost_fraction")
    @classmethod
    def _cost(cls, value: str | None) -> str | None:
        if value is not None and not _DECIMAL.match(value):
            raise ValueError("cost fractions are plain decimal strings")
        return value


class Advisory(_Frozen):
    schema_version: Literal["kestrel-advisory-1"] = SCHEMA_VERSION
    advisory_id: str = Field(min_length=1)
    advisor_id: Literal["C1_BRC", "C2_ECDR"]
    spec_version: int = Field(ge=1)
    spec_sha256: str
    code_sha256: str
    mode: Literal["SHADOW"] = "SHADOW"
    influence: Literal["NONE"] = "NONE"
    primary_decision_hash: str | None
    primary_action: Literal["NO_TRADE", "LONG_CANDIDATE", "UNKNOWN"]
    reference_action: Literal["PROSPECTIVE_LONG"] = "PROSPECTIVE_LONG"
    asset: AssetRef
    decision_at: str
    computed_at: str
    latest_event_end: str | None
    latest_input_available_at: str | None
    expires_at: str
    signal_direction: Literal["LONG", "AGAINST_LONG", "NEUTRAL", "UNKNOWN"]
    stance: Literal["SUPPORT", "CAUTION", "OPPOSE", "ABSTAIN"]
    candidate_signal: dict[str, str | None]
    horizon: Horizon
    rationale_codes: tuple[str, ...] = Field(min_length=1)
    inputs: tuple[InputRef, ...]
    freshness: Freshness
    evidence: Evidence
    uncertainty: Uncertainty
    regime: RegimeRef
    shared_dependencies: tuple[str, ...]
    economics: Economics
    input_sha256: str

    @field_validator("spec_sha256", "code_sha256", "input_sha256")
    @classmethod
    def _hashes(cls, value: str) -> str:
        if not _HASH.match(value):
            raise ValueError("expected a SHA-256 hex digest")
        return value

    @field_validator("primary_decision_hash")
    @classmethod
    def _decision(cls, value: str | None) -> str | None:
        if value is not None and not _HASH.match(value):
            raise ValueError("primary_decision_hash must be a SHA-256 hex digest")
        return value

    @field_validator("candidate_signal")
    @classmethod
    def _diagnostics(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        for name, number in value.items():
            if name not in DIAGNOSTICS:
                raise ValueError(f"{name} is not a named diagnostic (no probability or expected-return field)")
            if number is not None and not _DECIMAL.match(number):
                raise ValueError(f"{name} must be a plain decimal string")
        return value

    @model_validator(mode="after")
    def _semantics(self) -> Self:
        decided = _time(self.decision_at)
        if _time(self.computed_at) < decided:
            raise ValueError("an advisory cannot be computed before its decision")
        _time(self.expires_at)
        for ref in self.inputs:
            if _time(ref.event_end) > decided or _time(ref.first_available_at) > decided:
                raise ValueError(f"input {ref.store}:{ref.row_id} was not over and received by the decision time")
        for name in ("latest_event_end", "latest_input_available_at"):
            value = getattr(self, name)
            if value is not None and _time(value) > decided:
                raise ValueError(f"{name} is after the decision time")
        if self.stance == "SUPPORT":
            if self.economics.status != "CURRENT_COST_QUALIFIED_ONLY" or self.freshness.status != "OK":
                raise ValueError("SUPPORT needs fresh, qualified exact-ticket economics")
            if self.signal_direction != "LONG":
                raise ValueError("SUPPORT refers to a prospective long")
        return self


__all__ = ["ADVISORS", "DIAGNOSTICS", "SCHEMA_VERSION", "STANCES", "Advisory", "AssetRef", "Economics", "Evidence",
           "Freshness", "Horizon", "InputRef", "RegimeRef", "Uncertainty", "zulu"]
