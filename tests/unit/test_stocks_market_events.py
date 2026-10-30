"""Notable market events: fixed rules, each day judged from data through its close, appended once and never rewritten."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.stocks import calendar as cal
from desk.stocks import market_events as me
from desk.stocks.contracts import RatePoint
from desk.stocks.web import create_app
from tests.stocks_world import NOW, make_world

AI = me.BASKETS[0].members


def world_closes(sessions: int = 300, seed: int = 3) -> tuple[me.Closes, dict[str, np.ndarray]]:
    """A quiet market of 400 liquid stocks, the benchmarks and the AI basket; tests plant moves in ``paths``."""
    rng = np.random.default_rng(seed)
    days = cal.trading_days(date(2025, 6, 2), date(2027, 1, 1))[:sessions]
    paths: dict[str, np.ndarray] = {}

    def walk(start: float, sigma: float) -> np.ndarray:
        return np.asarray(start * np.cumprod(1 + rng.normal(0.0003, sigma, sessions)))

    for k in range(400):
        paths[f"Q{k:03d}"] = walk(50.0, 0.012)
    for symbol in (*me.INDEXES, *me.GROUPS, "SMH"):
        paths[symbol] = walk(400.0, 0.008)
    for symbol in {*AI, *me.BASKETS[1].members}:
        paths[symbol] = walk(150.0, 0.012)
    return closes_from(paths, days), paths


def closes_from(paths: dict[str, np.ndarray], days: list[date], volume: dict[str, float] | None = None) -> me.Closes:
    symbols = sorted(paths)
    close = np.array([paths[s] for s in symbols], dtype=float)
    vol = np.array([[(volume or {}).get(s, 1e6)] * len(days) for s in symbols], dtype=float)
    funds = {*me.INDEXES, *me.GROUPS, "SMH"}
    names = {"GOOGL": "Alphabet", "NVDA": "NVIDIA"}
    return me.Closes(symbols, days, close, vol, names, np.array([s not in funds for s in symbols]))


def drop_last(paths: dict[str, np.ndarray], symbol: str, move: float) -> None:
    paths[symbol][-1] = paths[symbol][-2] * (1 + move)


def test_an_ai_selloff_day_is_logged_as_a_record_stock_drop_a_basket_move_and_a_sector_move() -> None:
    closes, paths = world_closes()
    days = closes.days
    drop_last(paths, "GOOGL", -0.131)
    for member in AI:
        if member != "GOOGL":
            drop_last(paths, member, -0.035)
    drop_last(paths, "XLC", -0.042)
    drop_last(paths, "QQQ", -0.014)          # an ordinary bad day for the index: not notable on its own
    volume = {"GOOGL": 3e7}                  # the most traded stock
    reported = {"GOOGL": {days[-1]: days[-2]}}
    found = me.scan_prices(closes_from(paths, days, volume), len(days) - 1, lambda s: reported.get(s, {}))
    by = {(e.kind, e.subject): e for e in found}

    stock = by[("move", "GOOGL")]
    assert stock.family == "stock" and stock.direction == "down" and stock.tone == "negative"
    assert stock.title == "Alphabet (GOOGL) fell 13.1%"
    assert stock.detail["most_traded_rank"] == 1 and stock.detail["results_filed"] == days[-2].isoformat()
    assert stock.detail["record_since"] == days[0].isoformat() and stock.severity == 3
    assert "reported results" in stock.summary and "largest one-day fall" in stock.summary

    basket = by[("move", "ai")]
    assert basket.family == "theme" and basket.title.startswith("AI stocks fell")
    assert basket.detail["members"][0]["symbol"] == "GOOGL"          # biggest faller first
    assert basket.detail["counted"] == len(AI) and basket.detail["moved"] == len(AI)
    assert ("move", "XLC") in by and by[("move", "XLC")].title == "Communication services fell 4.2%"
    assert ("move", "QQQ") not in by


def test_judging_a_day_never_reads_later_sessions() -> None:
    closes, paths = world_closes()
    days = closes.days
    before = me.scan_prices(closes_from(paths, days), len(days) - 2)
    drop_last(paths, "SPY", -0.08)
    paths["NVDA"][-1] *= 0.5
    after = me.scan_prices(closes_from(paths, days), len(days) - 2)
    assert [(e.kind, e.subject, e.title) for e in before] == [(e.kind, e.subject, e.title) for e in after]


def test_broad_selloff_counts_only_actively_traded_stocks() -> None:
    closes, paths = world_closes()
    days = closes.days
    stocks = [s for s in sorted(paths) if s not in {*me.INDEXES, *me.GROUPS, "SMH"}]
    for k, symbol in enumerate(stocks):
        drop_last(paths, symbol, -0.02 if k < 0.93 * len(stocks) else 0.01)     # 93% fell
    events = [e for e in me.scan_prices(closes_from(paths, days), len(days) - 1) if e.kind == "breadth"]
    assert len(events) == 1 and events[0].direction == "down" and events[0].severity == 2
    assert events[0].detail["counted"] == len(stocks) and events[0].title.startswith("Broad sell-off: 93% of")
    thin = {f"Q{k:03d}": 1e4 for k in range(400)}                           # $0.5M a day: not actively traded
    assert not [e for e in me.scan_prices(closes_from(paths, days, thin), len(days) - 1) if e.kind == "breadth"]


def test_correction_bear_market_and_recovery_are_logged_once_on_the_first_close_past_each_line() -> None:
    days = cal.trading_days(date(2024, 1, 2), date(2026, 12, 31))[:420]
    level = np.concatenate([np.linspace(80, 100, 260), np.linspace(99.5, 89.5, 20), np.full(20, 89.0),
                            np.linspace(88, 79, 20), np.linspace(80, 101, 100)])
    closes = closes_from({"SPY": level, "QQQ": level * 2}, days)
    found = [(closes.days[j], e) for j in range(len(days)) for e in me._drawdowns(closes, j) if e.subject == "SPY"]
    titles = [e.title for _, e in found]
    assert titles == ["S&P 500 entered a correction: 10.5% below its high",
                      "S&P 500 entered a bear market: 20.1% below its high", "S&P 500 is back at a 52-week high"]
    assert [e.severity for _, e in found] == [2, 3, 1]
    assert found[0][1].detail["high_day"] == days[259].isoformat()


def test_trend_break_needs_twenty_sessions_on_the_other_side() -> None:
    days = cal.trading_days(date(2024, 1, 2), date(2026, 12, 31))[:260]
    level = np.concatenate([np.linspace(100, 130, 250), np.full(10, 100.0)])
    closes = closes_from({"SPY": level}, days)
    found = [(j, e.title) for j in range(len(days)) for e in me._trends(closes, j)]
    assert found == [(250, "S&P 500 closed below its 200-day average")]
    whipsaw = level.copy()
    whipsaw[240:250:2] = 80.0               # below the average for a day at a time: not a held trend
    assert not [e for j in range(len(days)) for e in me._trends(closes_from({"SPY": whipsaw}, days), j)
                if j >= 251]


def test_a_multi_day_slide_is_logged_on_the_first_day_it_qualifies() -> None:
    closes, paths = world_closes()
    days = closes.days
    for k in range(6):
        paths["QQQ"][-6 + k:] *= 0.98
    closes = closes_from(paths, days)
    runs = [(j, e) for j in range(len(days) - 6, len(days)) for e in me._runs(closes, j) if e.subject == "QQQ"]
    assert len(runs) == 1 and runs[0][1].title.startswith("Nasdaq 100 fell") and "over 5 sessions" in runs[0][1].title


def test_vix_yield_and_credit_rules() -> None:
    d = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]
    points = {"VIXCLS": [RatePoint("VIXCLS", d[0], 15.0), RatePoint("VIXCLS", d[1], 21.0), RatePoint("VIXCLS", d[2], 31.0)],
              "UST10Y": [RatePoint("UST10Y", d[0], 4.30), RatePoint("UST10Y", d[1], 4.32),
                         RatePoint("UST10Y", d[2], 4.52)],
              "BAMLH0A0HYM2": [RatePoint("BAMLH0A0HYM2", x, 3.0 + 0.1 * k) for k, x in enumerate(d)]}
    assert [e.title for e in me.scan_rates(points, d[1])] == ["VIX jumped 40.0% to 21.0"]
    later = me.scan_rates(points, d[2])
    assert [(e.subject, e.severity) for e in later] == [("VIX", 2), ("UST10Y", 1)]
    assert later[0].title == "VIX closed above 30 (31.0)"
    assert later[1].title == "10-year Treasury yield rose 0.20 points to 4.52%" and later[1].tone == "neutral"
    assert me.scan_rates(points, date(2026, 10, 29)) == []        # no observation that day: nothing judged


def test_scan_backfills_once_waits_for_split_data_and_never_rewrites(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        research = w.dbs.research
        first = me.scan(w.market, research, NOW)
        assert first["price_sessions"] == len(w.days) - 2 and "split data" in first["waiting"]
        rows = research.execute("SELECT * FROM stk_market_events ORDER BY day, kind, subject").fetchall()
        assert rows and {r["basis"] for r in rows} == {"history"} and {r["spec_sha256"] for r in rows} == {me.SPEC_SHA256}
        assert w.dbs.market.execute("SELECT 1 FROM stk_specs WHERE name=? AND spec_sha256=?",
                                    (me.NAME, me.SPEC_SHA256)).fetchone()

        w.market.job("corporate_actions", NOW, detail={"through": w.days[-1].isoformat()})
        second = me.scan(w.market, research, NOW + timedelta(minutes=5))
        assert second["price_sessions"] == 2 and "waiting" not in second
        assert me.scan(w.market, research, NOW + timedelta(minutes=10))["price_sessions"] == 0
        again = research.execute("SELECT * FROM stk_market_events WHERE day <= ? ORDER BY day, kind, subject",
                                 (w.days[-3].toordinal(),)).fetchall()
        assert [tuple(r) for r in again] == [tuple(r) for r in rows]
        page = me.page(research)
        assert page["coverage"]["sessions"] == len(w.days) and page["standing"][0]["subject"] == "SPY"
        assert page["events"] == sorted(page["events"], key=lambda e: e["day"], reverse=True)
        assert {r["kind"] for r in page["rules"]} >= {"move", "run", "breadth", "drawdown", "volatility", "rates"}
    finally:
        w.close()


class News:
    configured = True

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, datetime | None, datetime | None]] = []
        self.fail = fail

    async def news(self, symbol: str, limit: int = 20, *, published_after: datetime | None = None,
                   published_before: datetime | None = None) -> list[dict[str, Any]]:
        self.calls.append((symbol, published_after, published_before))
        if self.fail:
            raise ProviderError("massive", ProviderErrorKind.HTTP_ERROR, "news: HTTP 429", 429)
        return [{"title": "Alphabet slides after results", "url": "https://example.com/a", "source": "Wire",
                 "published": "2026-10-29T13:05:00+00:00", "summary": "not kept", "insights": []}]


def test_headlines_are_fetched_for_recent_stock_moves_inside_the_session_window(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=8)
    try:
        research, day = w.dbs.research, date(2026, 10, 29)
        now = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
        stock = me.Event(day, "move", "stock", "GOOGL", 3, "down", "negative", "Alphabet (GOOGL) fell 13.1%", "…",
                         {"symbol": "GOOGL", "move": -0.131})
        index = me.Event(day, "move", "index", "QQQ", 1, "down", "negative", "Nasdaq 100 fell 3.0%", "…", {})
        with me.transaction(research):
            me.save(research, [stock, index], now, "close")
        failing = News(fail=True)
        report = asyncio.run(me.fetch_headlines(failing, research, now))
        assert report["headlines"] == 0 and "429" in report["headline_errors"][0]
        assert me.headlines_due(research, now + timedelta(hours=1)) == []         # rests after a failure
        source = News()
        assert asyncio.run(me.fetch_headlines(source, research, now + timedelta(hours=7)))["headlines"] == 1
        symbol, start, end = source.calls[0]
        assert symbol == "GOOGL" and start == cal.session_close(date(2026, 10, 28))
        assert end is not None and cal.new_york(end).date() == day
        event = next(e for e in me.read(research) if e["subject"] == "GOOGL")
        assert event["headlines"] == [{"title": "Alphabet slides after results", "url": "https://example.com/a",
                                       "source": "Wire", "published": "2026-10-29T13:05:00+00:00"}]
        assert me.headlines_due(research, now + timedelta(days=2)) == []
        assert asyncio.run(me.fetch_headlines(source, research, now + timedelta(days=40)))["headlines"] == 0
        assert [e["subject"] for e in me.read(research, group="stocks")] == ["GOOGL"]
        assert [e["subject"] for e in me.read(research, symbol="QQQ")] == ["QQQ"]
        assert json.loads(research.execute("SELECT items_json FROM stk_market_event_news").fetchone()[0])
    finally:
        w.close()


def test_events_endpoint_and_home_summary(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        w.market.job("corporate_actions", NOW, detail={"through": w.days[-1].isoformat()})
        me.scan(w.market, w.dbs.research, NOW)
        with TestClient(create_app(w.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
            value = client.get("/api/stocks/events").json()
            assert value["events"] and value["coverage"]["sessions"] == len(w.days)
            assert client.get("/api/stocks/events?group=market&severity=2").status_code == 200
            assert client.get("/api/stocks/events?group=nope").status_code == 422
            assert client.get("/api/stocks/events?symbol=bad%20symbol").status_code == 422
            home = client.get("/api/stocks/home").json()["events"]
            assert home["standing"] and isinstance(home["recent"], list)
    finally:
        w.close()


def test_rules_are_fingerprinted() -> None:
    assert me.sha256_hex(me.canonical_json(me.content())) == me.SPEC_SHA256
    assert set(me.content()["baskets"][0]["members"]) == set(AI)
