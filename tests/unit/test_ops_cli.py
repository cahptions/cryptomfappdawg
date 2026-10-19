"""CLI operation wiring: explicit side effects, restore exclusion and worker lifetime locks."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desk import cli
from desk.core.errors import ConfigError, DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.core.worker_lock import WorkerBusy, WorkerLock
from desk.ops import backups, services, telegram
from desk.storage.databases import Databases


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeskPaths:
    paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "private.env")
    monkeypatch.setattr(cli, "_bootstrap", lambda _: (paths, []))
    monkeypatch.setattr(cli, "load_secrets", lambda _: Secrets())
    return paths


def arguments(*words: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(words)


@pytest.mark.parametrize("command,paper", [("up", False), ("up --paper", True), ("up --port 9877", False)])
def test_up_installs_and_starts_only_the_requested_services(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, command: str, paper: bool,
) -> None:
    calls = []
    monkeypatch.setattr(services, "install_services", lambda *args, **kwargs: calls.append((args, kwargs)) or [])
    args = arguments(*command.split())
    assert args.func(args) == 0
    assert calls == [((paths, sys.executable), {"port": args.port, "start": True, "paper": paper})]


@pytest.mark.parametrize("command,action", [("down", "stop"), ("services status", "status")])
def test_down_and_status_delegate_fixed_service_actions(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, command: str, action: str,
) -> None:
    calls = []
    monkeypatch.setattr(services, "control_services", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(services, "install_services", lambda *_, **__: pytest.fail("no installation requested"))
    args = arguments(*command.split())
    assert args.func(args) == 0
    assert calls == [((action,), {"paper": False})]


def test_service_install_respects_port_and_never_starts(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(services, "install_services", lambda *args, **kwargs: calls.append((args, kwargs)) or [])
    args = arguments("services", "install", "--port", "9876")
    assert args.func(args) == 0
    assert calls == [((paths, sys.executable), {"port": 9876, "start": False})]


def _manifest(paths: DeskPaths, capsys: pytest.CaptureFixture[str]) -> Path:
    Databases.open(paths).close()
    assert cli.cmd_backups(arguments("backups", "create")) == 0
    return Path(json.loads(capsys.readouterr().out)["manifest"])


def test_backup_cli_creates_verifies_and_restores_while_all_startup_locks_are_held(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _manifest(paths, capsys)
    assert cli.cmd_backups(arguments("backups", "verify", str(manifest))) == 0
    verified = json.loads(capsys.readouterr().out)
    assert set(verified["databases"]) == {"market", "research", "ledger-paper"}
    conn = sqlite3.connect(paths.db_dir / "ledger-paper.db")
    try:
        conn.execute("INSERT INTO ledger_meta(key,value) VALUES ('after_backup','new')")
        conn.commit()
    finally:
        conn.close()
    original = backups.restore_backup

    def guarded_restore(*args: Any, **kwargs: Any) -> Path | None:
        assert all(WorkerLock(paths, role).held() for role in ("entry-worker", "dashboard", "guardian", "telegram"))
        return original(*args, **kwargs)

    monkeypatch.setattr(backups, "restore_backup", guarded_restore)
    monkeypatch.setattr("builtins.input", lambda _: "RESTORE ledger-paper")
    assert cli.cmd_backups(arguments("backups", "restore", str(manifest), "--kind", "ledger-paper")) == 0
    previous = Path(json.loads(capsys.readouterr().out)["previous_backup"])
    assert backups.verify_backup(previous)["databases"]["ledger-paper"]
    with sqlite3.connect(paths.db_dir / "ledger-paper.db") as restored:
        assert restored.execute("SELECT value FROM ledger_meta WHERE key='after_backup'").fetchone() is None
    assert not any(WorkerLock(paths, role).held() for role in ("entry-worker", "dashboard", "guardian", "telegram"))


def test_restore_cli_requires_typed_confirmation_and_an_explicit_role(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _manifest(paths, capsys)
    monkeypatch.setattr(backups, "restore_backup", lambda *_, **__: pytest.fail("restore must not run"))
    with pytest.raises(DeskError, match="requires --kind"):
        cli.cmd_backups(arguments("backups", "restore", str(manifest)))
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert cli.cmd_backups(arguments("backups", "restore", str(manifest), "--kind", "ledger-paper")) == 1


@pytest.mark.parametrize("role", ["entry-worker", "dashboard", "guardian", "telegram"])
def test_restore_cli_rejects_a_running_worker(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], role: str,
) -> None:
    manifest = _manifest(paths, capsys)
    monkeypatch.setattr("builtins.input", lambda _: "RESTORE ledger-paper")
    monkeypatch.setattr(backups, "restore_backup", lambda *_, **__: pytest.fail("active worker must prevent restore"))
    with WorkerLock(paths, role), pytest.raises(DeskError, match="stop dashboard"):
        cli.cmd_backups(arguments("backups", "restore", str(manifest), "--kind", "ledger-paper"))


def test_restore_cli_refuses_cross_mode_target(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _manifest(paths, capsys)
    monkeypatch.setattr("builtins.input", lambda _: "RESTORE ledger-paper")
    with pytest.raises(DeskError, match="expected mode"):
        cli.cmd_backups(arguments("backups", "restore", str(manifest), "--kind", "ledger-paper", "--mode", "LIVE"))


@pytest.mark.parametrize("command", ["collect", "discover", "paper"])
def test_collector_commands_retain_entry_lock_until_completion(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    class FakeCollector:
        def __init__(self, *_: Any, **kwargs: Any) -> None:
            assert kwargs["paper"] is (command == "paper")
            assert kwargs["discovery"] is (command != "collect")
            self.stats = SimpleNamespace(run_id="fake", unhandled_exceptions=0)

        def stop(self) -> None:
            pass

        async def run(self, _: float) -> Any:
            assert WorkerLock(paths).held()
            return self.stats

    async def close() -> None:
        assert WorkerLock(paths).held()

    monkeypatch.setattr("desk.ops.collector.Collector", FakeCollector)
    monkeypatch.setattr("desk.ops.collector.render_stats", lambda _: "fake report")
    monkeypatch.setattr("desk.providers.factory.build_suite", lambda *_: SimpleNamespace(aclose=close))
    monkeypatch.setattr(cli, "_ensure_guardian", lambda *_: None)
    assert cli.cmd_collect(arguments(command, "--minutes", "1")) == 0
    assert not WorkerLock(paths).held()


def test_shadow_retains_entry_lock_and_duplicate_collection_cannot_reach_provider_startup(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_shadow(*_: Any) -> int:
        assert WorkerLock(paths).held()
        return 0

    monkeypatch.setattr(cli, "_run_shadow", run_shadow)
    assert cli.cmd_shadow(arguments("shadow")) == 0
    monkeypatch.setattr("desk.providers.factory.build_suite", lambda *_: pytest.fail("duplicate worker must not start"))
    with WorkerLock(paths), pytest.raises(WorkerBusy):
        cli.cmd_collect(arguments("collect"))


def test_telegram_report_is_local_and_never_loads_outbound_credentials(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "load_secrets", lambda _: pytest.fail("local report needs no outbound credentials"))
    monkeypatch.setattr(telegram, "poll_telegram", lambda *_: pytest.fail("report must not poll"))
    assert cli.cmd_telegram(arguments("telegram", "report")) == 0
    assert "PAPER" in capsys.readouterr().out


def test_explicit_telegram_poll_uses_allowlisted_chat_and_holds_worker_lock(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "123:" + "a" * 24
    monkeypatch.setattr(cli, "load_secrets", lambda _: Secrets({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "-77"}))
    called = []

    async def fake_poll(actual: DeskPaths, actual_token: str, chat: int, stop: asyncio.Event) -> None:
        assert actual == paths and actual_token == token and chat == -77
        assert WorkerLock(paths, "telegram").held()
        assert not stop.is_set()
        called.append(chat)

    monkeypatch.setattr(telegram, "poll_telegram", fake_poll)
    assert cli.cmd_telegram(arguments("telegram", "poll")) == 0
    assert called == [-77] and not WorkerLock(paths, "telegram").held()


def test_telegram_poll_without_credentials_fails_before_starting_worker(paths: DeskPaths) -> None:
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        cli.cmd_telegram(arguments("telegram", "poll"))


@pytest.mark.parametrize("token,chat", [("invalid-token", "7"), ("123:" + "a" * 24, "7; shell")])
def test_telegram_poll_refuses_malformed_credentials_as_configuration_errors(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch, token: str, chat: str,
) -> None:
    monkeypatch.setattr(cli, "load_secrets", lambda _: Secrets({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat}))
    monkeypatch.setattr(telegram, "poll_telegram", lambda *_: pytest.fail("invalid credentials must never start polling"))
    with pytest.raises(ConfigError, match="valid bot token"):
        cli.cmd_telegram(arguments("telegram", "poll"))
