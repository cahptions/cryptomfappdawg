"""M6-11 champion/challenger, M6-13 local model logging/benchmarks, M6-14 zero influence (incl. import graph)."""

from __future__ import annotations

import ast
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from desk.core.config import load_config
from desk.core.errors import ConfigError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.models import logistic
from desk.models.research import FEATURES, MIN_TRAINING_TRADES, benchmark, score_decisions
from desk.paper.ledger import PaperLedger
from desk.stats import champion
from desk.storage.databases import Databases
from desk.strategies import evaluate_decision
from desk.strategies.specs import paper_specs
from tests.unit.test_strategies import _context

START = datetime(2026, 11, 1, tzinfo=UTC)
VERSIONS = {spec.strategy_id: spec.version_id for spec in paper_specs()}


@pytest.fixture
def dbs(tmp_path: Path):
    databases = Databases.open(DeskPaths(home=tmp_path / "home", config_dir=REPO_ROOT / "config",
                                         secrets_file=tmp_path / "s.env"))
    PaperLedger(databases.paper_ledger).init_book(START, Decimal(100), Decimal(100), Decimal(100_000))
    yield databases
    databases.close()


def add_trades(dbs: Databases, strategy: str, returns: np.ndarray, *, days: float = 70, offset: int = 0,
               features: dict[str, float] | None = None) -> None:
    """Closed PAPER positions with the given conservative returns, spread over ``days`` (one decision each)."""
    ledger = PaperLedger(dbs.paper_ledger)
    step = days * 86400 / len(returns)
    for i, r in enumerate(returns):
        opened = START + timedelta(seconds=(i + offset) * step)
        pid = f"{strategy}-{offset + i}"
        decision_hash = f"dh-{pid}"
        if features is not None:
            values = {name: {"value": str(features.get(name, 0.0) + float(r)), "status": "ok"} for name in FEATURES}
            ledger.event(opened, "DECISION", {"decision_hash": decision_hash, "context": {"features": {
                "values": values, "feature_set_version": "fixture"}}}, candidate_id=pid)
        ledger.open_position(position_id=pid, intent_id=f"i-{pid}", candidate_id=pid, mint=f"m-{pid}",
                             pool_address="p", strategy_id=strategy, strategy_version=VERSIONS[strategy],
                             decision_hash=decision_hash, regime_label="RISK_ON", at=opened, ticket_lamports=10 ** 8,
                             tokens_raw=10 ** 9, entry_fees_lamports=0, failed_fees_lamports=0, rent_lamports=0,
                             entry={"profiles": {}})
        exit_lamports = int(10 ** 8 * (1 + float(r)))
        profile = {"return": str(r)}
        ledger.close_position(pid, opened + timedelta(hours=1), reason="MAX_HOLD", exit_lamports=exit_lamports,
                              exit_fees_lamports=0, profiles={"observed": profile, "conservative": profile, "stress": profile})


# ── M6-11 ─────────────────────────────────────────────────────────────────────────────────────────────
def test_m6_11_challenger_needs_rules_and_typed_approval_and_history_is_kept(dbs: Databases) -> None:
    rng = np.random.default_rng(11)
    add_trades(dbs, "H3", 0.05 + rng.normal(0, 0.1, 320))
    add_trades(dbs, "B2", 0.30 + rng.normal(0, 0.1, 320))           # a baseline is never a challenger
    first = champion.record_evaluation(dbs.paper_ledger, dbs.research, START + timedelta(days=71), resamples=500)
    assert [c["strategy_id"] for c in first["challengers"]] == ["H3"]
    assert first["challengers"][0]["eligible_to_replace"]
    with pytest.raises(ValueError, match="typed confirmation"):
        champion.approve(dbs.research, VERSIONS["H3"], "yes", START)
    champion.approve(dbs.research, VERSIONS["H3"], f"PROMOTE {VERSIONS['H3']}", START + timedelta(days=71))
    assert champion.current_champion(dbs.research) == VERSIONS["H3"]

    add_trades(dbs, "H4", 0.05 + rng.normal(0, 0.1, 320), offset=1000)   # no better than the champion
    add_trades(dbs, "H6", 0.20 + rng.normal(0, 0.1, 320), offset=2000)   # clearly better
    second = champion.record_evaluation(dbs.paper_ledger, dbs.research, START + timedelta(days=72), resamples=500)
    by = {c["strategy_id"]: c for c in second["challengers"]}
    assert not by["H4"]["eligible_to_replace"] and by["H6"]["eligible_to_replace"]
    assert by["H4"]["p_better_holm"] >= by["H4"]["p_better"]            # Holm never makes a p-value smaller
    with pytest.raises(ValueError, match="does not allow"):
        champion.approve(dbs.research, VERSIONS["H4"], f"PROMOTE {VERSIONS['H4']}", START)
    champion.approve(dbs.research, VERSIONS["H6"], f"PROMOTE {VERSIONS['H6']}", START + timedelta(days=72))
    history = champion.history(dbs.research)
    assert [h["strategy_version"] for h in history] == [VERSIONS["H3"], VERSIONS["H6"]]
    assert history[1]["previous_version"] == VERSIONS["H3"]
    with pytest.raises(ValueError, match="champion changed"):         # the old comparison is stale now
        champion.approve(dbs.research, VERSIONS["H6"], f"PROMOTE {VERSIONS['H6']}", START)
    for table in ("champions", "approvals"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            dbs.research.execute(f"DELETE FROM {table}")


def test_insufficient_evidence_cannot_become_champion(dbs: Databases) -> None:
    add_trades(dbs, "H3", np.full(50, 0.2))
    result = champion.record_evaluation(dbs.paper_ledger, dbs.research, START + timedelta(days=71), resamples=300)
    assert not result["challengers"][0]["eligible_to_replace"]


# ── M6-13 ─────────────────────────────────────────────────────────────────────────────────────────────
def test_logistic_fit_recovers_the_direction_of_a_known_relation() -> None:
    rng = np.random.default_rng(13)
    x = rng.normal(size=(2000, 2))
    y = (rng.random(2000) < 1 / (1 + np.exp(-(2 * x[:, 0] - x[:, 1])))).astype(float)
    model = logistic.fit(x, y, l2=1.0)
    assert model.weights[1] > 1.0 and model.weights[2] < -0.5
    p = model.predict(np.array([[2.0, -1.0], [-2.0, 1.0]]))
    assert p[0] > 0.9 and p[1] < 0.1


def test_m6_13_scores_are_point_in_time_logged_and_benchmarked(dbs: Databases) -> None:
    rng = np.random.default_rng(14)
    add_trades(dbs, "H3", rng.normal(0.0, 0.1, 260), days=30, features={"log_return_1h": 0.0})
    result = score_decisions(dbs.paper_ledger, dbs.research, START + timedelta(days=31))
    rows = dbs.research.execute("SELECT * FROM model_scores ORDER BY decided_at").fetchall()
    assert len(rows) == 260 and result["scored"] + result["abstained"] == 260
    for row in rows:
        if row["score"] is not None:
            assert row["training_end"] < row["decided_at"]                 # trained only on earlier labels
            assert row["training_trades"] >= MIN_TRAINING_TRADES and row["model_version"].startswith("logistic")
            assert row["feature_set_version"] == "fixture" and row["inputs_hash"]
    assert rows[0]["score"] is None                                         # no model before 100 known labels
    assert result["scored"] > 0
    assert score_decisions(dbs.paper_ledger, dbs.research, START + timedelta(days=32)) == {"scored": 0, "abstained": 0}
    report = benchmark(dbs.paper_ledger, dbs.research)
    assert report["status"] == "EVALUATED" and report["mean_return_B0"] == 0.0
    assert "influence" in report
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.research.execute("DELETE FROM model_scores")


# ── M6-14 ─────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("forced", [0.0, 1.0, None])
def test_m6_14_decision_hash_is_identical_whatever_the_model_says(forced: float | None,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = evaluate_decision("c1", _context(), paper_specs()).decision_hash

    def predict(self: object, x: np.ndarray) -> np.ndarray:
        if forced is None:
            raise RuntimeError("model unavailable")
        return np.full(len(x), forced)

    monkeypatch.setattr(logistic.LogisticModel, "predict", predict)
    assert evaluate_decision("c1", _context(), paper_specs()).decision_hash == baseline


def test_m6_14_trading_modules_cannot_import_model_scoring() -> None:
    root = REPO_ROOT / "src" / "desk"
    guarded = ["paper", "risk", "security", "strategies", "discovery", "providers", "signer", "live"]
    offenders = []
    for package in guarded:
        for path in (root / package).rglob("*.py") if (root / package).exists() else []:
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                offenders += [f"{path.relative_to(root)}: {n}" for n in names if n.startswith("desk.models")]
    assert offenders == []


def test_model_influence_cannot_be_enabled_by_configuration(tmp_path: Path) -> None:
    config = tmp_path / "config"
    import shutil

    shutil.copytree(REPO_ROOT / "config", config)
    text = (config / "default.toml").read_text().replace('model_influence = "none"', 'model_influence = "rank"')
    (config / "default.toml").write_text(text)
    with pytest.raises(ConfigError):
        load_config(config)
