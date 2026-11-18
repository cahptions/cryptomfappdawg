"""Where each trade's money came from (desk.stocks.attribution): the parts always add up to the recorded P/L with the
remainder shown; a pure stock move is direction, a pure change in implied volatility is volatility, the passing of
time is time decay, the spread and fees are costs; nothing modelled is graded and nothing after the exit is read."""

from __future__ import annotations

import zlib
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desk.core.hashing import canonical_json
from desk.stocks import attribution as at
from desk.stocks import options as opt
from desk.storage.db import DbKind, connect, migrate

R = 0.04
DAY = date(2026, 9, 1)
EXP = date(2026, 10, 16)
CALL = at.Leg("XYZ261016C00100000", "call", 100.0, EXP, 2.0)


def price(s: float, iv: float, day: date, leg: at.Leg = CALL) -> float:
    return opt.bs_price(leg.kind, s, leg.strike, opt.year_fraction(day, leg.expiration), iv, R)


def point(s: float, iv: float, day: date, leg: at.Leg = CALL) -> at.Point:
    return at.Point(day, s, {leg.occ: price(s, iv, day, leg)}, {leg.occ: iv})


def trade(points: list[at.Point], half: float = 0.05, fees: float = 2.6) -> dict[str, Any]:
    """Bought at the first mid + half, sold at the last mid − half, ``fees`` in all."""
    first, last = points[0].mids[CALL.occ], points[-1].mids[CALL.occ]
    total = CALL.qty * 100 * ((last - half) - (first + half)) - fees
    return at.split([CALL], points, {CALL.occ: first + half}, total, {CALL.occ: last - half}, None, R)


def parts_sum(p: dict[str, Any]) -> float:
    return sum(p[k] for k in at.PARTS)


def test_parts_add_up_to_the_total_and_the_costs_are_the_spread_and_fees() -> None:
    p = trade([point(100, 0.3, DAY), point(103, 0.33, DAY + timedelta(days=3)), point(99, 0.31, DAY + timedelta(days=9))])
    assert parts_sum(p) == pytest.approx(p["total"], abs=0.02)
    assert p["costs"] == pytest.approx(-(2 * 100 * 0.05 * 2) - 2.6, abs=0.01)    # half spread in and out, two contracts


def test_a_pure_move_is_direction_a_pure_iv_change_is_volatility_time_is_decay() -> None:
    small = timedelta(days=0)
    move = trade([point(100, 0.3, DAY), point(101, 0.3, DAY + small)], half=0.0, fees=0.0)
    assert abs(move["direction"]) > 50 and abs(move["unexplained"]) < 0.02 * abs(move["direction"])
    assert move["volatility"] == pytest.approx(0) and move["time"] == pytest.approx(0)
    vol = trade([point(100, 0.3, DAY), point(100, 0.32, DAY + small)], half=0.0, fees=0.0)
    assert vol["volatility"] > 50 and abs(vol["unexplained"]) < 0.02 * vol["volatility"] and vol["direction"] == 0
    decay = trade([point(100, 0.3, DAY), point(100, 0.3, DAY + timedelta(days=1))], half=0.0, fees=0.0)
    assert decay["time"] < 0 and abs(decay["unexplained"]) < 0.05 * abs(decay["time"])
    # a big jump in one step: the greeks miss part of it, and that part is shown as unexplained, not hidden
    jump = trade([point(100, 0.3, DAY), point(120, 0.3, DAY)], half=0.0, fees=0.0)
    assert jump["unexplained"] != 0 and parts_sum(jump) == pytest.approx(jump["total"], abs=0.02)


def test_settlement_and_a_missing_iv_leave_the_rest_unexplained() -> None:
    points = [point(100, 0.3, DAY), at.Point(DAY + timedelta(days=5), 104, {CALL.occ: price(104, 0.3, DAY)},
                                             {CALL.occ: None})]
    entry = {CALL.occ: points[0].mids[CALL.occ] + 0.05}
    settle = 107.0
    total = CALL.qty * 100 * (7.0 - entry[CALL.occ]) - 1.3
    p = at.split([CALL], points, entry, total, None, settle, R)
    assert parts_sum(p) == pytest.approx(total, abs=0.02) and p["steps"] == 2
    assert p["costs"] == pytest.approx(-2 * 100 * 0.05 - 1.3, abs=0.01)              # no exit spread at settlement
    nothing = at.split([CALL], [], entry, total, None, settle, R)
    assert nothing["unexplained"] == pytest.approx(total - nothing["costs"], abs=0.02) and nothing["notes"]


def test_words_name_the_largest_parts_first() -> None:
    assert at.words({"direction": -610, "volatility": 3, "time": 420, "costs": -95, "unexplained": 0.2}) == \
        "−$610 from direction moves, +$420 from time decay, −$95 costs (spreads and fees), +$3 from volatility changes"


def chain_db(tmp_path: Path) -> Any:
    conn = connect(tmp_path / "market.db", DbKind.STOCKS_MARKET)
    migrate(conn, DbKind.STOCKS_MARKET)

    def save(day: date, s: float, iv: float) -> None:
        mid = price(s, iv, day)
        quote = [CALL.occ, "call", 100.0, round(mid - 0.05, 4), round(mid + 0.05, 4), None, 10, 100, None,
                 None, None, None]
        conn.execute("INSERT INTO stk_option_chains(underlying, day, expiration, observed_at, underlying_price, "
                     "contracts, chain_zlib) VALUES (?,?,?,?,?,?,?)",
                     ("XYZ", day.toordinal(), EXP.toordinal(), f"{day.isoformat()}T20:00:00+00:00", s, 1,
                      zlib.compress(canonical_json([quote]).encode())))
    return conn, save


def test_a_paper_trade_reads_stored_chains_between_entry_and_exit_only(tmp_path: Path) -> None:
    conn, save = chain_db(tmp_path)
    days = [DAY + timedelta(days=i) for i in range(0, 15, 3)]
    for i, d in enumerate(days):
        save(d, 100 + i, 0.3 + 0.01 * i)
    first, last = price(100, 0.3, days[0]), price(104, 0.34, days[-1])
    p = SimpleNamespace(asset="option", symbol="XYZ", entry_day=days[0], exit_day=days[-1], exit_reason="take profit",
                        legs=[{"occ": CALL.occ, "kind": "call", "strike": 100.0, "expiration": EXP.isoformat(),
                               "quantity": 2, "entry_price": first + 0.05}],
                        pnl=2 * 100 * ((last - 0.05) - (first + 0.05)) - 2.6)
    evidence = {"legs": [{"occ": CALL.occ, "exit_price": last - 0.05}]}
    got = at.paper_trade(conn, p, evidence)
    assert got is not None and got["points"] == len(days)
    assert parts_sum(got) == pytest.approx(p.pnl, abs=0.05) and got["direction"] > 0 and got["volatility"] > 0
    save(days[-1] + timedelta(days=2), 150, 0.9)                     # a later chain changes nothing
    assert at.paper_trade(conn, p, evidence) == got
    assert at.paper_trade(conn, p, {**evidence, "modeled_exit": True}) is None       # never grades a modelled exit
    conn.execute("INSERT INTO stk_splits VALUES (?,?,?,?,?)", ("XYZ", days[2].toordinal(), 1, 2, "x"))
    assert at.paper_trade(conn, p, evidence) is None                                  # nor across a split
