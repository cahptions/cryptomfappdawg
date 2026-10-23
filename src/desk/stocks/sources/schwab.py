"""Schwab market data, normalized to the same stock/options contracts as Tradier. No account/order methods.

Only standard 100-share options are admitted; adjusted deliverables require an explicit contract model.
Schwab option symbols contain spaces; the ledger's OCC identifiers do not. Missing averages stay missing.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime
from typing import Any

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.stocks.contracts import OptionQuote, StockQuote
from desk.stocks.schwab_auth import SchwabTokens

OCC = re.compile(r"^[A-Z0-9.]{1,6}\d{6}[CP]\d{8}$")


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
        return out if math.isfinite(out) and out >= 0 else None
    except (ValueError, TypeError):
        return None


def _time(value: Any) -> datetime | None:
    n = _f(value)
    if n is None or n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n / 1000, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _body(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or "errors" in value or value.get("status") == "FAILED":
            raise ValueError
        return dict(value)
    except (ValueError, TypeError) as exc:
        raise SchemaError("schwab market data: invalid response (details suppressed)") from exc


def parse_expirations(body: bytes) -> list[date]:
    value = _body(body)
    try:
        return sorted({date.fromisoformat(row["expirationDate"][:10]) for row in value["expirationList"]})
    except (KeyError, ValueError, TypeError) as exc:
        raise SchemaError("schwab expirations: invalid expiration list") from exc


def _standard_deliverable(row: dict[str, Any], symbol: str) -> bool:
    """Schwab also describes *standard* contracts with an explicit 100-stock deliverable.

    A nonempty list is not itself an adjustment. Cash, baskets, other underlyings and unknown
    deliverables still need a separate contract model and are excluded.
    """
    if row.get("nonStandard", False) or row.get("mini", False) or _f(row.get("multiplier")) != 100:
        return False
    items = row.get("optionDeliverablesList")
    if items is None or items == []:
        return True
    return (isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict)
            and items[0].get("symbol") == symbol and items[0].get("assetType") == "STOCK"
            and _f(items[0].get("deliverableUnits")) == 100)


def parse_chain(body: bytes, symbol: str, expiration: date) -> list[OptionQuote]:
    value = _body(body)
    out: dict[str, OptionQuote] = {}
    try:
        if value.get("symbol", symbol) != symbol:
            raise ValueError
        if value.get("isDelayed") is True:
            raise SchemaError("schwab chains: delayed data requires a separate delayed-execution experiment")
        for field, kind in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
            for key, strikes in (value.get(field) or {}).items():
                exp = date.fromisoformat(key.split(":")[0])
                if exp != expiration:
                    continue
                for contracts in strikes.values():
                    for row in contracts:
                        occ = str(row.get("symbol", "")).replace(" ", "")
                        strike = _f(row.get("strikePrice"))
                        if (not OCC.fullmatch(occ) or strike is None or strike <= 0
                                or not _standard_deliverable(row, symbol)):
                            continue
                        # Contract identity must agree with its map, not just have a plausible-looking name.
                        if (occ[:-15] != symbol or occ[-15:-9] != exp.strftime("%y%m%d") or occ[-9] != kind[0].upper()
                                or int(occ[-8:]) != round(strike * 1000)):
                            continue
                        out[occ] = OptionQuote(occ, symbol, kind, strike, exp, _f(row.get("bid")), _f(row.get("ask")),
                                               _f(row.get("last")), int(_f(row.get("totalVolume")) or 0),
                                               int(_f(row.get("openInterest")) or 0), None,
                                               _time(row.get("quoteTimeInLong")))
        return list(out.values())
    except (ValueError, TypeError, AttributeError) as exc:
        raise SchemaError("schwab chains: invalid expiration/strike map") from exc


def parse_quotes(body: bytes) -> list[StockQuote]:
    value = _body(body)
    out = []
    try:
        for symbol, row in value.items():
            q = row.get("quote")
            if not isinstance(q, dict):
                continue
            out.append(StockQuote(str(row.get("symbol") or symbol), _f(q.get("lastPrice")), _f(q.get("bidPrice")),
                                  _f(q.get("askPrice")), _f(q.get("closePrice")), _f(q.get("totalVolume")),
                                  _time(q.get("quoteTime"))))
        return out
    except (ValueError, TypeError, AttributeError) as exc:
        raise SchemaError("schwab quotes: invalid quote map") from exc


class SchwabClient:
    def __init__(self, http: ProviderHttp, tokens: SchwabTokens) -> None:
        self.http, self.tokens = http, tokens
        self.http.auth_headers = tokens.headers

    @property
    def configured(self) -> bool:
        return self.tokens.configured

    async def _get(self, endpoint: str, params: dict[str, Any]) -> bytes:
        if not self.configured:
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, "Schwab is not connected; run desk stocks connect")
        response = await self.http.request(endpoint, params=params)
        if response.status != 200:
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {response.status}", response.status)
        return response.body

    async def expirations(self, symbol: str) -> list[date]:
        return parse_expirations(await self._get("expirations", {"symbol": symbol}))

    async def chain(self, symbol: str, expiration: date) -> list[OptionQuote]:
        return parse_chain(await self._get("chains", {"symbol": symbol, "contractType": "ALL", "strategy": "SINGLE",
                                                       "fromDate": expiration.isoformat(), "toDate": expiration.isoformat(),
                                                       "includeUnderlyingQuote": "true"}), symbol, expiration)

    async def quotes(self, symbols: list[str]) -> list[StockQuote]:
        if not 1 <= len(symbols) <= 100:
            raise ValueError("Schwab quote batches contain 1..100 symbols")
        return parse_quotes(await self._get("quotes", {"symbols": ",".join(symbols), "fields": "quote"}))
