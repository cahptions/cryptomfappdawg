"""Verify the allocation decision fits only earlier data and the HTTP views use the right evidence."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk.core.clock import to_iso
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import capital
from desk.stocks.paper.book import MICRO, PaperBook
from desk.stocks.repo import StocksDatabases, day_of
from desk.stocks.runner import saved_path
from desk.stocks.web import create_app
from tests.conftest import REPO
from tests.stocks_world import NOW

ACCOUNTS = ("momentum", "undervalued")


def series(days: list[date]) -> tuple[dict[str, dict[date, float]], dict[date, float]]:
    curves: dict[str, dict[date, float]] = {a: {} for a in ACCOUNTS}
    spy = {}
    low, high, market = 1_000., 1_000., 500.
    for i, day in enumerate(days):
        move = (i % 7 - 3) / 400
        low *= 1 + .001 + move * .20
        high *= 1 + .002 + move * 2
        market *= 1 + .001 + move
        curves[ACCOUNTS[0]][day] = low
        curves[ACCOUNTS[1]][day] = high
        spy[day] = market
    return curves, spy


def test_fit_uses_only_earlier_sessions_and_has_reproducible_inputs() -> None:
    days = cal.trading_days(date(2026, 1, 5), date(2026, 5, 8))
    curves, spy = series(days)
    assumptions = {"window": 126, "reserve": .20, "cap": .60, "entry_bps": 10}
    excluded = {days[21]}
    baseline = capital.compare(curves, spy, excluded, **assumptions)
    assert baseline["status"] == "ready"
    assert baseline["coverage"]["matched"] == len(days) - 2
    assert baseline["coverage"]["excluded"] == 1
    assert baseline["fit"]["last"] < baseline["test"]["first"]
    assert baseline["allocations"][0]["weight"] == pytest.approx(.60 * .80)
    assert sum(a["weight"] for a in baseline["allocations"]) == pytest.approx(.80)
    assert sum(a["risk_share"] for a in baseline["allocations"]) == pytest.approx(1.)
    assert baseline["metrics"]["cash"]["return"] == 0
    assert baseline["metrics"]["market"]["beta"] == pytest.approx(.80, abs=.01)
    reversed_curves = dict(reversed(list(curves.items())))
    assert capital.compare(reversed_curves, spy, excluded, **assumptions) == baseline
    zero_cost = capital.compare(curves, spy, excluded, **(assumptions | {"entry_bps": 0}))
    for key in ("risk", "equal", "market"):
        assert baseline["curve"][-1][key] == pytest.approx(.20 + (zero_cost["curve"][-1][key] - .20) * .999)
    for day in days:
        if day.isoformat() >= baseline["test"]["first"]:
            curves[ACCOUNTS[0]][day] *= 0.5
    shocked = capital.compare(curves, spy, excluded, **assumptions)
    assert shocked["allocations"] == baseline["allocations"]
    assert shocked["fit"] == baseline["fit"]
    assert shocked["inputs_sha256"] != baseline["inputs_sha256"]
    assert shocked["metrics"]["risk"]["return"] < baseline["metrics"]["risk"]["return"]


def test_api_uses_ledger_flow_and_gaps_and_frozen_replay_benchmark(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    days = cal.trading_days(date(2026, 1, 5), date(2026, 5, 8))
    curves, spy = series(days)
    dbs = StocksDatabases.open(paths)
    book = PaperBook(dbs.ledger)
    try:
        for account, curve in curves.items():
            for day, equity in curve.items():
                if account == ACCOUNTS[0] and day == days[12]:
                    continue
                book.snapshot(day, account, cal.session_close(day), equity, 0.)
        book.add_cash(ACCOUNTS[0], cal.session_close(days[23]), days[23], 200 * MICRO, "DEPOSIT")
        book.add_cash(ACCOUNTS[0], cal.session_close(days[27]) + timedelta(minutes=1), days[27], 200 * MICRO, "DEPOSIT")
        book.event(cal.session_close(days[25]), None, "RECOVERY", None, {"session": days[25].isoformat()})
        assert capital.ledger_curves(dbs.ledger, ACCOUNTS, 126)[1] == {days[23], days[28]}
        for day, close in spy.items():
            dbs.market.execute("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) "
                               "VALUES ('SPY',?,?,?,?,?,1e6)", (day_of(day), close, close, close, close))
        from desk.stocks.paper.report import report
        from desk.stocks.paper.strategies import STRATEGIES
        from desk.stocks.repo import StocksMarket

        stats = report(book, StocksMarket(dbs.market), tuple(s for s in STRATEGIES if s.id in ACCOUNTS))
    finally:
        dbs.close()
    saved_path(paths, "paper").write_text(json.dumps(stats | {"generated_at": to_iso(NOW)}))
    rows = [(key, [{"day": d.isoformat(), "equity": value} for d, value in curve.items()])
            for key, curve in curves.items()]
    archive = {"run_id": "synthetic", "generated_at": to_iso(NOW),
               "spy_total_curve": {day.isoformat(): close for day, close in spy.items()},
               "accounts": [{"id": key, "title": key, "asset": "stock", "sessions": len(curve), "curve": curve}
                            for key, curve in rows]}
    path = paths.home / "stocks-backtest-report.json"
    path.write_text(json.dumps(archive))
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        query = [("accounts", a) for a in ACCOUNTS]
        live = client.get("/api/stocks/portfolio", params=query).json()
        assert live["status"] == "ready"
        assert live["coverage"]["matched"] == len(days) - 6
        assert live["coverage"]["excluded"] == 3
        assert live["benchmark_basis"] == "Total return from the current market store"
        assert client.get("/api/stocks/portfolio", params=[*query, ("cap", "49")]).status_code == 422
        assert client.get("/api/stocks/portfolio", params=[*query, query[0]]).status_code == 422

        query.append(("source", "replay"))
        replay = client.get("/api/stocks/portfolio", params=query).json()
        assert replay["status"] == "ready" and replay["run_id"] == "synthetic"
        assert replay["coverage"]["matched"] == len(days) - 1
        assert replay["benchmark_basis"] == "Frozen with replay"
        del archive["spy_total_curve"]
        archive["accounts"].append({"id": "spy_hold", "baseline": True,
                                    "curve": [{"day": day.isoformat(), "equity": value} for day, value in spy.items()]})
        path.write_text(json.dumps(archive))
        legacy = client.get("/api/stocks/portfolio", params=query).json()
        assert legacy["status"] == "ready"
        assert legacy["inputs_sha256"] == replay["inputs_sha256"]
        archive["accounts"].pop()
        path.write_text(json.dumps(archive))
        unsupported = client.get("/api/stocks/portfolio", params=query).json()
        assert unsupported["status"] == "insufficient" and unsupported["coverage"]["benchmark"] == 0
