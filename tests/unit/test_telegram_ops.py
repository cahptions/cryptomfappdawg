from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from desk.core.paths import DeskPaths
from desk.ops.telegram import build_daily_report, handle_update, poll_telegram
from desk.paper.ledger import PaperLedger
from desk.storage.databases import Databases


def update(chat: Any, text: str) -> dict[str, Any]:
    return {"update_id": 10, "message": {"chat": {"id": chat}, "text": text}}


def paths_at(tmp_path: Path) -> DeskPaths:
    return DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private.env")


@pytest.mark.parametrize("chat", [8, "7", True, None])
def test_other_chats_are_ignored_before_filesystem_access(tmp_path: Path, chat: Any) -> None:
    paths = paths_at(tmp_path)
    assert handle_update(update(chat, "/stop"), 7, paths) is None
    assert not paths.home.exists()


@pytest.mark.parametrize("command", ["/resume", "/arm", "/run rm -rf ~", "/stop extra", "/STOP", "x" * 101])
def test_commands_strictly_allowlisted(tmp_path: Path, command: str) -> None:
    paths = paths_at(tmp_path)
    assert handle_update(update(7, command), 7, paths) is None
    assert not paths.home.exists()


def test_stop_sets_file_and_persistent_paper_halt(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    dbs = Databases.open(paths)
    try:
        result = handle_update(update(7, "/stop"), 7, paths)
        assert result is not None and "PAPER" in result.text and "guardian" in result.text
        assert paths.stop_file.exists()
        assert PaperLedger(dbs.paper_ledger).halted() == "Telegram /stop"
        assert dbs.paper_ledger.execute("SELECT COUNT(*) FROM paper_events WHERE kind='HALT'").fetchone()[0] == 1
        assert "LIVE" not in result.text
    finally:
        dbs.close()


def test_report_is_readonly_and_missing_ledger_remains_absent(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    assert "PAPER" in build_daily_report(paths)
    assert not paths.home.exists()
    Databases.open(paths).close()
    result = handle_update(update(7, "/status"), 7, paths)
    assert result is not None and "Cash: 0.000000 SOL" in result.text
    assert not paths.stop_file.exists()


def test_stop_before_first_run_remains_effective(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    result = handle_update(update(7, "/stop"), 7, paths)
    assert result is not None and "STOPPED" in result.text
    assert paths.stop_file.exists()
    assert not paths.db_dir.exists()


async def test_opt_in_worker_sends_only_allowlisted_response_and_persists_offset(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    stop = asyncio.Event()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("getUpdates"):
            assert json.loads(request.content)["offset"] == 0
            return httpx.Response(200, json={"ok": True, "result": [
                {**update(999, "/stop"), "update_id": 10}, {**update(7, "/status"), "update_id": 11}]})
        assert request.url.path.endswith("sendMessage")
        body = json.loads(request.content)
        assert body["chat_id"] == 7 and "PAPER" in body["text"]
        stop.set()
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await poll_telegram(paths, "123:" + "a" * 24, 7, stop, client=client)
    assert len(requests) == 2
    assert not paths.stop_file.exists()
    assert json.loads((paths.home / "telegram-offset.json").read_text()) == {"offset": 12}


async def test_network_errors_never_log_token_bearing_url(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    paths = paths_at(tmp_path)
    stop = asyncio.Event()
    token = "123:" + "sensitive" * 3

    async def handler(request: httpx.Request) -> httpx.Response:
        stop.set()
        raise httpx.ConnectError(str(request.url), request=request)

    caplog.set_level(logging.DEBUG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await poll_telegram(paths, token, 7, stop, client=client)
    assert token not in caplog.text
    assert "Telegram connection failed" in caplog.text


async def test_bad_configuration_cannot_start_network_worker(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    with pytest.raises(ValueError, match="valid Telegram"):
        await poll_telegram(paths, "bad", 7, asyncio.Event())
    assert not paths.home.exists()
