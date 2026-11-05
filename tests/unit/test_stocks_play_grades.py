"""Every chance a play shows is graded against what happened: the exit plan's take-profit on the play's own quoted
closing marks (never across an estimate), and once 30 have resolved, the headline chance is calibrated on them."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import odds as od
from desk.stocks import play_learning as lrn
from desk.stocks import play_performance as perf
from desk.stocks import play_plan as pp
from desk.stocks import plays as pl
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch, day_of
from tests.conftest import REPO

START = date(2025, 3, 3)
NOW = datetime(2025, 12, 1, 22, tzinfo=UTC)


@pytest.fixture
def dbs(tmp_path: Path) -> Any:
    out = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield out
    out.close()


def test_the_plan_outcome_is_the_first_level_reached_on_quoted_closes() -> None:
    quoted = [(0, -0.02, "quoted"), (1, 0.2, "quoted"), (2, 0.55, "quoted"), (3, -0.6, "quoted")]
    assert lrn.plan_outcome(quoted, 0.5, -0.5, 10) == "take_profit"
    assert lrn.plan_outcome(quoted, 0.6, -0.5, 10) == "stop"
    assert lrn.plan_outcome(quoted, 0.6, -0.7, 3) == "neither"            # the leave-by session came first
    assert lrn.plan_outcome(quoted, 0.6, -0.7, 10) is None               # still open
    assert lrn.plan_outcome(quoted, 0.6, None, 2) == "neither"
    gap = [(0, 0.0, "quoted"), (1, 0.7, "estimated"), (2, 0.55, "quoted")]
    assert lrn.plan_outcome(gap, 0.5, -0.5, 10) is None                  # an estimate could hide either level
    hole = [(0, 0.0, "quoted"), (2, 0.55, "quoted")]
    assert lrn.plan_outcome(hole, 0.5, -0.5, 10) is None                 # a session not marked yet
    settled = [(0, 0.0, "quoted"), (1, 0.1, "quoted"), (2, 1.2, "settled")]
    assert lrn.plan_outcome(settled, 0.5, -0.5, 2) == "take_profit"     # the settlement counts as the last mark
    assert lrn.plan_outcome([(0, 0.0, "quoted"), (1, -1.0, "settled")], 0.5, None, 1) == "neither"


def _record(conn: sqlite3.Connection, session: date, rank: int, take: float, raw: float, category: str = "edge",
            dte: int = 20) -> tuple[date, date]:
    expiration = session + timedelta(days=dte)
    leave = cal.trading_days(session + timedelta(days=1), expiration)[4]
    play = {"odds_version": od.MODEL_VERSION, "exit_plan": {
        "take_profit": {"return": 0.5}, "stop": {"return": -0.5}, "leave_by": leave.isoformat(),
        "odds": {"take_profit": take, "take_profit_raw": raw, "stop": 0.3, "neither": 0.7 - take}}}
    conn.execute(
        "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
        " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
        " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES (?, ?, ?, 'X', 'BUY', 'long_call',"
        " ?, 'itm', 'call', 10, 0, 1, 101, '[]', 10, 0.5, 0.5, 0.5, 0.5, 0.4, ?, 'm', 'x')",
        (day_of(session), category, rank, day_of(expiration), json.dumps(play)))
    return expiration, leave


def _marks(conn: sqlite3.Connection, session: date, rank: int, path: list[tuple[float, str]],
           category: str = "edge") -> None:
    days = cal.trading_days(session, session + timedelta(days=40))
    conn.executemany("INSERT INTO stk_play_marks(session, category, rank, twin, day, status, value, mid, pnl, pnl_pct,"
                     " underlying, note, recorded_at) VALUES (?, ?, ?, 0, ?, ?, 1, 1, 0, ?, 10, NULL, 'x')",
                     [(day_of(session), category, rank, day_of(days[i]), status, r)
                      for i, (r, status) in enumerate(path)])


def test_each_recorded_plan_is_graded_once_on_its_own_marks(dbs: Any) -> None:
    conn = dbs.research
    session = cal.trading_days(START, START + timedelta(days=10))[0]
    _record(conn, session, 1, 0.42, 0.40)
    _marks(conn, session, 1, [(0.0, "quoted"), (0.2, "quoted"), (0.6, "quoted")])
    _record(conn, session, 2, 0.30, 0.30)
    _marks(conn, session, 2, [(0.0, "quoted"), (-0.1, "quoted"), (0.1, "quoted"), (0.0, "quoted"), (0.2, "quoted"),
                              (0.3, "quoted")])                            # the fifth session is the leave-by date
    _record(conn, session, 3, 0.30, 0.30)
    _marks(conn, session, 3, [(0.0, "quoted"), (0.6, "estimated"), (0.7, "quoted")])
    assert pl.grade_events(StocksMarket(dbs.market), StocksResearch(conn), NOW) == 2
    got = {r["rank"]: dict(r) for r in conn.execute("SELECT * FROM stk_play_grades")}
    assert got[1]["outcome"] == "take_profit" and got[1]["hit"] == 1 and got[1]["predicted"] == 0.42
    assert got[1]["predicted_raw"] == 0.40 and got[1]["dte"] == 20 and got[1]["event"] == "take_profit"
    assert got[2]["outcome"] == "neither" and got[2]["hit"] == 0
    assert 3 not in got                                                    # across an estimate: never graded
    assert pl.grade_events(StocksMarket(dbs.market), StocksResearch(conn), NOW) == 0            # graded once
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE stk_play_grades SET hit = 0")


def test_thirty_graded_plans_calibrate_the_headline_chance_and_the_card_quotes_them(dbs: Any) -> None:
    conn = dbs.research
    sessions = cal.trading_days(START, START + timedelta(days=90))
    for i in range(40):                     # shown about 40%, but only one in ten really reached the take-profit
        session = sessions[i]
        _record(conn, session, 1, 0.41, 0.41)
        hit = i % 10 == 0
        _marks(conn, session, 1, [(0.0, "quoted"), (0.6 if hit else -0.6, "quoted")])
    assert pl.grade_events(StocksMarket(dbs.market), StocksResearch(conn), NOW) == 40
    groups = pl.calibration(StocksResearch(conn))
    found, level = pl.calibrator(groups, "take_profit", "edge", 20)
    assert level == "this category at this expiry" and found.resolved == 40 and found.apply(0.41) < 0.3
    history = pl.event_history(StocksResearch(conn))
    line = pl.history_line(history, "edge", 0.45)
    assert line == {"plays": 40, "reached": 0.1, "said": 0.41, "level": "this category", "bucket": "40–50%"}
    assert pl.history_line(history, "lottos", 0.43)["level"] == "all plays"
    assert pl.history_line(history, "edge", 0.85)["plays"] == 0
    # a new play's plan: the shown chance is calibrated, the raw share kept, and the three chances still add up
    play = {"structure": "long_call", "action": "BUY", "dte": 20, "underlying_price": 100.0, "entry": 2.0,
            "max_loss": 200.65, "max_gain": None, "forecast": {"sigma": 0.3, "drift": 0.04},
            "legs": [{"occ": "X     251121C00100000", "kind": "call", "side": "buy", "strike": 100.0, "iv": 0.3,
                      "bid": 1.9, "ask": 2.0}], "expiration": (START + timedelta(days=20)).isoformat()}
    plan = pp.exit_plan(play, "edge", None, 0.04, START)
    plain = pp.plan_odds(play, plan, 0.04, START)
    tuned = pp.plan_odds(play, plan, 0.04, START, calibrations=groups, category="edge")
    assert plain and tuned and plain["calibration"] is None and tuned["calibration"] == "this category at this expiry"
    assert tuned["take_profit_raw"] == plain["take_profit"] and tuned["take_profit"] < plain["take_profit"]
    assert tuned["take_profit"] + tuned["stop"] + tuned["neither"] == pytest.approx(1.0, abs=2e-3)
    assert tuned["version"] == pp.PLAN_ODDS_VERSION == 2
    attached = pp.attach(dict(play), "edge", None, None, 0.04, START, calibrations=groups, history=history)
    assert attached["exit_plan"]["odds"]["history"]["plays"] >= 0
    # Performance charts the graded take-profit chance beside the expiry event
    view = perf.event_calibration(conn)["take_profit"]
    assert view["resolved"] == 40 and view["outcomes"] == {"take_profit": 4, "stop": 36}
    assert view["points"] == [{"predicted": 0.41, "observed": 0.1, "n": 40, "bucket": "40–50%"}]
    assert view["brier"] == pytest.approx(0.1 * 0.59 ** 2 + 0.9 * 0.41 ** 2, abs=1e-4)
