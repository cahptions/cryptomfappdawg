"""M1-12 (cache + single-flight), M1-15 (health state machine), M1-16 (registry), M1-20 (no paid/LLM deps)."""

from __future__ import annotations

import asyncio
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from desk.core.config import HealthSection
from desk.core.errors import ConfigError
from desk.providers.cache import SingleFlightCache
from desk.providers.factory import ADAPTERS
from desk.providers.health import HealthState, HealthTracker
from desk.providers.registry import load_registry
from desk.storage.db import DbKind, connect, migrate
from tests.conftest import REPO

# ── M1-12 ────────────────────────────────────────────────────────────────────────────────────────


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def test_single_flight_concurrent_identical_requests_make_one_call() -> None:
    clock = Clock()
    cache = SingleFlightCache(clock=clock)
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "value"

    results = await asyncio.gather(*(cache.get_or_fetch("k", 30, fetch) for _ in range(10)))
    assert results == ["value"] * 10 and calls == 1

    assert await cache.get_or_fetch("k", 30, fetch) == "value" and calls == 1   # within TTL: cached
    clock.t = 31
    assert await cache.get_or_fetch("k", 30, fetch) == "value" and calls == 2   # after TTL: refetched


async def test_failures_are_not_cached_and_reach_all_waiters() -> None:
    cache = SingleFlightCache(clock=Clock())
    calls = 0

    async def boom() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise RuntimeError("upstream down")

    outcomes = await asyncio.gather(*(cache.get_or_fetch("k", 30, boom) for _ in range(5)), return_exceptions=True)
    assert all(isinstance(o, RuntimeError) for o in outcomes) and calls == 1
    with pytest.raises(RuntimeError):
        await cache.get_or_fetch("k", 30, boom)
    assert calls == 2


async def test_cache_is_bounded() -> None:
    cache = SingleFlightCache(clock=Clock(), max_entries=3)

    async def value() -> int:
        return 1

    for i in range(10):
        await cache.get_or_fetch(f"k{i}", 30, value)
    assert len(cache._values) == 3


# ── M1-15 ────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tracker(tmp_path: Path) -> HealthTracker:
    conn = connect(tmp_path / "runtime.db", DbKind.RUNTIME)
    migrate(conn, DbKind.RUNTIME)
    times = iter(datetime(2026, 10, 18, tzinfo=UTC) + timedelta(seconds=i) for i in range(10_000))
    return HealthTracker(conn, HealthSection(degraded_after_failures=3, down_after_failures=10,
                                             recover_after_successes=3), clock=lambda: next(times))


def test_health_degrades_goes_down_and_recovers(tracker: HealthTracker) -> None:
    states = [tracker.record_failure("gt", "rate_limited", "429") for _ in range(10)]
    assert states[1] is HealthState.OK and states[2] is HealthState.DEGRADED and states[9] is HealthState.DOWN
    assert tracker.record_success("gt") is HealthState.DOWN        # one success is not recovery
    assert tracker.record_success("gt") is HealthState.DOWN
    assert tracker.record_success("gt") is HealthState.OK
    transitions = [(r["from_state"], r["to_state"]) for r in tracker.transitions("gt")]
    assert transitions == [("ok", "degraded"), ("degraded", "down"), ("down", "ok")]
    assert all(r["at"] for r in tracker.transitions("gt"))


def test_intermittent_failures_do_not_degrade(tracker: HealthTracker) -> None:
    for _ in range(20):
        tracker.record_failure("ds", "timeout", "t")
        tracker.record_failure("ds", "timeout", "t")
        tracker.record_success("ds")
    assert tracker.get("ds").state is HealthState.OK


def test_health_is_persisted(tracker: HealthTracker) -> None:
    for _ in range(3):
        tracker.record_failure("jup", "http_error", "HTTP 503")
    fresh = HealthTracker(tracker.conn, tracker.settings)
    record = fresh.get("jup")
    assert record.state is HealthState.DEGRADED and record.last_error == "http_error: HTTP 503"


# ── M1-16 ────────────────────────────────────────────────────────────────────────────────────────


def test_registry_validates_and_every_adapter_has_an_entry() -> None:
    registry = load_registry(REPO / "config", REPO)
    assert set(ADAPTERS) <= set(registry.providers)
    for name, spec in registry.providers.items():
        assert spec.documented_limit and spec.failure_handling and spec.fallback and spec.allowed_usage, name
        assert spec.freshness_seconds >= 0 and spec.evidence, name
        for endpoint in spec.endpoints.values():
            assert endpoint.verified_fields or endpoint.status == "to_verify"


def test_registry_rejects_unknown_bucket(tmp_path: Path) -> None:
    text = (REPO / "config" / "providers.toml").read_text().replace('bucket = "promotions"', 'bucket = "nope"')
    (tmp_path / "providers.toml").write_text(text)
    with pytest.raises(ConfigError, match="unknown bucket"):
        load_registry(tmp_path)


def test_registry_rejects_missing_evidence(tmp_path: Path) -> None:
    text = (REPO / "config" / "providers.toml").read_text().replace(
        "research/evidence/2026-10-18/dexscreener_tokens_v1_10mints.json", "research/evidence/nope.json")
    (tmp_path / "providers.toml").write_text(text)
    with pytest.raises(ConfigError, match="missing evidence"):
        load_registry(tmp_path, REPO)


# ── M1-20 ────────────────────────────────────────────────────────────────────────────────────────

LLM_PACKAGES = {"anthropic", "openai", "langchain", "litellm", "google-generativeai", "cohere", "mistralai", "ollama"}
LLM_HOSTS = re.compile(r"api\.anthropic\.com|api\.openai\.com|generativelanguage\.googleapis\.com|api\.mistral\.ai")


def test_no_llm_or_paid_dependencies() -> None:
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    names = {pkg["name"] for pkg in lock["package"]}
    assert not names & LLM_PACKAGES, names & LLM_PACKAGES
    for source in (REPO / "src").rglob("*.py"):
        assert not LLM_HOSTS.search(source.read_text()), source
    registry = load_registry(REPO / "config")
    assert all(spec.cost_usd_month == 0 for spec in registry.providers.values())


async def test_expired_entries_are_dropped_not_kept_until_capacity() -> None:
    clock = Clock()
    cache = SingleFlightCache(clock=clock, max_entries=2048)

    async def value() -> bytes:
        return b"x" * 30_000

    for i in range(100):
        clock.t = i * 61.0            # every entry has expired by the time the next is stored
        await cache.get_or_fetch(f"k{i}", 60, value)
    assert len(cache) == 1
