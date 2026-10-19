"""SHADOW persistence and replay using actual SQLite, with no wallet/provider calls."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from desk.contracts.discovery import DiscoveryReason, Outcome, SecurityAssessment, StageResult
from desk.contracts.features import FeatureSnapshot
from desk.contracts.strategy import DecisionContext, FeatureCondition
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.ops.replay import replay_run
from desk.ops.shadow import ShadowRunner, decision_context
from desk.storage.databases import Databases
from desk.storage.db import DbKind, StorageError, connect
from desk.storage.discovery_repo import DiscoveryRepository
from desk.storage.market_repo import MarketRepository
from desk.storage.shadow_repo import ShadowRepository
from desk.strategies import builtin_specs, evaluate_decision, revise_spec
from desk.strategies.evaluate import EVALUATOR_VERSION
from tests.unit.test_discovery import pool_snapshot
from tests.unit.test_strategies import NOW, _context


@pytest.fixture
def dbs(tmp_path: Path):
    dbs = Databases.open(DeskPaths(home=tmp_path / "data", config_dir=REPO_ROOT / "config",
                                  secrets_file=tmp_path / "none.env"))
    try:
        yield dbs
    finally:
        dbs.close()


def _register(dbs: Databases, context: DecisionContext) -> tuple[ShadowRepository, str, str]:
    discovery = DiscoveryRepository(dbs.market, load_config(dbs.paths.config_dir).settings.discovery)
    candidate = discovery.observe(context.features.mint, context.features.pool_address, "fixture", NOW,
                                  reason=DiscoveryReason.MIGRATION)
    repo = ShadowRepository(dbs.market)
    repo.register_specs(builtin_specs(), NOW)
    run = repo.start_run(NOW, {"seed": 7, "interval_seconds": 30, "evaluator_version": EVALUATOR_VERSION,
                              "specs": [spec.model_dump(mode="json") for spec in builtin_specs()]})
    return repo, candidate, run


def _shift(context: DecisionContext, seconds: int, momentum: str = "0.2") -> DecisionContext:
    delta = timedelta(seconds=seconds)
    snapshot = context.features
    values = {name: value.model_copy(update={
        "observed_at": None if value.observed_at is None else value.observed_at + delta})
              for name, value in snapshot.values.items()}
    values["log_return_1h"] = values["log_return_1h"].model_copy(update={"value": Decimal(momentum)})
    return context.model_copy(update={
        "features": FeatureSnapshot.model_validate({**snapshot.model_dump(), "as_of": snapshot.as_of + delta,
                                                   "values": values}),
        "security_assessed_at": snapshot.as_of + delta})


def test_record_and_replay_reproduce_signals_without_writing_paper_or_source(dbs: Databases) -> None:
    context = _context()
    repo, candidate, run = _register(dbs, context)
    ledger_before = list(dbs.paper_ledger.iterdump())
    for seconds in (0, 30, 60):
        shifted = _shift(context, seconds)
        decision = evaluate_decision(candidate, shifted, builtin_specs(), seed=7)
        assert decision.chosen != "NO_TRADE"
        record = repo.save(run, shifted, decision)
        assert repo.save(run, shifted, decision) == record
    repo.finish_run(run, NOW + timedelta(seconds=60), {"elapsed_monotonic_seconds": 60,
                                                     "completed_requested_duration": True})
    source_before = list(dbs.market.iterdump())
    result = replay_run(dbs.market, dbs.research, run)
    assert result["checked"] == result["identical"] == 3
    assert result["passed"] and result["kind"] == "REPLAY"
    assert list(dbs.market.iterdump()) == source_before
    assert list(dbs.paper_ledger.iterdump()) == ledger_before
    assert dbs.research.execute("SELECT kind FROM experiments").fetchone()[0] == "REPLAY"


def test_changed_threshold_affects_expected_inputs_and_preserves_originals(dbs: Databases) -> None:
    context = _context()
    repo, candidate, _ = _register(dbs, context)
    b0, _, b2, *_ = builtin_specs()
    specs = (b0, b2)
    run = repo.start_run(NOW, {"seed": 7, "interval_seconds": 30, "evaluator_version": EVALUATOR_VERSION,
                              "specs": [spec.model_dump(mode="json") for spec in specs]})
    for seconds, momentum in ((0, "0.1"), (30, "0.2"), (60, "0.4")):
        shifted = _shift(context, seconds, momentum)
        repo.save(run, shifted, evaluate_decision(candidate, shifted, specs, seed=7))
    revision = revise_spec(b2, version=2, entry_conditions=(FeatureCondition(
        feature="log_return_1h", operator="gte", threshold=Decimal("0.3")),))
    source_before = list(dbs.market.iterdump())
    result = replay_run(dbs.market, dbs.research, run, specs=(b0, revision))
    # Every changed spec has a new config hash; compare chosen actions separately from hashes.
    rows = list(repo.decisions(run))
    changed_choices = []
    for row in rows:
        ctx = DecisionContext.model_validate_json(row["context_json"])
        changed_choices.append(evaluate_decision(candidate, ctx, (b0, revision), seed=7).chosen)
    assert changed_choices == ["NO_TRADE", "NO_TRADE", "B2"]
    assert result["experiment"] and result["changed"] == 3
    assert result["action_changed"] == 2
    payload = json.loads(dbs.research.execute("SELECT payload_json FROM experiments WHERE id = ?",
                                             (result["experiment_id"],)).fetchone()[0])
    assert payload["replay_specs"][1]["version"] == 2 and payload["seed"] == 7
    assert list(dbs.market.iterdump()) == source_before
    assert replay_run(dbs.market, dbs.research, run)["identical"] == 3


def test_used_strategy_cannot_change_and_revision_retains_existing_parent(dbs: Databases) -> None:
    repo, _, _ = _register(dbs, _context())
    original = builtin_specs()[3]
    changed = original.model_copy(update={"hypothesis": "changed in place"})
    with pytest.raises(StorageError, match="immutable"):
        repo.register_specs((changed,), NOW)
    child = revise_spec(original, version=2, hypothesis="new research hypothesis")
    repo.register_specs((child,), NOW)
    orphan = child.model_copy(update={"version": 3, "parent_version": "not-an-existing-version"})
    with pytest.raises(StorageError, match="existing parent"):
        repo.register_specs((orphan,), NOW)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        dbs.market.execute("UPDATE strategy_versions SET content_json = '{}'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        dbs.market.execute("DELETE FROM strategy_versions")


def test_storage_rejects_forged_decisions_and_snapshots_are_append_only(dbs: Databases) -> None:
    context = _context()
    repo, candidate, run = _register(dbs, context)
    decision = evaluate_decision(candidate, context, builtin_specs(), seed=7)
    with pytest.raises(StorageError, match="does not reproduce"):
        repo.save(run, context, decision.model_copy(update={"chosen": "LIVE"}))
    repo.save(run, context, decision)
    for table in ("feature_snapshots", "shadow_decisions", "security_assessments"):
        # The security table needs at least one row for row-level triggers to execute.
        if table == "security_assessments":
            dbs.market.execute("INSERT INTO security_assessments(candidate_id, assessed_at, config_hash, payload_json) "
                               "VALUES (?, ?, 'fixture', '{}')", (candidate, NOW.isoformat()))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            dbs.market.execute(f"DELETE FROM {table}")


def test_threshold_experiments_still_detect_corrupted_original_inputs(dbs: Databases) -> None:
    context = _context()
    repo, candidate, run = _register(dbs, context)
    record = repo.save(run, context, evaluate_decision(candidate, context, builtin_specs(), seed=7))
    # Simulate out-of-band corruption on a disposable database, beyond ordinary immutable writes.
    dbs.market.execute("DROP TRIGGER shadow_decisions_no_update")
    changed = _shift(context, 0, "0.99")
    dbs.market.execute("UPDATE shadow_decisions SET context_json = ? WHERE id = ?",
                       (changed.model_dump_json(), record))
    b0, _, b2, *_ = builtin_specs()
    result = replay_run(dbs.market, dbs.research, run, specs=(b0, revise_spec(b2, version=2)))
    assert result["experiment"] and not result["passed"]
    assert result["input_integrity_failures"] > 0


def test_strategy_registration_revalidates_copies_that_bypass_pydantic(dbs: Databases) -> None:
    invalid = builtin_specs()[3].model_copy(update={"version": 2, "parent_version": None})
    with pytest.raises(ValueError, match="parent_version"):
        ShadowRepository(dbs.market).register_specs((invalid,), NOW)


@pytest.mark.parametrize("case", ["short", "zero_work", "one_decision", "large_gap", "interrupted"])
def test_six_hour_acceptance_cannot_be_satisfied_by_short_empty_or_gapped_evidence(dbs: Databases, case: str) -> None:
    context = _context()
    repo, candidate, run = _register(dbs, context)
    if case != "zero_work":
        repo.save(run, context, evaluate_decision(candidate, context, builtin_specs(), seed=7))
    if case in ("large_gap", "interrupted"):
        later = _shift(context, 21600)
        repo.save(run, later, evaluate_decision(candidate, later, builtin_specs(), seed=7))
    stats = {"elapsed_monotonic_seconds": 1 if case == "short" else 21600,
             "completed_requested_duration": case != "interrupted"}
    repo.finish_run(run, NOW + timedelta(hours=6), stats)
    result = replay_run(dbs.market, dbs.research, run, require_hours=6)
    assert not result["passed"] and result["failures"]


def test_point_in_time_context_ignores_later_mutable_state_and_future_observations(dbs: Databases) -> None:
    snapshot = pool_snapshot().model_copy(update={"observed_at": NOW})
    MarketRepository(dbs.market).save_pool_snapshots([snapshot])
    discovery = DiscoveryRepository(dbs.market, load_config(dbs.paths.config_dir).settings.discovery)
    candidate = discovery.observe(snapshot.base_mint, snapshot.pool_address, "fixture", NOW,
                                  reason=DiscoveryReason.MIGRATION)
    stage = StageResult(candidate_id=candidate, stage=4, outcome=Outcome.PASS, rule_id="SECURITY_PASS",
                        observed_values={}, thresholds={}, config_hash="fixture", decided_at=NOW)
    assessment = SecurityAssessment(mint=snapshot.base_mint, assessed_at=NOW, outcome=Outcome.PASS, reasons=())
    discovery.save_results([stage], assessment)
    before = decision_context(dbs.market, candidate, NOW)
    future = NOW + timedelta(microseconds=1)
    discovery.observe(snapshot.base_mint, snapshot.pool_address, "future", future,
                      reason=DiscoveryReason.RENEWED_ACTIVITY)
    discovery.save_results([stage.model_copy(update={"outcome": Outcome.REJECT, "decided_at": future})])
    assert decision_context(dbs.market, candidate, NOW) == before
    assert decision_context(dbs.market, candidate, future).security_outcome is Outcome.REJECT
    assert before.regime_label is None  # no fabricated thirty-day reference


def test_runner_reads_collector_records_and_streams_no_trade_without_provider_or_ledger_writes(dbs: Databases) -> None:
    snapshot = pool_snapshot()
    MarketRepository(dbs.market).save_pool_snapshots([snapshot])
    discovery = DiscoveryRepository(dbs.market, load_config(dbs.paths.config_dir).settings.discovery)
    discovery.sync()
    ledger_before = list(dbs.paper_ledger.iterdump())
    runner = ShadowRunner(dbs.market, max_candidates=1)
    repo = runner.repo
    repo.register_specs(runner.specs, snapshot.observed_at)
    run = repo.start_run(snapshot.observed_at, {"seed": 0, "interval_seconds": 30, "evaluator_version": EVALUATOR_VERSION,
                                              "specs": [spec.model_dump(mode="json") for spec in runner.specs]})
    result = runner.cycle(run, snapshot.observed_at)
    assert result == {"decisions": 1, "signals": 0, "no_trade": 1}
    row = next(repo.decisions(run))
    assert json.loads(row["result_json"])["chosen"] == "NO_TRADE"
    assert list(dbs.paper_ledger.iterdump()) == ledger_before
    assert replay_run(dbs.market, dbs.research, run)["passed"]


def test_run_inputs_and_completed_evidence_are_immutable(dbs: Databases) -> None:
    repo, _, run = _register(dbs, _context())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        dbs.market.execute("UPDATE shadow_runs SET settings_json = '{}' WHERE id = ?", (run,))
    repo.finish_run(run, NOW, {"completed_requested_duration": False})
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        dbs.market.execute("UPDATE shadow_runs SET stats_json = '{}' WHERE id = ?", (run,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.market.execute("DELETE FROM shadow_runs WHERE id = ?", (run,))


def test_collector_writes_and_shadow_share_wal_with_independent_connections(dbs: Databases) -> None:
    snapshot = pool_snapshot()
    MarketRepository(dbs.market).save_pool_snapshots([snapshot])
    DiscoveryRepository(dbs.market, load_config(dbs.paths.config_dir).settings.discovery).sync()
    runner = ShadowRunner(dbs.market, max_candidates=1)
    repo = runner.repo
    repo.register_specs(runner.specs, snapshot.observed_at)
    run = repo.start_run(snapshot.observed_at, {"seed": 0, "interval_seconds": 30, "evaluator_version": EVALUATOR_VERSION,
                                              "specs": [spec.model_dump(mode="json") for spec in runner.specs]})
    failures: list[BaseException] = []
    barrier = threading.Barrier(2)

    def collector() -> None:
        conn = connect(dbs.paths.db_dir / "market.db", DbKind.MARKET)
        try:
            barrier.wait(timeout=5)
            for i in range(40):
                MarketRepository(conn).save_pool_snapshots([
                    snapshot.model_copy(update={"observed_at": snapshot.observed_at + timedelta(seconds=i)})])
        except BaseException as exc:
            failures.append(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=collector)
    thread.start()
    barrier.wait(timeout=5)
    try:
        for i in range(10):
            assert runner.cycle(run, snapshot.observed_at + timedelta(seconds=i))["decisions"] == 1
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive() and not failures
    assert dbs.market.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 41
    assert repo.report(run)["decisions"] == 10
    assert replay_run(dbs.market, dbs.research, run)["identical"] == 10


def test_replay_refuses_runs_recorded_by_another_evaluator_version(dbs: Databases) -> None:
    repo = ShadowRepository(dbs.market)
    run = repo.start_run(NOW, {"seed": 0, "interval_seconds": 30, "max_candidates": 1, "requested_minutes": 1,
                               "specs": [spec.model_dump(mode="json") for spec in builtin_specs()]})
    result = replay_run(dbs.market, dbs.research, run)
    assert not result["passed"]
    assert any("shadow-rules-v1" in failure for failure in result["failures"])
