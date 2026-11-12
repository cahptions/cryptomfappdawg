"""The read-only Schwab account client: documentation-shaped synthetic responses (never a real account), the
GET-only allowlist enforced in code, and a source inspection proving no order path or write method exists."""

from __future__ import annotations

import ast
import inspect
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.providers.cache import SingleFlightCache
from desk.providers.health import HealthTracker
from desk.providers.http import ProviderHttp
from desk.providers.ratelimit import RateLimiter
from desk.providers.registry import load_registry
from desk.stocks.repo import StocksDatabases
from desk.stocks.schwab_auth import SchwabTokens, token_path
from desk.stocks.sources import schwab_account as sa
from tests.conftest import REPO

FIXTURES = REPO / "tests" / "fixtures" / "stocks"
BASE = "https://api.schwabapi.com/trader/v1"
NUMBERS = (FIXTURES / "schwab_account_numbers_doc_example.json").read_bytes()
POSITIONS = (FIXTURES / "schwab_accounts_positions_doc_example.json").read_bytes()
TRADES = (FIXTURES / "schwab_transactions_doc_example.json").read_bytes()


def test_parsers_keep_only_the_last_four_digits_and_a_one_way_key() -> None:
    accounts = sa.parse_account_numbers(NUMBERS)
    seen, positions = sa.parse_positions(POSITIONS, accounts)
    assert [a.last4 for a in seen] == ["1234", "5678"]
    by = {p.symbol: p for p in positions}
    assert set(by) == {"AAPL", "AAPL261218C00200000", "MSFT261218P00400000", "SWVXX", "SPY"}
    call = by["AAPL261218C00200000"]
    assert (call.asset_type, call.underlying, call.put_call, call.strike, call.expiration, call.quantity) == (
        "OPTION", "AAPL", "CALL", 200.0, date(2026, 12, 18), 2)
    assert by["MSFT261218P00400000"].quantity == -1 and by["AAPL"].average_price == 180.25
    fills = sa.parse_transactions(TRADES, seen[0])
    assert [(f.activity_id, f.symbol, f.quantity, f.price) for f in fills] == [
        ("90000001", "AAPL", 10, 170.0), ("90000002", "AAPL", -10, 185.0),
        ("90000003", "AAPL261016C00190000", 2, 3.0), ("90000004", "AAPL261016C00190000", -2, 1.0)]
    assert fills[2].fees == 1.3 and fills[0].trade_day == date(2026, 9, 1) and fills[1].position_effect == "CLOSING"
    # Nothing a caller receives carries the account number or Schwab's hashValue.
    text = repr((seen, positions, fills))
    assert "SYNTH0001234" not in text and "SYNTH0005678" not in text and "SYNTHETICHASH" not in text


def test_unknown_accounts_and_malformed_rows_are_skipped() -> None:
    assert sa.parse_positions(POSITIONS, {}) == ([], [])
    assert sa.parse_account_numbers(b'[{"accountNumber": "1", "hashValue": "bad hash"}]') == {}
    assert sa.option_parts("AAPL  261218C00200000") == ("AAPL", date(2026, 12, 18), "CALL", 200.0)
    assert sa.option_parts("not an option") is None


# ── the read-only boundary ───────────────────────────────────────────────────────────────────────
def _code_strings(tree: ast.AST) -> list[str]:
    """Every string constant that is not a docstring."""
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)}
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_the_module_can_only_issue_get_to_the_three_account_paths() -> None:
    tree = ast.parse(inspect.getsource(sa))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            assert name.lower() not in {"post", "put", "patch", "delete", "stream", "send"}, name
            for keyword in node.keywords:
                if keyword.arg == "method":
                    assert isinstance(keyword.value, ast.Constant) and keyword.value.value == "GET"
            if name == "request":   # every ProviderHttp request names its method, and it is GET
                assert any(k.arg == "method" for k in node.keywords)
    strings = _code_strings(tree)
    for text in strings:
        # "PUT" alone is an option's side (putCall), not a method: methods are checked on every call above.
        assert not re.search(r"order|preview", text, re.I), text
        assert text.upper() not in {"POST", "PATCH", "DELETE"}, text
    paths = [p.pattern for _, p in sa.ALLOWED]
    assert paths == [r"^/accounts/accountNumbers$", r"^/accounts$", r"^/accounts/[A-Za-z0-9]{8,128}/transactions$"]
    # Every path piece the client builds (an f-string's pieces are separate constants) belongs to those shapes.
    assert {t for t in strings if t.startswith("/")} <= {"/accounts/accountNumbers", "/accounts", "/accounts/",
                                                         "/transactions"}


def _client(tmp_path: Path, dbs: StocksDatabases, http_client: httpx.AsyncClient) -> sa.SchwabAccountClient:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    loaded = load_config(paths.config_dir)
    credentials = Secrets(values={"SCHWAB_APP_KEY": "public-key", "SCHWAB_APP_SECRET": "private-value"})
    tokens = SchwabTokens(token_path(paths), credentials)
    tokens.save({"format": 1, "access_token": "private-bearer", "refresh_token": "private-refresh",
                 "expires_at": time.time() + 1800, "authorized_at": time.time()})
    http = ProviderHttp("schwab_account", load_registry(paths.config_dir, REPO).get("schwab_account"),
                        secrets=credentials, limiter=RateLimiter(paths.db_dir / "runtime.db"),
                        health=HealthTracker(dbs.runtime, loaded.settings.health), settings=loaded.settings.http,
                        client=http_client, cache=SingleFlightCache())
    return sa.SchwabAccountClient(http, tokens)


async def test_paths_outside_the_allowlist_are_refused_before_any_request(tmp_path: Path) -> None:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    async with httpx.AsyncClient() as http_client:
        client = _client(tmp_path, dbs, http_client)
        with respx.mock(assert_all_called=False) as mock:
            for path in ("/accounts/SYNTHETICHASHA000000001/orders", "/orders", "/accounts/x/transactions",
                         "/accounts/SYNTHETICHASHA000000001/previewOrder", "/userPreference"):
                with pytest.raises(ValueError, match="read-only"):
                    await client._get(path)
            assert not mock.calls
    dbs.close()


async def test_reads_numbers_positions_and_trades_with_get_and_the_bearer(tmp_path: Path) -> None:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    async with httpx.AsyncClient() as http_client:
        client = _client(tmp_path, dbs, http_client)
        with respx.mock() as mock:
            mock.get(f"{BASE}/accounts/accountNumbers").respond(200, content=NUMBERS)
            mock.get(f"{BASE}/accounts", params={"fields": "positions"}).respond(200, content=POSITIONS)
            trades = mock.get(url__regex=rf"{BASE}/accounts/SYNTHETICHASHA0+1/transactions.*").respond(200, content=TRADES)
            accounts = await client.accounts()
            seen, positions = await client.positions(accounts)
            fills = await client.trades(seen[0], datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))
            assert len(positions) == 5 and len(fills) == 4
            assert all(call.request.method == "GET" for call in mock.calls)
            assert all(call.request.headers["Authorization"] == "Bearer private-bearer" for call in mock.calls)
            query = trades.calls[0].request.url.params
            assert query["types"] == "TRADE" and query["startDate"] == "2026-09-01T00:00:00.000Z"
    dbs.close()


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refusal_means_the_accounts_product_is_not_enabled(tmp_path: Path, status: int) -> None:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    async with httpx.AsyncClient() as http_client:
        client = _client(tmp_path, dbs, http_client)
        with respx.mock() as mock:
            route = mock.get(f"{BASE}/accounts/accountNumbers").respond(status, json={"message": "not entitled"})
            with pytest.raises(sa.AccountNotEnabled):
                await client.accounts()
            assert route.call_count == 1          # never retried in a loop
    dbs.close()
