"""Regression checks for prospective quote support, revision retention, and PAPER scheduling."""
from __future__ import annotations

import asyncio
import json
import math
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from desk.core import clock
from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import earnings_dates, execution, runner
from desk.stocks.contracts import Bar, OptionQuote
from desk.stocks.forecast.panel import candidates, earnings_days
from desk.stocks.learner import forward, model
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import StocksDatabases
from desk.stocks.web import create_app
from tests.stocks_world import NOW, add_chains, make_world


def test_execution_receipt_needs_joint_prices_and_size() -> None:
    leg = [("LONG", 1), ("SHORT", -1)]
    buy = execution.Quote("LONG", NOW, 1.9, 2.0, 3, 1)
    sell = execution.Quote("SHORT", NOW, 1.0, 1.1, 1, 2)
    both = {q.occ: q for q in (buy, sell)}
    receipt = execution.evidence(leg, both, NOW, NOW, max_debit=100.)
    assert receipt["status"] == "supported" and receipt["capacity"] == 1
    assert execution.evidence(leg, both, NOW, NOW, units=2)["status"] == "unsupported"
    assert execution.evidence(leg, both, NOW, NOW, max_debit=99.)["status"] == "unsupported"
    assert execution.evidence(leg, {"LONG": buy}, NOW, NOW)["status"] == "unknown"
    staggered = {**both, "SHORT": replace(sell, at=NOW - timedelta(seconds=32))}
    assert execution.evidence(leg, staggered, NOW, NOW)["status"] == "unknown"
    no_size = {**both, "SHORT": replace(sell, bid_size=None)}
    assert execution.evidence(leg, no_size, NOW, NOW)["status"] == "unknown"
    assert execution.evidence(leg, both, NOW, NOW - timedelta(seconds=1))["status"] == "unknown"


def test_archive_keeps_cutoff_quotes_sizes_and_earnings_after_restart(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    day, expiry, symbol = w.days[-1], date(2026, 11, 20), "SPY"
    q = OptionQuote("SPY261120C00400000", symbol, "call", 400, expiry, 2, 2.2, None, 10, 20, None, NOW, 2, 3)
    before = NOW + timedelta(seconds=5)
    later = NOW + timedelta(hours=1)
    w.market.save_option_chain(symbol, day, expiry, before, 410, [q])
    w.market.save_option_chain(symbol, day, expiry, later, 411, [replace(q, ask=3.)])
    earnings_dates.save(w.dbs.market, [{"symbol": "SPY", "date": "2026-11-10"}], {"SPY"}, before)
    earnings_dates.save(w.dbs.market, [{"symbol": "SPY", "date": "2026-11-14"}], {"SPY"}, later)
    expected_members = candidates(w.dbs.market, 1, min_sessions=1, through=day)
    after = cal.next_trading_day(day)
    w.market.save_bars(after, [Bar("ZZZ", after, 100, 101, 99, 100, 1e9)], "synthetic", later)
    assert candidates(w.dbs.market, 1, min_sessions=1, through=day) == expected_members
    w.close()
    with closing(StocksDatabases.open(w.paths)) as reopened:
        from desk.stocks.repo import StocksMarket

        market = StocksMarket(reopened.market)
        chains = market.chains_on(day, [symbol], observed_by=before)[symbol]
        assert chains["price"] == 410 and chains["expirations"][expiry][0].ask_size == 3
        assert market.option_chain(symbol, day, observed_by=before)[1][expiry][0].ask == 2.2
        assert market.option_chain(symbol, day)[1][expiry][0].ask == 3
        dates = earnings_dates.known(reopened.market, day, before, {symbol})
        assert dates[symbol]["date"] == "2026-11-10"
        announced_before_close = cal.session_close(day) - timedelta(minutes=1)
        first, second = cal.next_trading_day(day), cal.next_trading_day(after)
        report = (second + timedelta(days=7)).isoformat()
        earnings_dates.save(reopened.market, [{"symbol": symbol, "date": report}], {symbol}, announced_before_close)
        earnings_dates.save(reopened.market, [], {symbol}, cal.session_close(first) - timedelta(minutes=1))
        actual = earnings_days(reopened.market, [symbol], [day, first, second])[0]
        assert actual[0] == (date.fromisoformat(report) - day).days
        assert math.isnan(actual[1]) and math.isnan(actual[2])


@pytest.mark.asyncio
async def test_ledger_steps_before_optional_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = make_world(tmp_path, n=12)
    expected = w.days[-1]
    w.close()
    monkeypatch.setattr(runner, "utc_now", lambda: NOW)
    reached = asyncio.Event()

    async def listings(*args: Any) -> dict[str, Any]:
        # This job never returns during the test. The ledger and deadline should already be published.
        reached.set()
        await asyncio.Future()
        return {}

    monkeypatch.setattr(runner.collect, "listings", listings)
    task = asyncio.create_task(runner.cycle(w.paths, load_config(w.paths.config_dir)))
    try:
        await asyncio.wait_for(reached.wait(), 15)
        assert runner.saved(w.paths, "paper")["asof"] == expected.isoformat()
        assert runner.saved(w.paths, "deadline")["stepped"] is True
        with closing(StocksDatabases.open(w.paths)) as db:
            assert PaperBook(db.ledger).last_step() == expected
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_forward_summary_counts_cash_and_abstentions_but_waits_for_independent_blocks(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        now = NOW + timedelta(days=2)
        conn = w.dbs.research
        for i, d in enumerate(w.days[-20:]):
            cohort = {"model": model.version_hash(), "policy": forward.policy.version_hash(),
                      "learner": None if i >= 3 else {"symbol": "SPY"},
                      "challenger": {"symbol": "SPY"}, "rejected": None}
            conn.execute("INSERT INTO stk_execution_cohort VALUES (?,?,?,?)",
                         (d.toordinal(), forward.VERSION, NOW.isoformat(), json.dumps(cohort)))
            if i < 3:
                result = {"learner": {"conservative": 10.}, "challenger": {"conservative": 1.},
                          "rejected": {}, "through": w.days[-1].isoformat()}
                conn.execute("INSERT INTO stk_execution_grades VALUES (?,?,?,?)",
                             (d.toordinal(), forward.VERSION, now.isoformat(), json.dumps(result)))
        report = forward.summary(conn)
        assert report["eligible"] == 20 and report["paired"] == 3 and report["cash_days"] == 17
        assert report["independent_blocks"] == 1 and report["confirmation"] is None
    finally:
        w.close()


def test_positive_position_blocks_cannot_hide_opportunity_cost_on_cash_days(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.dbs.research
        arm = {"symbol": "SPY"}
        for i, day in enumerate(w.days[-40:]):
            traded = i % 2 == 1
            cohort = {"model": model.version_hash(), "policy": forward.policy.version_hash(),
                      "learner": arm if traded else None, "challenger": arm, "rejected": None,
                      "learner_outcome": "selected" if traded else "cash"}
            result = {"learner": {"conservative": 20. if traded else 0.},
                      "challenger": {"conservative": 0. if traded else 40.},
                      "rejected": {}, "through": day.isoformat()}
            conn.execute("INSERT INTO stk_execution_cohort VALUES (?,?,?,?)",
                         (day.toordinal(), forward.VERSION, NOW.isoformat(), json.dumps(cohort)))
            conn.execute("INSERT INTO stk_execution_grades VALUES (?,?,?,?)",
                         (day.toordinal(), forward.VERSION, NOW.isoformat(), json.dumps(result)))
        summary = forward.summary(conn)
        assert summary["cash_days"] == 20
        confirmation = summary["confirmation"]
        assert confirmation["paired_lower"] > forward.FLOOR and confirmation["cash_lower"] > forward.FLOOR
        assert confirmation["all_comparable_vs_peer"] < 0 and not confirmation["passes"]
    finally:
        w.close()


def test_read_only_execution_route_and_paper_controls_are_wired(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    add_chains(w, ["SPY", "CHEAP", "SQZ"], [w.days[-1]])
    runner.publish_plays(w.paths, w.dbs, NOW)
    w.close()
    app = create_app(w.paths, auto_start=False)
    with TestClient(app, base_url="http://localhost:8767") as client:
        board = client.get("/api/stocks/plays").json()
        play = next(p for c in board["categories"] for p in c["plays"] if len(p.get("legs", [])) >= 2)
        result = client.get(f"/api/stocks/plays/idea/{play['idea_id']}/execution").json()
        assert result["status"] in {"supported", "unknown", "unsupported"}
        assert [a["label"] for a in result["accounts"]] == ["$1,000 account", "$5,000 account"]
        assert client.get("/api/stocks/learner/account").json()["execution_study"]["eligible"] == 0


@pytest.mark.parametrize("source", ("prices", "schwab_close"))
def test_paper_shows_how_to_recover_an_expired_close_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      source: str) -> None:
    w = make_world(tmp_path, n=12)
    now = cal.session_close(cal.next_trading_day(w.days[-1])) + timedelta(hours=2)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    if source == "prices":
        w.market.job(source, now, error="quotes: HTTP 401")
    else:
        w.market.job(source, now, detail={"close": {"skipped": "Schwab sign-in needed"}})
    w.close()
    with TestClient(create_app(w.paths, auto_start=False), base_url="http://localhost:8767") as client:
        status = client.get("/api/stocks/paper").json()["session_status"]
        assert status["waiting_for_prices"] and status["source_health"][source]["authorization"]
        assert "Open Settings to reconnect" in status["reason"]


@pytest.mark.asyncio
async def test_sample_claims_one_request_per_slot_across_restart(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    day = w.days[-1]
    opened = forward.open_at(cal.next_trading_day(day)) + timedelta(minutes=1)
    arms = {"learner": None, "challenger": None,
            "rejected": {"legs": [("LONG", 1), ("SHORT", -1)], "entry": 1.0}}
    w.dbs.research.execute("INSERT INTO stk_execution_cohort VALUES (?,?,?,?)",
                          (day.toordinal(), forward.VERSION, NOW.isoformat(), json.dumps(arms)))
    w.close()
    calls = []
    async def quotes(occs: list[str]) -> list[Any]:
        calls.append(occs)
        return [SimpleNamespace(occ=occ, quoted_at=opened, bid=bid, ask=ask, bid_size=1, ask_size=1)
                for occ, bid, ask in (("LONG", 1.9, 2.), ("SHORT", 1., 1.1))]

    client = SimpleNamespace(configured=True, option_quotes=quotes)
    with closing(StocksDatabases.open(w.paths)) as db:
        assert (await forward.sample(db.research, client, opened, lambda: opened))["requests"] == 1
    with closing(StocksDatabases.open(w.paths)) as db:
        assert await forward.sample(db.research, client, opened, lambda: opened) is None
        stored = db.research.execute("SELECT evidence_json FROM stk_execution_windows").fetchone()
        assert json.loads(stored[0])["rejected"]["status"] == "supported"
    assert calls == [["LONG", "SHORT"]]
