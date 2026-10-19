"""Dashboard controls launch only bounded local research workers, never arbitrary commands."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.worker_lock import WorkerBusy, WorkerLock
from desk.paper.ledger import PaperLedger
from desk.storage.databases import Databases
from desk.web import control
from desk.web.control import ControlError, WorkerManager

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


Spawned = list[tuple[list[str], dict[str, Any], FakeProcess]]


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeskPaths:
    monkeypatch.setattr(control, "utc_now", lambda: NOW)
    result = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "private.env")
    Databases.open(result).close()
    return result


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Spawned:
    calls: Spawned = []

    def spawn(command: list[str], **kwargs: Any) -> FakeProcess:
        child = FakeProcess(10000 + len(calls))
        calls.append((list(command), kwargs, child))
        return child

    monkeypatch.setattr(control.subprocess, "Popen", spawn)
    return calls


@pytest.mark.parametrize("kind", ["live", "keygen", "arm", "paper; touch /tmp/injected", "../paper", [], None])
def test_control_refuses_arbitrary_commands(paths: DeskPaths, spawned: Spawned, kind: Any) -> None:
    manager = WorkerManager(paths)
    with pytest.raises(ControlError, match="unsupported worker"):
        manager.start(kind)
    assert not spawned and not manager.jobs()


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf"), 0, -1, 10080.1,
                                      True, "5", None, [5], 10 ** 1000])
def test_control_refuses_unbounded_or_wrong_type_duration(paths: DeskPaths, spawned: Spawned, duration: Any) -> None:
    with pytest.raises(ControlError, match="duration"):
        WorkerManager(paths).start("paper", duration)
    assert not spawned


@pytest.mark.parametrize("kind,arguments", [("paper", ["paper", "--minutes", "60"]),
                                          ("shadow", ["shadow", "--minutes", "60"]),
                                          ("report", ["confidence", "--json"])])
def test_control_uses_fixed_argv_paths_and_detached_private_logs(
    paths: DeskPaths, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, kind: str, arguments: list[str],
) -> None:
    monkeypatch.setenv("DESK_HOME", "/tmp/wrong-dashboard-home")
    monkeypatch.setenv("DESK_CONFIG_DIR", "/tmp/wrong-dashboard-config")
    monkeypatch.setenv("DESK_SECRETS_FILE", "/tmp/wrong-dashboard-secrets")
    manager = WorkerManager(paths)
    result = manager.start(kind, 60)
    assert len(spawned) == 1
    argv, options, child = spawned[0]
    assert argv == [sys.executable, "-m", "desk", *arguments]
    assert options["shell"] is False and options["start_new_session"] and options["close_fds"]
    assert options["stdin"] == subprocess.DEVNULL and options["stderr"] == subprocess.STDOUT
    assert options["env"]["DESK_HOME"] == str(paths.home)
    assert options["env"]["DESK_CONFIG_DIR"] == str(paths.config_dir)
    assert options["env"]["DESK_SECRETS_FILE"] == str(paths.secrets_file)
    assert options["stdout"].closed
    assert stat.S_IMODE((paths.logs_dir / f"dashboard-{kind}.log").stat().st_mode) == 0o600
    assert result == {"name": kind, "pid": child.pid, "started_at": NOW.isoformat(), "returncode": None}
    result["name"] = "tampered"
    assert manager.jobs()[0]["name"] == kind


def test_control_rejects_duplicate_entry_workers_but_allows_report(paths: DeskPaths, spawned: Spawned) -> None:
    manager = WorkerManager(paths)
    manager.start("paper", 60)
    with pytest.raises(ControlError, match="already running"):
        manager.start("paper", 60)
    with pytest.raises(ControlError, match="already active"):
        manager.start("shadow", 60)
    manager.start("report")
    assert len(spawned) == 2
    with pytest.raises(ControlError, match="already running"):
        manager.start("report")


@pytest.mark.parametrize("process,age,blocks", [("collector", 10, True), ("collector", 90, True),
                                              ("collector", -30, True), ("collector", 91, False),
                                              ("guardian", 10, False)])
def test_external_worker_heartbeat_blocks_duplicates_but_not_guardian(
    paths: DeskPaths, spawned: Spawned, process: str, age: float, blocks: bool,
) -> None:
    dbs = Databases.open(paths)
    dbs.runtime.execute("INSERT INTO heartbeats(process,pid,at) VALUES (?,?,?)",
                        (process, 99999, (NOW - timedelta(seconds=age)).isoformat()))
    dbs.close()
    manager = WorkerManager(paths)
    if blocks:
        with pytest.raises(ControlError, match="already active"):
            manager.start("paper", 60)
        assert not spawned
    else:
        manager.start("paper", 60)
        assert len(spawned) == 1


def test_corrupt_worker_heartbeat_does_not_claim_idle(paths: DeskPaths, spawned: Spawned) -> None:
    dbs = Databases.open(paths)
    dbs.runtime.execute("INSERT INTO heartbeats(process,pid,at) VALUES ('collector',99999,'broken')")
    dbs.close()
    with pytest.raises(ControlError, match="cannot verify existing workers"):
        WorkerManager(paths).start("paper")
    assert not spawned


def test_durable_worker_lock_prevents_start_before_external_heartbeat(paths: DeskPaths, spawned: Spawned) -> None:
    with WorkerLock(paths):
        with pytest.raises(ControlError, match="already active"):
            WorkerManager(paths).start("paper", 60)
        # Another dashboard must make the same decision; no per-manager child registry is needed.
        with pytest.raises(ControlError, match="already active"):
            WorkerManager(paths).start("shadow", 60)
        assert not spawned
    WorkerManager(paths).start("paper", 60)
    assert len(spawned) == 1


def test_worker_lock_stays_on_same_inode_and_releases_on_exception(paths: DeskPaths) -> None:
    original_inode = None
    with pytest.raises(RuntimeError, match="crashed"), WorkerLock(paths) as first:
        original_inode = first.path.stat().st_ino
        assert WorkerLock(paths).held()
        with pytest.raises(WorkerBusy):
            WorkerLock(paths).acquire()
        raise RuntimeError("crashed")
    assert not WorkerLock(paths).held()
    with WorkerLock(paths) as second:
        assert second.path.stat().st_ino == original_inode
    assert stat.S_IMODE(second.path.stat().st_mode) == 0o600


def test_entry_dashboard_and_guardian_locks_are_independent(paths: DeskPaths) -> None:
    with WorkerLock(paths), WorkerLock(paths, "dashboard"), WorkerLock(paths, "guardian"):
        assert all(WorkerLock(paths, name).held() for name in ("entry-worker", "dashboard", "guardian"))
    assert not any(WorkerLock(paths, name).held() for name in ("entry-worker", "dashboard", "guardian"))


@pytest.mark.parametrize("name", ["../entry-worker", "/tmp/elsewhere", "entry-worker/child", "other"])
def test_worker_lock_names_cannot_supply_paths(paths: DeskPaths, name: str) -> None:
    with pytest.raises(ValueError, match="unsupported worker lock"):
        WorkerLock(paths, name)


def test_persisted_paper_halt_blocks_start_without_stop_file(paths: DeskPaths, spawned: Spawned) -> None:
    dbs = Databases.open(paths)
    PaperLedger(dbs.paper_ledger).halt("DRAWDOWN", NOW)
    dbs.close()
    assert not paths.stop_file.exists()
    with pytest.raises(ControlError, match="entries are stopped"):
        WorkerManager(paths).start("paper")
    assert not spawned


def test_stop_file_blocks_start_even_without_ledger_halt(paths: DeskPaths, spawned: Spawned) -> None:
    paths.stop_file.write_text("STOP\n")
    with pytest.raises(ControlError, match="entries are stopped"):
        WorkerManager(paths).start("paper")
    assert not spawned


def test_stop_persists_both_halts_without_stopping_guardian_or_children(paths: DeskPaths, spawned: Spawned) -> None:
    dbs = Databases.open(paths)
    dbs.runtime.execute("INSERT INTO heartbeats(process,pid,at) VALUES ('guardian',12345,?)", (NOW.isoformat(),))
    dbs.close()
    manager = WorkerManager(paths)
    manager.start("paper", 60)
    result = manager.stop_entries()
    assert result["halted"] and result["guardian"] == "continues supervising open positions"
    assert paths.stop_file.read_text() == f"stopped {NOW.isoformat()}\n"
    dbs = Databases.open(paths)
    try:
        assert PaperLedger(dbs.paper_ledger).halted() == "STOP"
        row = dbs.runtime.execute("SELECT pid,at FROM heartbeats WHERE process='guardian'").fetchone()
        assert row["pid"] == 12345 and row["at"] == NOW.isoformat()
    finally:
        dbs.close()
    assert len(spawned) == 1 and manager.jobs()[0]["returncode"] is None
    with pytest.raises(ControlError, match="entries are stopped"):
        WorkerManager(paths).start("paper", 60)
    manager.start("report")
    assert len(spawned) == 2


def test_stop_marker_survives_database_failure(paths: DeskPaths, spawned: Spawned, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_paths: DeskPaths) -> Databases:
        raise OSError("disk unavailable")

    monkeypatch.setattr(control.Databases, "open", unavailable)
    with pytest.raises(OSError, match="disk unavailable"):
        WorkerManager(paths).stop_entries()
    assert paths.stop_file.exists() and not spawned


def test_job_history_is_bounded_and_remembers_completed_previous_process(paths: DeskPaths, spawned: Spawned) -> None:
    manager = WorkerManager(paths)
    for index in range(25):
        manager.start("report")
        spawned[-1][2].returncode = index % 2
    rows = manager.jobs()
    assert len(rows) == 20
    assert [row["pid"] for row in rows] == [child.pid for _, _, child in spawned[-20:]]
    assert [row["returncode"] for row in rows] == [index % 2 for index in range(5, 25)]
    rows[0]["name"] = "changed"
    assert manager.jobs()[0]["name"] == "report"


def test_failed_spawn_does_not_record_running_job(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_command: list[str], **_kwargs: Any) -> None:
        raise OSError("cannot start worker")

    monkeypatch.setattr(control.subprocess, "Popen", fail)
    manager = WorkerManager(paths)
    with pytest.raises(OSError, match="cannot start worker"):
        manager.start("report")
    assert not manager.jobs()


def test_log_symlink_is_not_followed(paths: DeskPaths, spawned: Spawned, tmp_path: Path) -> None:
    target = tmp_path / "must-not-overwrite"
    target.write_text("original")
    os.symlink(target, paths.logs_dir / "dashboard-report.log")
    with pytest.raises(OSError):
        WorkerManager(paths).start("report")
    assert target.read_text() == "original" and not spawned
