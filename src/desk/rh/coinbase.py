"""Coinbase Exchange public candles: price history for the Robinhood desk (Robinhood's API has none).

History only: a Coinbase price is never a fill price. Rows are ``[bucket_start_s, low, high, open, close,
volume]``, newest first, and the newest bar is still forming, so only bars whose end is at or before the receipt
time are returned. Missing bars (no trades) stay missing; nothing is interpolated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.core.clock import ensure_utc
from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.rh.contracts import Candle

GRANULARITIES = frozenset({60, 300, 900, 3600, 21600, 86400})
MAX_BARS = 300


def parse_candles(body: bytes, product: str, granularity: int, received_at: datetime) -> list[Candle]:
    try:
        rows = json.loads(body, parse_float=Decimal, parse_int=Decimal)
    except ValueError as exc:
        raise SchemaError(f"coinbase candles {product}: not JSON") from exc
    if not isinstance(rows, list):
        raise SchemaError(f"coinbase candles {product}: expected a list")
    out: dict[int, Candle] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise SchemaError(f"coinbase candles {product}: malformed row")
        start_s = int(row[0])
        start = datetime.fromtimestamp(start_s, UTC)
        if start + timedelta(seconds=granularity) > received_at:
            continue                                   # still forming
        low, high, open_, close, volume = (Decimal(str(v)) for v in row[1:6])
        if not (low <= min(open_, close) and high >= max(open_, close) and low > 0 and volume >= 0):
            raise SchemaError(f"coinbase candles {product}: inconsistent bar at {start_s}")
        out[start_s] = Candle(product, granularity, start, open_, high, low, close, volume)
    return [out[k] for k in sorted(out)]


class CoinbaseClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    async def candles(self, product: str, granularity: int, start: datetime, end: datetime) -> list[Candle]:
        """Closed bars with start in [start, end), oldest first, paging by at most 300 bars."""
        if granularity not in GRANULARITIES:
            raise ValueError(f"granularity must be one of {sorted(GRANULARITIES)}")
        start, end = ensure_utc(start), ensure_utc(end)
        step = timedelta(seconds=granularity * MAX_BARS)
        out: dict[datetime, Candle] = {}
        cursor = start
        while cursor < end:
            stop = min(cursor + step, end)
            params: dict[str, Any] = {"granularity": granularity, "start": cursor.isoformat(), "end": stop.isoformat()}
            resp = await self.http.request("candles", path=f"/products/{product}/candles", params=params,
                                           label="candles")
            if resp.status == 404:
                raise ProviderError("coinbase", ProviderErrorKind.HTTP_ERROR, f"candles: no product {product}", 404)
            if resp.status != 200:
                raise ProviderError("coinbase", ProviderErrorKind.HTTP_ERROR, f"candles {product}: HTTP {resp.status}",
                                    resp.status)
            for candle in parse_candles(resp.body, product, granularity, resp.received_at):
                if start <= candle.start < end:
                    out[candle.start] = candle
            cursor = stop
        return [out[k] for k in sorted(out)]

    async def usd_products(self) -> set[str]:
        resp = await self.http.request("products", label="products")
        if resp.status != 200:
            raise ProviderError("coinbase", ProviderErrorKind.HTTP_ERROR, f"products: HTTP {resp.status}", resp.status)
        rows = json.loads(resp.body)
        return {str(r["id"]) for r in rows if isinstance(r, dict) and r.get("quote_currency") == "USD"
                and r.get("status") == "online" and not r.get("trading_disabled")}


__all__ = ["GRANULARITIES", "MAX_BARS", "CoinbaseClient", "parse_candles"]
