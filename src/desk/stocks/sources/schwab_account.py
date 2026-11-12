"""Read-only Schwab account client: account numbers, positions and executed trades. It cannot place an order.

Schwab's Trader API ("Accounts and Trading Production", the same OAuth sign-in as market data) also has order
endpoints. This module only ever issues HTTP GET, and only to the three paths in ``ALLOWED`` below; anything else is
refused before a request is made. ``tests/unit/test_stocks_schwab_account.py`` inspects this file to prove it has
no other method and no order path.

Account numbers never leave this module: Schwab's own ``hashValue`` addresses an account in requests, and callers
receive a one-way ``key`` (derived from the hashValue) plus the last four digits for display. Parsers were written
from Schwab's documentation; the fixtures are documentation-shaped and synthetic, never a real account.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.core.hashing import sha256_hex
from desk.providers.http import ProviderHttp
from desk.stocks import calendar as cal
from desk.stocks.schwab_auth import SchwabTokens

# (registry endpoint, exact path pattern). Nothing else can be requested.
ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("account_numbers", re.compile(r"^/accounts/accountNumbers$")),
    ("accounts", re.compile(r"^/accounts$")),
    ("transactions", re.compile(r"^/accounts/[A-Za-z0-9]{8,128}/transactions$")),
)
SOURCE = "schwab_trader_api"
OCC = re.compile(r"^([A-Z0-9.]{1,6})(\d{6})([CP])(\d{8})$")


class AccountNotEnabled(ProviderError):
    """Schwab refused account access (401/403): the app lacks the Accounts and Trading product, or the sign-in
    predates it."""


@dataclass(frozen=True)
class Account:
    key: str          # one-way, from Schwab's hashValue; safe to store and show
    last4: str
    hash_value: str = field(repr=False)   # Schwab's own opaque token for requests; in memory only, never shown


@dataclass(frozen=True)
class Position:
    account_key: str
    account_last4: str
    asset_type: str
    symbol: str
    underlying: str | None
    put_call: str | None
    strike: float | None
    expiration: date | None
    quantity: float
    average_price: float | None
    market_value: float | None
    day_pnl: float | None
    day_pnl_pct: float | None


@dataclass(frozen=True)
class Fill:
    account_key: str
    account_last4: str
    activity_id: str
    leg: int
    traded_at: datetime
    asset_type: str
    symbol: str
    underlying: str | None
    put_call: str | None
    strike: float | None
    expiration: date | None
    quantity: float          # + bought, − sold
    price: float
    fees: float
    position_effect: str | None

    @property
    def trade_day(self) -> date:
        return cal.new_york(self.traded_at).date()


def account_key(hash_value: str) -> str:
    return sha256_hex("schwab-account:" + hash_value)[:16]


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, TypeError) as exc:
        raise SchemaError("schwab account: invalid response (details suppressed)") from exc


def option_parts(symbol: str) -> tuple[str, date, str, float] | None:
    """(underlying, expiration, CALL/PUT, strike) from an OCC symbol with or without Schwab's padding spaces."""
    m = OCC.fullmatch(symbol.replace(" ", ""))
    if not m:
        return None
    try:
        expiry = datetime.strptime(m.group(2), "%y%m%d").date()
    except ValueError:
        return None
    return m.group(1), expiry, "CALL" if m.group(3) == "C" else "PUT", int(m.group(4)) / 1000


def parse_account_numbers(body: bytes) -> dict[str, Account]:
    """Account number → Account. The numbers are only map keys here; callers keep the Account values."""
    value = _json(body)
    if not isinstance(value, list):
        raise SchemaError("schwab accountNumbers: expected a list")
    out = {}
    for row in value:
        if not isinstance(row, dict):
            continue
        number, hashed = str(row.get("accountNumber") or ""), str(row.get("hashValue") or "")
        if number and re.fullmatch(r"[A-Za-z0-9]{8,128}", hashed):
            out[number] = Account(account_key(hashed), number[-4:], hashed)
    return out


def _instrument(raw: Any) -> tuple[str, str, str | None, str | None, float | None, date | None] | None:
    if not isinstance(raw, dict):
        return None
    asset = str(raw.get("assetType") or "").upper()
    symbol = str(raw.get("symbol") or "").strip()
    if not asset or not symbol:
        return None
    if asset == "OPTION":
        parts = option_parts(symbol)
        if parts is None:
            return None
        under, expiry, kind, strike = parts
        return asset, symbol.replace(" ", ""), str(raw.get("underlyingSymbol") or under).upper(), kind, strike, expiry
    return asset, symbol.upper(), None, None, None, None


def parse_positions(body: bytes, accounts: dict[str, Account]) -> tuple[list[Account], list[Position]]:
    """Every position of every account Schwab returned that ``accounts`` (from accountNumbers) knows."""
    value = _json(body)
    if not isinstance(value, list):
        raise SchemaError("schwab accounts: expected a list")
    seen: list[Account] = []
    out: list[Position] = []
    for row in value:
        sec = row.get("securitiesAccount") if isinstance(row, dict) else None
        if not isinstance(sec, dict):
            continue
        account = accounts.get(str(sec.get("accountNumber") or ""))
        if account is None:
            continue
        seen.append(account)
        for p in sec.get("positions") or []:
            if not isinstance(p, dict):
                continue
            inst = _instrument(p.get("instrument"))
            if inst is None:
                continue
            asset, symbol, under, kind, strike, expiry = inst
            qty = (_num(p.get("longQuantity")) or 0.0) - (_num(p.get("shortQuantity")) or 0.0)
            if qty == 0:
                continue
            out.append(Position(account.key, account.last4, asset, symbol, under, kind, strike, expiry, qty,
                                _num(p.get("averagePrice")), _num(p.get("marketValue")),
                                _num(p.get("currentDayProfitLoss")), _num(p.get("currentDayProfitLossPercentage"))))
    return seen, out


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", text):
        text = text[:-2] + ":" + text[-2:]
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def parse_transactions(body: bytes, account: Account) -> list[Fill]:
    """Executed trades: one Fill per traded instrument in each TRADE activity; currency/fee items become fees."""
    value = _json(body)
    if not isinstance(value, list):
        raise SchemaError("schwab transactions: expected a list")
    out = []
    for row in value:
        if not isinstance(row, dict) or str(row.get("type") or "") != "TRADE":
            continue
        if str(row.get("status") or "VALID") not in ("VALID", "EXECUTED"):
            continue
        activity, at = row.get("activityId"), _when(row.get("time") or row.get("tradeDate"))
        if activity is None or at is None:
            continue
        items = [i for i in row.get("transferItems") or [] if isinstance(i, dict)]
        fees = sum(abs(_num(i.get("cost")) or 0.0) for i in items
                   if str((i.get("instrument") or {}).get("assetType") or "").upper() == "CURRENCY")
        legs = []
        for item in items:
            inst = _instrument(item.get("instrument"))
            if inst is None or inst[0] == "CURRENCY":
                continue
            amount, price, cost = _num(item.get("amount")), _num(item.get("price")), _num(item.get("cost"))
            if amount is None or amount == 0 or price is None or price < 0:
                continue
            # Schwab's ``cost`` is negative for money paid (a purchase); its sign is the most reliable side.
            sign = -1.0 if cost is not None and cost > 0 else 1.0 if cost is not None and cost < 0 else math.copysign(
                1.0, amount)
            effect = str(item.get("positionEffect") or "").upper() or None
            legs.append((inst, sign * abs(amount), price, effect if effect in ("OPENING", "CLOSING") else None))
        for n, (inst, qty, price, effect) in enumerate(legs):
            asset, symbol, under, kind, strike, expiry = inst
            out.append(Fill(account.key, account.last4, str(activity), n, at, asset, symbol, under, kind, strike,
                            expiry, qty, price, round(fees / len(legs), 4), effect))
    return out


class SchwabAccountClient:
    """GET-only access to ``ALLOWED``. Uses the market-data sign-in (``SchwabTokens``); never stores raw bodies."""

    def __init__(self, http: ProviderHttp, tokens: SchwabTokens) -> None:
        if http.raw_store is not None:
            raise ValueError("account responses are never kept as raw evidence")
        self.http, self.tokens = http, tokens
        self.http.auth_headers = tokens.headers

    @property
    def configured(self) -> bool:
        return self.tokens.configured

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        endpoint = next((name for name, pattern in ALLOWED if pattern.fullmatch(path)), None)
        if endpoint is None:
            raise ValueError("schwab account: only the read-only account paths may be requested")
        try:
            response = await self.http.request(endpoint, path=path, method="GET", params=params or {})
        except ProviderError as exc:
            if exc.status_code in (401, 403):
                raise AccountNotEnabled("schwab_account", ProviderErrorKind.HTTP_ERROR,
                                        f"{endpoint}: HTTP {exc.status_code}", exc.status_code) from exc
            raise
        if response.status in (401, 403):
            raise AccountNotEnabled("schwab_account", ProviderErrorKind.HTTP_ERROR,
                                    f"{endpoint}: HTTP {response.status}", response.status)
        if response.status != 200:
            raise ProviderError("schwab_account", ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {response.status}",
                                response.status)
        return response.body

    async def accounts(self) -> dict[str, Account]:
        return parse_account_numbers(await self._get("/accounts/accountNumbers"))

    async def positions(self, accounts: dict[str, Account]) -> tuple[list[Account], list[Position]]:
        return parse_positions(await self._get("/accounts", {"fields": "positions"}), accounts)

    async def trades(self, account: Account, start: datetime, end: datetime) -> list[Fill]:
        stamp = "%Y-%m-%dT%H:%M:%S.000Z"
        body = await self._get(f"/accounts/{account.hash_value}/transactions",
                               {"startDate": start.astimezone(UTC).strftime(stamp),
                                "endDate": end.astimezone(UTC).strftime(stamp), "types": "TRADE"})
        return parse_transactions(body, account)


__all__ = ["ALLOWED", "SOURCE", "Account", "AccountNotEnabled", "Fill", "Position", "SchwabAccountClient",
           "account_key", "option_parts", "parse_account_numbers", "parse_positions", "parse_transactions"]
