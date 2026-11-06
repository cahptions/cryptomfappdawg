"""A failing PAPER process remains failed, but exports useful, credential-free context."""

from __future__ import annotations

import json
import os
import sqlite3
from argparse import Namespace
from unittest.mock import Mock

import pytest

from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks import cli, worker_failure
from desk.stocks.diagnostics import diagnose
from desk.stocks.research_worker import ResearchWorker
from tests.conftest import REPO


def test_paper_failure_is_retained_without_messages_or_locals_and_still_propagates(tmp_path, monkeypatch) -> None:
    paths = DeskPaths(tmp_path / "data", REPO / "config", tmp_path / "no-secrets")
    monkeypatch.setattr(DeskPaths, "from_env", lambda: paths)
    sentinel = "PRIVATE-PROVIDER-URL-AND-TOKEN"

    async def cycle(*args, **kwargs):
        raise AttributeError(sentinel)

    monkeypatch.setattr("desk.stocks.runner.cycle", cycle)
    with pytest.raises(AttributeError, match=sentinel):
        cli.run(Namespace(stocks_command="paper", price_days=20, companies=20, options=5,
                          minutes=1, interval=60))
    value = worker_failure.read(paths)
    assert value and value["exception_type"] == "AttributeError" and value["pid"] == os.getpid()
    assert any(frame["file"] == "stocks/cli.py" for frame in value["frames"])
    assert sentinel not in json.dumps(value) and str(tmp_path) not in json.dumps(value)
    assert (paths.home / worker_failure.FILE).stat().st_mode & 0o777 == 0o600
    assert not WorkerLock(paths, "stocks-worker").held()
    assert diagnose(paths)["worker"]["last_failure"] == value


def test_diagnostic_write_failure_does_not_replace_original_failure(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_run", Mock(side_effect=KeyError("original")))
    monkeypatch.setattr(worker_failure, "record", Mock(side_effect=OSError("full disk")))
    with pytest.raises(KeyError, match="original"):
        cli.run(Namespace(stocks_command="paper"))
    assert "could not be saved" in capsys.readouterr().out


def test_real_child_startup_failure_survives_dashboard_recreation(tmp_path) -> None:
    # Invalid private configuration fails before any provider work. Exercise the actual CLI/Popen boundary.
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "no-secrets")
    paths.config_dir.mkdir()
    (paths.config_dir / "default.toml").write_text("invalid = [")
    worker = ResearchWorker(paths)
    worker.start()
    assert worker.process is not None
    try:
        assert worker.process.wait(timeout=15) == 2
        state = worker.maintain()
        assert "ConfigError" in state["error"] and not state["running"]
        retained = diagnose(paths)["worker"]["last_failure"]
        assert retained["exception_type"] == "ConfigError"
        assert any(f["file"] == "core/config.py" for f in retained["frames"])
        worker.stop()
        resumed = ResearchWorker(paths)
        assert not resumed.maintain()["running"] and not resumed.status()["enabled"]
        assert diagnose(paths)["worker"]["last_failure"] == retained
        assert not list(paths.db_dir.glob("*.db"))  # no bootstrap, PAPER book, or order work took place
    finally:
        if worker.process.poll() is None:
            worker.process.terminate()
            worker.process.wait(timeout=5)


def test_supervisor_shows_matching_failure_and_never_restarts_it(tmp_path, monkeypatch) -> None:
    paths = DeskPaths(tmp_path / "data", REPO / "config", tmp_path / "no-secrets")
    child = Mock(pid=os.getpid())
    child.poll.return_value = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr("desk.stocks.research_worker.subprocess.Popen", spawn)
    worker = ResearchWorker(paths)
    worker.start()
    worker_failure.record(paths, NameError("private detail"))
    child.poll.return_value = 1
    for _ in range(3):
        state = worker.maintain()
    assert "NameError" in state["error"] and "Evidence" in state["error"]
    assert "private detail" not in state["error"] and not state["running"]
    spawn.assert_called_once()
    child.pid += 1
    worker.blocked = False
    assert "NameError" not in worker.maintain()["error"]  # prior run is never attributed to a different child


@pytest.mark.parametrize("patch", [
    {"exception_type": "private-token"}, {"exception_type": []}, {"pid": True},
    {"frames": [{"file": "../../private.env", "line": 1}]},
    {"frames": [{"file": "stocks/cli.py", "line": "private-token"}]},
    {"at": "private-token"},
])
def test_untrusted_failure_records_do_not_reach_exports(tmp_path, patch) -> None:
    paths = DeskPaths(tmp_path, REPO / "config", tmp_path / "no-secrets")
    value = {"at": "2026-11-05T18:43:10+00:00", "pid": 1, "exception_type": "ValueError", "frames": []}
    (tmp_path / worker_failure.FILE).write_text(json.dumps({**value, **patch}))
    assert worker_failure.read(paths) is None


def test_integrity_budget_does_not_hide_source_health_or_history(tmp_path, monkeypatch) -> None:
    from tests.stocks_world import NOW, make_world

    world = make_world(tmp_path, n=12)
    real_connect = sqlite3.connect

    class InterruptedCheck(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA quick_check(1)":
                exc = sqlite3.OperationalError("interrupted")
                exc.sqlite_errorcode = sqlite3.SQLITE_INTERRUPT
                raise exc
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=InterruptedCheck)

    try:
        world.market.job("prices", NOW, error="HTTP 429 private-token")
        before = list(world.dbs.ledger.iterdump())
        monkeypatch.setattr("desk.stocks.diagnostics.sqlite3.connect", connect)
        result = diagnose(world.paths)
        assert result["databases"]["stocks-market.db"]["status"] == "integrity_check_time_budget_exceeded"
        assert result["databases"]["stocks-market.db"]["integrity"] == "not_checked"
        assert result["source_health"]["prices"]["error_category"] == "rate_limit"
        assert result["history"]["sessions"] > 60
        assert result["databases"]["stocks-ledger-paper.db"]["paper_mode_verified"]
        assert "private-token" not in json.dumps(result)
        assert list(world.dbs.ledger.iterdump()) == before
    finally:
        world.close()
