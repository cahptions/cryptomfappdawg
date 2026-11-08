"""Massive's two years are survivorship-free: companies delisted during them are listed from Massive's reference data,
their grouped-daily bars are kept up to the delisting day, and the point-in-time universe of a rebuilt or replayed day
includes them. Schwab's earlier years hold survivors only, so evaluations report the two periods separately."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import delisted
from desk.stocks import validation as val
from desk.stocks.analysis import Analyzer
from desk.stocks.backtest import periods
from desk.stocks.contracts import Bar, Listing
from desk.stocks.prices import load_panel
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch
from desk.stocks.universe import listed_on, stored_symbols
from tests.conftest import REPO

NOW = datetime(2026, 11, 6, 23, 0, tzinfo=UTC)
SETTINGS = load_config(REPO / "config").settings.stocks.model_copy(update={"history_days": 150})
DAYS = cal.trading_days(NOW.date() - timedelta(days=150), cal.last_closed_session(NOW, timedelta(minutes=75)))
GONE_ON = DAYS[-15]                      # delisted three weeks ago, before collection began


def bar(symbol: str, d: date, price: float) -> Bar:
    return Bar(symbol, d, price, price * 1.01, price * 0.99, price, 2e6)


class Massive:
    def __init__(self) -> None:
        self.days: list[date] = []

    async def delisted_tickers(self, kind: str) -> list[dict[str, Any]]:
        if kind != "CS":
            return []
        return [{"symbol": "GONE", "name": "Gone Inc", "type": "CS", "active": False, "cik": 77, "delisted": GONE_ON},
                {"symbol": "LIVE", "name": "An older LIVE", "type": "CS", "active": False, "cik": 78,
                 "delisted": DAYS[5]},                                    # reused ticker: left out
                {"symbol": "OLD", "name": "Old Inc", "type": "CS", "active": False, "cik": 79,
                 "delisted": DAYS[0] - timedelta(days=30)},               # before Massive's window
                {"symbol": "WRNT", "name": "A warrant", "type": "WARRANT", "active": False, "cik": None,
                 "delisted": DAYS[10]}]

    async def grouped_daily(self, d: date) -> list[Bar]:
        self.days.append(d)
        # Massive's file for a past day still has the delisted company; after its delisting the ticker is someone
        # else's, which is never kept under it
        return [bar("LIVE", d, 40.0), bar("SPY", d, 500.0), bar("GONE", d, 30.0 if d <= GONE_ON else 1.0)]

    async def splits(self, since: date, until: date | None = None) -> list[Any]:
        return []

    async def dividends(self, since: date, until: date | None = None) -> list[Any]:
        return []


def world(tmp_path: Path) -> tuple[StocksDatabases, StocksMarket]:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(paths)
    m = StocksMarket(dbs.market)
    # Collection began today: GONE never appeared in a listing file, and ingest kept only today's listings.
    m.save_listings([Listing("LIVE", "Live Corp", "NYSE", False, False, "common"),
                     Listing("SPY", "SPDR S&P 500", "NYSE Arca", True, False, "etf")], {}, NOW)
    for d in DAYS:
        m.save_bars(d, [bar("LIVE", d, 40.0), bar("SPY", d, 500.0)], "massive", NOW)
    return dbs, m


def test_companies_delisted_during_massives_window_join_the_point_in_time_universe(tmp_path: Path) -> None:
    dbs, m = world(tmp_path)
    try:
        before = DAYS[-30]
        assert "GONE" not in {r["symbol"] for r in listed_on(m, before)}           # the survivorship gap, before
        assert delisted.survivorship_free_from(m.conn) == NOW.date()
        massive = Massive()
        suite = SimpleNamespace(massive=massive)
        live_rows = list(m.conn.execute("SELECT * FROM stk_bars WHERE symbol = 'LIVE' ORDER BY day"))
        for _ in range(60):
            out = asyncio.run(delisted.job(suite, m, NOW, SETTINGS))               # type: ignore[arg-type]
            assert out["ok"], out
            if out.get("complete"):
                break
        assert out["complete"] and out["delisted"] == 1 and out["reused_tickers"] == 1
        assert {r[0] for r in m.conn.execute("SELECT symbol FROM stk_delisted")} == {"GONE"}
        gone = [r[0] for r in m.conn.execute("SELECT day FROM stk_bars WHERE symbol = 'GONE' ORDER BY day")]
        assert gone == [d.toordinal() for d in DAYS if d <= GONE_ON]                  # up to the delisting day only
        assert list(m.conn.execute("SELECT * FROM stk_bars WHERE symbol = 'LIVE' ORDER BY day")) == live_rows
        assert m.conn.execute("SELECT rows FROM stk_bar_days WHERE day = ?", (before.toordinal(),)).fetchone()[0] == 3
        assert "GONE" in stored_symbols(m) and "GONE" not in stored_symbols(m, delisted=False)
        names = {r["symbol"]: r for r in listed_on(m, before)}
        assert names["GONE"]["kind"] == "common" and names["GONE"]["cik"] == 77
        assert "GONE" not in {r["symbol"] for r in listed_on(m, DAYS[-1])}
        assert delisted.survivorship_free_from(m.conn) == DAYS[0]
        # The rebuilt / replayed day's universe trades it.
        panel = load_panel(m, DAYS[0], before)
        day = Analyzer(m, StocksResearch(dbs.research), SETTINGS).build(panel, before, historical=True)
        assert "GONE" in day.ctx.symbols
        again = asyncio.run(delisted.job(suite, m, NOW, SETTINGS))                    # type: ignore[arg-type]
        assert again["complete"] and len(massive.days) == len([d for d in DAYS if d <= GONE_ON])
    finally:
        dbs.close()


def test_the_list_waits_for_massives_two_years(tmp_path: Path) -> None:
    dbs, m = world(tmp_path)
    try:
        long = SETTINGS.model_copy(update={"history_days": 400})
        out = asyncio.run(delisted.job(SimpleNamespace(massive=Massive()), m, NOW, long))  # type: ignore[arg-type]
        assert "Waiting" in out["waiting"] and not m.conn.execute("SELECT 1 FROM stk_delisted").fetchone()
    finally:
        dbs.close()


def test_returns_are_reported_for_the_survivors_only_years_and_the_survivorship_free_years() -> None:
    curve = [{"day": f"2026-0{m}-01", "equity": e} for m, e in ((1, 100.0), (2, 110.0), (3, 121.0), (4, 133.1))]
    spy = [{"day": p["day"], "equity": 100.0 + 5 * k} for k, p in enumerate(curve)]
    split = periods(curve, spy, date(2026, 3, 1))
    assert [p["survivorship_free"] for p in split] == [False, True]
    assert abs(split[0]["return"] - 0.10) < 1e-9 and abs(split[1]["return"] - 0.21) < 1e-9
    assert abs(split[1]["spy_return"] - (115 / 105 - 1)) < 1e-9
    assert [p["survivorship_free"] for p in periods(curve, spy, None)] == [True]
    record = val._periods(date(2026, 3, 1), [(date(2026, 1, 30), 1), (date(2026, 3, 31), 2)], lambda part: {"n": len(part)})
    assert record == {"survivorship_free": {"n": 1}, "earlier": {"n": 1}}
