"""The Robinhood desk's PAPER engine end to end, with fake Robinhood and Coinbase answers (no network)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.rh import multiframe as mf
from desk.rh import strategies as st
from desk.rh.contracts import BidAsk, Candle, Estimate, Pair, Route
from desk.rh.engine import RhDesk
from desk.rh.repo import RhDatabases, RhLedger, RhMarket
from desk.rh.suite import build_rh_suite
from desk.rh.view import RhView
from desk.storage.db import StorageError
from tests.conftest import REPO

D = Decimal
SESSION = date(2026, 10, 20)                                    # a Tuesday
NOW = datetime(2026, 10, 20, 0, 10, tzinfo=UTC)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def daily(symbol: str, closes: list[Decimal]) -> list[Candle]:
    start = datetime(SESSION.year, SESSION.month, SESSION.day, tzinfo=UTC) - timedelta(days=len(closes))
    out, prev = [], closes[0]
    for i, close in enumerate(closes):
        out.append(Candle(symbol, 86400, start + timedelta(days=i), prev, max(prev, close) * D("1.01"),
                          min(prev, close) * D("0.99"), close, D(1_000_000)))
        prev = close
    return out


def intraday(symbol: str, closes: list[Decimal], seconds: int, end: datetime) -> list[Candle]:
    step = timedelta(seconds=seconds)
    prev, out = closes[0], []
    for i, close in enumerate(closes):
        out.append(Candle(symbol, seconds, end - (len(closes) - i) * step, prev,
                          max(prev, close) * D("1.001"), min(prev, close) * D("0.999"), close, D(1_000_000)))
        prev = close
    return out


class FakeRobinhood:
    """Robinhood's estimates: MM spread 0.4% each side, EX fee 0.95% each side around the given mid."""

    def __init__(self, mids: dict[str, Decimal], clock: Clock) -> None:
        self.mids, self.clock, self.calls = mids, clock, 0
        self.crossed = False

    async def estimates(self, symbol: str, route: Route, quantities: Iterable[Decimal]) -> list[Estimate]:
        self.calls += 1
        mid, out = self.mids[symbol], []
        for q in quantities:
            if route == "MM":
                ask, bid = mid * D("1.004"), mid * D("0.996")
                out += [Estimate(symbol, route, "BUY", q, ask, D(0), ask * q, None, None, self.clock(), f"mm{self.calls}"),
                        Estimate(symbol, route, "SELL", q, bid, D(0), bid * q, None, None, self.clock(), f"mm{self.calls}")]
            else:
                ask, bid, fee = mid * D("1.0005"), mid * D("0.9995"), D("0.0095")
                out += [Estimate(symbol, route, "BUY", q, ask, ask * q * fee, ask * q * (1 + fee), fee, None, self.clock(),
                                 f"ex{self.calls}"),
                        Estimate(symbol, route, "SELL", q, bid, bid * q * fee, bid * q * (1 - fee), fee, None, self.clock(),
                                 f"ex{self.calls}")]
        return out

    async def best_bid_ask(self, symbols: Iterable[str]) -> list[BidAsk]:
        bid, ask = (D("1.01"), D("0.99")) if self.crossed else (D("0.9995"), D("1.0005"))
        return [BidAsk(s, self.mids[s] * bid, self.mids[s] * ask, self.clock(), "q") for s in symbols
                if s in self.mids]

    async def trading_pairs(self) -> list[Pair]:
        return [Pair(s, s.split("-")[0], "USD", D("0.00000001"), D("0.01"), D(1000000), D(1), "tradable", True)
                for s in self.mids]


class World:
    def __init__(self, tmp_path: Path, *, key: bool = True) -> None:
        secrets_file = tmp_path / "cfg" / "secrets.env"
        self.paths = DeskPaths(home=tmp_path / "home", config_dir=REPO / "config", secrets_file=secrets_file)
        self.loaded = load_config(REPO / "config")
        self.dbs = RhDatabases.open(self.paths)
        self.clock = Clock(NOW)
        if key:
            from desk.rh.signing import generate_key, key_path

            generate_key(key_path(secrets_file))
        self.suite = build_rh_suite(self.paths, self.loaded,
                                    Secrets(values={"ROBINHOOD_API_KEY": "rh-api-test"} if key else {}),
                                    self.dbs.runtime, client=httpx.AsyncClient())
        self.market, self.ledger = RhMarket(self.dbs.market), RhLedger(self.dbs.ledger)
        self.mids = {"BTC-USD": D(320), "ALT-USD": D(60)}
        self.fake = FakeRobinhood(self.mids, self.clock)
        self.suite.robinhood = self.fake  # type: ignore[assignment]
        self.desk = RhDesk(self.paths, self.loaded, self.suite, self.dbs, clock=self.clock, sleep=self._sleep,
                           wait_seconds=0)
        self.desk.market.register_specs(NOW)
        self.desk.ledger.ensure_book(self.loaded.settings.robinhood.learning_cash_usd, NOW)
        btc = daily("BTC-USD", [D(100) + i for i in range(221)])                   # rising: RISK_ON, no breakout
        alt = daily("ALT-USD", [D(50)] * 216 + [D(52), D(54), D(56), D(58), D(60)])  # 20-day breakout
        self.market.save_candles("coinbase", btc + alt, NOW)
        end = mf.cutoff(NOW)
        for symbol, hours, quarters in (
                ("BTC-USD", [D(250) + i * D("0.2") for i in range(336)], [D(320)] * 61),
                ("ALT-USD", [D(30) + i * D("0.02") for i in range(335)] + [D(60)], [D(50)] * 60 + [D(60)])):
            self.market.save_candles("coinbase", intraday(symbol, hours, mf.HOUR, end)
                                     + intraday(symbol, quarters, mf.QUARTER, end), NOW)

    async def _sleep(self, _: float) -> None:
        return None

    async def prime(self) -> None:
        await self.desk.refresh_pairs()
        await self.desk.poll_quotes()

    def decisions(self) -> list[sqlite3.Row]:
        return self.dbs.market.execute("SELECT * FROM rh_decisions ORDER BY id").fetchall()


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = World(tmp_path)
    monkeypatch.setattr("desk.rh.view.utc_now", w.clock)
    yield w
    await w.suite.aclose()
    w.dbs.close()


async def test_session_enters_on_robinhood_prices_and_records_why(world: World) -> None:
    await world.prime()
    await world.desk.session()
    open_ = {(p.strategy_id, p.symbol): p for p in world.ledger.positions("OPEN")}
    assert set(open_) == {("B1_HOLD_BTC", "BTC-USD"), ("R1_TREND", "ALT-USD")}
    trend = open_[("R1_TREND", "ALT-USD")]
    assert trend.entry_route == "MM"                                      # cheaper buy: 0.4% spread vs 1% fee
    assert trend.entry_cost <= D("10.00") * D("1.004") and trend.entry_cost > D("9.90")
    assert trend.roundtrip_cost_fraction == (D("1.004") - D("0.9995") * (1 - D("0.0095"))) / D("1.004") or (
        trend.roundtrip_cost_fraction < D("0.02"))
    assert trend.stop_price is not None and trend.stop_price < trend.entry_unit_price
    assert open_[("B1_HOLD_BTC", "BTC-USD")].stop_price is None             # the hold baseline never stops out
    enters = [r for r in world.decisions() if r["action"] == "ENTER"]
    assert {r["strategy_id"] for r in enters} == {"B1_HOLD_BTC", "R1_TREND"}
    assert any("MULTIFRAME_V2" in r["reasons"] and r["spec_sha256"] == mf.spec_sha256("R1_TREND") for r in enters)
    assert trend.spec_sha256 != st.spec_sha256("R1_TREND")
    assert world.ledger.session_done(SESSION)
    await world.desk.session()                                             # once per day
    assert len(world.ledger.positions("OPEN")) == 2
    assert world.ledger.cash() < world.loaded.settings.robinhood.learning_cash_usd


async def test_cost_gate_refuses_an_expensive_round_trip(world: World) -> None:
    await world.prime()
    world.desk.settings = world.desk.settings.model_copy(update={"max_roundtrip_cost_fraction": D("0.001")})
    await world.desk.session()
    assert world.ledger.positions("OPEN") == []
    skips = [r for r in world.decisions() if r["action"] == "SKIP"]
    assert skips and all("ROUNDTRIP_COST_ABOVE_CEILING" in r["reasons"] for r in skips)


async def test_crossed_quotes_are_recorded_but_do_not_bypass_exact_size_costs(world: World,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    world.fake.crossed = True
    await world.prime()
    quotes = world.market.latest_quotes()
    assert quotes["BTC-USD"].bid == world.mids["BTC-USD"] * D("1.01")
    assert quotes["BTC-USD"].ask == world.mids["BTC-USD"] * D("0.99")
    assert quotes["BTC-USD"].crossed and quotes["BTC-USD"].spread_fraction < 0
    monkeypatch.setattr("desk.rh.view.utc_now", world.clock)
    coin = RhView(world.paths, world.loaded).coin("BTC-USD", "1M")
    assert coin is not None and coin["quote_crossed"]
    world.desk.settings = world.desk.settings.model_copy(update={"max_roundtrip_cost_fraction": D("0.001")})
    await world.desk.session()
    assert world.fake.calls > 0 and world.ledger.positions("OPEN") == []
    skips = [r for r in world.decisions() if r["action"] == "SKIP"]
    assert skips and all("ROUNDTRIP_COST_ABOVE_CEILING" in r["reasons"] for r in skips)


async def test_hard_stop_sells_at_the_worse_estimate_and_books_the_loss(world: World) -> None:
    await world.prime()
    await world.desk.session()
    trend = next(p for p in world.ledger.positions("OPEN") if p.strategy_id == "R1_TREND")
    world.mids["ALT-USD"] = D(40)                                          # collapse below the stop
    world.clock.at = NOW + timedelta(minutes=5)
    await world.desk.poll_quotes()
    closed = {p.id: p for p in world.ledger.positions("CLOSED")}
    assert closed[trend.id].exit_reason == "HARD_STOP"
    assert closed[trend.id].realized_pnl is not None and closed[trend.id].realized_pnl < 0
    assert closed[trend.id].exit_credit == (trend.quantity * D(40) * D("0.996")).quantize(D("0.000001"),
                                                                                           rounding="ROUND_FLOOR")
    btc = next(p for p in world.ledger.positions("OPEN") if p.strategy_id == "B1_HOLD_BTC")
    world.mids["BTC-USD"] = D(1)
    await world.desk.poll_quotes()
    assert btc.id in {p.id for p in world.ledger.positions("OPEN")}       # no stop on the BTC baseline


async def test_ledger_entries_and_closed_positions_never_change(world: World) -> None:
    await world.prime()
    await world.desk.session()
    pos = world.ledger.positions("OPEN")[0]
    with pytest.raises(sqlite3.IntegrityError):
        world.dbs.ledger.execute("UPDATE rh_positions SET entry_cost_micros = 1 WHERE id = ?", (pos.id,))
    with pytest.raises(sqlite3.IntegrityError):
        world.dbs.ledger.execute("DELETE FROM rh_cash")
    with pytest.raises(sqlite3.IntegrityError):
        world.dbs.market.execute("UPDATE rh_decisions SET reasons = 'X'")
    world.ledger.update_peak(pos.id, pos.peak_close + 1)                   # allowed while OPEN
    sell = Estimate(pos.symbol, "MM", "SELL", pos.quantity, D(1), D(0), pos.quantity, None, None, NOW, "x")
    world.ledger.close_position(pos.id, at=NOW, sell=sell, reason="TEST")
    with pytest.raises(sqlite3.IntegrityError):
        world.dbs.ledger.execute("UPDATE rh_positions SET exit_reason = 'Y' WHERE id = ?", (pos.id,))
    with pytest.raises(StorageError):
        world.ledger.close_position(pos.id, at=NOW, sell=sell, reason="AGAIN")


async def test_cumulative_loss_halts_entries(world: World) -> None:
    await world.prime()
    world.desk.settings = world.desk.settings.model_copy(update={"learning_max_cumulative_loss_usd": D("0.01")})
    await world.desk.session()
    trend = next(p for p in world.ledger.positions("OPEN") if p.strategy_id == "R1_TREND")
    world.mids["ALT-USD"] = D(40)
    await world.desk.poll_quotes()
    assert world.ledger.halted() == "CUMULATIVE_LOSS"
    assert trend.id in {p.id for p in world.ledger.positions("CLOSED")}


async def test_without_a_key_signals_are_recorded_but_nothing_trades(tmp_path: Path) -> None:
    w = World(tmp_path, key=False)
    try:
        assert w.suite.key_problem is not None
        w.market.save_pairs(await w.fake.trading_pairs(), NOW)       # a pair list fetched while a key worked
        await w.desk.session()
        assert w.ledger.positions() == []
        skips = [r for r in w.decisions() if r["action"] == "SKIP"]
        assert {r["strategy_id"] for r in skips} >= {"B1_HOLD_BTC", "R1_TREND"}
        assert all("NO_ROBINHOOD_PRICE" in r["reasons"] for r in skips)
    finally:
        await w.suite.aclose()
        w.dbs.close()


async def test_dashboard_view_shows_the_100_dollar_account(world: World) -> None:
    await world.prime()
    await world.desk.session()
    await world.desk.snapshot_equity()
    snap = RhView(world.paths, world.loaded).snapshot()
    account = snap["account"]
    assert D(account["starting_capital"]) == D(100)
    assert D(account["equity"]) < D(100) + 1 and D(account["equity"]) > D(95)
    assert {p["strategy_id"] for p in snap["positions"]} == {"B1_HOLD_BTC", "R1_TREND"}
    assert {s["id"] for s in snap["strategies"]} == set(st.ORDER)
    coin = RhView(world.paths, world.loaded).coin("ALT-USD", "1M")
    assert coin is not None and coin["eligible"] and coin["points"]
    r1 = next(s for s in coin["signals"] if s["strategy_id"] == "R1_TREND")
    assert all(c["met"] for c in r1["conditions"])
    assert RhView(world.paths, world.loaded).coin("NOPE-USD", "1M") is None

