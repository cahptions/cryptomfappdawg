"""$VIX, $TNX and $IRX from Schwab's quotes: only a finished session's closing print, stored as its own source, preferred
on the days it exists, with Treasury and FRED filling every other day. Synthetic quotes only."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks import collect
from desk.stocks.contracts import RatePoint
from desk.stocks.signals import macro
from tests.stocks_world import ASOF, make_world

CLOSE = cal.session_close(ASOF)


def quote(value: float, at: datetime) -> dict[str, Any]:
    return {"last": value, "trade_time": at.isoformat()}


def test_only_the_sessions_closing_print_counts() -> None:
    after = CLOSE + timedelta(minutes=20)
    assert collect.index_close(quote(18.5, CLOSE + timedelta(minutes=15)), "VIXCLS", after) == RatePoint(
        "VIXCLS@schwab", ASOF, 18.5)
    assert collect.index_close(quote(42.5, CLOSE), "UST10Y", after) == RatePoint("UST10Y@schwab", ASOF, 4.25)
    assert collect.index_close(quote(4.3, CLOSE), "UST10Y", after).value == 4.3          # type: ignore[union-attr]
    assert collect.index_close(quote(18.5, CLOSE - timedelta(hours=2)), "VIXCLS", CLOSE - timedelta(hours=1)) is None
    assert collect.index_close(quote(19.0, CLOSE + timedelta(hours=2)), "VIXCLS", after + timedelta(hours=3)) is None
    assert collect.index_close({"last": None, "trade_time": CLOSE.isoformat()}, "VIXCLS", after) is None


def test_schwab_closes_are_stored_apart_and_win_only_on_their_own_days(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=8)
    try:
        days = w.days[-3:]
        w.market.save_rates([RatePoint("VIXCLS", d, 20.0) for d in days] + [RatePoint("UST10Y", d, 4.3) for d in days])

        class Schwab:
            configured = True

            async def closing_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
                assert symbols == ["$VIX", "$TNX", "$IRX"]
                return {"$VIX": quote(31.0, CLOSE), "$TNX": quote(44.0, CLOSE)}

        out = asyncio.run(collect.index_closes(SimpleNamespace(schwab=Schwab()), w.market,     # type: ignore[arg-type]
                                               CLOSE + timedelta(minutes=30)))
        assert out["ok"] and out["stored"] == 2 and out["missing"] == ["$IRX"]
        vix = w.market.rates("VIXCLS")
        assert [(p.day, p.value) for p in vix] == [(days[0], 20.0), (days[1], 20.0), (ASOF, 31.0)]
        assert [p.value for p in w.market.rates("VIXCLS", merged=False)] == [20.0, 20.0, 20.0]
        assert w.market.rates("UST10Y")[-1].value == 4.4
        after = macro(w.market, ASOF + timedelta(days=1), 0.05)
        assert after["vix"] == 31.0 and after["regime"] == "RISK_OFF" and after["ust10y"] == 4.4
        assert macro(w.market, ASOF, 0.05)["vix"] == 20.0                                   # a day is read only after it
    finally:
        w.close()
