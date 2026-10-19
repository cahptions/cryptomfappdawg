"""Jupiter: swap quotes (Swap API v2 ``/order``) and token claims (Tokens API v2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.contracts.discovery import ShieldObservation, ShieldWarning
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.contracts.tokens import TokenAudit, TokenClaims, TokenStatsWindow
from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.providers.parsing import (
    ITEM_ERRORS,
    Fetched,
    ParseResult,
    expect_dict,
    expect_list,
    load_json,
    opt_bool,
    opt_decimal,
    opt_int,
    opt_iso,
    opt_str,
    strict_int,
    untrusted,
)

PROVIDER = "jupiter"
_STAT_KEYS = {"stats5m": "5m", "stats1h": "1h", "stats6h": "6h", "stats24h": "24h"}


def _price_impact_pct(doc: dict[str, Any]) -> Decimal | None:
    """v2 ``priceImpact`` is already in percentage points; legacy ``priceImpactPct`` is a fraction."""
    if doc.get("priceImpact") is not None:
        return opt_decimal(doc["priceImpact"])
    legacy = opt_decimal(doc.get("priceImpactPct"))
    return None if legacy is None else legacy * 100


def parse_quote(
    body: bytes,
    *,
    http_status: int,
    source: str,
    input_mint: str,
    output_mint: str,
    in_amount_raw: int,
    requested_at: datetime,
    received_at: datetime,
    latency_ms: Decimal,
    raw_sha256: str | None = None,
) -> QuoteObservation | QuoteUnavailable:
    doc = expect_dict(load_json(body), "quote")
    if http_status != 200 or ("outAmount" not in doc and ("error" in doc or "errorCode" in doc)):
        return QuoteUnavailable(
            source=source, input_mint=input_mint, output_mint=output_mint, in_amount_raw=in_amount_raw,
            error_code=None if doc.get("errorCode") is None else str(doc.get("errorCode")),
            error_message=opt_str(doc.get("error")) or opt_str(doc.get("errorMessage")),
            http_status=http_status, received_at=received_at, raw_sha256=raw_sha256,
        )
    route = expect_list(doc.get("routePlan") or [], "routePlan")
    labels = tuple(str(expect_dict(step, "route step")["swapInfo"]["label"]) for step in route)
    if "totalTime" in doc:
        provider_time = opt_decimal(doc.get("totalTime"))                       # v2: milliseconds
    else:
        seconds = opt_decimal(doc.get("timeTaken"))                              # v1: seconds
        provider_time = None if seconds is None else seconds * 1000
    transaction = doc.get("transaction")
    return QuoteObservation(
        source=source,
        input_mint=doc["inputMint"],
        output_mint=doc["outputMint"],
        in_amount_raw=strict_int(doc["inAmount"]),
        out_amount_raw=strict_int(doc["outAmount"]),
        min_out_raw=opt_int(doc.get("otherAmountThreshold")),
        swap_mode=str(doc.get("swapMode", "ExactIn")),
        slippage_bps=opt_int(doc.get("slippageBps")),
        price_impact_pct=_price_impact_pct(doc),
        router=opt_str(doc.get("router")),
        route_labels=labels,
        fee_bps=opt_int(doc.get("feeBps")),
        fee_mint=opt_str(doc.get("feeMint")),
        signature_fee_lamports=opt_int(doc.get("signatureFeeLamports")),
        prioritization_fee_lamports=opt_int(doc.get("prioritizationFeeLamports")),
        rent_fee_lamports=opt_int(doc.get("rentFeeLamports")),
        gasless=opt_bool(doc.get("gasless")),
        transaction_included=isinstance(transaction, str) and len(transaction) > 0,
        request_id=opt_str(doc.get("requestId")),
        context_slot=opt_int(doc.get("contextSlot")),
        taker=opt_str(doc.get("taker")),
        provider_time_ms=provider_time,
        error_code=None if doc.get("errorCode") is None else str(doc.get("errorCode")),
        error_message=opt_str(doc.get("errorMessage")),
        requested_at=requested_at,
        received_at=received_at,
        latency_ms=latency_ms,
        raw_sha256=raw_sha256,
    )


def _stats(raw: dict[str, Any]) -> TokenStatsWindow:
    return TokenStatsWindow(
        price_change_pct=opt_decimal(raw.get("priceChange")),
        holder_change_pct=opt_decimal(raw.get("holderChange")),
        liquidity_change_pct=opt_decimal(raw.get("liquidityChange")),
        volume_change_pct=opt_decimal(raw.get("volumeChange")),
        buy_volume_usd=opt_decimal(raw.get("buyVolume")),
        sell_volume_usd=opt_decimal(raw.get("sellVolume")),
        buy_organic_volume_usd=opt_decimal(raw.get("buyOrganicVolume")),
        sell_organic_volume_usd=opt_decimal(raw.get("sellOrganicVolume")),
        num_buys=opt_int(raw.get("numBuys")),
        num_sells=opt_int(raw.get("numSells")),
        num_traders=opt_int(raw.get("numTraders")),
        num_organic_buyers=opt_int(raw.get("numOrganicBuyers")),
        num_net_buyers=opt_int(raw.get("numNetBuyers")),
    )


def _token(raw: dict[str, Any], source: str, observed_at: datetime, raw_sha256: str | None) -> TokenClaims:
    flags: list[str] = []
    audit_raw = raw.get("audit")
    audit = None
    if isinstance(audit_raw, dict):
        audit = TokenAudit(
            mint_authority_disabled=opt_bool(audit_raw.get("mintAuthorityDisabled")),
            freeze_authority_disabled=opt_bool(audit_raw.get("freezeAuthorityDisabled")),
            top_holders_pct=opt_decimal(audit_raw.get("topHoldersPercentage")),
            dev_mints=opt_int(audit_raw.get("devMints")),
            dev_migrations=opt_int(audit_raw.get("devMigrations")),
            is_sus=opt_bool(audit_raw.get("isSus")),
        )
    first_pool_raw = raw.get("firstPool")
    first_pool: dict[str, Any] = first_pool_raw if isinstance(first_pool_raw, dict) else {}
    tags = raw.get("tags") or []
    return TokenClaims(
        source=source,
        mint=raw["id"],
        symbol=untrusted(raw.get("symbol"), 32, flags, "SYMBOL_SANITIZED"),
        name=untrusted(raw.get("name"), 80, flags, "NAME_SANITIZED"),
        decimals=opt_int(raw.get("decimals")),
        token_program=opt_str(raw.get("tokenProgram")),
        dev=opt_str(raw.get("dev")),
        launchpad=untrusted(raw.get("launchpad"), 40, flags, "LAUNCHPAD_SANITIZED"),
        graduated_pool=opt_str(raw.get("graduatedPool")),
        graduated_at=opt_iso(raw.get("graduatedAt")),
        first_pool=opt_str(first_pool.get("id")),
        first_pool_created_at=opt_iso(first_pool.get("createdAt")),
        created_at=opt_iso(raw.get("createdAt")),
        holder_count=opt_int(raw.get("holderCount")),
        liquidity_usd=opt_decimal(raw.get("liquidity")),
        mcap_usd=opt_decimal(raw.get("mcap")),
        fdv_usd=opt_decimal(raw.get("fdv")),
        usd_price=opt_decimal(raw.get("usdPrice")),
        circ_supply=opt_decimal(raw.get("circSupply")),
        total_supply=opt_decimal(raw.get("totalSupply")),
        organic_score=opt_decimal(raw.get("organicScore")),
        organic_score_label=opt_str(raw.get("organicScoreLabel")),
        tags=tuple(str(t)[:40] for t in expect_list(tags, "tags")),
        stats={window: _stats(expect_dict(raw[key], key)) for key, window in _STAT_KEYS.items()
               if isinstance(raw.get(key), dict)},
        audit=audit,
        provider_updated_at=opt_iso(raw.get("updatedAt")),
        flags=tuple(flags),
        observed_at=observed_at,
        raw_sha256=raw_sha256,
    )


def parse_tokens(body: bytes, source: str, observed_at: datetime, raw_sha256: str | None = None) -> ParseResult[TokenClaims]:
    """Parse Tokens v2 ``search``, ``recent`` and category responses (all bare lists)."""
    items = expect_list(load_json(body), "tokens")
    result: ParseResult[TokenClaims] = ParseResult()
    for raw in items:
        try:
            result.items.append(_token(expect_dict(raw, "token"), source, observed_at, raw_sha256))
        except ITEM_ERRORS as exc:
            ident = raw.get("id") if isinstance(raw, dict) else "?"
            result.errors.append(f"{ident}: {type(exc).__name__}: {exc}")
    return result


class JupiterClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    async def shield(self, mint: str) -> ShieldObservation:
        """Use the measured Ultra route; never guess the unverified Swap-v2 shield endpoint."""
        resp = await self.http.request("shield", params={"mints": mint}, prefer_keyless_host=True, cache_ttl=30.0)
        try:
            warnings = expect_dict(expect_dict(load_json(resp.body), "shield").get("warnings"), "warnings")
            if mint not in warnings:
                raise SchemaError("Shield did not report the requested mint; missing is not an empty warning list")
            return ShieldObservation(mint=mint, warnings=tuple(
                ShieldWarning.model_validate({"type": expect_dict(w, "warning")["type"], "severity": w["severity"]})
                for w in expect_list(warnings[mint], "warnings for mint")),
                observed_at=resp.received_at, raw_sha256=resp.sha256)
        except ITEM_ERRORS as exc:
            self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
            raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, str(exc)) from exc

    async def order(self, input_mint: str, output_mint: str, amount_raw: int, *, taker: str | None = None,
                    slippage_bps: int | None = None) -> QuoteObservation | QuoteUnavailable:
        """A fresh quote (never cached). Without ``taker`` no transaction is built — read-only."""
        params: dict[str, Any] = {"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount_raw)}
        if taker:
            params["taker"] = taker
        if slippage_bps is not None:
            params["slippageBps"] = slippage_bps
        resp = await self.http.request("order", params=params)
        try:
            return parse_quote(resp.body, http_status=resp.status, source=f"{PROVIDER}.order_v2",
                               input_mint=input_mint, output_mint=output_mint, in_amount_raw=amount_raw,
                               requested_at=resp.requested_at, received_at=resp.received_at,
                               latency_ms=resp.latency_ms, raw_sha256=resp.sha256)
        except (SchemaError, *ITEM_ERRORS) as exc:
            self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
            raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, str(exc)) from exc

    async def _tokens(self, endpoint: str, path: str | None, params: dict[str, Any] | None,
                      source: str) -> Fetched[TokenClaims]:
        resp = await self.http.request(endpoint, path=path, params=params, prefer_keyless_host=True, cache_ttl=30.0)
        if resp.status != 200:
            raise ProviderError(PROVIDER, ProviderErrorKind.HTTP_ERROR, f"{endpoint} HTTP {resp.status}", resp.status)
        try:
            parsed = parse_tokens(resp.body, source, resp.received_at, resp.sha256)
        except SchemaError as exc:
            self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
            raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, str(exc)) from exc
        return Fetched(parsed.items, parsed.errors, resp.sha256, resp.status, resp.latency_ms)

    async def tokens_search(self, mints: list[str]) -> Fetched[TokenClaims]:
        if not mints:
            raise ValueError("at least one mint required")
        return await self._tokens("tokens_search", None, {"query": ",".join(mints)}, f"{PROVIDER}.tokens_search")

    async def tokens_recent(self) -> Fetched[TokenClaims]:
        return await self._tokens("tokens_recent", None, None, f"{PROVIDER}.tokens_recent")

    async def tokens_category(self, category: str = "toptrending", interval: str = "1h",
                              limit: int = 50) -> Fetched[TokenClaims]:
        if category not in {"toptrending", "toptraded", "toporganicscore"} or interval not in {"5m", "1h", "6h", "24h"}:
            raise ValueError("unsupported category/interval")
        return await self._tokens("tokens_category", f"/tokens/v2/{category}/{interval}", {"limit": limit},
                                  f"{PROVIDER}.{category}_{interval}")
