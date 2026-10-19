"""M6 end to end: PAPER ledger → confidence matrix → append-only research record; decisions replay identically."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from desk.stats.evidence import confidence_report, render
from tests.integration.test_paper_lifecycle import _setup, dbs  # noqa: F401  (fixture)


def test_report_from_a_real_paper_ledger_is_recorded_and_replays(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    jupiter.price = Decimal("0.8")
    clock.now += timedelta(seconds=30)
    asyncio.run(guardian.tick())                     # hard stop -> one closed trade
    report = confidence_report(dbs.paper_ledger, dbs.research, clock.now, resamples=200)
    assert report["closed_trades"] == 1
    assert report["replay"] == {"checked": 1, "identical": 1}
    matrix = report["matrices"][0]
    assert matrix["strategy_id"] == "B2" and matrix["readiness"].startswith("BASELINE")
    stored = dbs.research.execute("SELECT payload_json FROM experiments WHERE id = ?",
                                  (report["experiment_id"],)).fetchone()
    assert json.loads(stored[0])["inputs_hash"] == report["inputs_hash"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.research.execute("DELETE FROM experiments")
    assert "INSUFFICIENT" in render(report)


def test_empty_book_reports_insufficient_evidence(dbs: Any) -> None:  # noqa: F811
    report = confidence_report(dbs.paper_ledger, None, datetime(2026, 11, 1, tzinfo=UTC))
    assert report["matrices"] == [] and "INSUFFICIENT EVIDENCE" in render(report)
