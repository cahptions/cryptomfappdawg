"""Private source updates and ownership of the explicitly started research process."""

from __future__ import annotations

import asyncio
import signal
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.core.worker_lock import WorkerLock
from desk.stocks.connection import save_connection, save_sources
from desk.stocks.research_worker import ResearchWorker
from desk.stocks.schwab_auth import token_path
from tests.conftest import REPO


def test_sources_preserve_other_desks_and_schwab_token(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    original = "# my providers\nJUPITER_API_KEY=existing\nSCHWAB_APP_KEY=keep\nMASSIVE_API_KEY=old\n"
    paths.secrets_file.write_text(original)
    paths.secrets_file.chmod(0o600)
    token = token_path(paths)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("unmodified-token")
    save_sources(paths, massive_key="new", sec_contact="operator@example.org")
    secrets = load_secrets(paths.secrets_file)
    assert secrets.get("MASSIVE_API_KEY") == "new" and secrets.get("SEC_CONTACT_EMAIL") == "operator@example.org"
    assert secrets.get("JUPITER_API_KEY") == "existing" and secrets.get("SCHWAB_APP_KEY") == "keep"
    assert "# my providers" in paths.secrets_file.read_text() and token.read_text() == "unmodified-token"
    assert paths.secrets_file.stat().st_mode & 0o777 == 0o600
    before = paths.secrets_file.read_bytes()
    save_sources(paths)
    assert paths.secrets_file.read_bytes() == before
    for value in ("invalid", "bad\nNEW_KEY=inject", "no spaces@example.org"):
        with pytest.raises(DeskError):
            save_sources(paths, sec_contact=value)
        assert paths.secrets_file.read_bytes() == before


def test_sources_reject_public_or_symlink_secrets_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.env"
    target.write_text("JUPITER_API_KEY=keep\n")
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    paths.secrets_file.symlink_to(target)
    with pytest.raises(OSError):
        save_sources(paths, massive_key="new")
    paths.secrets_file.unlink()
    paths.secrets_file.write_text("JUPITER_API_KEY=keep\n")
    paths.secrets_file.chmod(0o644)
    with pytest.raises(DeskError):
        save_sources(paths, massive_key="new")
    assert paths.secrets_file.read_text() == target.read_text()


def test_identical_client_id_and_secret_are_rejected_before_writing(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    values = {"callback": "https://127.0.0.1:8182/oauth/callback", "provider": "schwab"}
    with pytest.raises(DeskError, match="must be different"):
        save_connection(paths, app_key="same-value", app_secret="same-value", **values)
    assert not paths.secrets_file.exists()
    save_connection(paths, app_key="client-id", app_secret="client-secret", **values)
    before = paths.secrets_file.read_bytes()
    with pytest.raises(DeskError, match="must be different"):
        save_connection(paths, app_key="client-secret", app_secret="", **values)
    assert paths.secrets_file.read_bytes() == before


def test_research_process_is_fixed_private_and_only_its_owner_can_pause(tmp_path: Path, monkeypatch: Any) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    child = Mock()
    child.poll.return_value = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr("desk.stocks.research_worker.subprocess.Popen", spawn)
    worker = ResearchWorker(paths)
    with WorkerLock(paths, "stocks-worker"):
        assert worker.status()["running"] and not worker.status()["owned"]
        assert not worker.stop()["owned"]
        child.send_signal.assert_not_called()
        with pytest.raises(DeskError):
            worker.start()
        spawn.assert_not_called()
    assert worker.start()["owned"]
    args, kwargs = spawn.call_args
    assert args[0][1:] == ["-m", "desk", "stocks", "collect", "--minutes", "1440"]
    assert kwargs["env"]["DESK_HOME"] == str(paths.home)
    assert kwargs["env"]["DESK_SECRETS_FILE"] == str(paths.secrets_file)
    assert (paths.logs_dir / "stocks-research.log").stat().st_mode & 0o777 == 0o600
    with pytest.raises(DeskError):
        worker.start()
    assert worker.stop()["stopping"]
    worker.stop()
    child.send_signal.assert_called_once_with(signal.SIGTERM)
    child.poll.return_value = 0
    assert not worker.status()["running"]


def test_collect_command_selects_research_only(tmp_path: Path, monkeypatch: Any) -> None:
    from desk.stocks import cli

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    monkeypatch.setattr(cli.DeskPaths, "from_env", lambda: paths)
    calls = []

    async def cycle(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"research_only": True}

    monkeypatch.setattr("desk.stocks.runner.cycle", cycle)
    assert cli.run(Namespace(stocks_command="collect", price_days=20, companies=20, options=5,
                             minutes=0.0000001, interval=300)) == 0
    assert len(calls) == 1 and calls[0]["research_only"] is True


def test_research_stop_interrupts_network_wait_and_finishes_cleanup_before_unlock(tmp_path: Path, monkeypatch: Any) -> None:
    from desk.stocks import cli

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    monkeypatch.setattr(cli.DeskPaths, "from_env", lambda: paths)
    handlers: dict[Any, Any] = {}
    original_loop = asyncio.get_running_loop
    cleaned = []

    def loop() -> Any:
        proxy = Mock(wraps=original_loop())
        proxy.add_signal_handler.side_effect = lambda sig, callback: handlers.update({sig: callback})
        return proxy

    async def cycle(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            assert kwargs["research_only"] and WorkerLock(paths, "stocks-worker").held()
            handlers[signal.SIGINT]()  # invoke the installed handler; do not signal pytest or any process
            await asyncio.Event().wait()  # a stalled request that would never finish a collection pass
            raise AssertionError("The pending request must be cancelled")
        finally:
            assert WorkerLock(paths, "stocks-worker").held()
            cleaned.append(True)

    monkeypatch.setattr(cli.asyncio, "get_running_loop", loop)
    monkeypatch.setattr("desk.stocks.runner.cycle", cycle)
    assert cli.run(Namespace(stocks_command="collect", price_days=20, companies=20, options=5,
                             minutes=1440, interval=300)) == 0
    assert cleaned == [True] and not WorkerLock(paths, "stocks-worker").held()
