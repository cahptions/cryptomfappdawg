"""KESTREL Stocks PAPER on a synthetic market: orders decided on a close fill at the next open (stocks) or the next
chain (options), stops and holds exit, options settle at expiry, corporate actions are paid, and the books can't be
rewritten."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from itertools import pairwise
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


def test_late_restart_never_fills_an_open_before_the_order_was_created(world: StocksWorld, tmp_path: Path) -> None:
    b = book(tmp_path)
    days = world.days[-6:]
    # Monday's stale analysis was only available after Tuesday closed: Tuesday's opening is already gone.
    analyzer = Analyzer(world.market, world.research, SETTINGS)
    d = analyzer.build(load_panel(world.market, world.days[0], days[0]), days[0], now=at(days[1]))
    engine = PaperEngine(world.market, b, SETTINGS, (BY_STRATEGY["spy_hold"],))
    engine.step(days[0], at(days[1]), d)
    engine.step(days[1], at(days[1]), None)
    assert b.positions() == [] and len(b.pending()) == 1
    engine.step(days[2], at(days[2]), None)
    assert b.positions()[0].entry_day == days[2]


def test_stop_cancels_pending_entries_and_still_marks_existing_positions(world: StocksWorld,
                                                                       analyses: dict[date, Day], tmp_path: Path) -> None:
    b = book(tmp_path)
    days = world.days[-45:-40]
    engine = PaperEngine(world.market, b, SETTINGS, (BY_STRATEGY["spy_hold"],))
    engine.step(days[0], at(days[0]), analyses[days[0]])
    engine.step(days[1], at(days[1]), analyses[days[1]], allow_entries=False)
    assert not b.pending() and not b.positions()
    engine.step(days[2], at(days[2]), analyses[days[2]])
    engine.step(days[3], at(days[3]), analyses[days[3]])
    assert b.positions()
    engine.step(days[4], at(days[4]), analyses[days[4]], allow_entries=False)
    assert b.equity("spy_hold")[-1][0] == days[4] and b.positions()


def test_interrupted_session_rolls_back_all_accounts_and_can_retry(world: StocksWorld, analyses: dict[date, Day],
                                                                  tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = book(tmp_path)
    d = world.days[-45]
    engine = PaperEngine(world.market, b, SETTINGS, (BY_STRATEGY["spy_hold"], BY_STRATEGY["committee_top"]))
    original = b.snapshot
    calls = []

    def crash(*args: object) -> None:
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("simulated power loss")
        original(*args)

    monkeypatch.setattr(b, "snapshot", crash)
    with pytest.raises(RuntimeError, match="power loss"):
        engine.step(d, at(d), analyses[d])
    assert b.accounts() == [] and b.pending() == [] and b.last_step() is None
    monkeypatch.setattr(b, "snapshot", original)
    assert engine.step(d, at(d), analyses[d]).skipped is None
    assert len(b.accounts()) == 2 and len(b.pending()) == 11


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


def test_lotto_strategies_buy_the_recorded_lotto_picks_and_random_lottos_pass_the_same_filters(
        world: StocksWorld, analyses: dict[date, Day], tmp_path: Path) -> None:
    """PAPER buys the exact contract the Plays Lottos section recorded, the same dollars per pick, at the next session's
    ask within its limit (a price that ran away overnight cancels, as a real limit order would); one version sells at
    triple, one holds to expiry; random lottos are the baseline both are judged against."""
    from dataclasses import replace

    from desk.stocks.paper.report import report
    from desk.stocks.paper.strategies import LOTTO_ASK

    days = world.days[-28:]

    def quotes(symbol: str, d: date) -> dict[str, object]:
        return {q.occ: q for qs in world.market.option_chain(symbol, d)[1].values() for q in qs}

    # The synthetic market has few short-dated contracts, so the test widens the expiry window to find a pick whose
    # next-session ask stays inside the order's limit; the shipped strategies keep 2-14 days.
    found = None
    for d0, d1 in pairwise(days):
        s0 = next(b.close for b in world.market.bars("CHEAP", d0) if b.day == d0)
        later = quotes("CHEAP", d1)
        for q in sorted(quotes("CHEAP", d0).values(), key=lambda q: q.ask or 1e9):
            nxt = later.get(q.occ)
            if (q.kind == "call" and q.strike > s0 and fills.fair_spread(q) and 2 <= (q.expiration - d0).days <= 40
                    and nxt is not None and nxt.ask is not None and nxt.ask <= fills.limit(q.ask) and q.ask * 100 < 300):
                found = d0, q
                break
        if found:
            break
    assert found, "the synthetic market has a fillable out-of-the-money call"
    d0, pick = found
    play = {"rank": 1, "symbol": "CHEAP", "reasons": ["Pays 4.0× its cost on a one-standard-deviation move"],
            "legs": [{"occ": pick.occ, "kind": pick.kind, "strike": pick.strike,
                      "expiration": pick.expiration.isoformat(), "side": "buy"}]}
    with_plays = {**analyses, d0: replace(analyses[d0], ctx=replace(analyses[d0].ctx, plays={"lottos": [play]}))}
    def widened(sid: str) -> Strategy:
        params = {**BY_STRATEGY[sid].p, "dte": [2, 40]}
        if "compare" in params:
            params["compare"] = "t_random_lottos"
        return replace(BY_STRATEGY[sid], id=f"t_{sid}", params=tuple(sorted(params.items())))

    wide = tuple(widened(sid) for sid in ("random_lottos", "lotto_picks", "lotto_runners"))
    b = book(tmp_path)
    at_d0 = days.index(d0)
    run(world, with_plays, b, wide, days[at_d0:at_d0 + 3])
    for sid, target in (("t_lotto_picks", 2.0), ("t_lotto_runners", 1e9)):
        [p] = b.positions(sid) + b.positions(sid, state="CLOSED")
        assert p.symbol == "CHEAP" and p.legs[0]["occ"] == pick.occ and p.structure == "long_call"
        assert p.entry_day > d0                                               # filled at a later session's quote
        units = int(50_000 * 0.006 // (pick.ask * 100 + fills.OPTION_FEE_PER_CONTRACT))   # $300 at the decision price
        assert p.legs[0]["quantity"] == units and p.plan["take_profit"] == target
        assert p.entry_cost + p.entry_fees <= units * (fills.limit(pick.ask) * 100 + fills.OPTION_FEE_PER_CONTRACT)
        assert any("Plays Lottos #1" in r for r in enter_reasons(b, sid))
    for p in b.positions("t_random_lottos") + b.positions("t_random_lottos", state="CLOSED"):
        [leg] = p.legs
        assert p.structure == f"long_{leg['kind']}"
    for d in b.decisions(limit=500):
        if d["account"] == "t_random_lottos" and d["action"] == "ENTER":
            assert all(LOTTO_ASK[0] <= leg["decision_price"] <= LOTTO_ASK[1] for leg in d["evidence"]["legs"])
    rep = {a["id"]: a for a in report(b, world.market, wide)["accounts"]}
    assert rep["t_random_lottos"]["baseline"] and rep["t_lotto_picks"]["params"]["compare"] == "t_random_lottos"
    assert rep["t_lotto_picks"]["vs_baseline"] is not None                    # judged against random lottos
    shipped = {sid: BY_STRATEGY[sid].p for sid in ("random_lottos", "lotto_picks", "lotto_runners")}
    assert all(p["dte"] == [2, 14] and p["premium"] == 0.006 for p in shipped.values())
    assert shipped["lotto_picks"]["take_profit"] == 2.0 and shipped["lotto_runners"]["take_profit"] > 1e6


def enter_reasons(b: PaperBook, account: str) -> list[str]:
    return [str(d["reasons"]) for d in b.decisions(limit=500) if d["account"] == account and d["action"] == "ENTER"]


def test_the_runner_hands_paper_the_lotto_picks_recorded_for_the_session(tmp_path: Path) -> None:
    import json

    from desk.core.paths import DeskPaths
    from desk.stocks.repo import StocksDatabases
    from desk.stocks.runner import recorded_plays

    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    try:
        legs = [{"occ": "XYZ261120C00055000", "kind": "call", "strike": 55.0, "expiration": "2026-11-20", "side": "buy"}]
        for session, category, rank in ((date(2026, 11, 3), "lottos", 2), (date(2026, 11, 3), "lottos", 1),
                                        (date(2026, 11, 3), "edge", 1), (date(2026, 11, 2), "lottos", 1)):
            dbs.research.execute(
                "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
                " event_strike, event_width, entry, max_loss, legs_json, underlying, play_json, method, recorded_at)"
                " VALUES (?,?,?,?,'BUY','long_call',?,'itm','call',55,0,0.2,20.65,?,50,?,'m','2026-11-03T21:00:00+00:00')",
                (session.toordinal(), category, rank, f"S{rank}", date(2026, 11, 20).toordinal(), json.dumps(legs),
                 json.dumps({"reasons": [f"reason {rank}"]})))
        got = recorded_plays(dbs.research, date(2026, 11, 3), ("lottos",))
        assert [(p["rank"], p["symbol"], p["reasons"]) for p in got["lottos"]] == [(1, "S1", ["reason 1"]),
                                                                                  (2, "S2", ["reason 2"])]
        assert got["lottos"][0]["legs"] == legs and set(got) == {"lottos"}
    finally:
        dbs.close()
