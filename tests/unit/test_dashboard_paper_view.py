from __future__ import annotations

import tracemalloc
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.paper.ledger import PaperLedger
from desk.paper.report import paper_report
from desk.storage.databases import Databases
from desk.web.paper_view import paper_snapshot


@pytest.mark.parametrize("scenario", ["empty", "profits_losses", "concurrent", "drawdown", "fixed_cost"])
def test_streamed_summary_matches_complete_report(config_dir: Path, tmp_path: Path, scenario: str) -> None:
    loaded = load_config(config_dir)
    paths = DeskPaths(tmp_path / "data", config_dir, tmp_path / "secrets")
    dbs = Databases.open(paths)
    ledger = PaperLedger(dbs.paper_ledger)
    start = datetime(2026, 10, 19, tzinfo=UTC)
    try:
        ledger.init_book(start, Decimal(100), Decimal(100), Decimal(100_000))
        count = 0 if scenario == "empty" else 12
        for index in range(count):
            at = start + timedelta(hours=index if scenario != "concurrent" else 0)
            fees = 50_000_000 if scenario == "fixed_cost" else 10_000
            ledger.open_position(position_id=str(index), intent_id=f"intent-{index}", candidate_id=f"candidate-{index}",
                                 mint=f"mint-{index}", pool_address=f"pool-{index}", strategy_id=f"strategy-{index % 2}",
                                 strategy_version="v1", decision_hash="hash", regime_label=None, at=at,
                                 ticket_lamports=100_000_000, tokens_raw=1_000, entry_fees_lamports=fees,
                                 failed_fees_lamports=0, rent_lamports=2_000_000, entry={})
            if scenario != "concurrent" and index < count - 1:
                exit_ = 1_000_000 if scenario == "drawdown" else (120_000_000 if index % 2 else 80_000_000)
                pnl = exit_ - 100_000_000 - fees - 10_000
                ledger.close_position(str(index), at + timedelta(minutes=1), reason="test", exit_lamports=exit_,
                                      exit_fees_lamports=10_000,
                                      profiles={name: {"pnl_lamports": pnl} for name in ("observed", "conservative", "stress")})
            else:
                ledger.record_mark(str(index), at, "EXECUTABLE", 110_000_000, "hash")
            ledger.event(at, "DECISION", {"chosen": "NO_TRADE" if index % 2 else "TRADE"})
            ledger.event(at, "ENTRY_BLOCKED", {"reasons": ["TEST", "COST"]})
        at = start + timedelta(days=3)
        assert paper_snapshot(dbs, loaded, at) == paper_report(dbs, loaded, at)
    finally:
        dbs.close()


def test_uninitialized_dashboard_paper_view(config_dir: Path, tmp_path: Path) -> None:
    dbs = Databases.open(DeskPaths(tmp_path / "data", config_dir, tmp_path / "secrets"))
    try:
        assert paper_snapshot(dbs, load_config(config_dir), datetime.now(UTC))["initialized"] is False
    finally:
        dbs.close()


def test_large_audit_history_does_not_load_payloads_into_python(config_dir: Path, tmp_path: Path) -> None:
    dbs = Databases.open(DeskPaths(tmp_path / "data", config_dir, tmp_path / "secrets"))
    loaded = load_config(config_dir)
    now = datetime.now(UTC)
    try:
        PaperLedger(dbs.paper_ledger).init_book(now, Decimal(100), Decimal(100), Decimal(100_000))
        payload = '{"reasons":["COST"],"unused_evidence":"' + "x" * 1000 + '"}'
        dbs.paper_ledger.execute("BEGIN")
        dbs.paper_ledger.executemany(
            "INSERT INTO paper_events(at,kind,payload_json) VALUES (?,'ENTRY_BLOCKED',?)",
            ((now.isoformat(), payload) for _ in range(12_000)))
        dbs.paper_ledger.execute("COMMIT")
        tracemalloc.start()
        try:
            snapshot = paper_snapshot(dbs, loaded, now)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert snapshot["entry_blocked_reasons"] == {"COST": 12_000}
        assert snapshot["events"] == {"ENTRY_BLOCKED": 12_000}
        assert peak < 1_000_000  # The stored payloads alone exceed 12 MiB.
    finally:
        dbs.close()
