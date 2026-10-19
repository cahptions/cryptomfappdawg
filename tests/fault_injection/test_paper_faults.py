"""M5-12 / M5-18 fault injection for the PAPER learning book.

M5-12: the entry engine is SIGKILLed mid-write while a separate guardian process supervises; no partial position
exists, the guardian keeps marking and exits every position on its own, and entries are impossible once the
guardian heartbeat is stale. M5-18: wall-clock jumps (suspend/resume) force fresh quotes before any exit decision,
and a collector gap halts PAPER entries for five minutes.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desk.core.clock import to_iso, utc_now
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT
from desk.ops import collector as collector_module
from desk.ops.collector import Collector
from desk.paper import guardian as guardian_module
from desk.paper.engine import PaperEngine, heartbeat_age
from desk.paper.ledger import PaperLedger
from tests.fault_injection.paper_procs import databases
from tests.integration.test_paper_lifecycle import MINT, FakeJupiter, _setup, dbs  # noqa: F401  (fixture)


def _spawn(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, "-m", "tests.fault_injection.paper_procs", *args], cwd=REPO_ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())


def _wait(condition: Any, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.mark.slow
def test_engine_kill9_leaves_no_partial_position_and_guardian_supervises_alone(tmp_path: Path) -> None:
    home = tmp_path / "data"
    dbs_ = databases(str(home))
    ledger = PaperLedger(dbs_.paper_ledger)
    ledger.init_book(utc_now(), Decimal(100), Decimal(100), Decimal(100_000))
    price = tmp_path / "price"
    price.write_text("1")
    guardian = _spawn("guardian", str(home), str(price))
    engine: subprocess.Popen[str] | None = None
    try:
        _wait(lambda: (age := heartbeat_age(dbs_.runtime, "guardian", utc_now())) is not None and age < 5, 30,
              "the guardian heartbeat")
        # Start writing only once the guardian supervises: the book then holds ~15 positions however slowly the
        # guardian started, so the marking deadline below does not depend on machine load.
        engine = _spawn("engine", str(home))
        assert engine.stdout is not None
        committed = [engine.stdout.readline().strip() for _ in range(15)]
        os.kill(engine.pid, signal.SIGKILL)                       # mid-loop: possibly inside a transaction
        engine.wait(timeout=10)
        assert dbs_.paper_ledger.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = {r["id"]: r for r in dbs_.paper_ledger.execute("SELECT * FROM positions")}
        assert set(committed) <= set(rows)                        # every acknowledged position survived
        for position_id in rows:                                  # ...and none is partial
            kinds = sorted(r["kind"] for r in dbs_.paper_ledger.execute(
                "SELECT kind FROM cash_entries WHERE position_id = ? AND kind IN ('ENTRY_TICKET','ENTRY_FEES','RENT_LOCK')",
                (position_id,)))
            assert kinds == ["ENTRY_FEES", "ENTRY_TICKET", "RENT_LOCK"], position_id
        orphans = dbs_.paper_ledger.execute(
            "SELECT COUNT(*) FROM cash_entries c LEFT JOIN positions p ON p.id = c.position_id "
            "WHERE c.position_id IS NOT NULL AND p.id IS NULL").fetchone()[0]
        assert orphans == 0 and ledger.reconcile()["difference_lamports"] == 0
        # the engine is dead; the guardian keeps marking every open position on its own
        killed_at = utc_now()
        _wait(lambda: dbs_.paper_ledger.execute(
            "SELECT COUNT(DISTINCT position_id) FROM marks WHERE at > ?", (to_iso(killed_at),)).fetchone()[0]
            == len(rows), 30, "fresh marks for every position after the engine died")
        price.write_text("0.5")                                   # -50%: every position must hit its hard stop
        _wait(lambda: not ledger.open_positions(), 60, "the guardian to exit every position")
        reasons = {r["exit_reason"] for r in dbs_.paper_ledger.execute("SELECT exit_reason FROM positions")}
        assert reasons == {"HARD_STOP"}
        assert ledger.reconcile()["difference_lamports"] == 0
    finally:
        for proc in (engine, guardian):
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        guardian.wait(timeout=20)
    assert guardian.returncode == 0, guardian.stderr.read() if guardian.stderr else ""
    # with the guardian gone its heartbeat goes stale and no entry can pass the gate
    engine_gate = PaperEngine(SimpleNamespace(jupiter=None), dbs_, load_config(REPO_ROOT / "config"))  # type: ignore[arg-type]
    assert "GUARDIAN_NOT_SUPERVISING" in engine_gate.blocked(MINT, utc_now() + timedelta(seconds=21))
    dbs_.close()


class _FakeTime:
    def __init__(self) -> None:
        self.wall, self.mono = time.time(), time.monotonic()

    def time(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono


def test_wall_clock_jump_forces_fresh_quotes_before_any_exit_decision(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    fake = _FakeTime()
    monkeypatch.setattr(guardian_module, "time", fake)
    guardian.paper = guardian.paper.model_copy(update={"guardian_loop_seconds": 1})
    ledger = PaperLedger(dbs.paper_ledger)
    ticks = 0
    original = guardian.tick

    async def tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            # the laptop slept for 30 minutes and the token collapsed meanwhile; the guardian clock barely moved
            jupiter.price = Decimal("0.5")
            clock.now += timedelta(seconds=1)
            guardian.stop()
        await original()
        if ticks == 1:
            fake.wall += 1800                                     # wall time jumps; monotonic does not

    monkeypatch.setattr(guardian, "tick", tick)
    marks_before = dbs.paper_ledger.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    stats = asyncio.run(guardian.run())
    assert stats["suspend_gaps"] == 1
    marks = dbs.paper_ledger.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    assert marks == marks_before + 2          # tick 1 marks; tick 2 re-quotes although the 25 s interval had not passed
    closed = dict(dbs.paper_ledger.execute("SELECT * FROM positions").fetchone())
    assert closed["state"] == "CLOSED" and closed["exit_reason"] == "HARD_STOP"
    assert closed["exit_lamports"] < closed["ticket_lamports"] * 0.6   # decided on the post-resume quote
    assert ledger.reconcile()["difference_lamports"] == 0


def test_collector_suspend_gap_halts_paper_entries_for_five_minutes(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _, guardian, jupiter, _, _ = _setup(dbs, monkeypatch)
    fake = _FakeTime()
    monkeypatch.setattr(collector_module, "time", fake)
    secrets = SimpleNamespace(all_secret_values=lambda: [])
    suite: Any = SimpleNamespace(jupiter=jupiter, rpc=SimpleNamespace(http=SimpleNamespace(secrets=secrets)))
    collector = Collector(suite, dbs, load_config(REPO_ROOT / "config"), paper=True)
    assert collector.paper_engine is not None
    now = utc_now()
    guardian.heartbeat()

    async def sleeping_job() -> None:
        fake.wall += 1800                                         # the job "ran" across a 30-minute suspend
        collector.stop()

    asyncio.run(collector._loop("probe", 1, sleeping_job))
    assert collector.stats.suspend_gaps == 1
    assert "SUSPEND_RECOVERY" in collector.paper_engine.blocked(MINT, now)
    fake.mono += 301
    assert "SUSPEND_RECOVERY" not in collector.paper_engine.blocked(MINT, now)
    assert sqlite3.sqlite_version
