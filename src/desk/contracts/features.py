"""Versioned point-in-time numerical features; uncertainty is never silently coerced to zero."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from desk.contracts.observed import DataStatus, SolAddress, UtcDatetime


class FeatureValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    value: Decimal | None
    status: DataStatus
    unit: str = Field(min_length=1)
    source: str = Field(min_length=1)
    observed_at: UtcDatetime | None
    age_seconds: float | None = Field(default=None, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def _usable_has_evidence(self) -> Self:
        if self.value is not None and not self.value.is_finite():
            raise ValueError("feature values must be finite")
        if self.status is DataStatus.MISSING and self.value is not None:
            raise ValueError("missing features cannot carry a value")
        if self.status is DataStatus.OK and (
            self.value is None or self.observed_at is None or self.age_seconds is None
        ):
            raise ValueError("OK features require a value, observation timestamp and age")
        return self


class FeatureSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mint: SolAddress
    pool_address: SolAddress
    as_of: UtcDatetime
    feature_set_version: str = Field(min_length=1)
    values: dict[str, FeatureValue]
    raw_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _no_future_evidence(self) -> Self:
        if any(v.observed_at is not None and v.observed_at > self.as_of for v in self.values.values()):
            raise ValueError("features cannot contain future observations")
        return self

    def usable(self, name: str) -> Decimal | None:
        value = self.values.get(name)
        return value.value if value is not None and value.status is DataStatus.OK else None


class RegimeLabel(StrEnum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    CHOPPY = "CHOPPY"
    RISK_OFF = "RISK_OFF"


class RegimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    label: RegimeLabel | None
    status: DataStatus
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def _label_requires_measured_reference(self) -> Self:
        if (self.label is not None) != (self.status is DataStatus.OK):
            raise ValueError("a regime label is available only with OK measured inputs")
        return self
