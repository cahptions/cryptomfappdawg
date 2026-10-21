"""Massive (formerly Polygon.io) Stocks Basic, free: end-of-day bars for every US stock, splits and dividends.

The free plan (verified 2026-10-21 on massive.com/pricing): $0, personal licence, 5 requests a minute, 2 years of
history, end-of-day data. One "grouped daily" request returns the day's bar for every US stock, so the daily refresh
is a single request and two years of history is about 500 (one background backfill). The key is the ``apiKey``
query parameter (``MASSIVE_API_KEY`` in secrets.env).

Bars are stored unadjusted (``adjusted=false``) and splits are applied when reading, so a split announced later
never silently rewrites history already used for a decision.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.stocks.contracts import Bar, Dividend, Split


def _market_day(epoch_ms: int) -> date:
    """A grouped daily bar is stamped at the 16:00 New York close (20:00 or 21:00 UTC; documented example
    1602705600000 = 2020-10-14 20:00 UTC), so its UTC date is the trading day."""
    return datetime.fromtimestamp(epoch_ms / 1000, UTC).date()


def parse_grouped(body: bytes) -> list[Bar]:
    try:
        d = json.loads(body)
        if d.get("status") not in ("OK", "DELAYED"):
            raise SchemaError(f"massive grouped daily: status {d.get('status')}")
        out = []
        for r in d.get("results") or []:
            if not all(k in r for k in ("T", "o", "h", "l", "c", "v", "t")):
                continue
            out.append(Bar(str(r["T"]), _market_day(int(r["t"])), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]),
                           float(r["v"]), None if r.get("vw") is None else float(r["vw"]),
                           None if r.get("n") is None else int(r["n"])))
        return out
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError(f"massive grouped daily: {exc}") from exc


def parse_splits(body: bytes) -> tuple[list[Split], str | None]:
    try:
        d = json.loads(body)
        out = [Split(str(r["ticker"]), date.fromisoformat(r["execution_date"]), float(r["split_from"]), float(r["split_to"]))
               for r in d.get("results") or [] if r.get("split_from") and r.get("split_to")]
        return out, _cursor(d.get("next_url"))
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError(f"massive splits: {exc}") from exc


def parse_dividends(body: bytes) -> tuple[list[Dividend], str | None]:
    try:
        d = json.loads(body)
        out = []
        for r in d.get("results") or []:
            if r.get("ex_dividend_date") is None or r.get("cash_amount") is None:
                continue
            out.append(Dividend(str(r["ticker"]), date.fromisoformat(r["ex_dividend_date"]),
                                float(r.get("split_adjusted_cash_amount") or r["cash_amount"]),
                                date.fromisoformat(r["pay_date"]) if r.get("pay_date") else None,
                                r.get("frequency"), r.get("distribution_type")))
        return out, _cursor(d.get("next_url"))
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError(f"massive dividends: {exc}") from exc


def _cursor(next_url: str | None) -> str | None:
    """The next page's cursor (the next_url itself is never requested: it would carry the key back to us)."""
    if not next_url:
        return None
    values = parse_qs(urlparse(next_url).query).get("cursor")
    return values[0] if values else None


class MassiveClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    @property
    def configured(self) -> bool:
        return self.http.has_key

    async def _get(self, endpoint: str, *, path: str | None = None, params: dict[str, Any] | None = None) -> bytes:
        if not self.configured:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, "MASSIVE_API_KEY is not in secrets.env")
        resp = await self.http.request(endpoint, path=path, params=params)
        if resp.status != 200:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {resp.status}", resp.status)
        return resp.body

    async def grouped_daily(self, day: date) -> list[Bar]:
        """Every US stock's bar for ``day`` (empty on a market holiday)."""
        body = await self._get("grouped_daily", path=f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
                               params={"adjusted": "false"})
        return parse_grouped(body)

    async def splits(self, since: date) -> list[Split]:
        return await self._paged("splits", parse_splits, {"execution_date.gte": since.isoformat(), "limit": 1000})

    async def dividends(self, since: date) -> list[Dividend]:
        return await self._paged("dividends", parse_dividends, {"ex_dividend_date.gte": since.isoformat(), "limit": 1000})

    async def _paged(self, endpoint: str, parse: Any, params: dict[str, Any]) -> list[Any]:
        out: list[Any] = []
        cursor: str | None = None
        for _ in range(200):
            rows, cursor = parse(await self._get(endpoint, params={**params, **({"cursor": cursor} if cursor else {})}))
            out += rows
            if not cursor:
                return out
        return out


__all__ = ["MassiveClient", "parse_dividends", "parse_grouped", "parse_splits"]
