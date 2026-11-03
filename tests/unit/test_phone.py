"""KESTREL on a phone through Tailscale Serve: the apps accept exactly this computer's own Tailscale name (with the
HTTPS origin Serve gives the phone) and still refuse every other host; ``kestrel phone`` runs Serve and prints the
address; both kestrel scripts route the command to the computer that runs the app."""

import asyncio
import os
import secrets
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from desk.core.errors import DeskError
from desk.ops import launcher, phone
from desk.web.app import install_browser_boundary

NAME = "appstuff.tail1234.ts.net"


def guarded(lookups: list[str | None]) -> tuple[TestClient, str, list[int]]:
    calls: list[int] = []

    def lookup() -> str | None:
        calls.append(1)
        return lookups.pop(0) if lookups else NAME

    app = FastAPI()
    csrf = secrets.token_urlsafe(8)

    @app.get("/api/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/save")
    def save() -> dict[str, bool]:
        return {"saved": True}

    install_browser_boundary(app, csrf, tailnet=phone.TailnetHost(lookup))
    return TestClient(app, base_url=f"http://{NAME}"), csrf, calls


def test_the_apps_accept_this_computers_tailscale_name_and_nothing_else() -> None:
    client, csrf, calls = guarded([NAME])
    assert client.get("/api/ping").json() == {"ok": True}
    assert client.get("/api/ping").status_code == 200 and len(calls) == 1          # looked up once, then remembered
    phone_origin = {"origin": f"https://{NAME}", "x-csrf-token": csrf}           # Serve ends HTTPS on this computer
    assert client.post("/api/save", headers=phone_origin).json() == {"saved": True}
    assert client.post("/api/save", headers={**phone_origin, "origin": f"http://{NAME}"}).status_code == 403
    assert client.post("/api/save", headers={"origin": f"https://{NAME}"}).status_code == 403   # CSRF still needed
    for host in ("evil.example", "other.tail1234.ts.net", f"{NAME}.evil.example"):
        assert client.get("/api/ping", headers={"host": host}).status_code == 403
    assert len(calls) == 1                                      # another .ts.net name: at most one fresh look a minute
    local = client.get("/api/ping", headers={"host": "127.0.0.1:8767"})
    assert local.status_code == 200 and len(calls) == 1                          # this computer never looks anything up


def test_tailscale_started_after_the_app_is_picked_up_on_the_next_look() -> None:
    client, _, calls = guarded([None])
    assert client.get("/api/ping").status_code == 403
    assert len(calls) == 1
    host = phone.TailnetHost(lambda: NAME)
    host.next_check = 0.0                                                        # a minute later
    assert asyncio.run(host.accepts(NAME)) and not asyncio.run(host.accepts("x.example"))


def test_kestrel_phone_serves_the_app_privately_and_prints_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []

    def run(command: list[str], check: bool = False) -> SimpleNamespace:
        ran.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(phone.shutil, "which", lambda _: "/usr/bin/tailscale")
    text = phone.serve("stocks", 8767, run=run, lookup=lambda: NAME)
    assert ran[-1] == ["/usr/bin/tailscale", "serve", "--bg", "--https=443", "http://127.0.0.1:8767"]
    assert f"https://{NAME}/" in text and "kestrel phone off" in text and "funnel" not in " ".join(ran[-1]).lower()
    text = phone.serve("arbitrage", 8768, run=run, lookup=lambda: NAME)
    assert ran[-1][3] == "--https=8443" and f"https://{NAME}:8443/" in text and "kestrel phone arbitrage off" in text
    phone.serve("stocks", 8767, off=True, run=run, lookup=lambda: NAME)
    assert ran[-1] == ["/usr/bin/tailscale", "serve", "--https=443", "off"]
    with pytest.raises(DeskError, match="not signed in"):
        phone.serve("stocks", 8767, run=run, lookup=lambda: None)
    with pytest.raises(DeskError, match="operator"):
        phone.serve("stocks", 8767, run=lambda c, check=False: SimpleNamespace(returncode=1), lookup=lambda: NAME)
    monkeypatch.setattr(phone.shutil, "which", lambda _: None)
    with pytest.raises(DeskError, match=r"install\.sh"):
        phone.serve("stocks", 8767, run=run, lookup=lambda: NAME)


def test_tailscale_status_gives_the_name_only_while_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(['{"BackendState": "Running", "Self": {"DNSName": "AppStuff.tail1234.ts.net."}}',
                    '{"BackendState": "NeedsLogin", "Self": {"DNSName": ""}}', "not json"])
    monkeypatch.setattr(phone.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(phone.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=next(answers)))
    assert phone.tailnet_name() == NAME
    assert phone.tailnet_name() is None and phone.tailnet_name() is None


def test_both_kestrel_scripts_route_phone_to_the_computer_that_runs_the_app(tmp_path: Path) -> None:
    here = launcher.install_command(Path("/home/neo/kestrel"), tmp_path / "hp", uv="/home/neo/.local/bin/uv")
    assert 'phone) shift; exec /home/neo/.local/bin/uv run desk phone "$@"' in here.read_text()
    dell = launcher.install_command(tmp_path, tmp_path / "dell", uv="/bin/echo",
                                    remote="neo@192.168.0.10")
    assert subprocess.run(["sh", "-n", str(dell)], check=False).returncode == 0
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "ssh").write_text('#!/bin/sh\necho ssh "$@"\n')
    (fake / "ssh").chmod(0o700)
    env = {**os.environ, "PATH": f"{fake}:/usr/bin:/bin"}

    def kestrel(*words: str) -> str:
        return subprocess.run(["sh", str(dell), *words], capture_output=True, text=True, check=False,
                              env=env).stdout.strip()

    assert kestrel("phone") == "ssh -t neo@192.168.0.10 ~/.local/bin/kestrel phone"            # Stocks lives on the HP
    assert kestrel("phone", "off") == "ssh -t neo@192.168.0.10 ~/.local/bin/kestrel phone off"
    assert kestrel("phone", "arbitrage") == "run desk phone arbitrage"                         # Arbitrage runs here
    assert kestrel("phone", "arbitrage", "off") == "run desk phone arbitrage off"
    assert kestrel("phone", "a;rm") == "ssh -t neo@192.168.0.10 ~/.local/bin/kestrel phone"     # nothing passed along


def test_desk_phone_command_parses_its_words() -> None:
    from desk.cli import build_parser

    args = build_parser().parse_args(["phone", "arbitrage", "off"])
    assert args.func.__name__ == "cmd_phone" and set(args.words) == {"arbitrage", "off"}
    with pytest.raises(SystemExit):
        build_parser().parse_args(["phone", "everything"])
