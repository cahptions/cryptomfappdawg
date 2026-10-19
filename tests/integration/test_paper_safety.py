"""Process and timing boundaries for PAPER supervision, entry cancellation and cash settlement."""

# ruff: noqa: F811

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from desk.core.clock import utc_now
from desk.core.config import Mode
from desk.core.paths import REPO_ROOT
from desk.paper.ledger import PaperLedger
from desk.storage.databases import Databases, open_ledger
from tests.fault_injection.paper_procs import databases
from tests.integration.test_paper_lifecycle import _setup, dbs  # noqa: F401 (fixture)


@pytest.mark.parametrize("interruption", ["STOP", "HALT", "GUARDIAN_LOST"])
def test_entry_cancels_when_safety_changes_during_quote_delay(
    dbs: Databases, monkeypatch: pytest.MonkeyPatch, interruption: str,
) -> None:
    engine, _, _, clock, candidate = _setup(dbs, monkeypatch)
    ledger = PaperLedger(dbs.paper_ledger)
    before = ledger.cash_lamports()
    engine.execution = dataclasses.replace(engine.execution, failure_rate=Decimal(0))

    async def interrupted_wait(_: float) -> None:
        if interruption == "STOP":
            dbs.paths.stop_file.write_text("operator stopped entries while quoting")
        elif interruption == "HALT":
            ledger.halt("OPERATOR_HALT", clock.now)
        else:
            clock.now += timedelta(seconds=21)

    engine.sleep = interrupted_wait
    assert asyncio.run(engine.consider(candidate)) == "BLOCKED"
    assert not ledger.open_positions() and ledger.cash_lamports() == before
    row = dbs.paper_ledger.execute(
        "SELECT payload_json FROM paper_events WHERE kind = 'ENTRY_ABORTED_AFTER_FILL'").fetchone()
    assert row is not None and json.loads(row["payload_json"])["reasons"]
    assert ledger.reconcile()["difference_lamports"] == 0


def test_exit_blocked_retries_while_marks_keep_refreshing(
    dbs: Databases, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    guardian.execution = dataclasses.replace(guardian.execution, failure_rate=Decimal(0))
    jupiter.price, jupiter.routes_left = Decimal("0.85"), 1
    for _ in range(3):
        clock.now += timedelta(seconds=30)
        asyncio.run(guardian.tick())
    ledger = PaperLedger(dbs.paper_ledger)
    assert ledger.open_positions()[0]["state"] == "EXIT_BLOCKED"
    jupiter.routes_left = None
    # The real five-second loop refreshes marks every 25s. Marks must not postpone the 60s exit retry.
    for _ in range(11):
        clock.now += timedelta(seconds=5)
        asyncio.run(guardian.tick())
        assert ledger.open_positions()
    clock.now += timedelta(seconds=5)
    asyncio.run(guardian.tick())
    assert not ledger.open_positions()
    assert ledger.reconcile()["difference_lamports"] == 0


def test_two_settlers_cannot_credit_the_same_close_twice(
    dbs: Databases, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _, _, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    ledger = PaperLedger(dbs.paper_ledger)
    position_id = ledger.open_positions()[0]["id"]
    ready = threading.Barrier(2)

    def settle() -> str:
        conn = open_ledger(dbs.paths.db_dir, Mode.PAPER)
        worker = PaperLedger(conn)
        original = worker.position

        def paused_read(key: str) -> sqlite3.Row:
            row = original(key)
            time.sleep(0.05)  # force overlapping reads if the state check ever moves outside the transaction
            return row

        monkeypatch.setattr(worker, "position", paused_read)
        try:
            ready.wait(timeout=5)
            try:
                worker.close_position(position_id, clock.now, reason="TEST", exit_lamports=110_000_000,
                                      exit_fees_lamports=105_000, profiles={})
                return "CLOSED"
            except ValueError as exc:
                assert "already closed" in str(exc)
                return "ALREADY_CLOSED"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: settle(), range(2)))
    assert sorted(results) == ["ALREADY_CLOSED", "CLOSED"]
    assert dbs.paper_ledger.execute("SELECT COUNT(*) FROM cash_entries WHERE kind = 'EXIT_PROCEEDS'").fetchone()[0] == 1
    assert dbs.paper_ledger.execute("SELECT COUNT(*) FROM cash_entries WHERE kind = 'RENT_REFUND'").fetchone()[0] == 1
    assert ledger.reconcile()["difference_lamports"] == 0


def _spawn_guardian(home: Path, price: Path) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, "-m", "tests.fault_injection.paper_procs", "guardian", str(home), str(price)],
                            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wait_for_pid(dbs: Databases, pid: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        row = dbs.runtime.execute("SELECT pid FROM heartbeats WHERE process = 'guardian'").fetchone()
        if row is not None and row["pid"] == pid:
            return
        time.sleep(0.05)
    raise AssertionError("guardian did not acquire supervision")


def test_guardian_process_is_singleton_and_sigkill_releases_its_lock(tmp_path: Path) -> None:
    home = tmp_path / "data"
    dbs_ = databases(str(home))
    PaperLedger(dbs_.paper_ledger).init_book(utc_now(), Decimal(100), Decimal(100), Decimal(100_000))
    price = tmp_path / "price"
    price.write_text("1")
    running: list[Any] = []
    try:
        first = _spawn_guardian(home, price)
        running.append(first)
        _wait_for_pid(dbs_, first.pid)
        duplicate = _spawn_guardian(home, price)
        running.append(duplicate)
        _, error = duplicate.communicate(timeout=15)
        assert duplicate.returncode != 0 and "guardian is already running" in error
        assert first.poll() is None
        os.kill(first.pid, signal.SIGKILL)
        first.wait(timeout=10)
        replacement = _spawn_guardian(home, price)
        running.append(replacement)
        _wait_for_pid(dbs_, replacement.pid)
        assert replacement.poll() is None
    finally:
        for process in running:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
        dbs_.close()
