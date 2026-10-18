"""Provider HTTP client + adapters against a mocked transport (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.providers.factory import ProviderSuite, build_suite
from desk.providers.health import HealthState
from desk.storage.databases import Databases
from tests.conftest import REPO, load_fixture

GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
JUP = "https://api.jup.ag"
JUP_LITE = "https://lite-api.jup.ag"
RPC = "https://api.mainnet-beta.solana.com"


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def suite(tmp_path: Path) -> ProviderSuite:
    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO / "config", secrets_file=tmp_path / "s.env")
    dbs = Databases.open(paths)
    loaded = load_config(REPO / "config")
    s = build_suite(paths, loaded, Secrets(), dbs, client=httpx.AsyncClient())
    for client in (s.geckoterminal, s.dexscreener, s.jupiter, s.rpc):
        client.http._sleep = _no_sleep          # retries without real waiting
    s.limiter._sleep = _no_sleep
    yield s
    await s.aclose()
    dbs.close()


def _clear_backoff(s: ProviderSuite) -> None:
    s.limiter._conn.execute("DELETE FROM rate_backoff")


@respx.mock
async def test_gt_429_then_success_backs_off_and_records_health(suite: ProviderSuite) -> None:
    route = respx.get(f"{GT}/networks/solana/new_pools").mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(200, content=load_fixture("provider/geckoterminal_new_pools_p1.json")),
    ])
    original_acquire = suite.limiter.try_acquire

    def acquire_ignoring_backoff(bucket: str, windows: object) -> float:  # simulate time passing past the backoff
        _clear_backoff(suite)
        return original_acquire(bucket, windows)  # type: ignore[arg-type]

    suite.limiter.try_acquire = acquire_ignoring_backoff  # type: ignore[method-assign]
    fetched = await suite.geckoterminal.new_pools()
    assert route.call_count == 2 and len(fetched.items) == 20 and fetched.errors == []
    record = suite.health.get("geckoterminal")
    assert record.consecutive_successes == 1 and "429" in (record.last_error or "")


@respx.mock
async def test_429_with_retry_after_zero_sets_real_backoff(suite: ProviderSuite) -> None:
    respx.get(f"{GT}/networks/solana/new_pools").mock(return_value=httpx.Response(429, headers={"retry-after": "0"}))
    suite.limiter._sleep = _no_sleep
    with pytest.raises(ProviderError) as info:
        await suite.geckoterminal.http.request("new_pools", params={"page": 9})
    assert info.value.kind is ProviderErrorKind.RATE_LIMITED
    state = suite.limiter.backoff_state("geckoterminal:default")
    assert state is not None and state["consecutive_429"] >= 1


@respx.mock
async def test_5xx_retried_then_raises_and_degrades(suite: ProviderSuite) -> None:
    route = respx.get(url__startswith=f"{DS}/tokens/v1/solana/").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderError) as info:
        await suite.dexscreener.tokens(["So11111111111111111111111111111111111111112"])
    assert info.value.kind is ProviderErrorKind.HTTP_ERROR
    assert route.call_count == 4  # max_retries = 3
    assert suite.health.get("dexscreener").state is HealthState.DEGRADED


@respx.mock
async def test_timeout_is_typed(suite: ProviderSuite) -> None:
    respx.get(url__startswith=f"{DS}/tokens/v1/solana/").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError) as info:
        await suite.dexscreener.tokens(["So11111111111111111111111111111111111111112"])
    assert info.value.kind is ProviderErrorKind.TIMEOUT


@respx.mock
async def test_401_not_retried(suite: ProviderSuite) -> None:
    route = respx.get(f"{JUP}/swap/v2/order").mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderError, match="check API key"):
        await suite.jupiter.order("So11111111111111111111111111111111111111112",
                                  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 100_000_000)
    assert route.call_count == 1


@respx.mock
async def test_schema_drift_is_typed_and_recorded(suite: ProviderSuite) -> None:
    respx.get(f"{GT}/networks/solana/trending_pools").mock(return_value=httpx.Response(200, content=b'{"data": 5}'))
    with pytest.raises(ProviderError) as info:
        await suite.geckoterminal.trending_pools()
    assert info.value.kind is ProviderErrorKind.SCHEMA
    assert "schema" in (suite.health.get("geckoterminal").last_error or "")


@respx.mock
async def test_quote_not_tradable_is_a_valid_answer(suite: ProviderSuite) -> None:
    respx.get(f"{JUP}/swap/v2/order").mock(return_value=httpx.Response(
        400, json={"error": "The token X is not tradable", "errorCode": "TOKEN_NOT_TRADABLE"}))
    quote = await suite.jupiter.order("So11111111111111111111111111111111111111112",
                                      "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 1)
    assert isinstance(quote, QuoteUnavailable) and quote.http_status == 400
    assert suite.health.get("jupiter").state is HealthState.OK


@respx.mock
async def test_quote_success_and_raw_evidence_stored(suite: ProviderSuite) -> None:
    body = load_fixture("provider/jupiter_v2_order_keyless_sol_usdc.json")
    respx.get(f"{JUP}/swap/v2/order").mock(return_value=httpx.Response(200, content=body))
    quote = await suite.jupiter.order("So11111111111111111111111111111111111111112",
                                      "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 100_000_000)
    assert isinstance(quote, QuoteObservation) and quote.raw_sha256
    stored = suite.jupiter.http.raw_store.get(quote.raw_sha256)  # type: ignore[union-attr]
    assert stored == body


@respx.mock
async def test_keyless_tokens_use_lite_host_and_key_header_when_configured(suite: ProviderSuite) -> None:
    lite = respx.get(f"{JUP_LITE}/tokens/v2/recent").mock(
        return_value=httpx.Response(200, content=load_fixture("provider/jupiter_tokens_v2_recent.json")))
    fetched = await suite.jupiter.tokens_recent()
    assert lite.call_count == 1 and len(fetched.items) == 30

    suite.jupiter.http.secrets = Secrets(values={"JUPITER_API_KEY": "test-key-123"})
    keyed = respx.get(f"{JUP}/tokens/v2/toptrending/1h").mock(
        return_value=httpx.Response(200, content=load_fixture("provider/jupiter_tokens_v2_toptrending_1h.json")))
    await suite.jupiter.tokens_category("toptrending", "1h", 20)
    assert keyed.calls.last.request.headers["x-api-key"] == "test-key-123"
    assert suite.jupiter.http.windows("default") == ((9, 10.0), (50, 60.0))


@respx.mock
async def test_identical_concurrent_requests_hit_upstream_once(suite: ProviderSuite) -> None:
    import asyncio
    route = respx.get(url__startswith=f"{DS}/tokens/v1/solana/").mock(
        return_value=httpx.Response(200, content=load_fixture("provider/dexscreener_tokens_v1_10mints.json")))
    mints = ["So11111111111111111111111111111111111111112"]
    results = await asyncio.gather(*(suite.dexscreener.tokens(mints) for _ in range(5)))
    assert route.call_count == 1 and all(len(r.items) == 9 for r in results)


@respx.mock
async def test_rpc_jsonrpc_level_429_is_retried(suite: ProviderSuite) -> None:
    rate_limited = load_fixture("provider/solana_public_rpc_getTokenLargestAccounts_429.json")
    ok = json.dumps({"jsonrpc": "2.0", "result": 458591147, "id": 1}).encode()
    route = respx.post(RPC).mock(side_effect=[httpx.Response(200, content=rate_limited),
                                              httpx.Response(200, content=ok)])
    original = suite.limiter.try_acquire
    suite.limiter.try_acquire = lambda b, w: (_clear_backoff(suite), original(b, w))[1]  # type: ignore[method-assign]
    assert await suite.rpc.get_slot() == 458591147
    assert route.call_count == 2


@respx.mock
async def test_rpc_other_errors_raise(suite: ProviderSuite) -> None:
    respx.post(RPC).mock(return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                                               "error": {"code": -32602, "message": "Invalid param"}}))
    with pytest.raises(ProviderError) as info:
        await suite.rpc.get_slot()
    assert info.value.kind is ProviderErrorKind.RPC_ERROR


@respx.mock
async def test_rpc_mint_account_through_adapter(suite: ProviderSuite) -> None:
    respx.post(RPC).mock(return_value=httpx.Response(
        200, content=load_fixture("provider/solana_public_rpc_getAccountInfo_token2022_mint.json")))
    mint = await suite.rpc.get_mint_account("7wrVy57WvGRxwn679U3buysv6FDdAV3hoDfJSqVrpump")
    assert mint is not None and mint.token_program == "spl-token-2022" and mint.raw_sha256


async def test_keyed_rpc_url_replaces_public_endpoint(suite: ProviderSuite) -> None:
    secret_url = "https://mainnet.helius-rpc.com/?api-key=abcdef"
    suite.rpc.http.secrets = Secrets(values={"SOLANA_RPC_URL": secret_url})
    assert suite.rpc.http.base_url() == secret_url
    assert suite.rpc.http.windows("default") == ((20, 10.0), (600, 60.0))


@respx.mock
async def test_budgets_and_backoff_are_isolated_per_provider(suite: ProviderSuite) -> None:
    """Regression: all providers used bucket name "default" and shared one request log and one backoff."""
    respx.get(f"{GT}/networks/solana/new_pools").mock(return_value=httpx.Response(429, headers={"retry-after": "0"}))
    ds_route = respx.get(url__startswith=f"{DS}/tokens/v1/solana/").mock(
        return_value=httpx.Response(200, content=load_fixture("provider/dexscreener_tokens_v1_10mints.json")))
    suite.geckoterminal.http.settings = suite.geckoterminal.http.settings.model_copy(update={"max_retries": 0})
    with pytest.raises(ProviderError):
        await suite.geckoterminal.new_pools(1)
    gt_backoff = suite.limiter.backoff_state("geckoterminal:default")
    assert gt_backoff is not None and gt_backoff["consecutive_429"] == 1
    # DexScreener is unaffected by GeckoTerminal's backoff and budget
    assert suite.limiter.backoff_state("dexscreener:default") is None
    for _ in range(3):
        await suite.dexscreener.tokens([f"So1111111111111111111111111111111111111111{n}" for n in (2,)])
        suite.dexscreener.http.cache = type(suite.dexscreener.http.cache)()  # defeat the cache: real requests
    assert ds_route.call_count == 3
    keys = {row[0] for row in suite.limiter._conn.execute("SELECT DISTINCT bucket FROM rate_events")}
    assert keys == {"geckoterminal:default", "dexscreener:default"}
