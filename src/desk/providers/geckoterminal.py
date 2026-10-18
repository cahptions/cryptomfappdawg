"""GeckoTerminal public API v2: discovery of pools and OHLCV backfill. Budget ≈ 8 calls/min (RESEARCH.md §1)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.contracts.market import WINDOWS, OhlcvBar, PoolSnapshot, WindowMetrics
from desk.core.clock import from_epoch_seconds
from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.providers.parsing import (
    ITEM_ERRORS,
    Fetched,
    ParseResult,
    expect_dict,
    expect_list,
    load_json,
    observed_decimal,
    opt_decimal,
    opt_int,
    opt_iso,
    strict_int,
    untrusted,
)
from desk.providers.venues import canonical_venue

PROVIDER = "geckoterminal"
_SOLANA_PREFIX = "solana_"


def _strip_network(identifier: Any) -> str:
    if not isinstance(identifier, str) or not identifier.startswith(_SOLANA_PREFIX):
        raise SchemaError(f"expected a solana_ prefixed id, got {identifier!r}")
    return identifier[len(_SOLANA_PREFIX):]


def _windows(attrs: dict[str, Any]) -> dict[str, WindowMetrics]:
    txns = attrs.get("transactions") or {}
    volume = attrs.get("volume_usd") or {}
    change = attrs.get("price_change_percentage") or {}
    out: dict[str, WindowMetrics] = {}
    for window in WINDOWS:
        tx = txns.get(window) or {}
        if window not in txns and window not in volume and window not in change:
            continue
        out[window] = WindowMetrics(
            volume_usd=opt_decimal(volume.get(window)),
            price_change_pct=opt_decimal(change.get(window)),
            buys=opt_int(tx.get("buys")),
            sells=opt_int(tx.get("sells")),
            buyers=opt_int(tx.get("buyers")),
            sellers=opt_int(tx.get("sellers")),
        )
    return out


def _pool(item: dict[str, Any], source: str, observed_at: datetime, raw_sha256: str | None) -> PoolSnapshot:
    attrs = expect_dict(item["attributes"], "attributes")
    rel = expect_dict(item["relationships"], "relationships")
    dex_id = rel["dex"]["data"]["id"]
    if not isinstance(dex_id, str):
        raise SchemaError("dex id missing")
    flags: list[str] = []

    reserve_raw = attrs.get("reserve_in_usd")
    reserve = opt_decimal(reserve_raw)
    liquidity_flags: tuple[str, ...] = ()
    if reserve is not None and reserve == 0:
        liquidity_flags = ("SUSPICIOUS_ZERO",)  # an active pool reporting exactly zero reserves
        flags.append("SUSPICIOUS_ZERO_LIQUIDITY")

    return PoolSnapshot(
        source=source,
        pool_address=attrs["address"],
        base_mint=_strip_network(rel["base_token"]["data"]["id"]),
        quote_mint=_strip_network(rel["quote_token"]["data"]["id"]),
        venue=canonical_venue(PROVIDER, dex_id),
        venue_raw=dex_id,
        name=untrusted(attrs.get("name"), 80, flags, "NAME_SANITIZED"),
        base_symbol=None,
        pool_created_at=opt_iso(attrs.get("pool_created_at")),
        price_usd=observed_decimal(attrs.get("base_token_price_usd"), source, observed_at),
        price_native=observed_decimal(attrs.get("base_token_price_native_currency"), source, observed_at),
        liquidity_usd=observed_decimal(reserve_raw, source, observed_at, liquidity_flags),
        fdv_usd=observed_decimal(attrs.get("fdv_usd"), source, observed_at),
        market_cap_usd=observed_decimal(attrs.get("market_cap_usd"), source, observed_at),
        locked_liquidity_pct=opt_decimal(attrs.get("locked_liquidity_percentage")),
        windows=_windows(attrs),
        flags=tuple(flags),
        observed_at=observed_at,
        raw_sha256=raw_sha256,
    )


def parse_pools(body: bytes, source: str, observed_at: datetime, raw_sha256: str | None = None) -> ParseResult[PoolSnapshot]:
    """Parse ``new_pools``, ``trending_pools`` and ``pools/multi`` responses (same JSON:API shape)."""
    doc = expect_dict(load_json(body), "response")
    data = expect_list(doc.get("data"), "data")
    result: ParseResult[PoolSnapshot] = ParseResult()
    for item in data:
        try:
            result.items.append(_pool(expect_dict(item, "pool"), source, observed_at, raw_sha256))
        except ITEM_ERRORS as exc:
            ident = item.get("id") if isinstance(item, dict) else "?"
            result.errors.append(f"{ident}: {type(exc).__name__}: {exc}")
    return result


def parse_ohlcv(body: bytes, pool_address: str, timeframe: str, observed_at: datetime) -> ParseResult[OhlcvBar]:
    doc = expect_dict(load_json(body), "response")
    rows = expect_list(expect_dict(expect_dict(doc.get("data"), "data").get("attributes"), "attributes")
                       .get("ohlcv_list"), "ohlcv_list")
    result: ParseResult[OhlcvBar] = ParseResult()
    for row in rows:
        try:
            values = expect_list(row, "bar")
            if len(values) != 6:
                raise SchemaError(f"expected 6 values per bar, got {len(values)}")
            ts, o, h, low, c, v = values
            bar = OhlcvBar(
                pool_address=pool_address,
                timeframe=timeframe,
                ts=from_epoch_seconds(strict_int(ts)),
                open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(c), volume_usd=Decimal(v),
                source=f"{PROVIDER}.ohlcv",
                observed_at=observed_at,
            )
            if not (bar.low <= min(bar.open, bar.close) and bar.high >= max(bar.open, bar.close)):
                raise SchemaError("inconsistent OHLC values")
            result.items.append(bar)
        except ITEM_ERRORS as exc:
            result.errors.append(f"bar {row!r}: {exc}")
    return result


class GeckoTerminalClient:
    """Network client. Every call goes through the shared budget (≈8/min) and the 60 s response cache."""

    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    def _ttl(self) -> float:
        return float(self.http.spec.freshness_seconds)

    async def _pools(self, endpoint: str, source: str, *, path: str | None = None,
                     params: dict[str, Any] | None = None) -> Fetched[PoolSnapshot]:
        resp = await self.http.request(endpoint, path=path, params=params, cache_ttl=self._ttl())
        if resp.status == 404:
            return Fetched([], ["HTTP 404"], resp.sha256, resp.status, resp.latency_ms)
        if resp.status != 200:
            raise ProviderError(PROVIDER, ProviderErrorKind.HTTP_ERROR, f"{endpoint} HTTP {resp.status}", resp.status)
        try:
            parsed = parse_pools(resp.body, source, resp.received_at, resp.sha256)
        except SchemaError as exc:
            self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
            raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, str(exc)) from exc
        return Fetched(parsed.items, parsed.errors, resp.sha256, resp.status, resp.latency_ms)

    async def new_pools(self, page: int = 1) -> Fetched[PoolSnapshot]:
        return await self._pools("new_pools", f"{PROVIDER}.new_pools", params={"page": page})

    async def trending_pools(self, duration: str = "1h", page: int = 1) -> Fetched[PoolSnapshot]:
        return await self._pools("trending_pools", f"{PROVIDER}.trending_pools",
                                 params={"duration": duration, "page": page})

    async def pools_multi(self, addresses: list[str]) -> Fetched[PoolSnapshot]:
        if not 1 <= len(addresses) <= 30:
            raise ValueError("pools_multi accepts 1-30 addresses")
        return await self._pools("pools_multi", f"{PROVIDER}.pools_multi",
                                 path=f"/networks/solana/pools/multi/{','.join(addresses)}")

    async def ohlcv(self, pool: str, timeframe: str = "minute", aggregate: int = 5, limit: int = 100) -> Fetched[OhlcvBar]:
        resp = await self.http.request("ohlcv", path=f"/networks/solana/pools/{pool}/ohlcv/{timeframe}",
                                       params={"aggregate": aggregate, "limit": limit}, cache_ttl=self._ttl())
        if resp.status != 200:
            raise ProviderError(PROVIDER, ProviderErrorKind.HTTP_ERROR, f"ohlcv HTTP {resp.status}", resp.status)
        try:
            parsed = parse_ohlcv(resp.body, pool, f"{timeframe}:{aggregate}", resp.received_at)
        except SchemaError as exc:
            self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
            raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, str(exc)) from exc
        return Fetched(parsed.items, parsed.errors, resp.sha256, resp.status, resp.latency_ms)
