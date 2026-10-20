"""Robinhood desk providers: request signing, the local key, response parsing and Coinbase history (no network).

Robinhood responses here are shaped from the documented schemas (research/evidence/2026-10-20/
robinhood_crypto_trading_api_openapi.json); they are not recorded responses, which need the operator's key.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from desk.core.config import load_config
from desk.core.errors import ConfigError, SchemaError
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.rh import signing
from desk.rh.coinbase import parse_candles
from desk.rh.suite import RhSuite, build_rh_suite
from desk.storage.databases import Databases
from tests.conftest import REPO

RH = "https://trading.robinhood.com"
CB = "https://api.exchange.coinbase.com"
DOCS_PRIVATE = "xQnTJVeQLmw1/Mg2YimEViSpw/SdJcgNXZ5kQkAXNPU="
DOCS_PUBLIC = "jPItx4TLjcnSUnmnXQQyAKL4eJj3+oWNNMmmm2vATqk="


def test_signature_matches_the_documented_example() -> None:
    key = signing.private_key_from_b64(DOCS_PRIVATE)
    assert signing.public_key_b64(key) == DOCS_PUBLIC
    # the docs' sample code formats the body with Python's str(dict); the published signature is of that text
    body = str({"client_order_id": "131de903-5a9c-4260-abc1-28d562a5dcf0", "side": "buy", "symbol": "BTC-USD",
                "type": "market", "market_order_config": {"asset_quantity": "0.1"}})
    assert signing.signature(key, "rh-api-6148effc-c0b1-486c-8940-a1d099456be6", 1698708981,
                             "/api/v1/crypto/trading/orders/", "POST", body) == (
        "q/nEtxp/P2Or3hph3KejBqnw5o9qeuQ+hYRnB56FaHbjDsNUY9KhB1asMxohDnzdVFSD7StaTqjSd9U9HvaRAw==")


def test_keygen_is_owner_only_and_never_overwrites(tmp_path: Path) -> None:
    path = signing.key_path(tmp_path / "desk" / "secrets.env")
    public = signing.generate_key(path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert signing.public_key_b64(signing.load_private_key(path)) == public
    with pytest.raises(FileExistsError):
        signing.generate_key(path)
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        signing.load_private_key(path)
    with pytest.raises(ConfigError, match="desk rh keygen"):
        signing.load_private_key(tmp_path / "missing.key")


@pytest.fixture
async def suite(tmp_path: Path) -> RhSuite:
    secrets_file = tmp_path / "cfg" / "secrets.env"
    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO / "config", secrets_file=secrets_file)
    signing.generate_key(signing.key_path(secrets_file))
    dbs = Databases.open(paths)
    s = build_rh_suite(paths, load_config(REPO / "config"), Secrets(values={"ROBINHOOD_API_KEY": "rh-api-test"}),
                       dbs.runtime, client=httpx.AsyncClient())
    yield s
    await s.aclose()
    dbs.close()


def _verify(request: httpx.Request, public: Ed25519PublicKey) -> None:
    timestamp = int(request.headers["x-timestamp"])
    assert abs(timestamp - datetime.now(UTC).timestamp()) < 5
    assert request.headers["x-api-key"] == "rh-api-test"
    message = signing.message("rh-api-test", timestamp, request.url.raw_path.decode(), request.method,
                              request.content.decode())
    public.verify(base64.b64decode(request.headers["x-signature"]), message)


@respx.mock
async def test_requests_are_signed_over_the_exact_path_and_query(suite: RhSuite, tmp_path: Path) -> None:
    public = signing.load_private_key(tmp_path / "cfg" / "robinhood.key").public_key()
    route = respx.get(url__startswith=f"{RH}/api/v2/crypto/marketdata/best_bid_ask/").mock(return_value=httpx.Response(
        200, json={"results": [{"symbol": "BTC-USD", "bid": 81990.1, "ask": 82010.3},
                               {"symbol": "ETH-USD", "bid": 3012.5, "ask": 3013.1}]}))
    quotes = await suite.robinhood.best_bid_ask(["eth-usd", "BTC-USD"])
    request = route.calls.last.request
    assert request.url.raw_path == b"/api/v2/crypto/marketdata/best_bid_ask/?symbol=BTC-USD&symbol=ETH-USD"
    _verify(request, public)
    assert quotes[0].bid == Decimal("81990.1") and quotes[0].ask == Decimal("82010.3")
    assert quotes[1].spread_fraction == (Decimal("3013.1") - Decimal("3012.5")) / Decimal("3012.8")
    assert not quotes[0].crossed


@respx.mock
async def test_crossed_aggregate_quote_preserves_both_reported_sides(suite: RhSuite) -> None:
    respx.get(url__startswith=f"{RH}/api/v2/crypto/marketdata/best_bid_ask/").mock(return_value=httpx.Response(
        200, json={"results": [{"symbol": "BTC-USD", "bid": "82010.12345678", "ask": "81990.87654321"},
                               {"symbol": "ETH-USD", "bid": "3000", "ask": "3000"}]}))
    crossed, locked = await suite.robinhood.best_bid_ask(["BTC-USD", "ETH-USD"])
    assert crossed.bid == Decimal("82010.12345678") and crossed.ask == Decimal("81990.87654321")
    assert crossed.crossed and crossed.spread_fraction < 0
    assert not locked.crossed and locked.spread_fraction == 0


@respx.mock
async def test_estimates_map_book_sides_to_buy_and_sell_for_both_routes(suite: RhSuite) -> None:
    respx.get(url__startswith=f"{RH}/api/v2/crypto/trading/estimated_price/").mock(return_value=httpx.Response(200, json={
        "results": [
            {"symbol": "SOL-USD", "side": "ask", "quantity": 0.05, "timestamp": "2026-10-20T04:00:00.123456Z",
             "bid": 150.1, "ask": 150.3, "fee_ratio": 0.0095, "est_fee": 0.0713925, "est_total_cost": 7.5863925,
             "est_total_credit": None},
            {"symbol": "SOL-USD", "side": "bid", "quantity": 0.05, "timestamp": "2026-10-20T04:00:00.123456Z",
             "bid": 150.1, "ask": 150.3, "fee_ratio": 0.0095, "est_fee": 0.0712975, "est_total_cost": None,
             "est_total_credit": 7.4337025}]}))
    respx.get(url__startswith=f"{RH}/api/v1/crypto/marketdata/estimated_price/").mock(return_value=httpx.Response(200, json={
        "results": [
            {"symbol": "SOL-USD", "side": "ask", "price": 150.2, "quantity": 0.05,
             "bid_inclusive_of_sell_spread": 149.6, "sell_spread": 0.004, "ask_inclusive_of_buy_spread": 150.8,
             "buy_spread": 0.004, "timestamp": "2026-10-20T04:00:00Z"},
            {"symbol": "SOL-USD", "side": "bid", "price": 150.2, "quantity": 0.05,
             "bid_inclusive_of_sell_spread": 149.6, "sell_spread": 0.004, "ask_inclusive_of_buy_spread": 150.8,
             "buy_spread": 0.004, "timestamp": "2026-10-20T04:00:00Z"}]}))
    ex = await suite.robinhood.estimates("sol-usd", "EX", [Decimal("0.0500")])
    assert [(e.side, e.total, e.fee) for e in ex] == [("BUY", Decimal("7.5863925"), Decimal("0.0713925")),
                                                       ("SELL", Decimal("7.4337025"), Decimal("0.0712975"))]
    mm = await suite.robinhood.estimates("SOL-USD", "MM", [Decimal("0.05")])
    assert [(e.side, e.unit_price, e.total, e.fee) for e in mm] == [
        ("BUY", Decimal("150.8"), Decimal("7.540"), 0), ("SELL", Decimal("149.6"), Decimal("7.480"), 0)]
    assert respx.calls[0].request.url.raw_path.endswith(b"symbol=SOL-USD&side=both&quantity=0.05")
    with pytest.raises(ValueError):
        await suite.robinhood.estimates("SOL-USD", "EX", [Decimal(1)] * 11)


@respx.mock
async def test_trading_pairs_follow_pagination_and_flag_tradability(suite: RhSuite) -> None:
    page = {"symbol": "BTC-USD", "asset_code": "BTC", "quote_code": "USD", "asset_increment": "0.00000001",
            "quote_increment": "0.01", "max_order_size": "20", "min_order_amount": "1", "status": "tradable",
            "is_api_tradable": True}
    respx.get(f"{RH}/api/v2/crypto/trading/trading_pairs/", params={"cursor": "abc"}).mock(
        return_value=httpx.Response(200, json={
            "next": None, "previous": None, "results": [{**page, "symbol": "USDC-USD", "asset_code": "USDC"},
                                                        {**page, "symbol": "XYZ-USD", "asset_code": "XYZ",
                                                         "status": "untradable"},
                                                        {**page, "symbol": "APPONLY-USD", "asset_code": "APPONLY",
                                                         "is_api_tradable": False}]}))
    respx.get(f"{RH}/api/v2/crypto/trading/trading_pairs/").mock(return_value=httpx.Response(200, json={
        "next": f"{RH}/api/v2/crypto/trading/trading_pairs/?cursor=abc", "previous": None, "results": [page]}))
    pairs = await suite.robinhood.trading_pairs()
    assert [p.symbol for p in pairs] == ["BTC-USD", "USDC-USD", "XYZ-USD", "APPONLY-USD"]
    assert [p.tradable for p in pairs] == [True, False, False, False]  # includes retail-only API exclusion


@respx.mock
@pytest.mark.parametrize("bid,ask", [(0, 10), (10, 0), (-1, 10), (10, -1), ("NaN", 10),
                                    (10, "Infinity"), (True, 10), (10, None), ("bad", 10)])
async def test_malformed_robinhood_payloads_are_refused(suite: RhSuite, bid: object, ask: object) -> None:
    respx.get(url__startswith=f"{RH}/api/v2/crypto/marketdata/best_bid_ask/").mock(return_value=httpx.Response(
        200, json={"results": [{"symbol": "BTC-USD", "bid": bid, "ask": ask}]}))
    with pytest.raises(SchemaError):
        await suite.robinhood.best_bid_ask(["BTC-USD"])


def test_missing_key_is_reported_not_guessed(tmp_path: Path) -> None:
    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO / "config", secrets_file=tmp_path / "secrets.env")
    dbs = Databases.open(paths)
    try:
        no_api_key = build_rh_suite(paths, load_config(REPO / "config"), Secrets(), dbs.runtime,
                                    client=httpx.AsyncClient())
        assert "ROBINHOOD_API_KEY" in (no_api_key.key_problem or "") and not no_api_key.robinhood.ready
        no_key_file = build_rh_suite(paths, load_config(REPO / "config"), Secrets(values={"ROBINHOOD_API_KEY": "x"}),
                                     dbs.runtime, client=httpx.AsyncClient())
        assert "desk rh keygen" in (no_key_file.key_problem or "")
    finally:
        dbs.close()


def test_coinbase_drops_the_forming_bar_and_orders_oldest_first() -> None:
    received = datetime(2026, 10, 20, 4, 32, tzinfo=UTC)
    today, yesterday = int(datetime(2026, 10, 20, tzinfo=UTC).timestamp()), int(datetime(2026, 10, 19, tzinfo=UTC).timestamp())
    body = json.dumps([[today, 81080.64, 82173.84, 81837.7, 81990.01, 765.9],
                       [yesterday, 81634.43, 83076.23, 82006.85, 81837.69, 4758.0],
                       [yesterday - 86400, 80564.57, 82183.56, 81387.16, 82006.85, 2937.7]]).encode()
    bars = parse_candles(body, "BTC-USD", 86400, received)
    assert [b.start for b in bars] == [datetime(2026, 10, 18, tzinfo=UTC), datetime(2026, 10, 19, tzinfo=UTC)]
    assert bars[-1].close == Decimal("81837.69")
    bad = json.dumps([[yesterday, 90000, 83076.23, 82006.85, 81837.69, 1]]).encode()
    with pytest.raises(SchemaError, match="inconsistent"):
        parse_candles(bad, "BTC-USD", 86400, received + timedelta(days=1))


@respx.mock
async def test_coinbase_pages_by_at_most_300_bars(suite: RhSuite) -> None:
    calls: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        start = datetime.fromisoformat(request.url.params["start"])
        return httpx.Response(200, json=[[int((start + timedelta(hours=i)).timestamp()), 1, 2, 1.5, 1.5, 10]
                                         for i in range(300)][::-1])

    respx.get(url__startswith=f"{CB}/products/ETH-USD/candles").mock(side_effect=answer)
    end = datetime(2026, 10, 1, tzinfo=UTC)
    bars = await suite.coinbase.candles("ETH-USD", 3600, end - timedelta(hours=450), end)
    assert len(calls) == 2 and len(bars) == 450
    assert bars[0].start == end - timedelta(hours=450) and bars[-1].start == end - timedelta(hours=1)
