"""KESTREL Stocks PAPER on a synthetic market: orders decided on a close fill at the next open (stocks) or the next
chain (options), stops and holds exit, options settle at expiry, corporate actions are paid, and the books can't be
rewritten."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from desk.core.config import load_config
from desk.stocks.analysis import Analyzer, Day
from desk.stocks.contracts import Dividend, Split
from desk.stocks.paper import fills
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.engine import PaperEngine, sessions_between
from desk.stocks.paper.strategies import BY_STRATEGY, Strategy
from desk.stocks.prices import load_panel
from desk.storage.db import DbKind, connect, migrate
from tests.conftest import REPO
from tests.stocks_world import StocksWorld, add_chains, make_world

SETTINGS = load_config(REPO / "config").settings.stocks
OPTIONED = ["CHEAP", "SQZ", "S10", "S11", "S12"]


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> StocksWorld:
    w = make_world(tmp_path_factory.mktemp("paper"))
    add_chains(w, OPTIONED, w.days[-45:])
    yield w
    w.close()


@pytest.fixture(scope="module")
def analyses(world: StocksWorld) -> dict[date, Day]:
    analyzer = Analyzer(world.market, world.research, SETTINGS)
    return {d: analyzer.build(load_panel(world.market, world.days[0], d), d, now=at(d)) for d in world.days[-45:]}


def at(d: date) -> datetime:
    return datetime.combine(d, time(22, 30), UTC)


def book(tmp_path: Path) -> PaperBook:
    conn = connect(tmp_path / "ledger.db", DbKind.STOCKS_LEDGER)
    migrate(conn, DbKind.STOCKS_LEDGER)
    return PaperBook(conn)


def run(world: StocksWorld, analyses: dict[date, Day], b: PaperBook, strategies: tuple[Strategy, ...],
        days: list[date]) -> PaperEngine:
    engine = PaperEngine(world.market, b, SETTINGS, strategies)
    for d in days:
        rep = engine.step(d, at(d), analyses.get(d))
        assert rep.skipped is None, rep.skipped
    return engine


def custom(sid: str, asset: str, style: str, **params: object) -> Strategy:
    return Strategy(sid, 1, sid, asset, style, "test", tuple(sorted(params.items())))


def test_stock_orders_fill_at_the_next_open_and_a_day_steps_once(world: StocksWorld, analyses: dict[date, Day],
                                                                tmp_path: Path) -> None:
    b = book(tmp_path)
    days = world.days[-45:-40]
    engine = run(world, analyses, b, (BY_STRATEGY["spy_hold"], BY_STRATEGY["committee_top"]), days[:1])
    assert b.positions() == [] and len(b.pending()) == 11                 # decided on the close, nothing filled yet
    run(world, analyses, b, (BY_STRATEGY["spy_hold"], BY_STRATEGY["committee_top"]), days[1:2])
    spy = b.positions("spy_hold")
    assert len(spy) == 1 and spy[0].entry_day == days[1]
    bar = next(x for x in world.market.bars("SPY", days[1]) if x.day == days[1])
    assert spy[0].legs[0]["entry_price"] == pytest.approx(bar.open * (1 + fills.SLIPPAGE_OTHER)) or spy[0].legs[0][
        "entry_price"] == pytest.approx(bar.open * (1 + fills.SLIPPAGE_LIQUID))
    assert b.cash("spy_hold") == pytest.approx(10_000 - spy[0].entry_cost, abs=0.01)
    assert len(b.positions("committee_top")) == 10
    for p in b.positions():
        decided = next(d for d in b.decisions(p.account) if d["action"] == "ENTER" and d["symbol"] == p.symbol)
        assert date.fromisoformat(decided["asof"]) < p.entry_day           # never filled on the close it was decided on
    assert engine.step(days[1], at(days[1]), analyses[days[1]]).skipped     # a day steps once
    assert engine.step(days[0], at(days[0]), analyses[days[0]]).skipped     # and never backwards
    equity = b.equity("spy_hold")
    assert [e[0] for e in equity] == days[:2] and equity[0][3] == pytest.approx(10_000)


def test_stops_and_holds_exit_and_events_respect_capacity(world: StocksWorld, analyses: dict[date, Day],
                                                         tmp_path: Path) -> None:
    b = book(tmp_path)
    tight = custom("tight", "stock", "event", rule="random", n=6, seed="t", hold=50, stop=0.0001, size=0.1, max_open=4)
    timed = custom("timed", "stock", "event", rule="random", n=6, seed="h", hold=2, stop=0.9, size=0.1, max_open=3)
    days = world.days[-40:-30]
    run(world, analyses, b, (tight, timed), days)
    closed = b.positions("tight", state="CLOSED")
    assert closed and all(p.exit_reason and p.exit_reason.startswith("stop at") for p in closed)
    for p in closed:
        bar = next(x for x in world.market.bars(p.symbol, p.exit_day) if x.day == p.exit_day)
        stop = p.legs[0]["entry_price"] * (1 - 0.0001)
        assert p.exit_credit == pytest.approx(p.legs[0]["quantity"] * min(stop, bar.open) * (1 - fills.SLIPPAGE_OTHER),
                                              rel=2e-3)
    held = b.positions("timed", state="CLOSED")
    assert held and all(p.exit_reason == "held 2 sessions" for p in held)
    assert all(sessions_between(p.entry_day, p.exit_day) == 3 for p in held if p.exit_day)    # decided at 2, filled next
    assert max(len(b.positions("timed")), 0) <= 3


def test_option_trades_fill_at_the_ask_mark_at_the_mid_and_expire_at_intrinsic(world: StocksWorld,
                                                                              analyses: dict[date, Day],
                                                                              tmp_path: Path) -> None:
    b = book(tmp_path)
    calls = custom("calls", "option", "event", rule="random_optioned", n=2, seed="c", structure="long_call", delta=0.5,
                   dte=[2, 40], premium=0.02, take_profit=50.0, stop=-1.0, exit_dte=-1, max_open=2)
    spreads = custom("spreads", "option", "event", rule="random_optioned", n=1, seed="s", structure="bull_call_spread",
                     delta=0.55, short_delta=0.3, dte=[20, 80], premium=0.02, take_profit=50.0, stop=-1.0, exit_dte=-1,
                     max_open=1)
    days = world.days[-28:]                    # option summaries (and so IV30) exist for the last 30 sessions
    run(world, analyses, b, (calls, spreads), days[:2])
    opened = b.positions("calls")
    assert opened
    p = opened[0]
    chain = world.market.option_chain(p.symbol, days[1])[1]
    q = next(x for quotes in chain.values() for x in quotes if x.occ == p.legs[0]["occ"])
    units = p.legs[0]["quantity"]
    assert p.legs[0]["entry_price"] == q.ask and p.entry_cost == pytest.approx(units * q.ask * 100, abs=0.01)
    assert p.entry_fees == pytest.approx(units * 0.65) and p.max_loss == pytest.approx(p.entry_cost + p.entry_fees, abs=0.01)
    assert p.entry_cost + p.entry_fees <= 50_000 * 0.02 * 1.1                # the premium budget
    held = b.held_expirations()
    assert p.expires in held[p.symbol]
    spread = b.positions("spreads")[0]
    assert [leg["quantity"] > 0 for leg in spread.legs] == [True, False] and spread.legs[0]["strike"] < spread.legs[1]["strike"]
    run(world, analyses, b, (calls, spreads), days[2:])
    expired = [x for x in b.positions("calls", state="CLOSED") if x.exit_reason and x.exit_reason.startswith("expired")]
    assert expired
    for x in expired:
        close = next(bar.close for bar in world.market.bars(x.symbol, x.expires) if bar.day == x.expires)
        assert x.exit_credit == pytest.approx(fills.settle(x.legs, close), abs=0.01)
        assert x.exit_day == x.expires
    marks = b.positions("spreads") + b.positions("spreads", state="CLOSED")
    assert marks[0].marks or marks[0].state == "CLOSED"


def test_options_respect_the_risk_caps(world: StocksWorld, analyses: dict[date, Day], tmp_path: Path) -> None:
    b = book(tmp_path)
    greedy = custom("greedy", "option", "event", rule="random_optioned", n=5, seed="g", structure="long_call", delta=0.5,
                    dte=[20, 80], premium=0.05, take_profit=50.0, stop=-1.0, exit_dte=-1, max_open=5)
    run(world, analyses, b, (greedy,), world.days[-28:-23])
    risk = sum(p.max_loss or 0 for p in b.positions("greedy"))
    assert 0 < risk <= 50_000 * 0.10 * 1.01                                # all option premium ≤ 10% of equity
    assert all(p.entry_cost + p.entry_fees <= 50_000 * 0.02 * 1.1 for p in b.positions("greedy"))   # 2% each


def test_splits_and_dividends_are_paid_on_held_stock(world: StocksWorld, analyses: dict[date, Day],
                                                     tmp_path: Path) -> None:
    b = book(tmp_path)
    days = world.days[-30:-24]
    run(world, analyses, b, (BY_STRATEGY["spy_hold"],), days[:2])
    p = b.positions("spy_hold")[0]
    world.market.save_dividends([Dividend("SPY", days[3], 1.5, days[4], 4, "CD")], at(days[0]))
    world.market.save_splits([Split("SPY", days[4], 1, 2)], at(days[0]))
    try:
        engine = run(world, analyses, b, (BY_STRATEGY["spy_hold"],), days[2:])
        paid = [e for e in b.events("spy_hold") if e["kind"] == "DIVIDEND"]
        assert len(paid) == 1 and paid[0]["amount"] == pytest.approx(round(p.legs[0]["quantity"] * 1.5, 2))
        assert engine.shares(p, days[5]) == pytest.approx(p.legs[0]["quantity"] * 2)
    finally:
        world.market.conn.execute("DELETE FROM stk_splits WHERE symbol = 'SPY'")
        world.market.conn.execute("DELETE FROM stk_dividends WHERE symbol = 'SPY'")


def test_the_books_cannot_be_rewritten(world: StocksWorld, analyses: dict[date, Day], tmp_path: Path) -> None:
    b = book(tmp_path)
    timed = custom("ledger_check", "stock", "event", rule="random", n=2, seed="x", hold=1, stop=0.9, size=0.1, max_open=2)
    run(world, analyses, b, (timed,), world.days[-20:-15])
    closed = b.positions("ledger_check", state="CLOSED")[0]
    for sql in ("UPDATE stk_positions SET exit_reason = 'x' WHERE id = ?", "DELETE FROM stk_positions WHERE id = ?"):
        with pytest.raises(Exception, match=r"never changes|append-only"):
            b.conn.execute(sql, (closed.id,))
    with pytest.raises(Exception, match="append-only"):
        b.conn.execute("UPDATE stk_cash SET amount_micros = 0")
    with pytest.raises(Exception, match="append-only"):
        b.conn.execute("DELETE FROM stk_equity")
    assert b.cash("ledger_check") + sum(p.last_value or 0 for p in b.positions("ledger_check")) == pytest.approx(
        b.equity("ledger_check")[-1][3], abs=0.02)
    total_pnl = sum(p.pnl or 0 for p in b.positions("ledger_check", state="CLOSED"))
    in_open = sum(p.entry_cost + p.entry_fees for p in b.positions("ledger_check"))
    assert b.cash("ledger_check") - b.deposits("ledger_check") + in_open == pytest.approx(total_pnl, abs=0.05)


def test_every_preregistered_strategy_runs(world: StocksWorld, analyses: dict[date, Day], tmp_path: Path) -> None:
    b = book(tmp_path)
    from desk.stocks.paper.strategies import STRATEGIES
    engine = run(world, analyses, b, STRATEGIES, world.days[-12:])
    assert set(b.accounts()) == {s.id for s in STRATEGIES}
    assert all(len(b.equity(s.id)) == 12 for s in STRATEGIES)
    assert b.positions("spy_hold") and b.positions("random_10")
    registered = {r["name"] for r in world.market.conn.execute("SELECT name FROM stk_specs WHERE name LIKE 'stocks_paper_%'")}
    assert registered >= {f"stocks_paper_{s.id}" for s in STRATEGIES}
    assert engine.rate > 0
    from desk.stocks.paper.report import report
    rep = report(b, world.market, STRATEGIES)
    by_id = {a["id"]: a for a in rep["accounts"]}
    assert set(by_id) == {s.id for s in STRATEGIES}
    assert by_id["spy_hold"]["grade"]["grade"] == "info" and by_id["random_calls"]["grade"]["grade"] == "info"
    assert by_id["committee_top"]["grade"]["grade"] == "early"                 # 12 sessions is nothing
    assert by_id["committee_top"]["vs_baseline"]["n"] == 11 and by_id["committee_top"]["sessions"] == 12
    assert by_id["spy_hold"]["return"] == pytest.approx(by_id["spy_hold"]["equity"] / 10_000 - 1)
    assert by_id["random_calls"]["deposits"] == 50_000
    assert rep["accounts"][-1]["baseline"]                                     # baselines listed last
