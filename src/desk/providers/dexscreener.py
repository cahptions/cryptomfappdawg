"""DexScreener: the refresh workhorse (300 req/min, 30 tokens per call). No neutral discovery endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from desk.contracts.market import PoolSnapshot, WindowMetrics
from desk.contracts.tokens import TokenClaims
from desk.core.base58 import is_solana_address
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
    opt_epoch_ms,
    opt_int,
    untrusted,
)
from desk.providers.venues import canonical_venue

PROVIDER = "dexscreener"
_DS_WINDOWS = ("m5", "h1", "h6", "h24")


def _windows(pair: dict[str, Any]) -> dict[str, WindowMetrics]:
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    out: dict[str, WindowMetrics] = {}
    for window in _DS_WINDOWS:
        if window not in txns and window not in volume and window not in change:
            continue
        tx = txns.get(window) or {}
        out[window] = WindowMetrics(
            volume_usd=opt_decimal(volume.get(window)),
            price_change_pct=opt_decimal(change.get(window)),
            buys=opt_int(tx.get("buys")),
            sells=opt_int(tx.get("sells")),
            # DexScreener does not report distinct traders: buyers/sellers stay None (unknown), never 0
        )
    return out


def _pair(pair: dict[str, Any], source: str, observed_at: datetime, raw_sha256: str | None) -> PoolSnapshot:
    if pair.get("chainId") != "solana":
        raise SchemaError(f"unexpected chainId {pair.get('chainId')!r}")
    base = expect_dict(pair["baseToken"], "baseToken")
    quote = expect_dict(pair["quoteToken"], "quoteToken")
    dex_id = pair["dexId"]
    flags: list[str] = []
    liquidity_raw = pair.get("liquidity")
    liquidity: dict[str, Any] = liquidity_raw if isinstance(liquidity_raw, dict) else {}
    liquidity_usd = liquidity.get("usd")
    liquidity_flags: tuple[str, ...] = ()
    if liquidity_usd is None:
        flags.append("LIQUIDITY_NOT_REPORTED")  # observed on every bonding-curve pair in M0
    elif opt_decimal(liquidity_usd) == 0:
        # observed 2026-10-19 on drained PumpSwap pools: usd 0, quote reserve ~0.00002 SOL, volume still shown
        liquidity_flags = ("ZERO_REPORTED",)
        flags.append("ZERO_LIQUIDITY_REPORTED")
    return PoolSnapshot(
        source=source,
        pool_address=pair["pairAddress"],
        base_mint=base["address"],
        quote_mint=quote.get("address"),
        venue=canonical_venue(PROVIDER, dex_id),
        venue_raw=dex_id,
        name=untrusted(base.get("name"), 80, flags, "NAME_SANITIZED"),
        base_symbol=untrusted(base.get("symbol"), 32, flags, "SYMBOL_SANITIZED"),
        pool_created_at=opt_epoch_ms(pair.get("pairCreatedAt")),
        price_usd=observed_decimal(pair.get("priceUsd"), source, observed_at),
        price_native=observed_decimal(pair.get("priceNative"), source, observed_at),
        liquidity_usd=observed_decimal(liquidity_usd, source, observed_at, liquidity_flags),
        liquidity_base_amount=opt_decimal(liquidity.get("base")),
        liquidity_quote_amount=opt_decimal(liquidity.get("quote")),
        fdv_usd=observed_decimal(pair.get("fdv"), source, observed_at),
        market_cap_usd=observed_decimal(pair.get("marketCap"), source, observed_at),
        windows=_windows(pair),
        flags=tuple(flags),
        observed_at=observed_at,
        raw_sha256=raw_sha256,
    )


def parse_pairs(body: bytes, source: str, observed_at: datetime, raw_sha256: str | None = None) -> ParseResult[PoolSnapshot]:
    """Parse ``/tokens/v1`` (a bare list) or ``/latest/dex/pairs`` (``{"pairs": [...]}``)."""
    doc = load_json(body)
    if isinstance(doc, dict):
        doc = doc.get("pairs") or []
    pairs = expect_list(doc, "pairs")
    result: ParseResult[PoolSnapshot] = ParseResult()
    for pair in pairs:
        try:
            result.items.append(_pair(expect_dict(pair, "pair"), source, observed_at, raw_sha256))
        except ITEM_ERRORS as exc:
            ident = pair.get("pairAddress") if isinstance(pair, dict) else "?"
            result.errors.append(f"{ident}: {type(exc).__name__}: {exc}")
    return result


class DexScreenerClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    async def profiles(self) -> Fetched[TokenClaims]:
        """Profile/promotional discovery is explicitly tagged; metadata URLs and images are never fetched."""
        resp = await self.http.request("token_profiles_latest", cache_ttl=300.0)
        try:
            doc = expect_list(load_json(resp.body), "profiles")
        except SchemaError as exc:
            self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
            raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, str(exc)) from exc
        result: ParseResult[TokenClaims] = ParseResult()
        for item in doc:
            try:
                profile = expect_dict(item, "profile")
                if profile.get("chainId") != "solana":
                    continue
                result.items.append(TokenClaims(source="dexscreener.promotions", mint=profile["tokenAddress"],
                                                observed_at=resp.received_at, raw_sha256=resp.sha256,
                                                flags=("PROMOTED",)))
            except ITEM_ERRORS as exc:
                result.errors.append(f"profile: {type(exc).__name__}: {exc}")
        return Fetched(result.items, result.errors, resp.sha256, resp.status, resp.latency_ms)

    async def tokens(self, mints: list[str]) -> Fetched[PoolSnapshot]:
        """All pairs for up to 30 mints. Mints DexScreener has not indexed yet are simply absent (not zero)."""
        if not 1 <= len(mints) <= 30:
            raise ValueError("tokens/v1 accepts 1-30 mints")
        bad = [m for m in mints if not is_solana_address(m)]
        if bad:
            raise ValueError(f"invalid mint addresses: {bad}")
        resp = await self.http.request("tokens_v1", path=f"/tokens/v1/solana/{','.join(mints)}",
                                       cache_ttl=float(self.http.spec.freshness_seconds))
        if resp.status != 200:
            raise ProviderError(PROVIDER, ProviderErrorKind.HTTP_ERROR, f"tokens_v1 HTTP {resp.status}", resp.status)
        try:
            parsed = parse_pairs(resp.body, f"{PROVIDER}.tokens_v1", resp.received_at, resp.sha256)
        except SchemaError as exc:
            self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
            raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, str(exc)) from exc
        return Fetched(parsed.items, parsed.errors, resp.sha256, resp.status, resp.latency_ms)
