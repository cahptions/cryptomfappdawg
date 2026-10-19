"""Dashboard GETs read persisted evidence without opening write-capable database connections."""

# ruff: noqa: F811

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from desk.core.config import load_config
from desk.core.logging import REDACTED, RedactionFilter
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.paper.ledger import PaperLedger
from desk.storage.databases import Databases
from desk.web.data import DashboardData
from tests.integration.test_paper_lifecycle import _setup, dbs  # noqa: F401 (fixture)
from tests.unit.test_market_context import NOW, _history


def test_empty_dashboard_does_not_create_runtime_files(tmp_path: Path) -> None:
    paths = DeskPaths(home=tmp_path / "missing", config_dir=REPO_ROOT / "config", secrets_file=tmp_path / "none")
    data = DashboardData(paths, load_config(paths.config_dir))
    snapshot = data.snapshot()
    assert snapshot["paper"]["initialized"] is False
    assert snapshot["live"]["buy_enabled"] is False
    assert snapshot["scanner"] == [] and data.candidate("missing") is None and data.trades() == []
    assert not paths.home.exists()


def test_dashboard_reads_paper_and_confidence_without_side_effects(
    dbs: Databases, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _, _, _, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    report = {"generated_at": "2026-10-19T00:00:00+00:00", "closed_trades": 0, "matrices": []}
    dbs.research.execute("INSERT INTO experiments(id, created_at, kind, payload_json) VALUES ('report', ?, ?, ?)",
                         (report["generated_at"], "CONFIDENCE_MATRIX", json.dumps(report)))
    data = DashboardData(dbs.paths, load_config(dbs.paths.config_dir))
    before = dbs.research.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    snapshot = data.snapshot()
    assert snapshot["paper"]["initialized"] is True
    assert snapshot["confidence"]["experiment_id"] == "report"
    assert snapshot["paper"]["reconciliation"]["difference_lamports"] == "0"
    assert len(snapshot["scanner"]) == 1 and snapshot["scanner"][0]["id"] == candidate
    position_id = PaperLedger(dbs.paper_ledger).open_positions()[0]["id"]
    position = data.position(position_id)
    assert position is not None and isinstance(position["tokens_raw"], str)
    assert position["entry_json"]["quotes"] and position["timeline"]
    assert data.candidate(candidate) is not None
    assert data.candidate("' OR 1=1 --") is None
    assert data.position("missing") is None
    assert data.strategy(position["strategy_id"]) is not None
    assert data.strategy("missing") is None
    assert len(data.trades()) == 1
    assert len(snapshot["live"]["gates"]) == 7 and snapshot["live"]["buy_enabled"] is False
    assert dbs.research.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == before
    with data._databases() as readonly:
        assert readonly is not None
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            readonly.research.execute("DELETE FROM experiments")


def test_dashboard_regime_uses_measured_history_without_creating_evidence(
    dbs: Databases, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("desk.web.data.utc_now", lambda: NOW + timedelta(seconds=30))
    data = DashboardData(dbs.paths, load_config(dbs.paths.config_dir))
    assert data.snapshot()["regime"]["status"] == "missing"
    _history(dbs, 31)
    count = dbs.market.execute("SELECT COUNT(*) FROM market_context").fetchone()[0]
    snapshot = data.snapshot()
    assert snapshot["regime"]["status"] == "ok" and snapshot["regime"]["label"] is not None
    assert dbs.market.execute("SELECT COUNT(*) FROM market_context").fetchone()[0] == count
    monkeypatch.setattr("desk.web.data.utc_now", lambda: NOW + timedelta(seconds=121))
    assert data.snapshot()["regime"]["label"] is None


def test_dashboard_redacts_secrets_inside_nested_stored_audit_payloads(
    dbs: Databases, monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "private-fixture-value-913"
    redactor = RedactionFilter([secret])
    monkeypatch.setattr("desk.web.data.redaction_filter", lambda: redactor)
    PaperLedger(dbs.paper_ledger).event(NOW, "TEST", {"nested": [{"message": "provider failed: " + secret}]})
    snapshot = DashboardData(dbs.paths, load_config(dbs.paths.config_dir)).snapshot()
    assert secret not in json.dumps(snapshot)
    assert REDACTED in snapshot["audit"][0]["payload"]["nested"][0]["message"]
