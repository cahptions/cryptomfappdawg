"""Announced earnings dates: Finnhub's free calendar (optional key) read daily for every stock, stored with when each
date was first and last listed, used by Plays' earnings sections and the stock page ahead of the window estimated from
past results filings, which stays (labelled) when no date is announced. Synthetic responses only."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from desk.core.config import load_config
from desk.stocks import earnings_dates as ed
from desk.stocks import plays as pl
from desk.stocks.analysis import Analyzer
from desk.stocks.daily import refresh
from desk.stocks.prices import load_panel
from desk.stocks.sources.finnhub import parse_earnings_calendar
from desk.stocks.stock_page import earnings
from tests.conftest import REPO
from tests.stocks_world import ASOF, NOW, add_chains, make_world

SETTINGS = load_config(REPO / "config").settings.stocks


def calendar(*rows: tuple[str, str, str]) -> bytes:
    return json.dumps({"earningsCalendar": [{"symbol": s, "date": d, "hour": h, "epsEstimate": 1.1} for s, d, h in rows]
                       + [{"symbol": None, "date": "2026-10-30"}, {"symbol": "X", "date": "not a date"}]}).encode()


def test_the_market_wide_calendar_keeps_each_rows_stock() -> None:
    rows = parse_earnings_calendar(calendar(("S10", "2026-10-28", "amc"), ("S11", "2026-10-29", "bmo")), None)
    assert [(r["symbol"], r["date"], r["when"]) for r in rows] == [("S10", "2026-10-28", "after the close"),
                                                                   ("S11", "2026-10-29", "before the open")]
    assert parse_earnings_calendar(calendar(("S10", "2026-10-28", "amc")), "S10")[0].get("symbol") is None


def test_dates_are_stored_daily_and_a_moved_date_drops_out(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        asked: list[tuple[date, date]] = []
        week = {0: [("S10", "2026-10-28", "amc"), ("NOTLISTED", "2026-10-28", "amc")], 1: [("S11", "2026-11-04", "bmo")]}

        class Finnhub:
            configured = True

            async def market_calendar(self, start: date, end: date) -> list[dict[str, Any]]:
                asked.append((start, end))
                return parse_earnings_calendar(calendar(*week.get(len(asked) - 1, [])), None)

        suite = SimpleNamespace(finnhub=Finnhub())
        first = asyncio.run(ed.job(suite, w.market, NOW))                     # type: ignore[arg-type]
        assert first["ok"] and first["stored"] == 2 and len(asked) == ed.WEEKS
        assert all((b - a).days == 6 for a, b in asked) and asked[0][0] == ASOF
        dates = ed.announced(w.market.conn, ASOF)
        assert dates["S10"]["date"] == "2026-10-28" and dates["S10"]["when"] == "after the close"
        assert "NOTLISTED" not in dates
        # next day S10 moved to Oct 29: the old date is no longer in the newest calendar
        asked.clear()
        week = {0: [("S10", "2026-10-29", "amc")]}
        later = NOW + timedelta(days=1)
        asyncio.run(ed.job(suite, w.market, later))                           # type: ignore[arg-type]
        dates = ed.announced(w.market.conn, ASOF)
        assert dates["S10"]["date"] == "2026-10-29" and "S11" not in dates
        kept = w.market.conn.execute("SELECT first_seen FROM stk_earnings_dates WHERE symbol='S10' AND report_date=?",
                                     (date(2026, 10, 28).toordinal(),)).fetchone()
        assert kept is not None                                               # history is kept, never rewritten away
    finally:
        w.close()


def test_plays_use_the_announced_date_and_otherwise_the_estimated_window(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        add_chains(w, ["S10", "S11"], w.days[-3:])
        refresh(Analyzer(w.market, w.research, SETTINGS), load_panel(w.market, w.days[0], ASOF), ASOF, NOW)
        ed.save(w.market.conn, [{"symbol": "S10", "date": "2026-10-28", "when": "after the close"}], {"S10"}, NOW)
        loaded = {u.symbol: u for u in pl.load(w.market, w.research, ASOF)}
        assert loaded["S10"].earnings == (date(2026, 10, 28), date(2026, 10, 28))
        assert loaded["S10"].earnings_when == "after the close"
        assert loaded["S11"].earnings_when is None                            # estimated window (or none), labelled
        before = pl.signature(w.market, w.research, ASOF, NOW)
        ed.save(w.market.conn, [{"symbol": "S10", "date": "2026-10-29", "when": None}], {"S10"}, NOW + timedelta(hours=1))
        assert pl.signature(w.market, w.research, ASOF, NOW) != before        # a new calendar rebuilds Plays
    finally:
        w.close()


def test_the_stock_page_says_whether_the_next_report_is_announced_or_estimated() -> None:
    today = date(2026, 10, 21)
    feed = {"calendar": {"value": [{"date": "2026-10-30", "when": "after the close"}], "error": None}}
    assert earnings(None, feed, today)["next"]["date"] == "2026-10-30"     # type: ignore[index]
    stored = earnings(None, {}, today, announced={"date": "2026-10-29", "when": "before the open",
                                                  "source": ed.LABEL}, window=["2026-10-25", "2026-11-08"])
    assert stored is not None and stored["next"]["date"] == "2026-10-29" and not stored["next"].get("estimated")
    guess = earnings(None, {}, today, window=["2026-10-25", "2026-11-08"])
    assert guess is not None and guess["next"]["estimated"] and guess["next"]["to"] == "2026-11-08"
    assert "Estimated" in guess["next"]["source"]
    assert earnings(None, {}, today, window=["2026-09-01", "2026-09-15"]) is None
