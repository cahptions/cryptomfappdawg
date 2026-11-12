"""Reading the real Schwab account (read only): the opt-in switch, the schedule, append-only snapshots and fills,
graceful refusals, the watchlist and ``account_positions``. Documentation-shaped synthetic responses only."""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import respx

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.stocks import account
from desk.stocks import calendar as cal
from desk.stocks.repo import StocksDatabases
from desk.stocks.schwab_auth import SchwabTokens, token_path
from tests.conftest import REPO

FIXTURES = REPO / "tests" / "fixtures" / "stocks"
BASE = "https://api.schwabapi.com/trader/v1"
NUMBERS = (FIXTURES / "schwab_account_numbers_doc_example.json").read_bytes()
POSITIONS = (FIXTURES / "schwab_accounts_positions_doc_example.json").read_bytes()
TRADES = (FIXTURES / "schwab_transactions_doc_example.json").read_bytes()
CREDENTIALS = Secrets(values={"SCHWAB_APP_KEY": "public-key", "SCHWAB_APP_SECRET": "private-value"})


def ny(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=cal.NEW_YORK).astimezone(UTC)


DAY = date(2026, 11, 12)      # a Thursday market day


@pytest.fixture
def paths(tmp_path: Path) -> DeskPaths:
    p = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(p)
    for symbol in ("AAPL", "MSFT", "SPY"):
        dbs.market.execute("INSERT INTO stk_listings VALUES (?, ?, 'Q', 'stock', 0, 0, NULL, 1, '2026', '2026')",
                           (symbol, symbol))
    dbs.close()
    return p


def signed_in(paths: DeskPaths) -> None:
    SchwabTokens(token_path(paths), CREDENTIALS).save({
        "format": 1, "access_token": "private-bearer", "refresh_token": "private-refresh",
        "expires_at": time.time() + 1800, "authorized_at": time.time()})


def factory(paths: DeskPaths) -> account.ClientFactory:
    return lambda runtime, h: account.build_client(paths, load_config(paths.config_dir), CREDENTIALS, runtime, h)


def test_slots_are_three_in_the_session_and_one_after_the_close() -> None:
    assert account.current_slot(ny(DAY, 9, 30)) is None
    assert account.current_slot(ny(DAY, 9, 50))[0] == "2026-11-12 09:45"
    assert account.current_slot(ny(DAY, 15, 59))[0] == "2026-11-12 15:30"
    label, _, after = account.current_slot(ny(DAY, 16, 30))
    assert label == "2026-11-12 close" and after
    assert account.current_slot(ny(date(2026, 11, 14), 12)) is None          # Saturday


async def test_nothing_is_requested_until_the_switch_is_on(paths: DeskPaths) -> None:
    signed_in(paths)
    assert not account.enabled(paths)
    with respx.mock(assert_all_called=False) as mock:
        assert await account.cycle(paths, load_config(paths.config_dir), ny(DAY, 10), factory=factory(paths)) is None
        assert not mock.calls


async def test_a_read_saves_positions_fills_once_and_watches_holdings(paths: DeskPaths) -> None:
    signed_in(paths)
    account.set_enabled(paths, True)
    loaded = load_config(paths.config_dir)
    with respx.mock() as mock:
        mock.get(f"{BASE}/accounts/accountNumbers").respond(200, content=NUMBERS)
        mock.get(f"{BASE}/accounts").respond(200, content=POSITIONS)
        trades = mock.get(url__regex=rf"{BASE}/accounts/SYNTHETICHASH\w+/transactions.*").respond(200, content=TRADES)
        first = await account.cycle(paths, loaded, ny(DAY, 10), factory=factory(paths))
        assert first is not None and first["outcome"] == "ok" and first["positions"] == 5
        assert first["fills_new"] == 8          # the fixture's four trade legs, answered for each of two accounts
        assert sorted(first["watched"]) == ["AAPL", "MSFT", "SPY"]      # SWVXX has no listing: not watched
        assert trades.call_count == 2                              # the first read looks a year back
        assert await account.cycle(paths, loaded, ny(DAY, 11), factory=factory(paths)) is None   # same slot
        after = await account.cycle(paths, loaded, ny(DAY, 16, 25), factory=factory(paths))
        assert after is not None and after["fills_new"] == 0       # the same activity ids are stored once
        assert all(call.request.method == "GET" for call in mock.calls)
    dbs = StocksDatabases.open(paths)
    try:
        held = account.account_positions(dbs.research)
        assert {(p.account_last4, p.symbol, p.quantity) for p in held} >= {("1234", "AAPL", 10),
                                                                          ("1234", "MSFT261218P00400000", -1),
                                                                          ("5678", "SPY", 5)}
        assert next(p for p in held if p.symbol.startswith("MSFT")).stock == "MSFT"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            dbs.research.execute("DELETE FROM stk_account_fills")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            dbs.research.execute("UPDATE stk_account_positions SET quantity = 0")
        assert dbs.research.execute("SELECT COUNT(*) FROM stk_account_pulls").fetchone()[0] == 2
    finally:
        dbs.close()
    # No account number or hashValue is written anywhere on disk.
    for file in (paths.db_dir).glob("*"):
        data = file.read_bytes()
        assert b"SYNTH0001234" not in data and b"SYNTHETICHASH" not in data, file.name


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refusal_is_saved_as_product_not_enabled_and_tried_once_a_slot(paths: DeskPaths, status: int) -> None:
    signed_in(paths)
    account.set_enabled(paths, True)
    loaded = load_config(paths.config_dir)
    with respx.mock() as mock:
        route = mock.get(f"{BASE}/accounts/accountNumbers").respond(status)
        result = await account.cycle(paths, loaded, ny(DAY, 13), factory=factory(paths))
        assert result is not None and result["outcome"] == "not_enabled"
        assert "Accounts and Trading Production" in result["detail"] and "developer.schwab.com" in result["detail"]
        assert await account.cycle(paths, loaded, ny(DAY, 14), factory=factory(paths)) is None
        assert route.call_count == 1
    dbs = StocksDatabases.open(paths)
    try:
        assert account.account_positions(dbs.research) == []
    finally:
        dbs.close()


async def test_signed_out_is_a_plain_state_and_requests_nothing(paths: DeskPaths) -> None:
    account.set_enabled(paths, True)
    with respx.mock(assert_all_called=False) as mock:
        result = await account.cycle(paths, load_config(paths.config_dir), ny(DAY, 10), factory=factory(paths))
        assert result is not None and result["outcome"] == "signed_out" and not mock.calls


async def test_manual_refresh_is_limited_to_once_a_minute(paths: DeskPaths) -> None:
    account.set_enabled(paths, True)
    loaded = load_config(paths.config_dir)
    now = ny(DAY, 20)
    assert (await account.cycle(paths, loaded, now, manual=True, factory=factory(paths)))["outcome"] == "signed_out"
    with pytest.raises(Exception, match="less than a minute"):
        await account.cycle(paths, loaded, now, manual=True, factory=factory(paths))
