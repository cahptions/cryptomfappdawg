"""New M2 HTTP contracts use real saved fixtures and never fetch token-supplied URLs."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.providers.factory import build_suite
from desk.storage.databases import Databases
from tests.conftest import load_fixture


async def test_shield_fixture_parses_and_missing_mint_is_schema_failure(tmp_path: Path) -> None:
    mint = "ArpPsesHXKbGeJsHdquM6acT3uNs7BChyetJjiNYpump"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/ultra/v1/shield"
        body = (load_fixture("provider/jupiter_ultra_v1_shield.json")
                if request.url.params["mints"] == mint else b'{"warnings":{}}')
        return httpx.Response(200, content=body)

    paths = DeskPaths(tmp_path / "home", REPO_ROOT / "config", tmp_path / "none.env")
    dbs = Databases.open(paths)
    suite = build_suite(paths, load_config(paths.config_dir), Secrets(), dbs,
                        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        result = await suite.jupiter.shield(mint)
        assert [(w.type, w.severity) for w in result.warnings] == [("NOT_VERIFIED", "info"), ("NEW_LISTING", "info")]
        with pytest.raises(ProviderError) as exc:
            await suite.jupiter.shield("So11111111111111111111111111111111111111112")
        assert exc.value.kind is ProviderErrorKind.SCHEMA
        assert len(calls) == 2
    finally:
        await suite.aclose()
        dbs.close()


async def test_profiles_are_flagged_and_remote_metadata_is_not_fetched(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.url.host == "api.dexscreener.com" and request.url.path == "/token-profiles/latest/v1"
        return httpx.Response(200, content=load_fixture("provider/dexscreener_token_profiles_latest.json"))

    paths = DeskPaths(tmp_path / "home", REPO_ROOT / "config", tmp_path / "none.env")
    dbs = Databases.open(paths)
    suite = build_suite(paths, load_config(paths.config_dir), Secrets(), dbs,
                        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        result = await suite.dexscreener.profiles()
        assert result.items and result.errors == []
        assert all(item.flags == ("PROMOTED",) for item in result.items)
        assert len(requests) == 1
    finally:
        await suite.aclose()
        dbs.close()
