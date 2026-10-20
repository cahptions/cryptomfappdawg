"""Robinhood desk strategies and the historical simulation, on synthetic daily bars."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from desk.rh import strategies as st
from desk.rh.contracts import Candle
from desk.rh.simulate import simulate

D = Decimal
DAY0 = datetime(2026, 1, 1, tzinfo=UTC)


VOLUME, SPREAD = D(1_000_000), D("0.01")


def bars(symbol: str, closes: list[Decimal], *, volume: Decimal = VOLUME, start: datetime = DAY0,
         spread: Decimal = SPREAD) -> list[Candle]:
    out, prev = [], closes[0]
    for i, close in enumerate(closes):
        out.append(Candle(symbol, 86400, start + timedelta(days=i), prev, max(prev, close) * (1 + spread),
                          min(prev, close) * (1 - spread), close, volume))
        prev = close
    return out


def session_after(series: list[Candle]) -> date:
    return (series[-1].start + timedelta(days=1)).date()


def test_specs_are_frozen_by_hash() -> None:
    assert len({st.spec_sha256(s) for s in st.SPECS}) == len(st.SPECS)
    assert st.spec_sha256("R1_TREND") == st.spec_sha256("R1_TREND")
    assert set(st.ORDER) == set(st.SPECS)


def test_snapshot_eligibility_reasons() -> None:
    series = bars("ETH-USD", [D(100) + i for i in range(150)])
    snap, reason = st.snapshot("ETH-USD", series, session_after(series))
    assert reason is None and snap is not None and snap.close == D(249)
    assert st.snapshot("ETH-USD", series, session_after(series) + timedelta(days=1))[1] == "STALE_HISTORY"
    assert st.snapshot("ETH-USD", series[:60], session_after(series[:60]))[1] == "SHORT_HISTORY"
    assert st.snapshot("ETH-USD", [], date(2026, 1, 1))[1] == "NO_HISTORY"
    thin = bars("THIN-USD", [D(1)] * 150, volume=D(10))
    assert st.snapshot("THIN-USD", thin, session_after(thin))[1] == "THIN_VOLUME"
    future = st.snapshot("ETH-USD", series, series[130].start.date())[0]
    assert future is not None and future.close == series[129].close     # never sees the bar of its own session


def test_regime_and_trend_breakout_entry() -> None:
    btc = bars("BTC-USD", [D(100) + i for i in range(150)])
    alt = bars("ALT-USD", [D(50)] * 140 + [D(50) + 2 * i for i in range(1, 11)])
    day = session_after(btc)
    eligible = {s.symbol: s for s in (st.snapshot("BTC-USD", btc, day)[0], st.snapshot("ALT-USD", alt, day)[0])
                if s is not None}
    reg = st.regime(eligible["BTC-USD"])
    assert reg == "RISK_ON"
    signals = st.entries(day, eligible, reg, [])
    assert ("B1_HOLD_BTC", "BTC-USD") in {(s.strategy_id, s.symbol) for s in signals}
    assert ("R1_TREND", "ALT-USD") in {(s.strategy_id, s.symbol) for s in signals}
    off = st.entries(day, eligible, "RISK_OFF", [])
    assert {s.strategy_id for s in off} <= {"B1_HOLD_BTC", "B2_RANDOM"}      # only baselines trade in RISK_OFF


def test_weekly_momentum_targets_only_on_mondays_and_must_beat_btc() -> None:
    btc = bars("BTC-USD", [D(100) + i for i in range(150)])
    fast = bars("FAST-USD", [D(10) * (D("1.02") ** i) for i in range(150)])
    day = session_after(btc)
    monday = day + timedelta(days=(7 - day.weekday()) % 7)
    series = {"BTC-USD": btc, "FAST-USD": fast}
    for sym, closes in list(series.items()):
        extra = [closes[-1].close] * (monday - day).days
        series[sym] = bars(sym, [c.close for c in closes] + extra)
    eligible = {s: st.snapshot(s, b, monday)[0] for s, b in series.items()}
    assert st.xmom_targets(monday, eligible, "RISK_ON") == ["FAST-USD"]
    assert st.xmom_targets(monday + timedelta(days=1), eligible, "RISK_ON") == []
    assert st.xmom_targets(monday, eligible, "RISK_OFF") == []


def test_exits_follow_each_strategy_rule() -> None:
    falling = bars("ALT-USD", [D(100)] * 140 + [D(100) - 3 * i for i in range(1, 11)])
    day = session_after(falling)
    snap = st.snapshot("ALT-USD", falling, day)[0]
    assert snap is not None
    held = [st.Held("p1", "R1_TREND", "ALT-USD", day - timedelta(days=5), D(100)),
            st.Held("p2", "R3_PULLBACK", "ALT-USD", day - timedelta(days=7), D(100)),
            st.Held("p3", "B2_RANDOM", "ALT-USD", day - timedelta(days=7), D(100)),
            st.Held("p4", "B1_HOLD_BTC", "BTC-USD", day - timedelta(days=900), D(100))]
    out = {s.position_id: s.reasons for s in st.exits(day, held, {"ALT-USD": snap}, {"ALT-USD": snap}, "RISK_ON")}
    assert "DONCHIAN_EXIT" in out["p1"] and "TRAILING_ATR" in out["p1"]
    assert out["p2"] == ("MAX_HOLD",)
    assert out["p3"] == ("HOLD_COMPLETE",)
    assert "p4" not in out                                                 # the BTC baseline never exits


def test_random_baseline_is_seeded_by_the_day() -> None:
    snaps = {s: None for s in ("A-USD", "B-USD", "C-USD")}
    picks = {st.random_pick(date(2026, 3, d), snaps) for d in range(1, 29)}  # type: ignore[arg-type]
    assert st.random_pick(date(2026, 3, 2), snaps) == st.random_pick(date(2026, 3, 2), snaps)  # type: ignore[arg-type]
    assert picks <= set(snaps) and len(picks) > 1


def test_stop_fraction_is_clamped() -> None:
    series = bars("ETH-USD", [D(100)] * 150, spread=D("0.0001"))
    snap = st.snapshot("ETH-USD", series, session_after(series))[0]
    assert snap is not None and snap.stop_fraction == D("0.05")
    wild = bars("WILD-USD", [D(100), D(200)] * 75, spread=D("0.2"))
    snap2 = st.snapshot("WILD-USD", wild, session_after(wild))[0]
    assert snap2 is not None and snap2.stop_fraction == D("0.25")


def test_simulation_pays_costs_on_both_legs_and_marks_open_positions() -> None:
    btc = bars("BTC-USD", [D(100) + i for i in range(260)])
    start = (btc[200].start).date()
    end = btc[-1].start.date()
    result = simulate({"BTC-USD": btc}, start, end, cost=lambda _: D("0.02"), cost_source=lambda _: "assumed",
                      starting_capital=D(100), fixed_fraction=D("0.10"), max_fraction=D("0.15"), max_open=10,
                      max_roundtrip_cost=D("0.02"))
    hold = [t for t in result.trades if t.strategy_id == "B1_HOLD_BTC"]
    assert len(hold) == 1 and hold[0].reason == "OPEN_AT_END"
    trade = hold[0]
    assert trade.entry_price == btc[200].open * D("1.01")
    assert trade.exit_price == btc[-1].close * D("0.99")
    assert trade.ticket == D("10.00")
    expensive = simulate({"BTC-USD": btc}, start, end, cost=lambda _: D("0.03"), cost_source=lambda _: "assumed",
                         starting_capital=D(100), fixed_fraction=D("0.10"), max_fraction=D("0.15"), max_open=10,
                         max_roundtrip_cost=D("0.02"))
    assert not expensive.trades and expensive.skipped["COST_ABOVE_CEILING"] > 0


def test_simulation_hard_stop_fills_at_the_worse_of_open_and_stop() -> None:
    btc = bars("BTC-USD", [D(100) + i for i in range(222)])
    alt = bars("ALT-USD", [D(50)] * 215 + [D(52), D(54), D(56), D(58), D(60)])          # breakout, bars 0..219
    entry_day = alt[-1].start + timedelta(days=1)
    alt += [Candle("ALT-USD", 86400, entry_day, D(60), D(61), D(59), D(60), D(1_000_000)),
            Candle("ALT-USD", 86400, entry_day + timedelta(days=1), D(50), D(51), D(40), D(45), D(1_000_000))]
    result = simulate({"BTC-USD": btc, "ALT-USD": alt}, entry_day.date(), (entry_day + timedelta(days=1)).date(),
                      cost=lambda _: D("0.01"), cost_source=lambda _: "assumed", starting_capital=D(100),
                      fixed_fraction=D("0.10"), max_fraction=D("0.15"), max_open=10, max_roundtrip_cost=D("0.02"))
    trend = [t for t in result.trades if t.strategy_id == "R1_TREND"]
    assert len(trend) == 1
    trade = trend[0]
    assert trade.symbol == "ALT-USD" and trade.reason == "HARD_STOP"
    assert trade.entry_price == D(60) * D("1.005")
    assert trade.exit_price == D(50) * D("0.995")             # gap below the stop: filled at the open, not the stop
    assert trade.exit_day == (entry_day + timedelta(days=1)).date()
