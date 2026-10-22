"""Versioned input contracts. Money remains decimal, evidence classes remain explicit."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"))]
Fraction = Annotated[Decimal, Field(ge=0, le=1)]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def public_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
        or len(value) > 1000
    ):
        raise ValueError("Use an HTTPS product URL without credentials, fragment or custom port")
    return value


class Settings(Record):
    capital: Money | None = None
    reserve: Money = Decimal("0")
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    region: str = Field(default="US", min_length=2, max_length=80)
    max_product_fraction: Fraction = Decimal("0.20")
    max_channel_fraction: Fraction = Decimal("0.60")
    max_units: int = Field(default=5, ge=1, le=100)
    min_profit: Money = Decimal("3")
    max_days: int = Field(default=60, ge=1, le=365)
    max_monthly_cost: Money = Decimal("0")
    interval_seconds: int = Field(default=300, ge=60, le=86400)
    enabled: bool = True
    dashboard_port: int = Field(default=8768, ge=1024, le=65535)


class Product(Record):
    id: Identifier
    title: str = Field(min_length=1, max_length=240)
    category: str = Field(default="Uncategorized", max_length=80)
    brand: str | None = Field(default=None, max_length=100)
    mpn: str | None = Field(default=None, max_length=100)
    gtin: str | None = Field(default=None, pattern=r"^\d{8,14}$")
    variant: str = Field(default="", max_length=200)
    pack_count: int | None = Field(default=None, ge=1, le=10000)
    condition: str = Field(default="new", max_length=50)
    identity_confirmed: bool = False
    image_url: str | None = Field(default=None, max_length=1000)

    @field_validator("image_url")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        return public_url(value) if value else None

    @field_validator("gtin")
    @classmethod
    def valid_gtin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) not in {8, 12, 13, 14}:
            raise ValueError("GTIN must have 8, 12, 13 or 14 digits")
        total = sum(int(digit) * (3 if i % 2 == 0 else 1) for i, digit in enumerate(reversed(value[:-1])))
        if (-total) % 10 != int(value[-1]):
            raise ValueError("GTIN check digit is invalid")
        return value.zfill(14)


class Source(Record):
    id: Identifier
    product_id: Identifier | None = None
    url: str
    adapter: Literal["jsonld", "ebay_browse"] = "jsonld"
    query: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return public_url(value)


class Observation(Record):
    product: Product
    source_id: Identifier
    url: str
    price: Money
    currency: str = Field(pattern="^[A-Z]{3}$")
    at: datetime
    availability: Literal["DECLARED_AVAILABLE", "UNAVAILABLE", "UNKNOWN", "VERIFIED_AVAILABLE"]
    method: str = Field(max_length=100)
    region: str | None = Field(default=None, max_length=80)
    quantity: int | None = Field(default=None, ge=0, le=10000)
    shipping: Money | None = None
    tax: Money | None = None
    verified_until: datetime | None = None
    evidence: str = Field(default="", max_length=1000)

    @field_validator("at", "verified_until")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Timestamp must include its timezone")
        return value.astimezone(UTC) if value else None

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return public_url(value)


class Route(Record):
    id: Identifier
    product_id: Identifier
    enabled: bool = True
    channel: str = Field(min_length=1, max_length=80)
    fulfillment: str = Field(default="Seller fulfilled", max_length=80)
    destination_url: str | None = None
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    sale_price: Money
    stress_price: Money
    fee_fraction: Fraction | None = None
    fixed_fee: Money | None = None
    fulfillment_cost: Money | None = None
    return_allowance: Money | None = None
    overhead: Money | None = None
    monthly_cost: Money = Decimal("0")
    days_to_cash: int | None = Field(default=None, ge=1, le=3650)
    demand_limit: int | None = Field(default=None, ge=0, le=10000)
    eligible: bool | None = None
    identity_confirmed: bool = False
    reviewed: bool = False
    fee_version: str = Field(default="", max_length=100)
    evidence_note: str = Field(default="", max_length=1000)
    evidence_kind: Literal["OPERATOR_SCENARIO", "OWN_SALES", "CONFIRMED_TRANSACTION", "EXECUTABLE_BID"] = "OPERATOR_SCENARIO"
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timestamp must include its timezone")
        return value.astimezone(UTC)

    @field_validator("destination_url")
    @classmethod
    def check_url(cls, value: str | None) -> str | None:
        return public_url(value) if value else None


class InventoryEvent(Record):
    id: Identifier
    lot_id: Identifier
    product_id: Identifier
    kind: Literal["RECEIVE", "RESERVE", "RELEASE", "SELL", "RETURN", "RESTOCK", "DAMAGE", "DISCARD_RETURN", "ADJUST_COST"]
    units: int = Field(ge=0, le=100000)
    amount: Money = Decimal("0")
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    at: datetime
    channel: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=1000)

    @field_validator("at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timestamp must include its timezone")
        return value.astimezone(UTC)


class SignalObservation(Record):
    id: Identifier
    product_id: Identifier
    channel: str = Field(min_length=1, max_length=80)
    kind: Literal["OWN_SALE", "CONFIRMED_UNITS", "SOLD_LISTINGS", "VIEWS", "WATCHERS", "MENTIONS", "OFFERS"]
    value: Annotated[float, Field(ge=0, le=1e9)]
    price: Money | None = None
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    at: datetime
    available_at: datetime
    source: str = Field(min_length=1, max_length=300)

    @field_validator("value")
    @classmethod
    def whole_count(cls, value: float) -> float:
        if not value.is_integer():
            raise ValueError("Units and attention counts must be whole numbers")
        return value

    @model_validator(mode="after")
    def available_after_event(self) -> SignalObservation:
        if self.available_at < self.at:
            raise ValueError("Evidence cannot be available before its event timestamp")
        return self

    @field_validator("at", "available_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timestamp must include its timezone")
        return value.astimezone(UTC)
