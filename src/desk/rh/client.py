"""Robinhood Crypto Trading API: read-only market data and account calls, signed per request.

PAPER needs prices only, so this client has no order methods: a key created with read-only actions is enough
and nothing here can place, cancel or change an order. Responses are parsed with Decimal; a missing or malformed
field raises SchemaError rather than being guessed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from desk.core.clock import parse_iso8601
from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import HttpResponse, ProviderHttp
from desk.rh.contracts import BidAsk, Estimate, Pair, Route, Side

PROVIDER = "robinhood"
MAX_QUANTITIES = 10           # docs: at most 10 comma-separated quantities per estimated-price request
MAX_SYMBOLS_PER_QUOTE = 50    # keeps the signed URL short; docs set no number


def _dec(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SchemaError(f"robinhood: {field} missing")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise SchemaError(f"robinhood: {field} is not a number") from exc
    if not number.is_finite():
        raise SchemaError(f"robinhood: {field} is not finite")
    return number


def _optional_dec(value: Any, field: str) -> Decimal | None:
    return None if value in (None, "") else _dec(value, field)


def _plain(number: Decimal) -> str:
    text = format(number.normalize(), "f")
    return text if text not in ("-0", "") else "0"


def _body(resp: HttpResponse) -> Any:
    if resp.status == 400:
        raise ProviderError(PROVIDER, ProviderErrorKind.HTTP_ERROR, f"{resp.endpoint}: rejected (HTTP 400)", 400)
    if resp.status == 404:
        raise ProviderError(PROVIDER, ProviderErrorKind.HTTP_ERROR, f"{resp.endpoint}: not found (HTTP 404)", 404)
    try:
        return json.loads(resp.body, parse_float=Decimal, parse_int=Decimal)
    except ValueError as exc:
        raise SchemaError(f"robinhood {resp.endpoint}: response is not JSON") from exc


def _results(body: Any, endpoint: str) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise SchemaError(f"robinhood {endpoint}: results list missing")
    return [item for item in body["results"] if isinstance(item, dict)]


def parse_pair(item: dict[str, Any]) -> Pair:
    try:
        return Pair(symbol=str(item["symbol"]), asset_code=str(item["asset_code"]), quote_code=str(item["quote_code"]),
                    asset_increment=_dec(item["asset_increment"], "asset_increment"),
                    quote_increment=_dec(item["quote_increment"], "quote_increment"),
                    max_order_size=_dec(item["max_order_size"], "max_order_size"),
                    min_order_amount=_optional_dec(item.get("min_order_amount"), "min_order_amount"),
                    status=str(item["status"]), is_api_tradable=item.get("is_api_tradable") is True)
    except KeyError as exc:
        raise SchemaError(f"robinhood trading pair: {exc.args[0]} missing") from exc


def parse_best_bid_ask(resp: HttpResponse) -> list[BidAsk]:
    out = []
    for item in _results(_body(resp), resp.endpoint):
        bid, ask = _dec(item.get("bid"), "bid"), _dec(item.get("ask"), "ask")
        if bid <= 0 or ask < bid:
            raise SchemaError(f"robinhood best_bid_ask: crossed or empty book for {item.get('symbol')}")
        out.append(BidAsk(str(item["symbol"]), bid, ask, resp.received_at, resp.sha256))
    return out


def parse_estimates_v2(resp: HttpResponse) -> list[Estimate]:
    out = []
    for item in _results(_body(resp), resp.endpoint):
        book_side = item.get("side")
        quantity = _dec(item.get("quantity"), "quantity")
        provider_at = parse_iso8601(item["timestamp"]) if item.get("timestamp") else None
        fee, ratio = _dec(item.get("est_fee"), "est_fee"), _optional_dec(item.get("fee_ratio"), "fee_ratio")
        if book_side == "ask":
            side: Side = "BUY"
            unit, total = _dec(item.get("ask"), "ask"), _dec(item.get("est_total_cost"), "est_total_cost")
        elif book_side == "bid":
            side = "SELL"
            unit, total = _dec(item.get("bid"), "bid"), _dec(item.get("est_total_credit"), "est_total_credit")
        else:
            raise SchemaError(f"robinhood estimated_price_v2: side {book_side!r}")
        out.append(Estimate(str(item["symbol"]), "EX", side, quantity, unit, fee, total, ratio, provider_at,
                            resp.received_at, resp.sha256))
    return out


def parse_estimates_v1(resp: HttpResponse) -> list[Estimate]:
    out = []
    for item in _results(_body(resp), resp.endpoint):
        book_side = item.get("side")
        quantity = _dec(item.get("quantity"), "quantity")
        provider_at = parse_iso8601(item["timestamp"]) if item.get("timestamp") else None
        if book_side == "ask":
            side: Side = "BUY"
            unit = _dec(item.get("ask_inclusive_of_buy_spread"), "ask_inclusive_of_buy_spread")
        elif book_side == "bid":
            side = "SELL"
            unit = _dec(item.get("bid_inclusive_of_sell_spread"), "bid_inclusive_of_sell_spread")
        else:
            raise SchemaError(f"robinhood estimated_price_v1: side {book_side!r}")
        out.append(Estimate(str(item["symbol"]), "MM", side, quantity, unit, Decimal(0), unit * quantity, None,
                            provider_at, resp.received_at, resp.sha256))
    return out


class RobinhoodClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    @property
    def ready(self) -> bool:
        """A key and a signer are both configured."""
        return self.http.has_key and self.http.signer is not None

    async def _get(self, endpoint: str, query: str = "", *, path: str | None = None) -> HttpResponse:
        base = path if path is not None else self.http.spec.endpoints[endpoint].path
        return await self.http.request(endpoint, path=base + query, label=endpoint)

    async def trading_pairs(self) -> list[Pair]:
        pairs: list[Pair] = []
        resp = await self._get("trading_pairs")
        for _ in range(50):                                   # bounded pagination
            body = _body(resp)
            pairs.extend(parse_pair(item) for item in _results(body, resp.endpoint))
            following = body.get("next") if isinstance(body, dict) else None
            if not following:
                return pairs
            parts = urlsplit(str(following))
            resp = await self._get("trading_pairs", path=parts.path + (f"?{parts.query}" if parts.query else ""))
        raise SchemaError("robinhood trading_pairs: pagination did not end")

    async def best_bid_ask(self, symbols: Iterable[str]) -> list[BidAsk]:
        wanted = sorted({s.upper() for s in symbols})
        out: list[BidAsk] = []
        for i in range(0, len(wanted), MAX_SYMBOLS_PER_QUOTE):
            chunk = wanted[i:i + MAX_SYMBOLS_PER_QUOTE]
            out.extend(parse_best_bid_ask(await self._get("best_bid_ask", "?" + "&".join(f"symbol={s}" for s in chunk))))
        return out

    async def estimates(self, symbol: str, route: Route, quantities: Iterable[Decimal]) -> list[Estimate]:
        """Both sides for each quantity (one request); quantities must already respect the pair's increment."""
        values = [_plain(q) for q in quantities]
        if not values or len(values) > MAX_QUANTITIES:
            raise ValueError(f"1 to {MAX_QUANTITIES} quantities")
        query = f"?symbol={symbol.upper()}&side=both&quantity={','.join(values)}"
        if route == "EX":
            return parse_estimates_v2(await self._get("estimated_price_v2", query))
        return parse_estimates_v1(await self._get("estimated_price_v1", query))

    async def accounts(self) -> list[dict[str, Any]]:
        resp = await self._get("accounts")
        return _results(_body(resp), resp.endpoint)


__all__ = ["MAX_QUANTITIES", "RobinhoodClient", "parse_best_bid_ask", "parse_estimates_v1", "parse_estimates_v2",
           "parse_pair"]
