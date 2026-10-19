"""``desk collect``: stable read-only data collection (M1). No decisions, no trades, no wallet.

Sources and cadence come from ``[collector]`` in config/default.toml. The tracked set focuses on the
"survivor" universe from PLAN.md §2: trending tokens and non-bonding-curve pools (e.g. migrations), refreshed
through DexScreener in batches of 30. Brand-new bonding-curve launches are recorded but not tracked.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from desk.contracts.market import PoolSnapshot
from desk.core.clock import parse_iso8601, to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import ProviderError
from desk.core.hashing import canonical_json
from desk.core.ids import new_id
from desk.ops import system
from desk.providers.factory import ProviderSuite
from desk.regime.context import INPUTS as CONTEXT_INPUTS
from desk.regime.context import (
    ContextRecord,
    hazard_followup_mints,
    hazard_rate,
    migrations_last_hour,
    save_context,
    sol_reference_metrics,
    watchlist_volume,
)
from desk.storage.databases import Databases
from desk.storage.market_repo import MarketRepository

log = logging.getLogger(__name__)


@dataclass
class TaskStats:
    scheduled: int = 0
    succeeded: int = 0
    failed: int = 0
    items: int = 0
    item_errors: int = 0
    last_error: str | None = None

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.scheduled if self.scheduled else 1.0


@dataclass
class CollectorStats:
    run_id: str
    started_at: str
    ended_at: str | None = None
    tasks: dict[str, TaskStats] = field(default_factory=dict)
    unhandled_exceptions: int = 0
    suspend_gaps: int = 0
    tracked_tokens: int = 0
    rss_mb: float | None = None
    peak_rss_mb: float | None = None
    db_counts: dict[str, int] = field(default_factory=dict)
    discovery: dict[str, object] | None = None
    elapsed_monotonic_seconds: float | None = None
    completed_requested_duration: bool = False


class TrackedSet:
    """Bounded LRU set of mints to refresh. Re-observing a mint moves it to the front."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._items: OrderedDict[str, None] = OrderedDict()

    def add(self, mint: str) -> None:
        self._items[mint] = None
        self._items.move_to_end(mint)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def batches(self, size: int = 30) -> list[list[str]]:
        mints = list(reversed(self._items))
        return [mints[i:i + size] for i in range(0, len(mints), size)]

    def __len__(self) -> int:
        return len(self._items)


class Collector:
    def __init__(self, suite: ProviderSuite, dbs: Databases, loaded: LoadedConfig, *, discovery: bool = False,
                 paper: bool = False) -> None:
        self.suite = suite
        self.dbs = dbs
        self.repo = MarketRepository(dbs.market)
        self.loaded = loaded
        self.settings = loaded.settings.collector
        self.tracked = TrackedSet(self.settings.max_tracked_tokens)
        self.stats = CollectorStats(run_id=new_id("run"), started_at=to_iso(utc_now()))
        self._stop = asyncio.Event()
        self._run_started_mono = time.monotonic()
        self._coverage_since_mono = self._run_started_mono   # continuous observation since start or last suspend
        self._last_success_mono: dict[str, float] = {}
        from desk.discovery.engine import DiscoveryEngine

        self.discovery_engine = DiscoveryEngine(suite, dbs, loaded) if discovery or paper else None
        self._last_gap_mono: float | None = None
        self.counterfactual_tracker = None
        if self.discovery_engine is not None:
            from desk.discovery.counterfactual import CounterfactualTracker

            self.counterfactual_tracker = CounterfactualTracker(suite, dbs.market, loaded.settings.discovery.ticket_sol)
        self.paper_engine = None
        if paper:
            from desk.paper.engine import PaperEngine

            self.paper_engine = PaperEngine(suite, dbs, loaded, suspended_recently=self._suspended_recently)
            assert self.discovery_engine is not None
            self.discovery_engine.on_pass = self.paper_engine.consider
        self._initial_db_bytes = sum(p.stat().st_size for p in dbs.paths.db_dir.glob("*.db*"))
        self._initial_budget = {r[0]: int(r[1]) for r in dbs.runtime.execute("SELECT bucket, allocated FROM rate_totals")}

    # ── individual collection jobs ─────────────────────────────────────────────────────────────────
    def _save_pools(self, name: str, snapshots: list[PoolSnapshot], errors: list[str], raw: str,
                    track: Callable[[PoolSnapshot], bool]) -> None:
        self.repo.save_pool_snapshots(snapshots)
        if errors:
            self.repo.record_ingest_errors(name, errors, raw)
        for snap in snapshots:
            if track(snap):
                self.tracked.add(snap.base_mint)
        stats = self.stats.tasks[name]
        stats.items += len(snapshots)
        stats.item_errors += len(errors)

    async def gt_new_pools(self) -> None:
        for page in range(1, self.settings.geckoterminal_new_pools_pages + 1):
            fetched = await self.suite.geckoterminal.new_pools(page)
            # migrations and fresh AMM pools are tracked; raw bonding-curve launches are only recorded
            self._save_pools("geckoterminal_new_pools", fetched.items, fetched.errors, fetched.raw_sha256,
                             lambda s: not s.is_bonding_curve)

    async def gt_trending(self) -> None:
        fetched = await self.suite.geckoterminal.trending_pools("1h")
        self._save_pools("geckoterminal_trending", fetched.items, fetched.errors, fetched.raw_sha256, lambda s: True)

    async def jupiter_recent(self) -> None:
        fetched = await self.suite.jupiter.tokens_recent()
        self.repo.save_token_claims(fetched.items)
        if fetched.errors:
            self.repo.record_ingest_errors("jupiter_recent", fetched.errors, fetched.raw_sha256)
        self.stats.tasks["jupiter_recent"].items += len(fetched.items)
        self.stats.tasks["jupiter_recent"].item_errors += len(fetched.errors)

    async def jupiter_trending(self) -> None:
        fetched = await self.suite.jupiter.tokens_category("toptrending", "1h", 50)
        self.repo.save_token_claims(fetched.items)
        if fetched.errors:
            self.repo.record_ingest_errors("jupiter_trending", fetched.errors, fetched.raw_sha256)
        for claim in fetched.items:
            self.tracked.add(claim.mint)
        self.stats.tasks["jupiter_trending"].items += len(fetched.items)
        self.stats.tasks["jupiter_trending"].item_errors += len(fetched.errors)

    async def dexscreener_refresh(self) -> None:
        for batch in self.tracked.batches(30):
            fetched = await self.suite.dexscreener.tokens(batch)
            self._save_pools("dexscreener_refresh", fetched.items, fetched.errors, fetched.raw_sha256,
                             lambda s: False)

    async def dexscreener_promotions(self) -> None:
        fetched = await self.suite.dexscreener.profiles()
        self.repo.save_token_claims(fetched.items)
        self.repo.record_ingest_errors("dexscreener_promotions", fetched.errors, fetched.raw_sha256)
        for claim in fetched.items:
            self.tracked.add(claim.mint)
        self.stats.tasks["dexscreener_promotions"].items += len(fetched.items)
        self.stats.tasks["dexscreener_promotions"].item_errors += len(fetched.errors)

    async def jupiter_categories(self) -> None:
        for category in ("toptraded", "toporganicscore"):
            for interval in ("5m", "1h"):
                fetched = await self.suite.jupiter.tokens_category(category, interval, 50)
                self.repo.save_token_claims(fetched.items)
                self.repo.record_ingest_errors("jupiter_categories", fetched.errors, fetched.raw_sha256)
                for claim in fetched.items:
                    self.tracked.add(claim.mint)
                self.stats.tasks["jupiter_categories"].items += len(fetched.items)
                self.stats.tasks["jupiter_categories"].item_errors += len(fetched.errors)

    async def rpc_slot(self) -> None:
        await self.suite.rpc.get_slot()
        self.stats.tasks["rpc_slot"].items += 1
        self.dbs.runtime.execute(
            "INSERT INTO heartbeats(process, pid, at) VALUES ('collector', ?, ?) "
            "ON CONFLICT(process) DO UPDATE SET pid = excluded.pid, at = excluded.at",
            (os.getpid(), to_iso(utc_now())),
        )
        if self.paper_engine is not None:
            self.dbs.runtime.execute(
                "INSERT INTO heartbeats(process, pid, at) VALUES ('paper-engine', ?, ?) "
                "ON CONFLICT(process) DO UPDATE SET pid = excluded.pid, at = excluded.at",
                (os.getpid(), to_iso(utc_now())),
            )

    async def market_context(self) -> None:
        """Record one context measurement; every unknown input is stored as NULL with its reason."""
        now, ctx = utc_now(), self.loaded.settings.context
        reasons: dict[str, str] = {}
        coverage: dict[str, int] = {}
        values: dict[str, Decimal | None] = dict.fromkeys(CONTEXT_INPUTS)
        raw: str | None = None
        failure: ProviderError | None = None
        try:
            fetched = await self.suite.geckoterminal.ohlcv(ctx.sol_reference_pool, "minute", aggregate=1, limit=70)
            raw = fetched.raw_sha256
            values["sol_return_1h"], values["sol_volatility_1h"], reason = sol_reference_metrics(fetched.items, now)
            if reason:
                reasons.update({n: reason for n in ("sol_return_1h", "sol_volatility_1h") if values[n] is None})
        except ProviderError as exc:
            failure = exc
            reasons.update(sol_return_1h="SOL_REFERENCE_UNAVAILABLE", sol_volatility_1h="SOL_REFERENCE_UNAVAILABLE")
        market = self.dbs.market
        refreshed = self._last_success_mono.get("dexscreener_refresh")
        if refreshed is None or time.monotonic() - refreshed > 150:
            reasons["watchlist_volume_usd_1h"] = "WATCHLIST_REFRESH_NOT_CURRENT"
        else:
            values["watchlist_volume_usd_1h"], coverage["watchlist_pools"], reason = watchlist_volume(market, now)
            if reason:
                reasons["watchlist_volume_usd_1h"] = reason
        if self.discovery_engine is None:
            reasons["migrations_per_hour"] = "DISCOVERY_NOT_RUNNING"
        elif time.monotonic() - self._coverage_since_mono < 3600:
            reasons["migrations_per_hour"] = "DISCOVERY_COVERAGE_UNDER_ONE_HOUR"
        else:
            values["migrations_per_hour"] = Decimal(migrations_last_hour(market, now))
        values["hazard_rate_24h"], counts, reason = hazard_rate(market, now)
        coverage.update(counts)
        if reason:
            reasons["hazard_rate_24h"] = reason
        save_context(market, ContextRecord(observed_at=now, reasons=reasons, coverage=coverage, raw_sha256=raw,
                                           **values))
        self.stats.tasks["market_context"].items += 1
        if failure is not None:
            raise failure

    async def hazard_followup(self) -> None:
        """Re-observe hazard-cohort pools at 24 h; evicted tokens would otherwise look like survivors."""
        mints = hazard_followup_mints(self.dbs.market, utc_now())
        for i in range(0, len(mints), 30):
            fetched = await self.suite.dexscreener.tokens(mints[i:i + 30])
            self._save_pools("hazard_followup", fetched.items, fetched.errors, fetched.raw_sha256, lambda s: False)

    async def counterfactuals(self) -> None:
        assert self.counterfactual_tracker is not None
        result = await self.counterfactual_tracker.run_once(utc_now())
        self.stats.tasks["counterfactuals"].items += result["recorded"] + result["no_data"]

    async def evaluate_discovery(self) -> None:
        assert self.discovery_engine is not None
        self.stats.tasks["discovery_evaluation"].items += await self.discovery_engine.evaluate_cycle()

    # ── scheduling ─────────────────────────────────────────────────────────────────────────────────
    async def _loop(self, name: str, interval: float, job: Callable[[], Awaitable[None]]) -> None:
        stats = self.stats.tasks.setdefault(name, TaskStats())
        while not self._stop.is_set():
            started_wall, started_mono = time.time(), time.monotonic()
            stats.scheduled += 1
            try:
                await job()
                stats.succeeded += 1
                self._last_success_mono[name] = time.monotonic()
            except ProviderError as exc:
                stats.failed += 1
                stats.last_error = str(exc)[:300]
                log.warning("collection job failed", extra={"job": name, "error": stats.last_error})
            except Exception as exc:
                stats.failed += 1
                self.stats.unhandled_exceptions += 1
                stats.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.exception("unhandled error in collection job", extra={"job": name})
            elapsed = time.monotonic() - started_mono
            # laptop sleep shows up as wall time jumping far ahead of monotonic time
            if (time.time() - started_wall) - elapsed > max(30.0, interval):
                self.stats.suspend_gaps += 1
                self._coverage_since_mono = self._last_gap_mono = time.monotonic()
                log.warning("suspend/resume gap detected; data before the gap is stale", extra={"job": name})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, interval - elapsed))

    def stop(self) -> None:
        self._stop.set()

    def _suspended_recently(self) -> bool:
        return self._last_gap_mono is not None and time.monotonic() - self._last_gap_mono < 300

    async def run(self, minutes: float) -> CollectorStats:
        s = self.settings
        jobs: list[tuple[str, float, Callable[[], Awaitable[None]]]] = [
            ("geckoterminal_new_pools", s.geckoterminal_new_pools_interval_seconds, self.gt_new_pools),
            ("geckoterminal_trending", s.geckoterminal_trending_interval_seconds, self.gt_trending),
            ("jupiter_recent", s.jupiter_recent_interval_seconds, self.jupiter_recent),
            ("jupiter_trending", s.jupiter_trending_interval_seconds, self.jupiter_trending),
            ("dexscreener_refresh", s.dexscreener_refresh_interval_seconds, self.dexscreener_refresh),
            ("rpc_slot", s.rpc_slot_interval_seconds, self.rpc_slot),
            ("market_context", self.loaded.settings.context.interval_seconds, self.market_context),
            ("hazard_followup", self.loaded.settings.context.hazard_followup_interval_seconds, self.hazard_followup),
        ]
        if self.discovery_engine is not None:
            jobs.append(("discovery_evaluation", self.loaded.settings.discovery.evaluation_interval_seconds,
                         self.evaluate_discovery))
            jobs.append(("dexscreener_promotions", s.dexscreener_promotions_interval_seconds,
                         self.dexscreener_promotions))
            jobs.append(("jupiter_categories", s.jupiter_trending_interval_seconds, self.jupiter_categories))
            jobs.append(("counterfactuals", 300, self.counterfactuals))
        for name, _, _ in jobs:
            self.stats.tasks[name] = TaskStats()
        if self.paper_engine is not None:
            await self.paper_engine.ensure_book()
        self.dbs.market.execute("INSERT INTO collector_runs(id, started_at, config_hash) VALUES (?, ?, ?)",
                                (self.stats.run_id, self.stats.started_at, self.loaded.snapshot_hash))
        loops = [asyncio.create_task(self._loop(n, i, j), name=n) for n, i, j in jobs]
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=minutes * 60)
        except TimeoutError:
            self.stats.completed_requested_duration = True
        finally:
            self._stop.set()
            await asyncio.gather(*loops, return_exceptions=True)
        return self.finish()

    def finish(self) -> CollectorStats:
        self.stats.ended_at = to_iso(utc_now())
        self.stats.elapsed_monotonic_seconds = time.monotonic() - self._run_started_mono
        self.stats.tracked_tokens = len(self.tracked)
        rss = system.process_rss_mb()
        if rss is not None:
            self.stats.rss_mb, self.stats.peak_rss_mb = round(rss[0], 1), round(rss[1], 1)
        self.stats.db_counts = self.repo.counts()
        if self.discovery_engine is not None:
            self.stats.discovery = self.discovery_engine.repo.report()
            self.stats.discovery["during_run"] = self.discovery_engine.repo.window_report(
                parse_iso8601(self.stats.started_at))
            self.stats.discovery["database_growth_bytes"] = (
                sum(p.stat().st_size for p in self.dbs.paths.db_dir.glob("*.db*")) - self._initial_db_bytes)
            totals = {r[0]: int(r[1]) for r in self.dbs.runtime.execute("SELECT bucket, allocated FROM rate_totals")}
            budget = {}
            for client in (self.suite.geckoterminal, self.suite.dexscreener, self.suite.jupiter, self.suite.rpc):
                for bucket in client.http.spec.buckets:
                    key = client.http.limiter_key(bucket)
                    budget[key] = {"shared_allocations_during_run": totals.get(key, 0) - self._initial_budget.get(key, 0),
                                   "configured_windows": client.http.windows(bucket)}
            self.stats.discovery["provider_budget"] = budget
        self.dbs.market.execute("UPDATE collector_runs SET ended_at = ?, stats_json = ? WHERE id = ?",
                                (self.stats.ended_at, canonical_json(asdict(self.stats)), self.stats.run_id))
        return self.stats


def render_stats(stats: CollectorStats) -> str:
    lines = [f"\nCollection run {stats.run_id}", f"  started {stats.started_at}", f"  ended   {stats.ended_at}", ""]
    lines.append(f"  {'job':<26}{'polls':>7}{'ok':>6}{'failed':>8}{'success':>9}{'items':>8}{'item err':>10}")
    for name, t in stats.tasks.items():
        lines.append(f"  {name:<26}{t.scheduled:>7}{t.succeeded:>6}{t.failed:>8}{t.success_rate:>9.0%}"
                     f"{t.items:>8}{t.item_errors:>10}")
        if t.last_error:
            lines.append(f"      last error: {t.last_error}")
    lines += ["", f"  unhandled exceptions: {stats.unhandled_exceptions}", f"  suspend gaps: {stats.suspend_gaps}",
              f"  tracked tokens: {stats.tracked_tokens}",
              f"  memory: {stats.rss_mb} MB now, {stats.peak_rss_mb} MB peak",
              f"  database rows: {stats.db_counts}"]
    if stats.discovery is not None:
        lines.append("\nDiscovery report (cumulative database evidence):\n" + canonical_json(stats.discovery))
    return "\n".join(lines)
