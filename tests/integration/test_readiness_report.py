"""M9 readiness report: one verdict from recorded data, reproducible bytes, sources, disclosures, no self-promotion."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from desk import cli
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.live.gates import evaluate_gates, live_allowed, status_context
from desk.ops import readiness
from desk.ops.readiness import (
    CONTINUE_PAPER,
    ELIGIBLE,
    FAILED_VALIDATION,
    INSUFFICIENT_EVIDENCE,
    overall_verdict,
    readiness_report,
)
from desk.regime.context import INPUTS, ContextRecord, save_context
from desk.storage.databases import Databases
from tests.integration.test_paper_lifecycle import _setup, dbs  # noqa: F401  (fixture)

LOADED = load_config(REPO_ROOT / "config")


def _book(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: F811
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    jupiter.price = Decimal("0.8")                                       # hard stop: one closed trade
    clock.now += timedelta(seconds=30)
    asyncio.run(guardian.tick())
    return clock


@pytest.mark.parametrize("states,expected", [
    ([], INSUFFICIENT_EVIDENCE),
    ([FAILED_VALIDATION, INSUFFICIENT_EVIDENCE], INSUFFICIENT_EVIDENCE),
    ([FAILED_VALIDATION, FAILED_VALIDATION], FAILED_VALIDATION),
    ([FAILED_VALIDATION, CONTINUE_PAPER, INSUFFICIENT_EVIDENCE], CONTINUE_PAPER),
    ([CONTINUE_PAPER, ELIGIBLE], ELIGIBLE),
    (["BASELINE (comparison only; never promoted)"], INSUFFICIENT_EVIDENCE),
])
def test_exactly_one_verdict_by_documented_precedence(states: list[str], expected: str) -> None:
    assert overall_verdict(states) == expected


def test_report_is_reproducible_from_recorded_data_alone(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    clock = _book(dbs, monkeypatch)
    markdown, summary = readiness_report(dbs, LOADED)
    assert summary["verdict"] == INSUFFICIENT_EVIDENCE and markdown.count("**Verdict:") == 1
    assert summary["as_of"] == clock.now.isoformat().replace("+00:00", ".000000+00:00")   # the last event, not now
    readiness.record(dbs, summary, "READINESS_REPORT.md", clock.now)     # the audit row is outside the report
    again, again_summary = readiness_report(dbs, LOADED)
    assert again == markdown and again_summary["markdown_sha256"] == summary["markdown_sha256"]
    for heading in ("## Forward PAPER operation (M9-01)", "## Cost profiles (observed, conservative, stress)",
                    "## Unresolved risks and data gaps", "## Disclosures", "## Sources"):
        assert heading in markdown
    assert "Past PAPER results do not guarantee future returns" in markdown
    assert "No calibrated 30-day market regime exists" in markdown
    assert "Your $100 account, replayed" in markdown
    for figure in ("as-of time (latest recorded PAPER event)", "closed trades", "open exit problems",
                   "market-context coverage and gaps", "confidence matrices, replay agreement",
                   "realized P&L by cost profile", "network fees paid", "$100-account replica"):
        assert f"| {figure} |" in markdown, figure
    row = dbs.research.execute("SELECT payload_json FROM experiments WHERE kind = 'READINESS_REPORT'").fetchone()
    assert summary["markdown_sha256"] in row["payload_json"]


def test_collection_gaps_are_reported(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    clock = _book(dbs, monkeypatch)
    start = clock.now
    for minutes in (1, 2, 125, 126):                                     # a two-hour hole in market context
        save_context(dbs.market, ContextRecord(observed_at=start + timedelta(minutes=minutes),
                                               reasons=dict.fromkeys(INPUTS, "TEST")))
    from desk.paper.ledger import PaperLedger

    PaperLedger(dbs.paper_ledger).event(start + timedelta(minutes=126), "HALT", {"reason": "STOP"})
    markdown, _ = readiness_report(dbs, LOADED)
    assert "| Collection gaps longer than 5 min | 1 (2.0 h in total) |" in markdown
    assert "gap(s) longer than 5 minutes" in markdown


def test_an_eligible_verdict_changes_no_mode_approval_or_gate(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _book(dbs, monkeypatch)
    version = dbs.paper_ledger.execute("SELECT strategy_version FROM positions").fetchone()[0]
    eligible = {"matrices": [{"strategy_id": "H1", "strategy_version": version, "trades": 400, "readiness": ELIGIBLE,
                              "cells": [{"dimension": "net_expectancy", "status": "PASS", "estimate": 0.03,
                                         "lower": 0.01, "upper": 0.05, "evidence": 400, "detail": "fixture"}]}],
                "replay": {"checked": 200, "identical": 200}}
    monkeypatch.setattr(readiness, "confidence_report", lambda *_, **__: eligible)
    approvals_before = dbs.research.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
    markdown, summary = readiness_report(dbs, LOADED)
    assert summary["verdict"] == ELIGIBLE and summary["deciding_versions"] == [version]
    assert f"Deciding version(s): `{version}`" in markdown and "typed `desk live approve <version>`" in markdown
    assert dbs.research.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == approvals_before
    assert load_config(REPO_ROOT / "config").settings.desk.mode == LOADED.settings.desk.mode
    gates = evaluate_gates(status_context(dbs.research, LOADED, version, signer_unlocked=False, ceiling=Decimal(2),
                                          halted=None, guardian_fresh=True))
    assert not live_allowed(gates) and not next(g for g in gates if g.name == "strategy approval").ok


def test_no_book_is_insufficient_evidence(dbs: Any) -> None:  # noqa: F811
    markdown, summary = readiness_report(dbs, LOADED)
    assert summary["verdict"] == INSUFFICIENT_EVIDENCE and "No PAPER book exists yet" in markdown


def test_cli_writes_the_report_and_records_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "private.env")
    monkeypatch.setattr(cli, "_bootstrap", lambda _: (paths, []))
    monkeypatch.setattr(cli, "load_secrets", lambda _: Secrets())
    args: argparse.Namespace = cli.build_parser().parse_args(["report", "readiness"])
    assert args.func(args) == 0
    report = paths.home / "reports" / "READINESS_REPORT.md"
    assert report.read_text().startswith("# KESTREL readiness report")
    out = capsys.readouterr().out
    assert "verdict: INSUFFICIENT EVIDENCE" in out and "unchanged" in out
    opened = Databases.open(paths)
    try:
        assert opened.research.execute("SELECT COUNT(*) FROM experiments WHERE kind = 'READINESS_REPORT'").fetchone()[0] == 1
    finally:
        opened.close()


def test_paper_collector_refreshes_evidence_off_the_event_loop(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from types import SimpleNamespace

    from desk.ops.collector import EVIDENCE_INTERVAL_SECONDS, Collector, TaskStats

    _book(dbs, monkeypatch)
    secrets = SimpleNamespace(all_secret_values=lambda: [])
    suite: Any = SimpleNamespace(rpc=SimpleNamespace(http=SimpleNamespace(secrets=secrets)))
    collector = Collector(suite, dbs, LOADED, discovery=True)
    collector.stats.tasks["evidence"] = TaskStats()
    asyncio.run(collector.evidence())
    kinds = [r[0] for r in dbs.research.execute("SELECT kind FROM experiments ORDER BY created_at, id")]
    assert kinds == ["CONFIDENCE_MATRIX", "READINESS_REPORT"]
    report = readiness.default_path(dbs.paths)
    assert report.read_text().startswith("# KESTREL readiness report") and collector.stats.tasks["evidence"].items == 1
    assert EVIDENCE_INTERVAL_SECONDS < 7 * 86_400 / 4      # sizing evidence (<= 7 days old) stays fresh with margin


def test_dashboard_shows_the_latest_recorded_verdict(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from desk.web.data import DashboardData

    clock = _book(dbs, monkeypatch)
    data = DashboardData(dbs.paths, LOADED)
    assert data.snapshot()["readiness"] is None
    _, summary = readiness_report(dbs, LOADED)
    readiness.record(dbs, summary, "/tmp/READINESS_REPORT.md", clock.now)
    shown = data.snapshot()["readiness"]
    assert shown["verdict"] == INSUFFICIENT_EVIDENCE and shown["next_step"].startswith("Keep PAPER running")
    assert shown["path"] == "/tmp/READINESS_REPORT.md"
