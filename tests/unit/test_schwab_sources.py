"""Documentation-shaped synthetic responses, not proof of live Schwab entitlements/schema."""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from desk.core.config import load_config
from desk.core.errors import SchemaError
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.stocks.connection import save_connection
from desk.stocks.repo import StocksDatabases, StocksMarket
from desk.stocks.schwab_auth import SchwabTokens, token_path
from desk.stocks.sources.schwab import parse_chain, parse_expirations, parse_quotes
from desk.stocks.suite import build_stocks_suite
from desk.storage.db import StorageError
from tests.conftest import REPO

EXP = date(2026, 11, 20)


def contract(**changes: object) -> dict:
    return {"symbol": "SPY   261120C00500000", "strikePrice": 500, "multiplier": 100,
            "bid": 5, "ask": 5.2, "last": 5.1, "totalVolume": 120, "openInterest": 400,
            "quoteTimeInLong": 1792602000000, **changes}


def chain(*rows: dict, **extra: object) -> bytes:
    return json.dumps({"symbol": "SPY", "callExpDateMap": {"2026-11-20:30": {"500.0": list(rows)}}, **extra}).encode()


def test_standard_contracts_normalize_identity_and_do_not_invent_volume_history() -> None:
    rows = parse_chain(chain(contract()), "SPY", EXP)
    assert len(rows) == 1 and rows[0].occ == "SPY261120C00500000"
    assert rows[0].average_volume is None and rows[0].quoted_at.tzinfo == UTC
    assert rows[0].bid == 5 and rows[0].ask == 5.2 and rows[0].kind == "call"
    assert parse_expirations(b'{"expirationList":[{"expirationDate":"2026-11-20"}]}') == [EXP]


@pytest.mark.parametrize("change", [{"multiplier": 10}, {"nonStandard": True}, {"optionDeliverablesList": [{}]},
                                    {"symbol": "QQQ   261120C00500000"}, {"strikePrice": 499},
                                    {"symbol": "SPY   261120P00500000"}, {"symbol": "SPY   261121C00500000"}])
def test_unsupported_or_misidentified_contracts_are_excluded(change: dict) -> None:
    assert parse_chain(chain(contract(**change)), "SPY", EXP) == []


def test_delayed_data_and_wrong_underlying_are_not_silently_accepted() -> None:
    with pytest.raises(SchemaError, match="delayed"):
        parse_chain(chain(contract(), isDelayed=True), "SPY", EXP)
    with pytest.raises(SchemaError):
        parse_chain(chain(contract(), symbol="QQQ"), "SPY", EXP)
    with pytest.raises(SchemaError):
        parse_expirations(b'{"errors":[{"message":"private response"}]}')
    quotes = parse_quotes(b'{"SPY":{"symbol":"SPY","quote":{"lastPrice":500,"bidPrice":499.9,"askPrice":500.1}}}')
    assert quotes[0].last == 500 and quotes[0].quoted_at is None


@respx.mock
async def test_suite_selects_schwab_and_only_calls_market_gets_with_private_bearer(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    save_connection(paths, app_key="public-key", app_secret="private-value", callback="https://127.0.0.1:8182/oauth/callback",
                    provider="schwab")
    credentials = Secrets(values={"SCHWAB_APP_KEY": "public-key", "SCHWAB_APP_SECRET": "private-value"})
    tokens = SchwabTokens(token_path(paths), credentials)
    tokens.save({"format": 1, "access_token": "private-bearer", "refresh_token": "private-refresh",
                 "expires_at": time.time() + 1800, "authorized_at": time.time()})
    dbs = StocksDatabases.open(paths)
    suite = build_stocks_suite(paths, load_config(paths.config_dir), credentials, dbs.runtime, client=httpx.AsyncClient())
    route = respx.get("https://api.schwabapi.com/marketdata/v1/quotes").respond(200, json={
        "SPY": {"symbol": "SPY", "quote": {"lastPrice": 500, "quoteTime": 1792602000000}}})
    try:
        assert suite.option_source == "schwab" and suite.options.configured
        assert (await suite.options.quotes(["SPY"]))[0].last == 500
        assert route.calls[0].request.headers["Authorization"] == "Bearer private-bearer"
        assert "private-value" not in str(route.calls[0].request.url)
        assert len(respx.calls) == 1
    finally:
        await suite.aclose()
        dbs.close()


def test_chain_source_cannot_be_mixed_with_another_provider_in_one_session(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(paths)
    try:
        market = StocksMarket(dbs.market)
        today = date(2026, 10, 21)
        now = datetime(2026, 10, 21, 22, tzinfo=UTC)
        market.save_option_chain("SPY", today, EXP, now, 500, parse_chain(chain(contract()), "SPY", EXP), source="schwab")
        with pytest.raises(StorageError, match="mix option providers"):
            market.save_option_chain("SPY", today, EXP, now, 500, [], source="tradier_sandbox")
        assert market.option_chain("SPY", today)[1][EXP][0].occ == "SPY261120C00500000"
    finally:
        dbs.close()
