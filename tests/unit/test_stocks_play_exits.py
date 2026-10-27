"""A profitable earlier sale must survive a worthless expiry, without rewarding invented or pre-entry peaks."""

from datetime import datetime, timedelta

import pytest

from desk.stocks import calendar as cal
from desk.stocks.play_history import _combine, exit_opportunities, pnl

START = datetime(2026, 10, 12, 16, tzinfo=cal.NEW_YORK)
LEG = {"occ": "TEST", "side": "buy", "kind": "call", "strike": 100.0}


def mark(at: datetime, value: float, status: str = "quoted") -> dict:
    return {"t": at.isoformat(), "at": at.isoformat(), "value": value, "status": status, "underlying": 100.0,
            "close": pnl("BUY", 2.0, [LEG], value, status == "settled"),
            "first": pnl("BUY", 2.5, [LEG], value, status == "settled")}


def test_profitable_intraday_exit_is_not_hidden_by_a_losing_close_or_worthless_expiry():
    high = mark(START + timedelta(hours=19), 8.0)
    close = mark(START + timedelta(days=1), 1.0)
    expiry = mark(START + timedelta(days=4), 0.0, "settled")
    estimate = mark(START + timedelta(days=2), 100.0, "estimated")
    out = exit_opportunities([close, estimate, expiry], [high], {"close": START}, START + timedelta(days=5))["close"]
    assert out["best"]["at"] == high["at"]
    assert out["best"]["pnl"] == pytest.approx(598.7)
    assert out["best"]["pct"] == pytest.approx(598.7 / 200.65, abs=1e-5)
    assert out["latest"]["pnl"] == pytest.approx(-200.65)
    assert out["giveback"] == pytest.approx(799.35)
    assert out["best_estimate"]["pnl"] > out["best"]["pnl"]
    assert out["observations"] == 2 and out["estimates"] == 1


def test_first_sighting_and_close_have_independent_entry_times_and_ignore_future_quotes():
    early = mark(START - timedelta(hours=3), 50)
    before_close = mark(START - timedelta(minutes=30), 9)
    later = mark(START + timedelta(hours=19), 8)
    future = mark(START + timedelta(days=20), 100)
    entries = {"close": START, "first": START - timedelta(hours=2)}
    out = exit_opportunities([], [early, before_close, later, future], entries, START + timedelta(days=1))
    assert out["close"]["best"]["at"] == later["at"]
    assert out["first"]["best"]["at"] == before_close["at"]
    assert out["first"]["best"]["pnl"] == pytest.approx(648.7)
    assert out["close"]["observations"] == 1 and out["first"]["observations"] == 2


def test_estimates_and_settlements_alone_do_not_claim_an_observed_earlier_exit():
    points = [mark(START + timedelta(days=1), 10, "provisional"),
              mark(START + timedelta(days=2), 15, "modelled"),
              mark(START + timedelta(days=3), 20, "settled")]
    out = exit_opportunities(points, [], {"close": START}, START + timedelta(days=4))["close"]
    assert out["best"] is None and out["worst"] is None
    assert out["observations"] == 0 and out["estimates"] == 2
    assert out["best_estimate"]["status"] == "modelled"
    assert out["latest"]["status"] == "settled"


def test_a_losing_best_exit_stays_negative_and_duplicate_quotes_count_once():
    best = mark(START + timedelta(hours=19), 1.5)
    worse = mark(START + timedelta(days=1), 0.5)
    out = exit_opportunities([best, worse], [best], {"close": START}, START + timedelta(days=2))["close"]
    assert out["observations"] == 2
    assert out["best"]["pnl"] == pytest.approx(-51.3)
    assert out["worst"]["pnl"] == pytest.approx(-151.3)


def test_spread_does_not_combine_a_stale_long_leg_high_with_a_later_short_leg_low():
    short = {"occ": "SHORT", "side": "sell", "kind": "call", "strike": 105}
    paths = {"TEST": [(START, 10, 10.5, 100)],
             "SHORT": [(START, 9, 9.5, 100), (START + timedelta(minutes=30), 0, 0.1, 100)]}
    result = _combine([LEG, short], paths, {"TEST": "quoted", "SHORT": "quoted"})
    assert len(result) == 1
    assert result[0][1] == pytest.approx(0.5)


def test_daily_point_time_uses_the_new_york_clock_across_dst():
    from desk.stocks.play_history import point_time

    winter = point_time({"t": "2026-03-06"})
    summer = point_time({"t": "2026-03-09"})
    assert winter.hour == summer.hour == 16
    assert winter.utcoffset() == timedelta(hours=-5)
    assert summer.utcoffset() == timedelta(hours=-4)
