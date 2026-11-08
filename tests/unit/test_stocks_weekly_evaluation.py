"""The evidence evaluation runs by itself once a week (never beside a replay, not again within six hours of a
failure) and its proposed weights are shown, never adopted."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from desk.core.paths import DeskPaths
from desk.stocks import evidence_job
from desk.stocks.evidence_job import EvidenceWorker, schedule
from tests.conftest import REPO

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _worker(tmp_path: Path, monkeypatch: Any) -> tuple[EvidenceWorker, list[str]]:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    paths.ensure()
    worker = EvidenceWorker(paths)
    monkeypatch.setattr(evidence_job, "utc_now", lambda: NOW)          # states are stamped at NOW
    started: list[str] = []
    monkeypatch.setattr(worker, "start", lambda: started.append("start") or {})
    monkeypatch.setattr(worker, "sessions", lambda: 300)              # enough stored prices to evaluate
    return worker, started


def _report(worker: EvidenceWorker, at: datetime) -> None:
    (worker.paths.home / "stocks-evidence-report.json").write_text(json.dumps({"generated_at": at.isoformat()}))


def test_it_starts_when_a_week_has_passed_and_not_before(tmp_path: Path, monkeypatch: Any) -> None:
    worker, started = _worker(tmp_path, monkeypatch)
    assert schedule(worker.paths, NOW)["due"] and worker.weekly(NOW) and started == ["start"]
    evidence_job.publish(worker.paths, status="completed", phase="Evaluation complete")
    _report(worker, NOW - timedelta(days=3))
    assert not worker.weekly(NOW) and started == ["start"]
    due = schedule(worker.paths, NOW)
    assert due["next"] == (NOW + timedelta(days=4)).isoformat() and not due["due"]
    _report(worker, NOW - timedelta(days=8))
    assert worker.weekly(NOW) and started == ["start", "start"]
    state = json.loads((worker.paths.home / "stocks-evidence-state.json").read_text())
    assert state["automatic"] and "Weekly" in state["phase"]


def test_a_recent_failure_or_a_running_replay_waits(tmp_path: Path, monkeypatch: Any) -> None:
    worker, started = _worker(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "sessions", lambda: 10)
    assert not worker.weekly(NOW) and started == []                    # too few stored prices yet
    monkeypatch.setattr(worker, "sessions", lambda: 300)
    evidence_job.publish(worker.paths, status="failed", phase="could not finish")
    assert not worker.weekly(NOW + timedelta(hours=1)) and started == []
    assert worker.weekly(NOW + timedelta(hours=7)) and started == ["start"]
    held = type("Held", (), {"held": lambda self: True})
    monkeypatch.setattr(evidence_job, "WorkerLock", lambda paths, name: held())
    _report(worker, NOW - timedelta(days=30))
    assert not worker.weekly(NOW) and started == ["start"]
