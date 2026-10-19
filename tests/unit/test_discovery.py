"""M2 identities, source deduplication, timestamps, filtering and transactional audit integrity."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from desk.contracts.discovery import DiscoveryReason, Outcome
from desk.contracts.market import WindowMetrics
from desk.contracts.observed import DataStatus, Observed
from desk.contracts.tokens import TokenClaims
from desk.core.base58 import b58encode
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.discovery.classify import classify
from desk.discovery.funnel import prefilter, stage_result
from desk.providers.dexscreener import parse_pairs
from desk.security.policy import SOL_MINT
from desk.storage.databases import Databases
from desk.storage.discovery_repo import DiscoveryRepository
from desk.storage.market_repo import MarketRepository
from tests.conftest import load_fixture
from tests.unit.test_security import MINT, NOW, POOL

LOADED = load_config(REPO_ROOT / "config")


def pool_snapshot():
    template = parse_pairs(load_fixture("provider/m1_dexscreener_tokens_v1_amm_pairs.json"),
                           "dexscreener.tokens_v1", NOW).items[0]

    def observed(value: str):
        return Observed[Decimal](value=Decimal(value), status=DataStatus.OK, source="dexscreener", observed_at=NOW)

    return template.model_copy(update={
        "pool_address": POOL, "base_mint": MINT, "quote_mint": SOL_MINT, "venue": "pumpswap",
        "pool_created_at": NOW - timedelta(hours=1), "observed_at": NOW,
        "liquidity_usd": observed("100000"), "fdv_usd": observed("1000000"),
        "windows": {"h1": WindowMetrics(volume_usd=Decimal(100000), buyers=150, sellers=140)},
    })


@pytest.fixture
def dbs(tmp_path: Path):
    databases = Databases.open(DeskPaths(home=tmp_path / "data", config_dir=REPO_ROOT / "config",
                                        secrets_file=tmp_path / "missing.env"))
    yield databases
    databases.close()


def test_one_mint_three_pools_and_three_sources_preserve_identity(dbs: Databases) -> None:
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    market = MarketRepository(dbs.market)
    snap = pool_snapshot()
    market.save_pool_snapshots([snap])
    ids = [repo.observe(MINT, POOL, source, NOW) for source in ("geckoterminal", "dexscreener", "jupiter")]
    assert len(set(ids)) == 1
    assert dbs.market.execute("SELECT COUNT(*) FROM discovery_observations").fetchone()[0] == 3
    for index in (7, 8):
        another = snap.model_copy(update={"pool_address": b58encode(bytes([index]) * 32)})
        market.save_pool_snapshots([another])
        repo.observe_snapshot(another)
    assert dbs.market.execute("SELECT COUNT(*) FROM tokens WHERE mint = ?", (MINT,)).fetchone()[0] == 1
    assert dbs.market.execute("SELECT COUNT(*) FROM pools WHERE base_mint = ?", (MINT,)).fetchone()[0] == 3
    assert [r[0] for r in dbs.market.execute("SELECT reason FROM discoveries ORDER BY rowid")] == [
        "NEW_TOKEN", "NEW_POOL", "NEW_POOL"]


def test_creation_timestamps_are_separate_and_conflicting_source_claims_retained(dbs: Databases) -> None:
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    token_time, pool_time = NOW - timedelta(days=2), NOW - timedelta(hours=1)
    candidate = repo.observe(MINT, POOL, "one", NOW, token_created_at=token_time, pool_created_at=pool_time)
    repo.observe(MINT, POOL, "two", NOW + timedelta(seconds=1), token_created_at=NOW)
    row = dbs.market.execute("SELECT * FROM discoveries WHERE id = ?", (candidate,)).fetchone()
    assert row["token_created_at"] != row["pool_created_at"] != row["first_observed_at"]
    assert datetime.fromisoformat(row["token_created_at"]) == token_time
    assert dbs.market.execute("SELECT COUNT(DISTINCT token_created_at) FROM discovery_observations").fetchone()[0] == 2


@pytest.mark.parametrize("kwargs,reason", [
    ({"known_token": False, "known_pool": False}, DiscoveryReason.NEW_TOKEN),
    ({"known_token": True, "known_pool": False}, DiscoveryReason.NEW_POOL),
    ({"known_token": True, "known_pool": False, "migration_evidence": True}, DiscoveryReason.MIGRATION),
    ({"known_token": True, "known_pool": True, "renewed": True}, DiscoveryReason.RENEWED_ACTIVITY),
    ({"known_token": True, "known_pool": True, "volume_ratio": Decimal(3)}, DiscoveryReason.VOLUME_EXPANSION),
    ({"known_token": True, "known_pool": True, "acceleration_ratio": Decimal(2)}, DiscoveryReason.ACCELERATION),
])
def test_six_discovery_reasons(kwargs: dict, reason: DiscoveryReason) -> None:
    assert classify(**kwargs, expansion_threshold=LOADED.settings.discovery.volume_expansion_ratio,
                    acceleration_threshold=LOADED.settings.discovery.acceleration_ratio) is reason


def test_normal_refresh_does_not_invent_renewed_activity() -> None:
    assert classify(known_token=True, known_pool=True) is None


def test_existing_pool_cannot_change_token_identity(dbs: Databases) -> None:
    from desk.storage.db import StorageError

    market = MarketRepository(dbs.market)
    market.save_pool_snapshots([pool_snapshot()])
    with pytest.raises(StorageError, match="identity conflict"):
        market.save_pool_snapshots([pool_snapshot().model_copy(update={"base_mint": b58encode(bytes([7]) * 32)})])
    assert dbs.market.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1


def test_resumable_backfill_does_not_duplicate_source_observations(dbs: Databases) -> None:
    market = MarketRepository(dbs.market)
    market.save_pool_snapshots([pool_snapshot()])
    market.save_token_claims([TokenClaims(source="jupiter", mint=MINT, observed_at=NOW,
                                         created_at=NOW - timedelta(days=1))])
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    assert repo.sync() == 2
    assert repo.sync() == 0
    restarted = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    assert restarted.sync() == 0
    assert dbs.market.execute("SELECT COUNT(*) FROM discovery_observations").fetchone()[0] == 2
    restored = repo.latest_snapshot(POOL, NOW)
    assert restored is not None and restored.liquidity_usd.value == 100000


def test_bonding_curve_to_pumpswap_is_migration(dbs: Databases) -> None:
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    market = MarketRepository(dbs.market)
    curve = pool_snapshot().model_copy(update={"venue": "pumpfun_bonding_curve"})
    market.save_pool_snapshots([curve])
    repo.observe_snapshot(curve)
    migrated = curve.model_copy(update={"pool_address": b58encode(bytes([7]) * 32), "venue": "pumpswap"})
    market.save_pool_snapshots([migrated])
    candidate = repo.observe_snapshot(migrated)
    assert dbs.market.execute("SELECT reason FROM discoveries WHERE id = ?", (candidate,)).fetchone()[0] == "MIGRATION"


def test_stage_evidence_and_candidate_transition_are_atomic_and_append_only(dbs: Databases) -> None:
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    candidate = repo.observe(MINT, POOL, "one", NOW)
    results = prefilter(candidate, pool_snapshot(), None, NOW, LOADED)
    repo.save_results(results)
    assert repo.report()["orphans"] == 0
    last = dbs.market.execute("SELECT * FROM stage_results ORDER BY id DESC LIMIT 1").fetchone()
    assert last["config_hash"] == LOADED.snapshot_hash
    assert last["observed_values_json"] and last["thresholds_json"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.market.execute("DELETE FROM stage_results")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.market.execute("UPDATE stage_results SET rule_id = 'changed'")


def test_audit_failure_rolls_back_candidate_transition(dbs: Databases) -> None:
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    candidate = repo.observe(MINT, POOL, "one", NOW)
    dbs.market.execute("CREATE TRIGGER test_fail BEFORE INSERT ON stage_results "
                       "WHEN NEW.stage = 2 BEGIN SELECT RAISE(ABORT, 'injected write failure'); END;")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repo.save_results(prefilter(candidate, pool_snapshot(), None, NOW, LOADED))
    assert dbs.market.execute("SELECT state FROM discoveries").fetchone()[0] == "PENDING"
    assert dbs.market.execute("SELECT COUNT(*) FROM stage_results").fetchone()[0] == 0


def test_trade_counts_are_not_used_as_distinct_wallet_counts() -> None:
    snap = pool_snapshot().model_copy(update={"windows": {"h1": WindowMetrics(volume_usd=Decimal(100000),
                                                                               buys=10000, sells=10000)}})
    results = prefilter("c", snap, None, NOW, LOADED)
    assert results[-1].rule_id == "DISTINCT_TRADERS_H1"
    assert results[-1].outcome is Outcome.DEFER


def test_buyers_and_sellers_are_not_added_to_fake_a_distinct_count() -> None:
    snap = pool_snapshot().model_copy(update={"windows": {"h1": WindowMetrics(volume_usd=Decimal(100000),
                                                                               buyers=60, sellers=60)}})
    assert prefilter("c", snap, None, NOW, LOADED)[-1].outcome is Outcome.DEFER


def test_missing_liquidity_and_stale_snapshot_never_qualify() -> None:
    snap = pool_snapshot()
    missing = Observed[Decimal](value=None, status=DataStatus.MISSING, source="dexscreener", observed_at=NOW)
    assert prefilter("c", snap.model_copy(update={"liquidity_usd": missing}), None, NOW, LOADED)[-1].outcome is Outcome.REJECT
    assert prefilter("c", snap, None, NOW + timedelta(seconds=121), LOADED)[-1].outcome is Outcome.DEFER


def test_unknown_candidate_cannot_receive_results(dbs: Databases) -> None:
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    with pytest.raises(ValueError, match="unknown candidate"):
        repo.save_results([stage_result("missing", 1, Outcome.REJECT, "NO", {}, {}, NOW, LOADED)])


def test_early_stage_pass_is_pending_and_cannot_become_security_eligible(dbs: Databases) -> None:
    repo = DiscoveryRepository(dbs.market, LOADED.settings.discovery)
    candidate = repo.observe(MINT, POOL, "one", NOW)
    repo.save_results(prefilter(candidate, pool_snapshot(), None, NOW, LOADED))
    assert dbs.market.execute("SELECT state FROM discoveries").fetchone()[0] == "PENDING"
    with pytest.raises(ValueError, match="passing assessment"):
        repo.save_results([stage_result(candidate, 4, Outcome.PASS, "FAKE_SCORE_OVERRIDE", {"score": 1}, {}, NOW, LOADED)])
