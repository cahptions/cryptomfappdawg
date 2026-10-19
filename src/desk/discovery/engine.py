"""M2 evaluator: consumes persisted collection, never submits a transaction or opens a position."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from pydantic import ValidationError

from desk.contracts.discovery import HolderMetrics, Outcome, PoolVerification, ShieldObservation
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.core.clock import to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.logging import RedactionFilter
from desk.core.units import raw_to_ui, sol_to_lamports
from desk.discovery.funnel import prefilter, source_conflict, stage_result
from desk.providers.factory import ProviderSuite
from desk.security.onchain import verify_holders, verify_pool
from desk.security.policy import SOL_MINT, USDC_MINT, assess, mint_rejections
from desk.storage.databases import Databases
from desk.storage.discovery_repo import DiscoveryRepository
from desk.storage.market_repo import MarketRepository

log = logging.getLogger(__name__)


class DiscoveryEngine:
    def __init__(self, suite: ProviderSuite, dbs: Databases, loaded: LoadedConfig) -> None:
        self.suite, self.dbs, self.loaded = suite, dbs, loaded
        self.settings = loaded.settings.discovery
        self.repo = DiscoveryRepository(dbs.market, self.settings)
        self.market = MarketRepository(dbs.market)
        self.redactor = RedactionFilter(suite.rpc.http.secrets.all_secret_values())
        # PAPER hook: called right after a security PASS is stored, while its exact-ticket quotes are fresh
        self.on_pass: Callable[[str], Awaitable[object]] | None = None

    async def evaluate_cycle(self) -> int:
        self.repo.sync()
        now = utc_now()
        before = to_iso(now - timedelta(seconds=self.settings.reassess_after_seconds))
        candidates = self.dbs.market.execute(
            "SELECT id, mint, pool_address FROM discoveries WHERE last_evaluated_at IS NULL OR last_evaluated_at <= ? "
            "OR last_rule_id = 'BUDGET_QUEUE' ORDER BY last_evaluated_at, first_observed_at LIMIT ?",
            (before, self.loaded.settings.collector.max_tracked_tokens),
        ).fetchall()
        assessed = 0
        for candidate in candidates:
            snap = self.repo.latest_snapshot(candidate["pool_address"], now)
            claim = self.repo.latest_claim(candidate["mint"], now)
            results = prefilter(candidate["id"], snap, claim, now, self.loaded)
            refs = tuple(r for r in (None if snap is None else snap.raw_sha256,
                                      None if claim is None else claim.raw_sha256) if r)
            if results[-1].outcome is not Outcome.PASS or snap is None:
                self.repo.save_results(results, raw_refs=refs)
                continue
            if assessed >= self.settings.max_assessments_per_cycle:
                # Persist explicit pending evidence; never silently drop candidates that clear cheap checks.
                results.append(stage_result(candidate["id"], 3, Outcome.DEFER, "BUDGET_QUEUE", {},
                                            {"per_cycle": self.settings.max_assessments_per_cycle}, now, self.loaded))
                self.repo.save_results(results, raw_refs=refs)
                continue
            assessed += 1
            try:
                mint = await self.suite.rpc.get_mint_account(candidate["mint"])
                if mint is not None:
                    self.market.save_mint_account(mint)
                    refs += tuple(r for r in (mint.raw_sha256,) if r)
                holders: HolderMetrics | None = None
                pool: PoolVerification | None = None
                buy: QuoteObservation | QuoteUnavailable | None = None
                sell: QuoteObservation | QuoteUnavailable | None = None
                shield: ShieldObservation | None = None
                if mint is not None and not mint_rejections(mint):
                    spot = await self.suite.jupiter.order(SOL_MINT, USDC_MINT, sol_to_lamports(self.settings.ticket_sol))
                    self.market.save_quote(spot)
                    refs += tuple(r for r in (spot.raw_sha256,) if r)
                    if not isinstance(spot, QuoteObservation) or spot.in_amount_raw <= 0 or spot.out_amount_raw <= 0:
                        raise ValueError("SOL/USD reference quote unavailable")
                    if (spot.input_mint != SOL_MINT or spot.output_mint != USDC_MINT
                            or spot.in_amount_raw != sol_to_lamports(self.settings.ticket_sol)):
                        raise ValueError("SOL/USD reference quote identity mismatch")
                    sol_usd = raw_to_ui(spot.out_amount_raw, 6) / raw_to_ui(spot.in_amount_raw, 9)
                    pool = await verify_pool(self.suite, snap, mint, sol_usd)
                    if not 0 <= (utc_now() - spot.received_at).total_seconds() <= self.settings.max_quote_age_seconds:
                        raise ValueError("SOL/USD reference quote expired during verification")
                    refs += pool.raw_refs
                    alternate = self.repo.latest_snapshot(snap.pool_address, utc_now(), "geckoterminal")
                    alternate_fresh = (alternate is not None and
                                       0 <= (utc_now() - alternate.observed_at).total_seconds()
                                       <= self.settings.max_snapshot_age_seconds)
                    if source_conflict(snap.liquidity_usd.value, pool.liquidity_usd,
                                       self.settings.max_source_conflict_fraction):
                        raise ValueError("CONFLICT:LIQUIDITY_ON_CHAIN")
                    if source_conflict(snap.price_usd.value, pool.pool_price_usd,
                                       self.settings.max_source_conflict_fraction):
                        raise ValueError("CONFLICT:PRICE_ON_CHAIN")
                    if (alternate_fresh and alternate is not None
                            and (source_conflict(snap.liquidity_usd.value, alternate.liquidity_usd.value,
                                                 self.settings.max_source_conflict_fraction)
                                 or source_conflict(snap.price_usd.value, alternate.price_usd.value,
                                                    self.settings.max_source_conflict_fraction))):
                        raise ValueError("CONFLICT:MARKET_PROVIDERS")
                    # Known exclusions end expensive work immediately; no holder or route requests are spent
                    # on a pool that cannot pass the current LP/feature policy.
                    if pool.canonical and pool.lp_burned and not pool.unsupported_features:
                        holders, holder_refs = await verify_holders(self.suite, mint, pool)
                        refs += holder_refs
                        shield = await self.suite.jupiter.shield(mint.mint)
                        refs += tuple(r for r in (shield.raw_sha256,) if r)
                        buy = await self.suite.jupiter.order(SOL_MINT, mint.mint, sol_to_lamports(self.settings.ticket_sol))
                        self.market.save_quote(buy)
                        if isinstance(buy, QuoteObservation) and buy.out_amount_raw > 0:
                            sell = await self.suite.jupiter.order(mint.mint, SOL_MINT, buy.out_amount_raw)
                            self.market.save_quote(sell)
                        refs += tuple(r for r in (buy.raw_sha256, None if sell is None else sell.raw_sha256) if r)
                assessed_at = utc_now()
                final_checks = prefilter(candidate["id"], snap, claim, assessed_at, self.loaded)
                if final_checks[-1].outcome is not Outcome.PASS:
                    self.repo.save_results(results + final_checks, raw_refs=refs)
                    continue
                assessment = assess(mint_address=candidate["mint"], mint=mint, holders=holders, pool=pool,
                                    claim=claim, buy=buy, sell=sell, now=assessed_at, settings=self.settings, shield=shield)
                results.append(stage_result(candidate["id"], 3, Outcome.PASS, "DOSSIER_COLLECTED",
                                            {"mint_available": mint is not None, "holders_available": holders is not None,
                                             "pool_available": pool is not None}, {}, assessed_at, self.loaded))
                for reason in assessment.reasons or ("SECURITY_PASS",):
                    results.append(stage_result(candidate["id"], 4, assessment.outcome, reason,
                                                assessment.model_dump(mode="json"),
                                                self.settings.model_dump(mode="json"), assessed_at, self.loaded))
                self.repo.save_results(results, assessment, refs)
                if self.on_pass is not None and assessment.outcome is Outcome.PASS:
                    try:
                        await self.on_pass(candidate["id"])
                    except Exception:
                        # a PAPER failure never changes or interrupts discovery evidence
                        log.exception("paper entry hook failed", extra={"candidate_id": candidate["id"]})
            except (DeskError, ValueError, KeyError, TypeError, ValidationError) as exc:
                # Expected unavailable/changed evidence is pending, never an eligibility pass.
                decided = utc_now()
                detail = self.redactor.redact(str(exc))[:300]
                rule = detail if detail.startswith("CONFLICT:") else "INSUFFICIENT_DATA:SECURITY_DOSSIER"
                outcome = Outcome.REJECT if rule.startswith("CONFLICT:") else Outcome.DEFER
                results.append(stage_result(candidate["id"], 3, outcome, rule,
                                            {"error_type": type(exc).__name__, "detail": detail}, {}, decided, self.loaded))
                self.repo.save_results(results, raw_refs=refs)
                log.warning("candidate safety evidence incomplete", extra={"candidate_id": candidate["id"],
                                                                            "error_type": type(exc).__name__})
        return assessed
