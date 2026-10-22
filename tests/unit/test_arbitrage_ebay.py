"""Synthetic API and credential fixtures. No live account, token, listing or completed-sale evidence is used."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.arbitrage.contracts import Source
from desk.arbitrage.ebay import Backoff, Browse, parse
from desk.arbitrage.integrations import Credentials, load, save, status
from desk.arbitrage.repo import Repo
from desk.arbitrage.runner import initialize, spend_ebay_request
from desk.arbitrage.sources import validate_source
from desk.arbitrage.web import create_app
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths

NOW = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture
def paths(tmp_path: Path) -> DeskPaths:
    return DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private" / "arbitrage.json")


def credentials() -> Credentials:
    return Credentials(
        client_id="SYNTHETIC_CLIENT_ID", client_secret="SYNTHETIC_SECRET", enabled=True, free_access_confirmed=True
    )


def source() -> Source:
    return Source(id="fixture", url="https://www.ebay.com/", adapter="ebay_browse", query="synthetic query")


def listings() -> dict[str, object]:
    return {
        "total": 2,
        "itemSummaries": [
            {
                "itemId": "synthetic-1",
                "title": "SYNTHETIC API fixture, not an actual offer",
                "itemWebUrl": "https://www.ebay.com/itm/synthetic-fixture",
                "buyingOptions": ["FIXED_PRICE"],
                "price": {"value": "25.50", "currency": "USD"},
                "condition": "New",
            },
            {"itemId": "synthetic-auction", "title": "Auction fixture", "buyingOptions": ["AUCTION"]},
        ],
    }


def test_credentials_private_atomic_blank_preservation_and_no_echo(paths: DeskPaths) -> None:
    save(paths, credentials())
    assert stat.S_IMODE(paths.secrets_file.stat().st_mode) == 0o600
    save(paths, Credentials(enabled=True, free_access_confirmed=True))
    assert load(paths).client_secret == "SYNTHETIC_SECRET"
    assert "SYNTHETIC" not in json.dumps(status(paths))
    previous = paths.secrets_file.read_bytes()
    with pytest.raises(DeskError):
        save(paths, Credentials(client_secret="bad\ninput", enabled=True, free_access_confirmed=True))
    assert paths.secrets_file.read_bytes() == previous
    os.chmod(paths.secrets_file, 0o644)
    with pytest.raises(DeskError, match="mode 600"):
        load(paths)


def test_symlink_refusal_and_no_enable_without_confirmed_free_access(paths: DeskPaths) -> None:
    paths.secrets_file.parent.mkdir()
    target = paths.secrets_file.parent / "target"
    target.write_text("{}")
    paths.secrets_file.symlink_to(target)
    with pytest.raises(DeskError, match="symlinks"):
        save(paths, credentials())
    paths.secrets_file.unlink()
    with pytest.raises(DeskError, match="no added API charges"):
        save(paths, credentials().model_copy(update={"free_access_confirmed": False}))
    assert not paths.secrets_file.exists()


async def test_oauth_search_is_bounded_read_only_and_reuses_token() -> None:
    calls: list[httpx.Request] = []
    budget: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "SYNTHETIC_TOKEN", "expires_in": 7200})
        assert request.method == "GET" and request.url.host == "api.ebay.com"
        assert request.url.params["limit"] == "20"
        assert request.headers["Authorization"] == "Bearer SYNTHETIC_TOKEN"
        return httpx.Response(200, json=listings())

    browse = Browse(credentials(), lambda: budget.append(1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        first = await browse.search(client, source(), NOW)
        await browse.search(client, source(), NOW)
    assert len(calls) == len(budget) == 3 and calls[0].method == "POST"
    assert len(first) == 1 and first[0].availability == "DECLARED_AVAILABLE"
    assert first[0].shipping is None and not first[0].product.identity_confirmed


@pytest.mark.parametrize("code", [301, 401, 403, 429, 500])
async def test_errors_do_not_echo_tokens_or_follow_redirects(code: int) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            code, text="SYNTHETIC_SECRET", headers={"Location": "https://elsewhere.test/", "Retry-After": "7200"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeskError) as exc:
            await Browse(credentials(), lambda: None).search(client, source(), NOW)
    assert len(requests) == 1 and "SYNTHETIC_SECRET" not in str(exc.value)
    if code == 429:
        assert isinstance(exc.value, Backoff) and exc.value.until > datetime.now(UTC) + timedelta(hours=1)


async def test_disabled_connection_never_makes_a_request() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("No request authorized"))) as client:
        with pytest.raises(DeskError, match="disabled"):
            await Browse(Credentials(), lambda: None).search(client, source(), NOW)


def test_schema_changes_and_forced_product_matches_rejected() -> None:
    for data in (
        {},
        {"errors": [{"message": "private response"}]},
        {"itemSummaries": "bad"},
        {"itemSummaries": ["unexpected scalar"]},
    ):
        with pytest.raises(DeskError):
            parse(data, source(), NOW)
    assert parse({"total": 0}, source(), NOW) == []
    with pytest.raises(DeskError):
        validate_source(source().model_copy(update={"product_id": "forced-identity"}))


def test_quota_and_global_backoff_persist_across_restarts(paths: DeskPaths) -> None:
    initialize(paths)
    spend_ebay_request(paths)
    with Repo.open(paths) as repo:
        budget = json.loads(repo.conn.execute("SELECT value FROM arb_meta WHERE key='ebay-http-budget'").fetchone()[0])
        assert budget["requests"] == 1
        budget["requests"] = 2000
        repo.conn.execute("UPDATE arb_meta SET value=? WHERE key='ebay-http-budget'", (json.dumps(budget),))
    with pytest.raises(DeskError, match="2000"):
        spend_ebay_request(paths)
    with Repo.open(paths) as repo:
        repo.job("ebay-provider", {"retry_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()})
    with pytest.raises(Backoff):
        spend_ebay_request(paths)


def test_cache_turnover_evicts_only_untouched_unreferenced_api_products(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("desk.arbitrage.repo.MAX_PRODUCTS", 2)
    example = parse(listings(), source(), NOW)[0]
    with Repo.open(paths) as repo:
        repo.observe(example)
        protected = example.product.model_copy(update={"id": "ebay-protected", "title": "Reviewed fixture"})
        repo.save_product(protected, manual=True)
        replacement = example.model_copy(update={"product": example.product.model_copy(update={"id": "ebay-new"})})
        repo.observe(replacement)
        assert {p.id for p in repo.products()} == {"ebay-protected", "ebay-new"}
        assert not repo.observations(example.product.id)
        repo.save_product(replacement.product, manual=True)
        blocked = example.model_copy(update={"product": example.product.model_copy(update={"id": "ebay-cannot-evict-owned"})})
        with pytest.raises(DeskError, match="Protected catalog capacity"):
            repo.observe(blocked)
        assert {p.id for p in repo.products()} == {"ebay-protected", "ebay-new"}


def test_private_settings_save_needs_no_terminal_stop_and_echoes_no_values(paths: DeskPaths) -> None:
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1") as client:
        token = client.get("/api/session").json()["csrf_token"]
        headers = {"X-CSRF-Token": token}
        assert client.post("/api/settings", json={"enabled": False}, headers=headers).status_code == 200
        response = client.post("/api/integrations/ebay", json=credentials().model_dump(), headers=headers)
        assert response.status_code == 200 and "SYNTHETIC" not in response.text
        body = client.get("/api/snapshot").json()
        assert "SYNTHETIC_SECRET" not in json.dumps(body) and body["integrations"]["ebay"]["app_secret_set"]
        assert sum(s["adapter"] == "ebay_browse" for s in body["sources"]) == 3
        assert client.post("/api/integrations/ebay", json={"enabled": False}, headers=headers).status_code == 200
        assert load(paths).client_id == "SYNTHETIC_CLIENT_ID"
