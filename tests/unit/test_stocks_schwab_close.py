"""The same-evening close from Schwab: valid bars only, a coverage gate, research the same evening, never rewritten,
and a next-day comparison with Massive that switches the feature off when the closes disagree."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.stocks import calendar as cal
from desk.stocks import schwab_close as sc
from desk.stocks.contracts import Bar
from tests.stocks_world import ASOF, make_world

DAY = cal.next_trading_day(ASOF)
CLOSE = cal.session_close(DAY)


def row(close: float, day: date = DAY, **extra: Any) -> dict[str, Any]:
    stamp = datetime.combine(day, time(16), cal.NEW_YORK).isoformat()
    return {"open": close * .99, "high": close * 1.01, "low": close * .98, "regular_last": close, "volume": 1e6,
            "regular_trade_time": stamp, **extra}


class Schwab:
    configured = True

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows, self.calls = rows, []

    async def closing_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        self.calls.append(list(symbols))
        assert len(symbols) <= 100
        return {s: self.rows[s] for s in symbols if s in self.rows}


class Massive:
    configured = True

    def __init__(self, bars: list[Bar] | None) -> None:
        self.bars = bars

    async def grouped_daily(self, day: date) -> list[Bar]:
        if self.bars is None:
            raise ProviderError("massive", ProviderErrorKind.HTTP_ERROR, "grouped_daily: HTTP 403", 403)
        return self.bars


def quotes_for(world: Any, share: float = 1.0) -> dict[str, dict[str, Any]]:
    last = world.market.conn.execute("SELECT symbol, close FROM stk_bars WHERE day=?", (ASOF.toordinal(),)).fetchall()
    keep = last[: int(len(last) * share)]
    return {r["symbol"]: row(r["close"] * 1.01) for r in keep}


def test_a_closing_bar_needs_every_price_and_a_regular_trade_that_day() -> None:
    bar = sc.closing_bar("SPY", row(100.), DAY)
    assert bar == Bar("SPY", DAY, 99., 101., 98., 100., 1e6)
    assert sc.closing_bar("SPY", {**row(100.), "open": None}, DAY) is None
    assert sc.closing_bar("SPY", row(100., day=ASOF), DAY) is None                # no trade today: stale close
    assert sc.closing_bar("SPY", {**row(100.), "high": 99.5}, DAY) is None         # high below the close
    assert sc.closing_bar("SPY", {**row(100.), "regular_last": 0.}, DAY) is None


def test_due_only_in_the_evening_window_and_once(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        assert sc.due(w.market, CLOSE + timedelta(minutes=4)) is None
        assert sc.due(w.market, CLOSE + timedelta(minutes=6)) == DAY
        assert sc.due(w.market, CLOSE + timedelta(hours=4, minutes=1)) is None
        assert sc.due(w.market, cal.session_close(ASOF) + timedelta(minutes=6)) is None   # that day is stored already
        w.market.job(sc.JOB, CLOSE, detail={"disabled": "fixture"})
        assert sc.due(w.market, CLOSE + timedelta(minutes=6)) is None
    finally:
        w.close()


def test_collect_stores_the_day_once_and_research_targets_it_the_same_evening(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        now = CLOSE + timedelta(minutes=6)
        assert sc.target_session(w.market, now, timedelta(minutes=75)) == ASOF       # nothing for today yet
        thin = Schwab(quotes_for(w, .5))
        result = asyncio.run(sc.collect(thin, w.market, now))
        assert result["stored"] == 0 and "retrying" in result["waiting"]
        assert DAY not in w.market.checked_days()
        assert sc.due(w.market, now + timedelta(minutes=5)) is None                   # rests 15 minutes after a try
        full = Schwab(quotes_for(w))
        later = now + timedelta(minutes=16)
        stored = asyncio.run(sc.collect(full, w.market, later))
        assert stored["stored"] > 0 and stored["coverage"] >= sc.MIN_COVERAGE
        assert all(len(c) <= 100 for c in full.calls)
        assert w.market.conn.execute("SELECT source FROM stk_bar_days WHERE day=?",
                                     (DAY.toordinal(),)).fetchone()[0] == sc.SOURCE
        assert sc.target_session(w.market, later, timedelta(minutes=75)) == DAY      # research runs tonight
        assert asyncio.run(sc.collect(full, w.market, later + timedelta(minutes=20)))["skipped"] == "not due"
    finally:
        w.close()


def test_massive_comparison_agrees_or_switches_the_same_evening_close_off(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        now = CLOSE + timedelta(minutes=6)
        asyncio.run(sc.collect(Schwab(quotes_for(w)), w.market, now))
        ours = [Bar(r["symbol"], DAY, r["open"], r["high"], r["low"], r["close"], r["volume"])
                for r in w.market.conn.execute("SELECT * FROM stk_bars WHERE day=?", (DAY.toordinal(),))]
        morning = now + timedelta(hours=16)
        assert asyncio.run(sc.verify(Massive(None), w.market, morning)) == {"verified": {}}   # not published yet
        agreed = asyncio.run(sc.verify(Massive(ours), w.market, morning))["verified"][DAY.isoformat()]
        assert agreed["disagree"] == 0 and agreed["compared"] == len(ours)
        assert asyncio.run(sc.verify(Massive(ours), w.market, morning))["verified"] == {}       # checked once
        # A later day whose Schwab closes are off: the feature switches itself off and research waits for Massive.
        next_day = cal.next_trading_day(DAY)
        w.market.save_bars(next_day, [Bar(b.symbol, next_day, b.open, b.high, b.low, b.close, b.volume) for b in ours],
                           sc.SOURCE, morning)
        wrong = [Bar(b.symbol, next_day, b.open, b.high, b.low, b.close * 1.03, b.volume) for b in ours]
        report = asyncio.run(sc.verify(Massive(wrong), w.market, morning + timedelta(days=1)))
        assert "disagreed" in report["disabled"]
        assert sc.due(w.market, cal.session_close(cal.next_trading_day(next_day)) + timedelta(minutes=6)) is None
        stored = w.market.conn.execute("SELECT close FROM stk_bars WHERE day=? ORDER BY symbol",
                                       (next_day.toordinal(),)).fetchall()
        assert [r[0] for r in stored] == [b.close for b in sorted(ours, key=lambda b: b.symbol)]   # never rewritten
    finally:
        w.close()


def test_a_signed_out_schwab_is_skipped_quietly(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from desk.stocks.schwab_auth import SchwabLoginExpired

    class Expired(Schwab):
        async def closing_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
            raise SchwabLoginExpired("Schwab sign-in expired")

    w = make_world(tmp_path, n=12)
    try:
        suite = SimpleNamespace(schwab=Expired({}), massive=SimpleNamespace(configured=False))
        result = asyncio.run(sc.job(suite, w.market, CLOSE + timedelta(minutes=6)))
        assert result["ok"] and "sign-in" in result["close"]["skipped"]
    finally:
        w.close()


@pytest.mark.parametrize("symbols", [[], [f"S{i}" for i in range(101)]])
def test_closing_quotes_rejects_batches_outside_one_to_a_hundred(symbols: list[str]) -> None:
    from desk.stocks.sources.schwab import SchwabClient

    client = SchwabClient.__new__(SchwabClient)
    with pytest.raises(ValueError):
        asyncio.run(client.closing_quotes(symbols))
