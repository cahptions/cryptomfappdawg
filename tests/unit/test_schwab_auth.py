"""Offline OAuth tests: state/URL matching, private atomic tokens, rotation and concurrent refresh."""

from __future__ import annotations

import asyncio
import json
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from desk.core.config import StocksSection
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets, load_secrets
from desk.stocks.connection import LoginAttempt, local_tls, preferences, save_connection
from desk.stocks.schwab_auth import (
    SchwabAuthError,
    SchwabLoginExpired,
    SchwabTokens,
    authorization_url,
    callback_code,
    token_path,
)
from tests.conftest import REPO

CALLBACK = "https://127.0.0.1:8182/oauth/callback"
CREDS = Secrets(values={"SCHWAB_APP_KEY": "test-app-key", "SCHWAB_APP_SECRET": "test-app-secret"})


def stored(tokens: SchwabTokens, *, lifetime: float = 1000) -> dict:
    value = {"format": 1, "access_token": "old-access", "refresh_token": "old-refresh",
             "expires_at": time.time() + lifetime, "authorized_at": time.time() - 600}
    tokens.save(value)
    return value


def test_callback_state_exact_address_and_one_decoding_pass() -> None:
    url = authorization_url("public-app", CALLBACK, "fresh-state")
    assert parse_qs(urlsplit(url).query)["redirect_uri"] == [CALLBACK]
    returned = CALLBACK + "?" + urlencode({"state": "fresh-state", "code": "one%2B+encoded"})
    assert callback_code(returned, CALLBACK, "fresh-state") == "one%2B+encoded"
    for bad in (returned.replace("8182", "8183"), returned.replace("fresh-state", "old-state"),
                returned + "&code=second", returned + "&state=fresh-state", returned.replace("https:", "http:"),
                returned.replace("/oauth/callback", "/other"), returned + "#fragment"):
        with pytest.raises(SchwabAuthError, match="did not match"):
            callback_code(bad, CALLBACK, "fresh-state")
    with pytest.raises(SchwabAuthError, match="declined"):
        callback_code(CALLBACK + "?state=fresh-state&error=access_denied", CALLBACK, "fresh-state")


def test_no_http_callback_or_embedded_credentials() -> None:
    for callback in ("http://127.0.0.1", "https://user:secret@example.com", "https://example.com?code=secret",
                     "https://example.com/#fragment", "https://example.com:99999", "https://example.com/ with-space"):
        with pytest.raises(SchwabAuthError):
            authorization_url("key", callback, "state")


async def test_token_exchange_and_refresh_rotation_do_not_use_evidence_http(tmp_path: Path) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == "https://api.schwabapi.com/v1/oauth/token"
        assert request.headers["authorization"].startswith("Basic ")
        fields = parse_qs(request.content.decode())
        if len(requests) == 1:
            assert fields["redirect_uri"] == [CALLBACK] and fields["code"] == ["once%2Bencoded"]
            return httpx.Response(200, json={"access_token": "first", "refresh_token": "rotate-1", "expires_in": 1})
        assert fields == {"grant_type": ["refresh_token"], "refresh_token": ["rotate-1"]}
        return httpx.Response(200, json={"access_token": "SECRET-SECOND-ACCESS", "refresh_token": "rotate-2", "expires_in": 1800})

    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS, transport=httpx.MockTransport(respond))
    tokens.exchange("once%2Bencoded", CALLBACK)
    initial = tokens.read()
    assert stat.S_IMODE(tokens.path.stat().st_mode) == 0o600
    assert await tokens.headers() == {"Authorization": "Bearer SECRET-SECOND-ACCESS"}
    assert tokens.read()["refresh_token"] == "rotate-2"
    assert tokens.read()["authorized_at"] == initial["authorized_at"]
    assert await tokens.headers() == {"Authorization": "Bearer SECRET-SECOND-ACCESS"}
    assert len(requests) == 2
    assert "SECRET-SECOND-ACCESS" not in json.dumps(tokens.status()) and "rotate-2" not in json.dumps(tokens.status())


def test_concurrent_process_style_refresh_rereads_disk_under_lock(tmp_path: Path) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800})

    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS, transport=httpx.MockTransport(respond))
    stored(tokens, lifetime=-1)
    instances = [SchwabTokens(tokens.path, CREDS, transport=tokens.transport) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(lambda instance: instance._headers(), instances))
    assert values == [{"Authorization": "Bearer new-access"}] * 6
    assert len(calls) == 1


@pytest.mark.parametrize("response", [httpx.Response(400, text="secret-echo-old-refresh"),
                                     httpx.Response(200, json={"access_token": "broken", "expires_in": -1}),
                                     httpx.Response(302, headers={"Location": "https://elsewhere.invalid"})])
def test_failed_refresh_keeps_the_old_token_and_suppresses_response(tmp_path: Path, response: httpx.Response) -> None:
    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS, transport=httpx.MockTransport(lambda _: response))
    previous = stored(tokens, lifetime=-1)
    with pytest.raises(SchwabAuthError) as error:
        tokens._headers()
    assert "secret-echo" not in str(error.value)
    assert tokens.read() == previous


def test_private_files_and_symlinks(tmp_path: Path) -> None:
    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS)
    stored(tokens)
    tokens.path.chmod(0o644)
    with pytest.raises(SchwabAuthError, match="private"):
        tokens.read()
    other = tmp_path / "other"
    other.write_text("do not touch")
    tokens.path.unlink()
    tokens.path.symlink_to(other)
    with pytest.raises(SchwabAuthError):
        tokens.save({})
    with pytest.raises(SchwabAuthError):
        tokens.read()
    assert other.read_text() == "do not touch"


def test_settings_preserve_other_keys_blank_fields_and_invalidate_changed_app(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private" / "secrets.env")
    paths.secrets_file.parent.mkdir()
    paths.secrets_file.write_text("# preserve\nJUPITER_API_KEY='keep-me'\n")
    paths.secrets_file.chmod(0o600)
    save_connection(paths, app_key="new", app_secret="private", callback=CALLBACK, provider="schwab")
    credentials = load_secrets(paths.secrets_file)
    assert credentials.get("JUPITER_API_KEY") == "keep-me" and credentials.get("SCHWAB_APP_SECRET") == "private"
    tokens = SchwabTokens(token_path(paths), credentials)
    stored(tokens)
    save_connection(paths, app_key="", app_secret="", callback=CALLBACK, provider="schwab")
    assert tokens.path.exists() and load_secrets(paths.secrets_file).get("SCHWAB_APP_SECRET") == "private"
    save_connection(paths, app_key="different", app_secret="", callback=CALLBACK, provider="schwab")
    assert not tokens.path.exists()
    assert preferences(paths, StocksSection())["options_provider"] == "schwab"
    assert stat.S_IMODE(paths.secrets_file.stat().st_mode) == 0o600


def test_callback_listener_state_rejection_does_not_consume_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS)
    exchanges = []
    monkeypatch.setattr(tokens, "exchange", lambda code, callback: exchanges.append((code, callback)))
    attempt = LoginAttempt(tokens, "https://test-node.tailnet.ts.net/oauth/callback", "public-app", port=port)
    try:
        attempt.start()
        with httpx.Client(trust_env=False) as client:
            assert client.get(f"http://127.0.0.1:{port}/oauth/callback?state=bad&code=do-not-use").status_code == 400
            assert not attempt.complete.is_set() and exchanges == []
            query = urlencode({"state": attempt.state, "code": "valid"})
            response = client.get(f"http://127.0.0.1:{port}/oauth/callback?" + query)
        assert response.status_code == 200 and "valid" not in response.text
        assert exchanges == [("valid", attempt.callback)]
    finally:
        attempt.stop()


def test_loopback_https_works_without_a_domain(tmp_path: Path) -> None:
    import socket

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private" / "secrets.env")
    context = local_tls(paths)
    assert stat.S_IMODE((paths.secrets_file.parent / "schwab-loopback.key").stat().st_mode) == 0o600
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    attempt = LoginAttempt(SchwabTokens(token_path(paths), CREDS), f"https://127.0.0.1:{port}/oauth/callback",
                           "public-app", port=port, timeout=1, tls=context)
    try:
        attempt.start()
        # Only this synthetic client's one-use local certificate check is bypassed, never broker HTTPS.
        with httpx.Client(trust_env=False, verify=False) as client:
            assert client.get(f"https://127.0.0.1:{port}/wrong").status_code == 400
    finally:
        attempt.stop()


async def test_async_refresh_does_not_block_event_loop(tmp_path: Path) -> None:
    def response(_: httpx.Request) -> httpx.Response:
        time.sleep(0.1)
        return httpx.Response(200, json={"access_token": "new", "expires_in": 1800})

    tokens = SchwabTokens(tmp_path / "token.json", CREDS, transport=httpx.MockTransport(response))
    stored(tokens, lifetime=-1)
    task = asyncio.create_task(tokens.headers())
    await asyncio.sleep(0.02)
    assert not task.done()
    assert await task == {"Authorization": "Bearer new"}
    assert tokens.read()["refresh_token"] == "old-refresh"


def test_expired_seven_day_login_is_named_once_and_not_retried_until_a_new_login(tmp_path: Path) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(parse_qs(request.content.decode())["grant_type"][0])
        if calls[-1] == "refresh_token":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "echo old-refresh"})
        return httpx.Response(200, json={"access_token": "fresh", "refresh_token": "r2", "expires_in": 1800})

    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS, transport=httpx.MockTransport(respond))
    previous = stored(tokens, lifetime=-1)
    with pytest.raises(SchwabLoginExpired) as error:
        tokens._headers()
    assert "7 days" in str(error.value) and "invalid_grant" in str(error.value) and "echo" not in str(error.value)
    assert tokens.read() == previous
    status = tokens.status()
    assert status["relogin_required"] and not status["connected"] and "Start browser authorization" in status["reason"]
    with pytest.raises(SchwabLoginExpired):
        tokens._headers()
    assert calls == ["refresh_token"]  # no repeated refusals while waiting for the operator
    tokens.exchange("code", CALLBACK)
    assert not tokens.relogin_required() and tokens.status()["connected"]
    assert tokens._headers() == {"Authorization": "Bearer fresh"}


def test_status_counts_down_the_seven_day_login_window(tmp_path: Path) -> None:
    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS)
    value = stored(tokens)
    tokens.save({**value, "authorized_at": time.time() - 6.5 * 86400})
    status = tokens.status()
    assert status["connected"] and 0.4 < status["login_days_left"] < 0.6 and "days left" in status["note"]
    tokens.save({**value, "authorized_at": time.time() - 7.1 * 86400})
    status = tokens.status()
    assert status["relogin_required"] and not status["connected"] and status["login_days_left"] == 0


@pytest.mark.parametrize("grant,response,expected", [
    ("authorization_code", httpx.Response(401, json={"error": "invalid_client"}), "app key or secret"),
    ("authorization_code", httpx.Response(400, json={"error": "invalid_request"}), "login code"),
])
def test_refusals_say_what_to_fix_without_marking_a_relogin(tmp_path: Path, grant: str, response: httpx.Response,
                                                            expected: str) -> None:
    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS, transport=httpx.MockTransport(lambda _: response))
    stored(tokens, lifetime=-1)
    with pytest.raises(SchwabAuthError) as error:
        tokens._headers() if grant == "refresh_token" else tokens.exchange("code", CALLBACK)
    assert expected in str(error.value) and not tokens.relogin_required()


def test_a_refused_refresh_is_not_retried_even_as_invalid_client(tmp_path: Path) -> None:
    """Schwab answers an expired refresh token with invalid_client: asking again on every request cannot help."""
    calls = []

    def respond(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "invalid_client"})

    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS, transport=httpx.MockTransport(respond))
    stored(tokens, lifetime=-1)
    for _ in range(3):
        with pytest.raises(SchwabLoginExpired):
            tokens._headers()
    assert len(calls) == 1 and tokens.relogin_required()


def test_an_early_ending_says_when_and_points_at_the_other_computer(tmp_path: Path) -> None:
    tokens = SchwabTokens(tmp_path / "tokens.json", CREDS, transport=httpx.MockTransport(
        lambda _: httpx.Response(400, json={"error": "invalid_grant"})))
    now = time.time()
    tokens.save({"format": 1, "access_token": "a", "refresh_token": "r", "expires_at": now - 60,
                 "authorized_at": now - 5 * 3600, "refreshed_at": now - 1860})
    with pytest.raises(SchwabLoginExpired) as error:
        tokens._headers()
    assert "5.0 hours after you signed in here" in str(error.value) and "another computer" in str(error.value)
    status = tokens.status()
    assert status["relogin_required"] and status["ended_early"] and status["ended_after_hours"] == 5.0
    assert status["error"] == "invalid_grant" and "HTTP 400, invalid_grant" in status["reason"]
    assert status["last_ok_at"] < status["ended_at"] and "between" in status["reason"]
    # Near the end of the 7 days it is the ordinary weekly login, named as such.
    stale = SchwabTokens(tmp_path / "old.json", CREDS, transport=httpx.MockTransport(
        lambda _: httpx.Response(400, json={"error": "invalid_grant"})))
    stale.save({"format": 1, "access_token": "a", "refresh_token": "r", "expires_at": now - 60,
                "authorized_at": now - 6.9 * 86400})
    with pytest.raises(SchwabLoginExpired):
        stale._headers()
    assert not stale.status()["ended_early"] and "every 7 days" in stale.status()["reason"]


def test_kestrel_signin_runs_beside_the_collector_once_schwab_is_chosen(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """The weekly sign-in over SSH changes no setting, so it does not wait for (or stop) the running collector."""
    from argparse import Namespace

    from desk.core.worker_lock import WorkerBusy, WorkerLock
    from desk.stocks.cli import run

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private" / "secrets.env")
    paths.secrets_file.parent.mkdir()
    monkeypatch.setattr(DeskPaths, "from_env", lambda: paths)
    monkeypatch.setattr("secrets.token_urlsafe", lambda _: "fixed-state")
    monkeypatch.setattr("getpass.getpass", lambda _: f"{CALLBACK}?code=abc&state=fixed-state")
    exchanged: list[tuple[str, str]] = []
    monkeypatch.setattr(SchwabTokens, "exchange", lambda self, code, callback: exchanged.append((code, callback)))
    signin = Namespace(stocks_command="connect", callback_url=None, manual=True, listen_port=None)
    # The first connection (choosing Schwab) still waits for the collector.
    with WorkerLock(paths, "stocks-worker"), pytest.raises(WorkerBusy):
        run(signin)
    save_connection(paths, app_key="test-app-key", app_secret="test-app-secret", callback=CALLBACK, provider="schwab")
    with WorkerLock(paths, "stocks-worker"):
        assert run(signin) == 0
    assert exchanged == [("abc", CALLBACK)]
