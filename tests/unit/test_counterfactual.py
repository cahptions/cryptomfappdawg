"""M6-05: rejected candidates receive +5m/15m/1h/6h/24h outcomes; dead tokens remain; a 10% sample is quote-checked."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desk.contracts.discovery import DiscoveryReason, Outcome, StageResult
from desk.contracts.market import PoolSnapshot
from desk.contracts.quotes import QuoteObservation
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.discovery.counterfactual import HORIZONS, CounterfactualTracker, quote_sampled, summary
from desk.providers.parsing import Fetched, observed_decimal
from desk.storage.databases import Databases
from desk.storage.discovery_repo import DiscoveryRepository
from desk.storage.market_repo import MarketRepository
from tests.unit.test_market_context import address, ds_snapshot

T0 = datetime(2026, 11, 2, 12, tzinfo=UTC)
SOURCE = "dexscreener.tokens_v1"


def snap(pool: str, mint: str, at: datetime, price: str, liquidity: str = "50000") -> PoolSnapshot:
    return ds_snapshot(pool, mint, at, liquidity).model_copy(update={"price_usd": observed_decimal(price, SOURCE, at)})


class FakeDex:
    def __init__(self) -> None:
        self.answers: dict[str, PoolSnapshot] = {}
        self.calls: list[list[str]] = []

    async def tokens(self, mints: list[str]) -> Fetched[PoolSnapshot]:
        self.calls.append(mints)
        return Fetched([self.answers[m] for m in mints if m in self.answers], [], "raw", 200, 1.0)


class FakeJupiter:
    def __init__(self, sellable: bool) -> None:
        self.sellable = sellable

    async def order(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation:
        out = amount * 1000 if output_mint != "So11111111111111111111111111111111111111112" else (
            amount // 1000 * 97 // 100 if self.sellable else 0)
        return QuoteObservation(source="fake", input_mint=input_mint, output_mint=output_mint, in_amount_raw=amount,
                                out_amount_raw=out, swap_mode="ExactIn", requested_at=T0, received_at=T0,
                                latency_ms=Decimal(1))


@pytest.fixture
def dbs(tmp_path: Path):
    databases = Databases.open(DeskPaths(home=tmp_path / "home", config_dir=REPO_ROOT / "config",
                                         secrets_file=tmp_path / "s.env"))
    yield databases
    databases.close()


def reject(dbs: Databases, *, sampled: bool | None = None, outcome: Outcome = Outcome.REJECT) -> tuple[str, str, str]:
    discovery = DiscoveryRepository(dbs.market, load_config(REPO_ROOT / "config").settings.discovery)
    while True:
        pool, mint = address(), address()
        MarketRepository(dbs.market).save_pool_snapshots([snap(pool, mint, T0 - timedelta(seconds=30), "1.0")])
        candidate = discovery.observe(mint, pool, "fixture", T0 - timedelta(minutes=1), reason=DiscoveryReason.NEW_POOL)
        if sampled is None or quote_sampled(candidate) == sampled:
            break
    discovery.save_results([StageResult(candidate_id=candidate, stage=1, outcome=outcome, rule_id="MIN_LIQUIDITY",
                                        observed_values={}, thresholds={}, config_hash="x", decided_at=T0)])
    return candidate, pool, mint


def tracker(dbs: Databases, dex: FakeDex, sellable: bool = True) -> CounterfactualTracker:
    suite: Any = SimpleNamespace(dexscreener=dex, jupiter=FakeJupiter(sellable))
    return CounterfactualTracker(suite, dbs.market, Decimal("0.1"))


def outcomes(dbs: Databases, candidate: str) -> dict[int, sqlite3.Row]:
    return {r["horizon_seconds"]: r for r in dbs.market.execute(
        "SELECT * FROM counterfactual_outcomes WHERE candidate_id = ?", (candidate,))}


def test_rejected_candidate_gets_outcomes_from_observations_near_each_horizon(dbs: Databases) -> None:
    candidate, pool, mint = reject(dbs, sampled=False)
    repo = MarketRepository(dbs.market)
    repo.save_pool_snapshots([snap(pool, mint, T0 + timedelta(seconds=310), "1.5"),
                              snap(pool, mint, T0 + timedelta(seconds=900 + 100), "0.5", "0")])
    stats = asyncio.run(tracker(dbs, FakeDex()).run_once(T0 + timedelta(seconds=1100)))
    rows = outcomes(dbs, candidate)
    assert rows[300]["status"] == "OK" and Decimal(rows[300]["return"]) == Decimal("0.5")
    assert rows[900]["status"] == "ZERO_LIQUIDITY" and Decimal(rows[900]["return"]) == Decimal("-0.5")
    assert 3600 not in rows and stats["recorded"] == 2           # later horizons are not due yet


def test_unobserved_pools_are_refetched_when_due(dbs: Databases) -> None:
    candidate, pool, mint = reject(dbs, sampled=False)
    dex = FakeDex()
    now = T0 + timedelta(seconds=300)
    dex.answers[mint] = snap(pool, mint, now, "2.0")
    asyncio.run(tracker(dbs, dex).run_once(now))
    assert dex.calls == [[mint]]
    assert Decimal(outcomes(dbs, candidate)[300]["return"]) == Decimal("1.0")


def test_dead_tokens_stay_in_the_dataset_as_no_data(dbs: Databases) -> None:
    candidate, _, _ = reject(dbs, sampled=False)
    later = T0 + timedelta(seconds=HORIZONS[-1] + 3600)
    asyncio.run(tracker(dbs, FakeDex()).run_once(later))          # the pool was never seen again
    rows = outcomes(dbs, candidate)
    assert set(rows) == set(HORIZONS) and {r["status"] for r in rows.values()} == {"NO_DATA"}
    report = summary(dbs.market)
    assert report["candidates"] == 1 and report["by_horizon"]["86400"]["no_data"] == 1


def test_waits_for_late_observations_inside_the_grace_period(dbs: Databases) -> None:
    candidate, _, _ = reject(dbs, sampled=False)
    asyncio.run(tracker(dbs, FakeDex()).run_once(T0 + timedelta(seconds=300 + 400)))
    assert 300 not in outcomes(dbs, candidate)                     # not yet NO_DATA: still within grace


def test_ten_percent_sample_gets_an_executable_quote_check_at_one_hour(dbs: Databases) -> None:
    sampled, pool, mint = reject(dbs, sampled=True)
    MarketRepository(dbs.market).save_pool_snapshots([snap(pool, mint, T0 + timedelta(seconds=3600), "1.2")])
    asyncio.run(tracker(dbs, FakeDex(), sellable=False).run_once(T0 + timedelta(seconds=3650)))
    row = outcomes(dbs, sampled)[3600]
    assert '"executable": false' in row["quote_json"] and "NO_SELL_ROUTE" in row["quote_json"]
    share = sum(quote_sampled(f"cand-{i}") for i in range(5000)) / 5000
    assert 0.08 < share < 0.12


def test_only_rejections_are_tracked_once_and_outcomes_are_append_only(dbs: Databases) -> None:
    candidate, _, _ = reject(dbs, sampled=False)
    reject(dbs, outcome=Outcome.DEFER)
    track = tracker(dbs, FakeDex())
    assert track.register() == 1 and track.register() == 0
    asyncio.run(track.run_once(T0 + timedelta(days=2)))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.market.execute("DELETE FROM counterfactual_outcomes")
    assert dbs.market.execute("SELECT COUNT(*) FROM counterfactuals").fetchone()[0] == 1
    assert candidate
