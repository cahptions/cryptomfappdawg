"""Tradier market data: option expirations and chains (with open interest), stock quotes, the market calendar.

Two ways to get a token, both free (docs verified 2026-10-21):
* a Tradier developer sandbox account: 15-minute delayed stocks and options, no greeks, 60 requests a minute;
* a Tradier Brokerage account: real-time data, greeks hourly, 120 requests a minute.
The desk computes implied volatility and greeks itself from bid/ask (desk.stocks.options), so both work the same.
The token is ``TRADIER_TOKEN`` in secrets.env; ``stocks.tradier_env`` picks sandbox or production. Market data only:
the desk never calls an order or account endpoint.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.stocks.contracts import Bar, OptionQuote, StockQuote


def _list(value: Any) -> list[Any]:
    """Tradier returns a single object instead of a one-item list, and null for none."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _f(value: Any) -> float | None:
    return None if value in (None, "", "NaN") else float(value)


def _ms(value: Any) -> datetime | None:
    if not value:
        return None
    v = int(value)
    return datetime.fromtimestamp(v / 1000 if v > 10_000_000_000 else v, UTC)


def parse_expirations(body: bytes) -> list[date]:
    try:
        d = json.loads(body)
        return [date.fromisoformat(x) for x in _list((d.get("expirations") or {}).get("date"))]
    except (ValueError, TypeError, AttributeError) as exc:
        raise SchemaError(f"tradier expirations: {exc}") from exc


def parse_chain(body: bytes) -> list[OptionQuote]:
    try:
        d = json.loads(body)
        out = []
        for o in _list((d.get("options") or {}).get("option")):
            kind = o.get("option_type")
            if kind not in ("call", "put") or o.get("strike") is None:
                continue
            quoted = max(filter(None, (_ms(o.get("bid_date")), _ms(o.get("ask_date")))), default=None)
            out.append(OptionQuote(str(o["symbol"]), str(o.get("underlying") or o.get("root_symbol")), kind, float(o["strike"]),
                                   date.fromisoformat(o["expiration_date"]), _f(o.get("bid")), _f(o.get("ask")),
                                   _f(o.get("last")), int(o.get("volume") or 0), int(o.get("open_interest") or 0),
                                   None if o.get("average_volume") is None else int(o["average_volume"]), quoted))
        return out
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SchemaError(f"tradier chain: {exc}") from exc


def parse_quotes(body: bytes) -> list[StockQuote]:
    try:
        d = json.loads(body)
        return [StockQuote(str(q["symbol"]), _f(q.get("last")), _f(q.get("bid")), _f(q.get("ask")), _f(q.get("prevclose")),
                           _f(q.get("volume")), _ms(q.get("trade_date")))
                for q in _list((d.get("quotes") or {}).get("quote")) if q.get("symbol")]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SchemaError(f"tradier quotes: {exc}") from exc


def parse_history(body: bytes, symbol: str) -> list[Bar]:
    try:
        d = json.loads(body)
        return [Bar(symbol, date.fromisoformat(r["date"]), float(r["open"]), float(r["high"]), float(r["low"]),
                    float(r["close"]), float(r.get("volume") or 0))
                for r in _list((d.get("history") or {}).get("day"))]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SchemaError(f"tradier history: {exc}") from exc


def parse_calendar(body: bytes) -> dict[date, bool]:
    """Day → market open (closed days included)."""
    try:
        d = json.loads(body)
        return {date.fromisoformat(r["date"]): r.get("status") == "open"
                for r in _list(((d.get("calendar") or {}).get("days") or {}).get("day"))}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SchemaError(f"tradier calendar: {exc}") from exc


class TradierClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    @property
    def configured(self) -> bool:
        return self.http.has_key

    async def _get(self, endpoint: str, params: dict[str, Any]) -> bytes:
        if not self.configured:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, "TRADIER_TOKEN is not in secrets.env")
        resp = await self.http.request(endpoint, params=params)
        if resp.status != 200:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {resp.status}", resp.status)
        return resp.body

    async def expirations(self, symbol: str) -> list[date]:
        return parse_expirations(await self._get("expirations", {"symbol": symbol, "includeAllRoots": "false"}))

    async def chain(self, symbol: str, expiration: date) -> list[OptionQuote]:
        return parse_chain(await self._get("chains", {"symbol": symbol, "expiration": expiration.isoformat(),
                                                      "greeks": "false"}))

    async def quotes(self, symbols: list[str]) -> list[StockQuote]:
        return parse_quotes(await self._get("quotes", {"symbols": ",".join(symbols)}))

    async def history(self, symbol: str, start: date, end: date) -> list[Bar]:
        return parse_history(await self._get("history", {"symbol": symbol, "interval": "daily",
                                                         "start": start.isoformat(), "end": end.isoformat()}), symbol)

    async def calendar(self, year: int, month: int) -> dict[date, bool]:
        return parse_calendar(await self._get("calendar", {"year": year, "month": month}))


__all__ = ["TradierClient", "parse_calendar", "parse_chain", "parse_expirations", "parse_history", "parse_quotes"]
