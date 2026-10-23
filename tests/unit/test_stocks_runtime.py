"""An updated page must not be paired with an older server or silently reused by its launcher."""

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.ops import dashboard_restart, launcher
from desk.stocks import runtime, web
from desk.stocks.cli import run
from tests.conftest import REPO


def test_served_interface_is_frozen_with_its_server_and_update_is_detected(tmp_path, monkeypatch):
    static = tmp_path / "assets"
    static.mkdir()
    (static / "index.html").write_text("<h1>Original interface</h1>")
    (static / "stocks.js").write_text("// original interface")
    monkeypatch.setattr(web, "STATIC", static)
    revision = ["original"]
    monkeypatch.setattr(web, "source_revision", lambda: revision[0])
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private.env")
    running = web.create_app(paths, auto_start=False)
    # This models git pull while Python still has its original route handlers loaded.
    (static / "index.html").write_text("<h1>Updated interface</h1>")
    (static / "stocks.js").write_text("// updated interface")
    revision[0] = "updated"
    with TestClient(running, base_url="http://127.0.0.1:8767") as client:
        assert client.get("/").text == "<h1>Original interface</h1>"
        asset = client.get("/assets/stocks.js")
        assert asset.text == "// original interface" and "javascript" in asset.headers["content-type"]
        assert client.get("/assets/not-a-file.js").status_code == 404
        status = client.get("/api/stocks/runtime").json()
        assert status["running_revision"] == "original" and status["installed_revision"] == "updated"
        assert status["restart_required"] is True and str(paths.home) not in json.dumps(status)
        assert client.get("/api/stocks/session").json()["ui_protocol"] == runtime.UI_PROTOCOL
    with TestClient(web.create_app(paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        assert client.get("/").text == "<h1>Updated interface</h1>"
        assert client.get("/assets/stocks.js").text == "// updated interface"
        assert client.get("/api/stocks/runtime").json()["restart_required"] is False


def test_source_revision_changes_when_python_or_interface_changes_but_ignores_data(tmp_path):
    code = tmp_path / "src" / "desk"
    static = code / "stocks" / "static"
    static.mkdir(parents=True)
    (code / "test.py").write_text("old = True")
    page = static / "index.html"
    page.write_text("original")
    first = runtime.source_revision(tmp_path)
    page.write_text("updated")
    second = runtime.source_revision(tmp_path)
    assert first != second
    (tmp_path / "secrets.env").write_text("THIS_FILE_MUST_NOT_BE_READ")
    assert runtime.source_revision(tmp_path) == second
    (code / "test.py").write_text("old = False")
    assert runtime.source_revision(tmp_path) != second


@pytest.mark.parametrize("payload,expected", [
    ({"application": "kestrel-stocks", "protocol": 1, "running_revision": "installed"}, True),
    ({"application": "kestrel-stocks", "protocol": 1, "running_revision": "previous"}, False),
    ({"application": "another-app", "protocol": 1, "running_revision": "installed"}, False),
    ({"application": "kestrel-stocks", "protocol": 0, "running_revision": "installed"}, False),
    ({}, False),
    ([], False),
])
def test_runtime_probe_checks_identity_contract_and_running_source(monkeypatch, payload, expected):
    monkeypatch.setattr(runtime, "source_revision", lambda: "installed")
    fetch = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(runtime.urllib.request, "urlopen", fetch)
    assert runtime.server_is_current("http://127.0.0.1:8767/") is expected
    fetch.assert_called_once_with("http://127.0.0.1:8767/api/stocks/runtime", timeout=2)


def test_legacy_missing_runtime_and_unreadable_or_oversized_response_are_not_current(monkeypatch):
    monkeypatch.setattr(runtime.urllib.request, "urlopen", Mock(side_effect=urllib.error.HTTPError(
        "http://127.0.0.1:8767/api/stocks/runtime", 404, "Not Found", {}, None)))
    assert not runtime.server_is_current("http://127.0.0.1:8767")
    for payload in (b"not JSON", b" " * 4097):
        monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda *_, payload=payload, **__: io.BytesIO(payload))
        assert not runtime.server_is_current("http://127.0.0.1:8767")


@pytest.mark.parametrize("current", [True, False])
def test_stocks_icon_reuses_current_server_and_restarts_only_a_stale_one(tmp_path, monkeypatch, current):
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private.env")
    paths.ensure()
    prefs = paths.home / "stocks-automation.json"
    prefs.write_text('{"enabled": false}')
    actions = []
    checks = iter([True, True] if current else [True, False, True])
    versions = iter([True, True] if current else [False, True])
    monkeypatch.setattr(launcher, "responding", lambda *_: next(checks))
    monkeypatch.setattr(runtime, "server_is_current", lambda *_: next(versions))
    monkeypatch.setattr(dashboard_restart, "restart_dashboard", lambda p, desk: actions.append(("restart", p, desk)))
    monkeypatch.setattr(launcher, "_start_dashboard", lambda d, p: actions.append(("start", d, p)))
    monkeypatch.setattr(launcher, "open_window", lambda *_: actions.append(("window",)))
    assert launcher.open_desk("stocks", 8767, paths) == "http://127.0.0.1:8767/"
    assert actions == ([("window",)] if current else [("restart", paths, "stocks"), ("start", "stocks", 8767), ("window",)])
    assert prefs.read_text() == '{"enabled": false}'


def test_stale_probe_cannot_stop_a_non_dashboard_lock_owner(tmp_path: Path, monkeypatch):
    from argparse import Namespace

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private.env")
    monkeypatch.setattr(DeskPaths, "from_env", lambda: paths)
    monkeypatch.setattr(launcher, "responding", lambda *_: True)
    monkeypatch.setattr(runtime, "server_is_current", lambda *_: False)
    with WorkerLock(paths, "stocks-dashboard"), pytest.raises(DeskError, match="no process was stopped"):
        run(Namespace(stocks_command="dashboard", port=None, open=True, restart=False))
