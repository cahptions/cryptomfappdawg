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


def _expired(conn: sqlite3.Connection, session: date, rank: int, net: float, *, touch: float | None = 0.6,
             profit: float = 0.55) -> date:
    expiration = cal.trading_days(session + timedelta(days=1), session + timedelta(days=12))[5]
    play = {"odds_version": od.MODEL_VERSION, "event": "itm", "touch_kestrel": touch, "max_gain": 300.0,
            "itm_history": 0.35,
            "probability_targets": {"returns": {"0": profit, "25": 0.4, "50": 0.3, "100": 0.1},
                                    "max_gain_probability": 0.2},
            "calibrated": {"profit": {"raw": profit, "shown": profit - 0.05, "level": None}}}
    conn.execute(
        "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
        " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
        " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES (?, 'debit_spreads', ?, 'AAA', 'BUY',"
        " 'bull_call_debit', ?, 'itm', 'call', 110, 0, 2, 201.3, '[]', 100, 0.4, 0.35, 0.45, 0.45, 0.5, ?, 'm', 'x')",
        (day_of(session), rank, day_of(expiration), json.dumps(play)))
    conn.execute("INSERT INTO stk_play_outcomes VALUES (?, 'debit_spreads', ?, 'x', 112, ?, 0, ?)",
                 (day_of(session), rank, int(net > 0), net))
    return expiration


def test_expiry_chances_are_graded_on_the_return_and_touch_on_daily_highs(dbs: Any) -> None:
    from desk.stocks.contracts import Bar

    conn = dbs.research
    market = StocksMarket(dbs.market)
    session = cal.trading_days(START, START + timedelta(days=10))[0]
    expiration = _expired(conn, session, 1, 1.49)                        # the spread's maximum: 300 / 201.3
    days = cal.trading_days(session + timedelta(days=1), expiration)
    market.save_bars(days[0], [Bar("AAA", days[0], 100, 104, 99, 103, 1e6)], "synthetic", NOW)
    assert pl.grade_events(market, StocksResearch(conn), NOW) == 5          # touch waits for the missing sessions
    for d in days[1:]:
        market.save_bars(d, [Bar("AAA", d, 103, 111, 102, 108, 1e6)], "synthetic", NOW)
    assert pl.grade_events(market, StocksResearch(conn), NOW) == 1
    got = {r["event"]: dict(r) for r in conn.execute("SELECT * FROM stk_play_grades")}
    assert {e: got[e]["hit"] for e in got} == {"profit": 1, "target25": 1, "target50": 1, "target100": 1,
                                               "max_gain": 1, "touch": 1}
    assert got["profit"]["predicted"] == 0.5 and got["profit"]["predicted_raw"] == 0.55   # shown vs model's own
    assert got["touch"]["outcome"] == "hit" and got["target25"]["predicted"] == 0.4
    later = cal.next_trading_day(expiration)
    loser = _expired(conn, later, 2, -0.2, touch=0.3)
    for d in cal.trading_days(later, loser):
        market.save_bars(d, [Bar("AAA", d, 103, 105, 102, 104, 1e6)], "synthetic", NOW)
    assert pl.grade_events(market, StocksResearch(conn), NOW) == 6
    miss = {r["event"]: r["hit"] for r in conn.execute("SELECT event, hit FROM stk_play_grades WHERE rank = 2")}
    assert set(miss.values()) == {0}                                     # no profit, no target, never reached $110


def test_graded_profit_chances_calibrate_what_the_card_and_confidence_show(dbs: Any) -> None:
    conn = dbs.research
    sessions = cal.trading_days(START, START + timedelta(days=90))
    for i in range(40):                    # shown 70% profit, but only a quarter really finished in profit
        _expired(conn, sessions[i], 1, 0.3 if i % 4 == 0 else -0.5, touch=None, profit=0.7)
    pl.grade_events(StocksMarket(dbs.market), StocksResearch(conn), NOW)
    groups = pl.calibration(StocksResearch(conn))
    play = {"event": "itm", "probability_targets": {"returns": {"0": 0.7, "25": 0.4, "50": 0.3, "100": 0.1},
                                                    "max_gain_probability": None}, "touch_kestrel": 0.5}
    shown = pl.calibrated(play, groups, "debit_spreads", 8)
    assert shown["profit"]["level"] == "this category at this expiry" and shown["profit"]["shown"] < 0.5
    assert shown["profit"]["raw"] == 0.7 and shown["touch"] == {"raw": 0.5, "shown": 0.5, "level": None}
    check = pp.confidence({"structure": "bull_call_debit", "profit_kestrel": 0.7, "calibrated": shown,
                           "expected_value": 5.0, "committee": 60.0, "liquidity": "A", "spread_pct": 0.02}, None)
    odds = check["checks"][0]
    assert not odds["ok"] and "calibrated on this category at this expiry" in odds["detail"]
    view = perf.event_calibration(conn)
    assert view["profit"]["resolved"] == 40 and view["profit"]["points"][0]["observed"] == 0.25
    assert set(view) == set(pl.GRADED_EVENTS)
    expiry = perf.calibration(perf.returns_table(conn))                 # the History view is the third line
    assert expiry["itm_history"]["label"] == "History" and expiry["itm_history"]["resolved"] == 40
    assert expiry["itm_history"]["points"][0]["predicted"] == 0.35


def test_a_play_is_judged_by_its_sections_record_at_its_own_expiry(dbs: Any) -> None:
    conn = dbs.research
    sessions = cal.trading_days(START, START + timedelta(days=60))
    for i in range(25):                         # the usual 40-day picks won; the 200-day band picks lost
        for category, dte, net in (("trend_calls", 40, 0.3), ("trend_calls@half_year", 200, -0.4)):
            expiration = sessions[i] + timedelta(days=dte)
            conn.execute(
                "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event,"
                " event_kind, event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market,"
                " itm_history, itm_kestrel, itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES"
                " (?, ?, 1, 'X', 'BUY', 'long_call', ?, 'itm', 'call', 10, 0, 1, 101, '[]', 10, 0.5, 0.5, 0.5, 0.5,"
                " 0.4, '{}', 'm', 'x')", (day_of(sessions[i]), category, day_of(expiration)))
            conn.execute("INSERT INTO stk_play_outcomes VALUES (?, ?, 1, 'x', 10, ?, 0, ?)",
                         (day_of(sessions[i]), category, int(net > 0), net))
    track = pl.records(StocksResearch(conn))
    assert track["trend_calls"]["resolved"] == 25 and track["trend_calls@half_year"]["mean_return"] == -0.4
    usual, far = pl.record_for(track, "trend_calls", 45), pl.record_for(track, "trend_calls", 270)
    assert usual["dte_bucket"] == "31–90 days" and usual["mean_return"] == pytest.approx(0.3)
    assert far["dte_bucket"] == "over 90 days" and far["mean_return"] == pytest.approx(-0.4)
    assert pl.record_for(track, "trend_calls", 5) == {"resolved": 0, "dte_bucket": "≤ 7 days"}
    play = {"structure": "long_call", "profit_kestrel": 0.55, "expected_value": 12.0, "committee": 64.0,
            "liquidity": "A", "spread_pct": 0.03}
    assert pp.confidence(play, usual)["checks"][4]["ok"]
    check = pp.confidence(play, far)["checks"][4]
    assert not check["ok"] and "25 resolved at over 90 days to expiry, averaging -40%" in check["detail"]
    assert "0 of 20 plays resolved at ≤ 7 days" in pp.confidence(play, pl.record_for(track, "x", 5))["checks"][4][
        "detail"]
    # a learned exit rule is applied only in the days-to-expiry group it was learned on
    rule = {"suggested": True, "dte_bucket": "31–90 days", "best_in_sample": {"params": [0.5, 0.5, None]}}
    assert pl.exit_rule_for(rule, {"dte": 45}) == (rule, None)
    skipped, why = pl.exit_rule_for(rule, {"dte": 200})
    assert skipped is None and why and "chosen on 31–90 days plays; this one has 200 days" in why
    older = {"suggested": True, "best_in_sample": {"params": [0.5, 0.5, None]}}
    assert pl.exit_rule_for(older, {"dte": 45})[0] is older
    assert pl.exit_rule_for(older, {"dte": 45, "discovery": "horizon"})[0] is None
    assert pl.exit_rule_for({"suggested": False}, {"dte": 400}) == ({"suggested": False}, None)
