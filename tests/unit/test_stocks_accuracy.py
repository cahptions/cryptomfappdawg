"""Accuracy of output (desk.stocks.accuracy): each invariant catches a deliberately broken record and passes a valid
one; the fixes it led to are tested where they were made."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.stocks import accuracy as acc
from desk.stocks import plays as pl
from desk.stocks.contracts import Bar
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.engine import PaperEngine, StepReport
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.paper.strategies import BY_STRATEGY
from desk.storage.db import DbKind, connect, migrate
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world

SETTINGS = load_config(REPO / "config").settings.stocks

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "desk" / "storage" / "migrations" / "stocks_research"


def test_the_mark_floor_migration_corrects_only_marks_below_minus_100_percent() -> None:
    """Marks recorded before play_marks.profit stopped a loss at the money at risk are corrected to exactly -100%;
    every other mark, settlements and the append-only rule are unchanged."""
    conn = sqlite3.connect(":memory:")
    migrate(conn, DbKind.STOCKS_RESEARCH)
    rows = [("lotto", -14.0, -1.0769, "quoted"),          # money at risk 13.0: corrected to -13.0
            ("fly", -91.6, -1.198, "quoted"),              # an iron butterfly bought back above its width
            ("fine", -6.5, -0.5, "quoted"), ("won", 20.0, 0.4, "settled"), ("zero", -13.0, -1.0, "estimated")]
    for i, (category, pnl, pct, status) in enumerate(rows):
        conn.execute("INSERT INTO stk_play_marks(session, category, rank, twin, day, status, value, pnl, pnl_pct, "
                     "recorded_at) VALUES (1, ?, 1, 0, ?, ?, 0, ?, ?, 'x')", (category, i + 1, status, pnl, pct))
    conn.execute("INSERT INTO stk_play_marks_provisional(session, category, rank, twin, day, value, pnl, pnl_pct, "
                 "computed_at) VALUES (1, 'lotto', 1, 0, 9, 0, -14.0, -1.0769, 'x')")
    sql = (MIGRATIONS / "0018_mark_floor.sql").read_text()
    conn.executescript(sql)                               # idempotent: applied again changes nothing more
    got = {r[0]: (round(r[1], 4), r[2]) for r in conn.execute("SELECT category, pnl, pnl_pct FROM stk_play_marks")}
    assert got["lotto"] == (round(-14.0 / 1.0769, 4), -1.0)
    assert got["fly"] == (round(-91.6 / 1.198, 4), -1.0)
    assert got["fine"] == (-6.5, -0.5) and got["won"] == (20.0, 0.4) and got["zero"] == (-13.0, -1.0)
    assert conn.execute("SELECT pnl_pct FROM stk_play_marks_provisional").fetchone()[0] == -1.0
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE stk_play_marks SET pnl = 0")


# ── a small world to break on purpose ─────────────────────────────────────────────────────────────
def _occ(symbol: str, kind: str, strike: float, expiration: date) -> str:
    return f"{symbol}{expiration:%y%m%d}{kind[0].upper()}{round(strike * 1000):08d}"


def _leg(symbol: str, kind: str, strike: float, expiration: date, side: str = "buy") -> dict[str, Any]:
    return {"occ": _occ(symbol, kind, strike, expiration), "kind": kind, "side": side, "strike": strike,
            "expiration": expiration.isoformat(), "bid": 1.0, "ask": 1.1}


def record_play(research: sqlite3.Connection, session: date, symbol: str, expiration: date, *, entry: float = 1.1,
                strike: float = 50.0, kind: str = "call", category: str = "edge", rank: int = 1,
                play: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    legs = [_leg(symbol, kind, strike, expiration)]
    research.execute(
        "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind, "
        "event_strike, event_width, entry, max_loss, legs_json, underlying, play_json, method, recorded_at) VALUES "
        "(?, ?, ?, ?, 'BUY', ?, ?, 'itm', ?, ?, 0, ?, ?, ?, 50, ?, 'm', 'x')",
        (session.toordinal(), category, rank, symbol, f"long_{kind}", expiration.toordinal(), kind, strike, entry,
         entry * 100 + FEE, json.dumps(legs), json.dumps(play or {})))
    return legs


@pytest.fixture
def world(tmp_path: Path) -> Any:
    w = make_world(tmp_path, n=12)
    yield w
    w.close()


def sources(world: Any, now: datetime = NOW, **views: Any) -> acc.Sources:
    return acc.Sources(world.paths, now, world.dbs.market, world.dbs.research, None, None,
                       {"plays": views.get("plays"), "plays-performance": views.get("performance")}, 5)


def test_a_play_is_graded_on_its_settlement_sessions_own_close_never_an_earlier_one(world: Any) -> None:
    """plays.grade took the latest close within four days of the expiry, so a stock missing from the expiry's bars
    was graded on the session before. It now waits for the settlement session's close (bars, else the Schwab
    history gap repair keeps), as the marks do; the accuracy check flags an outcome graded on any other close."""
    expiry, before = world.days[-1], world.days[-2]
    record_play(world.dbs.research, world.days[-6], "S10", expiry)
    world.dbs.market.execute("DELETE FROM stk_bars WHERE symbol = 'S10' AND day = ?", (expiry.toordinal(),))
    assert pl.grade(world.market, world.research, NOW) == 0                 # waits: no close of the expiry yet
    close = world.dbs.market.execute("SELECT close FROM stk_bars WHERE symbol = 'S10' AND day = ?",
                                     (before.toordinal(),)).fetchone()[0]
    world.dbs.research.execute("INSERT INTO stk_track_closes(symbol, session, close, source, fetched_at) VALUES "
                               "('S10', ?, ?, 'schwab_history', 'x')", (expiry.toordinal(), close + 7))
    assert pl.grade(world.market, world.research, NOW) == 1
    graded = world.dbs.research.execute("SELECT underlying_close FROM stk_play_outcomes").fetchone()[0]
    assert graded == pytest.approx(close + 7)
    found = acc.check_outcomes(sources(world))
    assert found.checked == 1 and found.violations == 1 and "graded without" in found.examples[0]
    world.market.save_bars(expiry, [Bar("S10", expiry, close + 7, close + 7, close + 7, close + 7, 1e6)], "t", NOW)
    assert acc.check_outcomes(sources(world)).violations == 0


def test_a_paper_option_expiry_waits_for_its_settlement_sessions_close(world: Any, tmp_path: Path) -> None:
    """A PAPER option whose expiry passed while a pass was missed settled on the last close before the expiry when
    that day's bar was missing; it now waits for the expiry's own close."""
    expiry, later = world.days[-2], world.days[-1]
    conn = connect(tmp_path / "ledger.db", DbKind.STOCKS_LEDGER)
    migrate(conn, DbKind.STOCKS_LEDGER)
    book = PaperBook(conn)
    strategy = BY_STRATEGY["random_calls"]
    at = datetime.combine(world.days[-6], time(20), UTC)
    book.open_account(strategy.id, 10_000, at, world.days[-6])
    decision = book.decide(at, world.days[-6], strategy.id, strategy.spec_sha256, "S10", "ENTER", [], {})
    leg = {**_leg("S10", "call", 1.0, expiry), "quantity": 1, "entry_price": 1.0}
    book.open_position(account=strategy.id, spec_sha256=strategy.spec_sha256, asset="option", symbol="S10",
                       structure="long_call", legs=[leg], decision_id=decision, at=at, day=world.days[-6], cost=100,
                       fees=FEE, max_loss=100 + FEE, stop_price=None, expires=expiry, plan={})
    world.dbs.market.execute("DELETE FROM stk_bars WHERE symbol = 'S10' AND day = ?", (expiry.toordinal(),))
    engine = PaperEngine(world.market, book, SETTINGS, (strategy,))
    report = StepReport(later)
    engine._expiries(later, datetime.combine(later, time(22), UTC), {}, report)
    assert report.closed == 0 and book.positions(strategy.id)                # waits for the expiry's close
    world.market.save_bars(expiry, [Bar("S10", expiry, 9, 9, 9, 9, 1e6)], "t", NOW)
    engine._expiries(later, datetime.combine(later, time(22), UTC), {}, report)
    closed = book.positions(strategy.id, state="CLOSED")[0]
    assert report.closed == 1 and closed.exit_credit == pytest.approx(800.0)  # intrinsic on that close: (9 - 1) x 100
    conn.close()


# ── each invariant: a broken record is caught, a valid one passes ─────────────────────────────────
def board(*plays: dict[str, Any], key: str = "plays", asof: str = "2026-10-21") -> dict[str, Any]:
    return {"asof": asof, "categories": [{"id": "edge", key: list(plays)}]}


def long_call(**extra: Any) -> dict[str, Any]:
    expiry = date(2026, 11, 20)
    play = {"symbol": "S10", "rank": 1, "action": "BUY", "structure": "long_call", "entry": 2.0, "cost": 200.65,
            "max_loss": 200.65, "breakeven": 52.0, "expiration": expiry.isoformat(), "dte": 30, "iv": 0.4,
            "legs": [_leg("S10", "call", 50.0, expiry)], "itm_kestrel": 0.4, "profit_kestrel": 0.3,
            "probability_targets": {"returns": {"0": 0.3, "25": 0.25, "50": 0.2, "100": 0.1}},
            "exit_plan": {"odds": {"take_profit": 0.3, "stop": 0.5}}}
    play.update(extra)
    return play


@pytest.mark.parametrize(("check", "broken"), [
    (acc.check_play_probabilities, {"itm_kestrel": 1.2}),
    (acc.check_play_probabilities, {"exit_plan": {"odds": {"take_profit": -0.1}}}),
    (acc.check_play_chance_order, {"probability_targets": {"returns": {"0": 0.3, "25": 0.25, "50": 0.28, "100": 0.1}}}),
    (acc.check_play_chance_order, {"calibrated": {"target50": {"shown": 0.4}, "target25": {"shown": 0.3}}}),
    (acc.check_play_chance_order, {"exit_plan": {"odds": {"take_profit": 0.6, "stop": 0.5}}}),
    (acc.check_play_risk, {"entry": 0.0}),
    (acc.check_play_risk, {"max_loss": 150.0}),
    (acc.check_play_breakevens, {"breakeven": 48.0}),
    (acc.check_play_expiration, {"expiration": "2026-10-16"}),
    (acc.check_option_quotes, {"iv": 9.0}),
])
def test_each_play_invariant_catches_a_broken_play_and_passes_a_valid_one(world: Any, check: Any,
                                                                         broken: dict[str, Any]) -> None:
    good = check(sources(world, plays=board(long_call())))
    assert good.checked > 0 and good.violations == 0, good.examples
    bad = check(sources(world, plays=board(long_call(**broken))))
    assert bad.violations == 1 and "S10" in bad.examples[0]


def test_break_evens_of_puts_spreads_and_straddles() -> None:
    expiry = date(2026, 11, 20)
    put = {"action": "BUY", "entry": 2.0, "legs": [_leg("S", "put", 50.0, expiry)]}
    assert not acc.breakeven_problems({**put, "breakeven": 48.0})
    assert acc.breakeven_problems({**put, "breakeven": 52.0})              # the wrong side of the strike
    spread = {"action": "SELL", "entry": 1.0, "legs": [_leg("S", "put", 50.0, expiry, "sell"),
                                                        _leg("S", "put", 45.0, expiry)]}
    assert not acc.breakeven_problems({**spread, "breakeven": 49.0})
    straddle = {"action": "BUY", "entry": 4.0, "legs": [_leg("S", "call", 50.0, expiry), _leg("S", "put", 50.0, expiry)]}
    assert not acc.breakeven_problems({**straddle, "breakevens": [46.0, 54.0]})
    assert acc.breakeven_problems({**straddle, "breakevens": [50.0, 54.0]})


def test_maximum_loss_must_cover_the_real_worst_case() -> None:
    expiry = date(2026, 11, 20)
    spread = [_leg("S", "put", 50.0, expiry, "sell"), _leg("S", "put", 45.0, expiry)]
    assert acc.worst_loss("SELL", 1.0, spread) == pytest.approx(400 + 2 * FEE)
    assert not acc.risk_problems("SELL", 1.0, 400 + 2 * FEE, spread)
    assert acc.risk_problems("SELL", 1.0, 300, spread)
    naked_call = [_leg("S", "call", 50.0, expiry, "sell")]
    assert acc.worst_loss("SELL", 1.0, naked_call) == float("inf")
    csp = [_leg("S", "put", 50.0, expiry, "sell")]
    assert acc.worst_loss("SELL", 1.0, csp) == pytest.approx(4900 + FEE)


def test_budget_and_horizon_bands(world: Any) -> None:
    ok = long_call(budget_band=[100.0, 250.0])
    assert acc.check_budget_bands(sources(world, plays=board(ok, key="budget_plays"))).violations == 0
    bad = long_call(budget_band=[250.0, 500.0])
    assert acc.check_budget_bands(sources(world, plays=board(bad, key="budget_plays"))).violations == 1
    assert acc.check_horizon_bands(sources(world, plays=board(long_call(horizon="month"), key="horizon_plays"))
                                   ).violations == 0
    assert acc.check_horizon_bands(sources(world, plays=board(long_call(horizon="leaps"), key="horizon_plays"))
                                   ).violations == 1


def insert_mark(research: sqlite3.Connection, session: date, day: date, pct: float, status: str = "quoted") -> None:
    research.execute("INSERT INTO stk_play_marks(session, category, rank, twin, day, status, value, pnl, pnl_pct, "
                     "recorded_at) VALUES (?, 'edge', 1, 0, ?, ?, 0, ?, ?, 'x')",
                     (session.toordinal(), day.toordinal(), status, pct * 110.65, pct))


def test_marks_never_below_minus_100_percent_and_settled_only_on_the_settlement_session(world: Any) -> None:
    session, expiry = world.days[-8], world.days[-3]
    record_play(world.dbs.research, session, "S10", expiry)
    insert_mark(world.dbs.research, session, world.days[-7], -0.5)
    insert_mark(world.dbs.research, session, expiry, -1.0, "settled")
    assert acc.check_mark_floor(sources(world)).violations == 0
    assert acc.check_mark_dates(sources(world)).violations == 0
    assert acc.check_settlement_waits(sources(world)).violations == 0
    insert_mark(world.dbs.research, session, world.days[-6], -1.04)
    insert_mark(world.dbs.research, session, world.days[-5], -0.9, "settled")      # settled before its expiry
    insert_mark(world.dbs.research, session, world.days[-1], -0.9)                 # a mark after settlement
    floor, dates = acc.check_mark_floor(sources(world)), acc.check_mark_dates(sources(world))
    assert floor.violations == 1 and "-104.0%" in floor.examples[0]
    assert dates.violations == 2


def test_a_play_past_its_settlement_with_the_close_stored_must_be_settled(world: Any) -> None:
    session, expiry = world.days[-8], world.days[-4]
    record_play(world.dbs.research, session, "S10", expiry)
    insert_mark(world.dbs.research, session, world.days[-7], -0.5)
    found = acc.check_settlement_waits(sources(world))
    assert found.violations == 1 and "no settled mark" in found.examples[0]
    record_play(world.dbs.research, session, "S11", world.days[-1], rank=2)        # still within its grace day
    assert acc.check_settlement_waits(sources(world)).violations == 1


def test_an_outcome_must_follow_from_its_payoff(world: Any) -> None:
    session, expiry = world.days[-8], world.days[-4]
    record_play(world.dbs.research, session, "S10", expiry, strike=1.0)
    close = world.dbs.market.execute("SELECT close FROM stk_bars WHERE symbol = 'S10' AND day = ?",
                                     (expiry.toordinal(),)).fetchone()[0]
    payoff = close - 1.0
    right = (payoff - 1.1 - FEE / 100) / (1.1 + FEE / 100)
    world.dbs.research.execute("INSERT INTO stk_play_outcomes VALUES (?, 'edge', 1, 'x', ?, 1, ?, ?)",
                               (session.toordinal(), close, payoff, right))
    assert acc.check_outcomes(sources(world)).violations == 0
    world.dbs.research.execute("INSERT INTO stk_play_outcomes VALUES (?, 'edge', 2, 'x', ?, 1, ?, ?)",
                               (session.toordinal(), close, payoff, right + 0.5))
    record_play(world.dbs.research, session, "S10", expiry, strike=1.0, rank=2)
    found = acc.check_outcomes(sources(world))
    assert found.violations == 1 and "net return" in found.examples[0]


def row(pct: float, twin: float | None = None, **extra: Any) -> dict[str, Any]:
    return {"latest": {"pct": pct, "status": "quoted"}, "twin": {"pct": twin}, "label": "S", "symbol": "S",
            "peak": max(pct, 0.1), "worst": min(pct, -0.1), "days_held": 2, "cost": 110.0, **extra}


def test_history_summaries_are_the_arithmetic_of_their_rows() -> None:
    from desk.stocks.play_history import _summary

    rows = [row(-0.5, -0.2), row(0.3), row(-1.0, 0.1), row(0.2, -0.3)]
    summary = _summary(rows)
    assert summary is not None and acc.summary_problems(summary, rows) == []
    assert summary["twins_paired"] == {"plays": 3, "average": pytest.approx(-0.43333, abs=1e-4)}
    assert acc.summary_problems({**summary, "average": 0.1}, rows)
    assert acc.summary_problems({**summary, "hundred_each": {"invested": 400, "value": 500.0}}, rows)
    assert acc.summary_problems({**summary, "twins_paired": None}, rows)       # twins vs all plays, not pairs
    assert acc.summary_problems({**summary, "best": {"pct": -2.0}}, rows)
    assert acc.row_problems(row(0.2)) == []
    assert acc.row_problems(row(0.2, peak=-0.3, worst=0.1))                    # a peak below the low
    assert acc.row_problems(row(-1.05))
    assert acc.row_problems(row(0.2, days_held=-1))


def test_the_screenshot_case_100_in_each_is_the_sum_of_each_rows_return() -> None:
    """'$71,084 of $78,900': 789 plays averaging -9.906%: consistent; a value that is not the rows' sum is not."""
    from desk.stocks.play_history import _summary

    rows = [row(-0.09906)] * 789
    summary = _summary(rows)
    assert summary is not None and summary["hundred_each"]["invested"] == 78_900
    assert summary["hundred_each"]["value"] == pytest.approx(71_084.17, abs=0.01)
    assert acc.summary_problems(summary, rows) == []
    assert acc.summary_problems({**summary, "hundred_each": {"invested": 78_900, "value": 71_084 + 900}}, rows)


def test_performance_twins_are_compared_over_the_same_pairs() -> None:
    from desk.stocks.play_performance import PlayReturn, by_horizon, cumulative

    def ret(r: float, twin: float | None) -> PlayReturn:
        p = PlayReturn(date(2026, 10, 1), "edge", 1, "m", "S", "BUY", "long_call", date(2026, 10, 9), 1.0, 8, {})
        p.path = [(6, r, "settled")]
        p.twin = [] if twin is None else [(6, twin, "settled")]
        return p

    rows = [ret(0.5, -0.2), ret(-1.0, None), ret(0.1, 0.0)]
    expiry = by_horizon(rows)[-1]
    assert expiry["plays"]["n"] == 3 and expiry["twins"]["n"] == expiry["paired"]["n"] == 2
    assert expiry["paired"]["mean"] == pytest.approx(0.3)
    curve = cumulative(rows, {})
    assert curve[-1]["settled"] == 3 and curve[-1]["twinned"] == 2 and curve[-1]["picks_twinned"] == pytest.approx(60)
    view = {"overall": by_horizon(rows), "cumulative": {"all": curve}, "plays": 3, "resolved": 3}
    assert acc.performance_problems(view) == []
    unpaired = {**view, "overall": [{k: v for k, v in h.items() if k != "paired"} for h in view["overall"]]}
    assert any("not the same pairs" in p for p in acc.performance_problems(unpaired))
    assert acc.performance_problems({**view, "cumulative": {"all": [{**curve[-1], "invested": 100}]}})


def test_books_cash_positions_and_fills(world: Any, tmp_path: Path) -> None:
    conn = connect(tmp_path / "ledger.db", DbKind.STOCKS_LEDGER)
    migrate(conn, DbKind.STOCKS_LEDGER)
    conn.row_factory = sqlite3.Row
    book = PaperBook(conn)
    day = world.days[-3]
    at = datetime.combine(day, time(20), UTC)
    book.open_account("spy_hold", 1_000, at, day)
    decision = book.decide(at, day, "spy_hold", "x", "SPY", "ENTER", [], {})
    bar = world.dbs.market.execute("SELECT open, high, low FROM stk_bars WHERE symbol='SPY' AND day=?",
                                   (day.toordinal(),)).fetchone()
    book.open_position(account="spy_hold", spec_sha256="x", asset="stock", symbol="SPY", structure="long_stock",
                       legs=[{"kind": "stock", "symbol": "SPY", "quantity": 1, "entry_price": bar[0]}],
                       decision_id=decision, at=at, day=day, cost=bar[0], fees=0, max_loss=None, stop_price=None,
                       expires=None, plan={})
    src = acc.Sources(world.paths, NOW, world.dbs.market, world.dbs.research, conn, None, {})
    for check in (acc.check_book_cash, acc.check_book_positions, acc.check_book_fills):
        found = check(src)
        assert found.checked > 0 and found.violations == 0, (check.__name__, found.examples)
    book.open_position(account="spy_hold", spec_sha256="x", asset="stock", symbol="SPY", structure="long_stock",
                       legs=[{"kind": "stock", "symbol": "SPY", "quantity": 20, "entry_price": bar[1] * 1.2}],
                       decision_id=decision, at=at, day=day, cost=bar[1] * 24, fees=0, max_loss=None,
                       stop_price=None, expires=None, plan={})
    assert acc.check_book_cash(src).violations == 1                             # spent more than it had
    assert acc.check_book_fills(src).violations == 1                            # above the day's high
    first = conn.execute("SELECT MIN(id) FROM stk_positions").fetchone()[0]
    conn.execute("UPDATE stk_positions SET closed_at = '2000-01-01', exit_day = 1, state = 'CLOSED', "
                 "exit_credit_micros = 0, exit_fees_micros = 0 WHERE id = ?", (first,))
    assert acc.check_book_positions(src).violations == 1                        # closed before it opened
    conn.close()


def test_price_bars_and_option_quotes(world: Any) -> None:
    src = sources(world)
    assert acc.check_prices(src).violations == 0 and acc.check_option_quotes(src).violations == 0
    world.dbs.market.execute("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) VALUES "
                             "('BAD', 1, 10, 11, 10.5, 10.2, 5)")
    world.dbs.research.execute("INSERT INTO stk_track_days(occ, session, status, bid, ask, iv, source, recorded_at) "
                               "VALUES ('S10261120C00050000', 1, 'quoted', 1.2, 1.1, 0.4, 't', 'x')")
    assert acc.check_prices(src).violations == 1
    assert acc.check_option_quotes(src).violations == 1


def test_report_numbers() -> None:
    assert acc.report_problems({"trades": 3, "net_return": -0.8, "rows": [{"n": 2}]}) == []
    problems = acc.report_problems({"trades": -1, "summary": {"net_return": -1.4}, "x": float("nan")})
    assert len(problems) == 3


def test_the_report_runs_on_an_empty_installation_and_on_a_world(tmp_path: Path, world: Any) -> None:
    empty = acc.check(DeskPaths(tmp_path / "empty", REPO / "config", tmp_path / "secrets.env"), NOW)
    assert empty["violations"] == 0 and all(f["skipped"] or f["checked"] == 0 for f in empty["findings"])
    report = acc.check(world.paths, NOW)
    assert report["checks"] == len(acc.CHECKS) and "accuracy check" in acc.text(report)
    assert set(acc.summary(report)["checks"][0]) == {"id", "area", "title", "checked", "violations", "skipped"}


def test_check_accuracy_prints_a_plain_report_and_fails_on_a_violation(world: Any, monkeypatch: Any,
                                                                        capsys: Any) -> None:
    from argparse import Namespace

    from desk.stocks.cli import run

    monkeypatch.setattr(DeskPaths, "from_env", lambda: world.paths)
    assert run(Namespace(stocks_command="check-accuracy", sessions=5, json=False)) == 0
    out = capsys.readouterr().out
    assert "KESTREL accuracy check" in out and "0 violations" in out and "Read-only" in out
    world.dbs.market.execute("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) VALUES "
                             "('BAD', 1, 10, 11, 10.5, 10.2, 5)")
    assert run(Namespace(stocks_command="check-accuracy", sessions=5, json=True)) == 1
    report = json.loads(capsys.readouterr().out)
    assert next(f for f in report["findings"] if f["id"] == "data.prices")["examples"][0].startswith("BAD")


def test_a_paper_option_of_bought_contracts_is_never_sold_for_less_than_its_exit_fees(world: Any,
                                                                                      tmp_path: Path) -> None:
    """A synthetic edge case: PAPER sold a long call at a $0.00 bid, or settled one a fraction of a cent in the
    money, and paid the $0.65 exit fee, a loss beyond what it cost (below -100% in the trade statistics). It is
    written off at zero instead; a position with a sold leg closes as quoted."""
    from dataclasses import replace

    from desk.stocks.paper.engine import written_off
    from desk.stocks.paper.report import trade_stats

    expiry, later = world.days[-2], world.days[-1]
    conn = connect(tmp_path / "ledger.db", DbKind.STOCKS_LEDGER)
    migrate(conn, DbKind.STOCKS_LEDGER)
    book = PaperBook(conn)
    strategy = BY_STRATEGY["random_calls"]
    at = datetime.combine(world.days[-6], time(20), UTC)
    book.open_account(strategy.id, 10_000, at, world.days[-6])
    decision = book.decide(at, world.days[-6], strategy.id, strategy.spec_sha256, "S10", "ENTER", [], {})
    close = world.dbs.market.execute("SELECT close FROM stk_bars WHERE symbol = 'S10' AND day = ?",
                                     (expiry.toordinal(),)).fetchone()[0]
    leg = {**_leg("S10", "call", round(close - 0.004, 4), expiry), "quantity": 1, "entry_price": 0.5}
    book.open_position(account=strategy.id, spec_sha256=strategy.spec_sha256, asset="option", symbol="S10",
                       structure="long_call", legs=[leg], decision_id=decision, at=at, day=world.days[-6], cost=50,
                       fees=FEE, max_loss=50 + FEE, stop_price=None, expires=expiry, plan={})
    position = book.positions(strategy.id)[0]
    assert written_off(position, 0.0, FEE)[:2] == (0.0, 0.0)
    assert written_off(position, 5.0, FEE)[:2] == (5.0, FEE)
    spread = replace(position, legs=[leg, {**leg, "quantity": -1}])
    assert written_off(spread, 0.0, 2 * FEE)[:2] == (0.0, 2 * FEE)
    engine = PaperEngine(world.market, book, SETTINGS, (strategy,))
    report = StepReport(later)
    engine._expiries(later, datetime.combine(later, time(22), UTC), {}, report)
    closed = book.positions(strategy.id, state="CLOSED")
    assert report.closed == 1 and closed[0].pnl == pytest.approx(-50 - FEE)
    assert trade_stats(closed)["avg_return"] == pytest.approx(-1.0)
    conn.close()
