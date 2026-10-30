"""One computer showing another's Stocks desk: the main launcher, `kestrel` over SSH and a reused tunnel."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import pytest

from desk import cli
from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.ops import launcher

HP = "neo@192.168.0.10"


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeskPaths:
    paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "private.env")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(cli, "_bootstrap", lambda _: (paths, []))
    monkeypatch.setattr(cli, "load_secrets", lambda _: Secrets())
    return paths


@pytest.mark.parametrize("host", ["-oProxyCommand=touch /tmp/x", "neo@hp;rm -rf ~", "neo@", "", "neo@hp name"])
def test_only_plain_user_at_address_is_accepted(host: str) -> None:
    with pytest.raises(DeskError, match="user@address"):
        launcher.check_host(host)


def test_the_choice_is_remembered_until_turned_off(paths: DeskPaths) -> None:
    assert launcher.remote_host(paths) is None
    launcher.set_remote_host(paths, HP)
    assert launcher.remote_host(paths) == HP
    launcher.remote_path(paths).write_text("-oProxyCommand=evil\n")      # a hand-edited file is ignored, not run
    assert launcher.remote_host(paths) is None
    launcher.set_remote_host(paths, None)
    assert not launcher.remote_path(paths).exists()


def test_the_main_launcher_says_which_computer_and_is_baby_blue(paths: DeskPaths, tmp_path: Path) -> None:
    launcher.set_remote_host(paths, HP)
    share = tmp_path / "share"
    launcher.install_launchers(paths, "/usr/bin/python3", desks=("stocks",), data_home=share)
    entry = (share / "applications" / "kestrel-stocks.desktop").read_text()
    assert "Name=KESTREL Stocks\n" in entry and f"Comment=Stock and options research hub (PAPER) · on {HP}" in entry
    assert "-m desk open --desk stocks --port 8767" in entry                   # the saved choice decides where
    assert 'fill="#89cff0"' in (share / "icons" / "hicolor" / "scalable" / "apps" / "kestrel-stocks.svg").read_text()


def test_remote_kestrel_runs_each_command_on_the_other_computer(tmp_path: Path) -> None:
    script = launcher.install_command(Path("/home/neo/kestrel"), tmp_path / "bin", uv="/home/neo/.local/bin/uv",
                                      remote=HP)
    assert subprocess.run(["sh", "-n", str(script)], check=False).returncode == 0
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "ssh").write_text('#!/bin/sh\necho "$@"\n')
    (fake / "ssh").chmod(0o700)
    env = {**os.environ, "PATH": f"{fake}:/usr/bin:/bin"}
    for action in ("update", "signin", "restart"):
        ran = subprocess.run(["sh", str(script), action], capture_output=True, text=True, check=False, env=env)
        assert ran.stdout.strip() == f"-t {HP} ~/.local/bin/kestrel {action}"
    ran = subprocess.run(["sh", str(script)], capture_output=True, text=True, check=False, env=env)
    assert ran.stdout.strip() == f"-t {HP} ~/.local/bin/kestrel status"
    assert "run desk open --desk stocks" in script.read_text()
    assert subprocess.run(["sh", str(script), "nonsense"], capture_output=True, check=False, env=env).returncode == 2


def _world(monkeypatch: pytest.MonkeyPatch, *, answering: set[int] = frozenset(), listening: set[int] = frozenset(),
           ssh: list[int] | None = None, tunnel_answers: bool = True) -> dict:
    """``answering``/``listening``: local ports; ``ssh``: exit codes of the ping/start calls in order."""
    seen: dict = {"commands": [], "windows": []}
    codes = list(ssh or [])
    monkeypatch.setattr(launcher, "responding", lambda url: int(url.rsplit(":", 1)[1].strip("/")) in answering)
    monkeypatch.setattr(launcher, "_listening", lambda port: port in listening)
    monkeypatch.setattr(launcher, "_answers", lambda url, seconds: tunnel_answers)
    monkeypatch.setattr(launcher, "notify", lambda text: None)

    def quiet(command: list[str], timeout: float = 30) -> int:
        seen["commands"].append(command)
        return 0 if "-L" in command else codes.pop(0)

    monkeypatch.setattr(launcher, "_quiet", quiet)
    monkeypatch.setattr(launcher, "open_window", lambda url, desk, profiles: seen["windows"].append(url))
    return seen


def test_a_tunnel_that_already_answers_is_reused(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _world(monkeypatch, answering={18768})
    assert launcher.remote_window(HP, 8767, paths) == "http://127.0.0.1:18768/"
    assert seen["commands"] == []


def test_the_hp_is_asked_directly_then_a_background_tunnel_opens(paths: DeskPaths,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _world(monkeypatch, ssh=[0])
    assert launcher.remote_window(HP, 8767, paths) == "http://127.0.0.1:18767/"
    ping, tunnel = seen["commands"]
    assert ping[:-1] == [*launcher.SSH, HP] and "http://127.0.0.1:8767/" in ping[-1]
    assert "BatchMode=yes" in tunnel and "ExitOnForwardFailure=yes" in tunnel
    assert tunnel[-5:] == ["-f", "-N", "-L", "18767:127.0.0.1:8767", HP]
    assert seen["windows"] == ["http://127.0.0.1:18767/"]


def test_a_stuck_old_tunnel_on_18767_is_stepped_over(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _world(monkeypatch, listening={18767}, ssh=[0])
    assert launcher.remote_window(HP, 8767, paths) == "http://127.0.0.1:18768/"
    assert seen["commands"][-1][-2] == "18768:127.0.0.1:8767"


def test_a_stopped_app_is_started_there_once(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _world(monkeypatch, ssh=[1, 0, 0])                   # not answering, start, answering
    launcher.remote_window(HP, 8767, paths)
    assert seen["commands"][1] == [*launcher.SSH, HP, launcher._START] and "kestrel restart" in launcher._START
    assert seen["windows"] == ["http://127.0.0.1:18767/"]
    seen = _world(monkeypatch, ssh=[1, 1, 1])                   # still not answering: say how to update it there
    with pytest.raises(DeskError, match=r"did not start\. Update it there once"):
        launcher.remote_window(HP, 8767, paths)
    assert seen["windows"] == []


def test_an_unreachable_computer_is_named_and_no_window_opens(paths: DeskPaths,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _world(monkeypatch, ssh=[255])
    with pytest.raises(DeskError, match=f"Could not reach {HP}"):
        launcher.remote_window(HP, 8767, paths)
    assert seen["windows"] == [] and len(seen["commands"]) == 1
    _world(monkeypatch, ssh=[0], tunnel_answers=False)
    with pytest.raises(DeskError, match="connection to it from here did not open"):
        launcher.remote_window(HP, 8767, paths)


def test_desk_open_remote_sets_up_once_then_every_open_goes_there(paths: DeskPaths, tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(launcher, "ssh_key_setup", lambda host: calls.append(("key", host)))
    monkeypatch.setattr(launcher, "remote_window", lambda host, port, p: calls.append(("window", host, port))
                        or "http://127.0.0.1:18767/")
    monkeypatch.setattr(launcher, "open_desk", lambda *a, **k: pytest.fail("this computer's own app stays closed"))
    monkeypatch.setattr("desk.ops.dashboard_restart.restart_dashboard",
                        lambda p, desk: calls.append(("stop local", desk)))

    def run(*words: str) -> None:
        args: argparse.Namespace = cli.build_parser().parse_args(words)
        assert args.func(args) == 0

    run("open", "--desk", "stocks", "--install", "--remote", HP)
    assert calls == [("key", HP), ("stop local", "stocks"), ("window", HP, 8767)]
    kestrel = (tmp_path / "home" / ".local" / "bin" / "kestrel").read_text()
    assert f"exec ssh -t {HP}" in kestrel
    calls.clear()
    run("open", "--desk", "stocks")                                   # the menu launcher: no setup, same place
    assert calls == [("window", HP, 8767)]
    monkeypatch.setattr(launcher, "open_desk", lambda *a, **k: calls.append(("local",)) or "http://127.0.0.1:8767/")
    run("open", "--desk", "stocks", "--install", "--remote", "off")
    assert launcher.remote_host(paths) is None and calls[-1] == ("local",)
    assert "exec ssh" not in (tmp_path / "home" / ".local" / "bin" / "kestrel").read_text()
