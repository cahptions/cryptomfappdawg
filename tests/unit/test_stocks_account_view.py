"""The "My Schwab account" view: KESTREL's view of each real position, and the decisions graded against random
twins. Synthetic positions, fills and prices only."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk.core.paths import DeskPaths
from desk.stocks import account, account_view
from desk.stocks import calendar as cal
from desk.stocks.repo import StocksDatabases, day_of
from desk.stocks.sources import schwab_account as sa
from desk.stocks.web import create_app, reader
from tests.conftest import REPO

NOW = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)
TODAY = date(2026, 11, 12)
EXPIRY = date(2026, 12, 18)
KEY = sa.account_key("SYNTHETICHASHA0000000000000000000000000000000000000000000000001")


def position(symbol: str, qty: float, avg: float, value: float, asset: str = "OPTION") -> sa.Position:
    parts = sa.option_parts(symbol)
    return sa.Position(KEY, "1234", asset, symbol, parts[0] if parts else None, parts[2] if parts else None,
                       parts[3] if parts else None, parts[1] if parts else None, qty, avg, value, 1.0, 0.1)


def fill(activity: str, symbol: str, qty: float, price: float, day: date, effect: str | None = None,
         asset: str = "EQUITY") -> sa.Fill:
    parts = sa.option_parts(symbol)
    at = datetime(day.year, day.month, day.day, 15, tzinfo=UTC)
    return sa.Fill(KEY, "1234", activity, 0, at, asset, symbol, parts[0] if parts else None,
                   parts[2] if parts else None, parts[3] if parts else None, parts[1] if parts else None, qty, price,
                   0.65, effect)


@pytest.fixture
def paths(tmp_path: Path) -> DeskPaths:
    p = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(p)
    try:
        for i, symbol in enumerate(("AAPL", "SPY", "T1", "T2", "T3")):
            dbs.market.execute("INSERT INTO stk_listings VALUES (?, ?, 'Q', 'stock', ?, 0, NULL, 1, '2026', '2026')",
                               (symbol, symbol, int(symbol == "SPY")))
            for n in range(80):
                day = TODAY - timedelta(days=79 - n)
                if cal.is_trading_day(day):
                    close = 100 * (1 + 0.001 * n * (i + 1))
                    dbs.market.execute("INSERT INTO stk_bars VALUES (?, ?, ?, ?, ?, ?, 1e6, NULL, NULL)",
                                       (symbol, day_of(day), close, close, close, close))
        detail = {"asof": TODAY.isoformat(), "committee": {"score": 71.0, "label": "Bullish"},
                  "price": {"close": 108.0, "vol_20d": 0.25},
                  "desks": {"options": {"facts": {"iv_rank": 42.0, "iv30": 0.3}}}}
        dbs.research.execute("INSERT INTO stk_scores_latest VALUES ('AAPL', ?, ?, ?)",
                             (day_of(TODAY), NOW.isoformat(), json.dumps(detail)))
    finally:
        dbs.close()
    return p


def test_each_position_gets_kestrels_view(paths: DeskPaths) -> None:
    dbs = StocksDatabases.open(paths)
    try:
        account._record(dbs.research, NOW, "manual", "ok", None, accounts=[], positions=[
            position("AAPL", 10, 100.0, 1080.0, "EQUITY"),
            position("AAPL261218C00110000", 2, 3.0, 2 * 100 * 3.4),
            position("AAPL261218P00100000", -1, 2.5, -1 * 100 * 2.0),
            position("AAPL261218C00130000", -1, 0.8, -1 * 100 * 0.5),
            position("ZZZZ", 5, 10.0, 50.0, "EQUITY")])
        rows = {r["symbol"]: r for r in account_view.positions_view(dbs.research, dbs.market, NOW)}
    finally:
        dbs.close()
    assert rows["AAPL"]["committee"]["score"] == 71 and rows["AAPL"]["committee"]["verdict"] == "Bullish"
    assert rows["AAPL"]["account"] == "…1234"
    assert rows["ZZZZ"]["committee"]["score"] is None and "no committee view" in rows["ZZZZ"]["committee"]["text"]
    call = rows["AAPL261218C00110000"]["option"]
    assert call["iv_rank"] == 42 and 0 < call["chance_profit"] < 1 and call["days_left"] == (EXPIRY - TODAY).days
    assert call["plan"]["take_profit"]["return"] == 1.0 and call["plan"]["stop"]["return"] == -0.5
    assert call["plan"]["take_profit"]["stock"]["above"] > 108 and call["plan"]["odds"]["take_profit"] is not None
    assert call["current_return"] == pytest.approx(0.4 / 3.0, abs=1e-3) and call["plan_state"].startswith("Hold")
    put = rows["AAPL261218P00100000"]["option"]
    assert put["plan"] is not None and put["plan"]["take_profit"]["return"] > 0 and put["current_return"] > 0
    sold_call = rows["AAPL261218C00130000"]["option"]
    assert sold_call["plan"] is None and "sold call" in sold_call["note"]


def test_closed_trades_are_graded_against_random_twins_and_kept_early(paths: DeskPaths) -> None:
    d1, d2, d3 = TODAY - timedelta(days=40), TODAY - timedelta(days=20), TODAY - timedelta(days=10)
    dbs = StocksDatabases.open(paths)
    try:
        account._record(dbs.research, NOW, "manual", "ok", None, fills=[
            fill("1", "AAPL", 10, 100.0, d1, "OPENING"), fill("2", "AAPL", -4, 110.0, d2, "CLOSING"),
            fill("3", "AAPL", -6, 90.0, d3, "CLOSING"),
            fill("4", "AAPL261218C00110000", 2, 3.0, d1, "OPENING", "OPTION"),
            fill("5", "AAPL261218C00110000", -2, 4.5, d2, "CLOSING", "OPTION"),
            fill("6", "T1", -5, 50.0, d2, "CLOSING"),                         # opened before the first stored fill
            fill("7", "AAPL261016P00090000", 1, 1.0, d1, "OPENING", "OPTION")])   # expired without a closing trade
        out = account_view.graded(dbs.research, dbs.market, NOW)
    finally:
        dbs.close()
    trades = out["trades"]
    assert len(trades) == 3 and not any(t["symbol"] == "T1" for t in trades)
    stock = sorted((t for t in trades if t["symbol"] == "AAPL"), key=lambda t: t["closed"])
    assert [t["quantity"] for t in stock] == [4, 6] and stock[0]["pnl"] == pytest.approx(40 - 0.65 * 4 * 0.2, abs=0.6)
    assert stock[0]["twin"]["symbol"] in {"T1", "T2", "T3"} and stock[0]["twin"]["basis"].startswith("a random")
    option = next(t for t in trades if t["asset_type"] == "OPTION")
    assert option["twin"]["symbol"] == "SPY" and option["return"] == pytest.approx(0.5, abs=0.01)
    summary = out["summary"]
    assert summary["strength"] == "early" and summary["compared"] == 3 and "Early" in summary["text"]
    assert summary["not_graded_expired"] == 1 and "not graded" in summary["expired_text"]


def test_the_view_routes_are_off_by_default_and_say_signed_out(paths: DeskPaths) -> None:
    app = create_app(paths, auto_start=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        data = client.get("/api/stocks/account").json()
        assert data["enabled"] is False and data["positions"] == [] and "read only" in data["label"]
        headers = {"X-CSRF-Token": client.get("/api/stocks/session").json()["csrf_token"]}
        assert client.post("/api/stocks/account/refresh", headers=headers).status_code == 400
        on = client.post("/api/stocks/account/settings", json={"read_account": True}, headers=headers).json()
        assert on["enabled"] is True and account.enabled(paths)
        read = client.post("/api/stocks/account/refresh", headers=headers).json()
        assert read["state"]["outcome"] == "signed_out" and "Sign in" in read["state"]["detail"]
        off = client.post("/api/stocks/account/settings", json={"read_account": False}, headers=headers).json()
        assert off["enabled"] is False
    assert reader is not None
