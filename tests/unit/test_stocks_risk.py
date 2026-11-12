"""Whole-portfolio risk: PAPER options positions and My tracked plays added up, greeks from quotes or modelled,
unknown kept unknown (never zero), betas from stored closes, modelled SPY and volatility shocks."""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks import risk
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import StocksDatabases, day_of
from tests.conftest import REPO

NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)
TODAY = date(2026, 11, 12)
EXPIRY = "2026-12-18"


def world(tmp_path: Path) -> DeskPaths:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(paths)
    rng = random.Random(3)
    try:
        days = cal.trading_days(TODAY - timedelta(days=200), TODAY - timedelta(days=1))
        spy, aaa, bbb = 500.0, 100.0, 50.0
        for d in days:
            move = rng.gauss(0, 0.01)
            spy, aaa = spy * (1 + move), aaa * (1 + 2 * move)               # AAA moves twice SPY: beta 2
            for symbol, close in (("SPY", spy), ("AAA", aaa)):
                dbs.market.execute("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) "
                                   "VALUES (?,?,?,?,?,?,1e6)", (symbol, day_of(d), close, close, close, close))
        dbs.market.execute("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) VALUES "
                           "('BBB',?,?,?,?,?,1e6)", (day_of(days[-1]), bbb, bbb, bbb, bbb))       # too little history
        dbs.market.execute("INSERT INTO stk_listings VALUES ('AAA','AAA Corp','NYSE','common',0,0,11,1,'x','x')")
        dbs.market.execute("INSERT INTO stk_companies(cik, name, sic) VALUES (11, 'AAA Corp', '7372')")
        book = PaperBook(dbs.ledger)

        def position(symbol: str, legs: list[dict[str, object]], iv: float | None) -> None:
            decision = book.decide(NOW, TODAY, "momentum_calls", "sha", symbol, "ENTER", ["x"], {})
            pid = book.open_position(account="momentum_calls", spec_sha256="sha", asset="option", symbol=symbol,
                                     structure="long_call", legs=legs, decision_id=decision, at=NOW, day=TODAY,
                                     cost=500.0, fees=1.3, max_loss=501.3, stop_price=None,
                                     expires=date.fromisoformat(EXPIRY), plan={})
            if iv is not None:
                book.mark(pid, 520.0, marks={"day": TODAY.isoformat(), "legs": {
                    str(legs[0]["occ"]): {"mark": 5.2, "iv": iv}}})

        position("AAA", [{"occ": "AAA261218C00100000", "kind": "call", "strike": float(round(aaa)), "expiration": EXPIRY,
                          "quantity": 2}], 0.40)
        position("BBB", [{"occ": "BBB261218P00050000", "kind": "put", "strike": 50.0, "expiration": EXPIRY,
                          "quantity": -1}], 0.30)
        position("CCC", [{"occ": "CCC261218C00020000", "kind": "call", "strike": 20.0, "expiration": EXPIRY,
                          "quantity": 1}], None)                                          # no price, no volatility
        play = {"symbol": "SPY", "structure": "long_put", "action": "BUY", "expiration": EXPIRY,
                "legs": [{"occ": "SPY261218P00480000", "strike": 480.0, "side": "buy", "kind": "put",
                          "expiration": EXPIRY}]}
        dbs.research.execute("INSERT INTO stk_play_ideas VALUES ('i1','2026-11-10T21:00:00+00:00',1,'c','k',?,'m')",
                             (json.dumps(play),))
        dbs.research.execute("INSERT INTO stk_play_selections VALUES ('s1','r1','i1','2026-11-10T21:00:00+00:00',"
                             "'2026-11-11T14:30:00+00:00',3)")
        dbs.research.execute("INSERT INTO stk_play_selection_events(selection_id,kind,at,detail_json) "
                             "VALUES ('s1','ENTRY','2026-11-11T15:00:00+00:00','{\"price\": 6.0}')")
        dbs.research.execute("INSERT INTO stk_track_days(occ, session, status, underlying, iv, delta, gamma, theta, "
                             "vega, note, recorded_at, source) VALUES ('SPY261218P00480000', ?, 'quoted', 500, 0.18, -0.3, "
                             "0.01, -0.12, 0.6, '', 'x', 'schwab')", (day_of(TODAY - timedelta(days=1)),))
    finally:
        dbs.close()
    return paths


def test_risk_adds_up_quoted_and_modelled_greeks_and_keeps_unknown_unknown(tmp_path: Path) -> None:
    paths = world(tmp_path)
    dbs = StocksDatabases.open(paths)
    try:
        found = risk.portfolio(dbs.ledger, dbs.research, dbs.market, NOW)
        aaa_beta = risk.beta(dbs.market, "AAA")
        spy = dbs.market.execute("SELECT close FROM stk_bars WHERE symbol='SPY' ORDER BY day DESC").fetchone()[0]
        aaa = dbs.market.execute("SELECT close FROM stk_bars WHERE symbol='AAA' ORDER BY day DESC").fetchone()[0]
    finally:
        dbs.close()
    assert aaa_beta == pytest.approx(2.0, abs=1e-3) and found["paper_positions"] == 3 and found["tracked_plays"] == 1
    by = {r["symbol"]: r for r in found["rows"]}
    t = opt.year_fraction(TODAY, date.fromisoformat(EXPIRY))
    strike = float(round(aaa))
    g = opt.greeks("call", aaa, strike, t, 0.40, risk.RATE)
    assert by["AAA"]["basis"] == "modelled" and by["AAA"]["delta_shares"] == pytest.approx(g.delta * 200, abs=0.01)
    assert by["SPY"]["basis"] == "quotes" and by["SPY"]["delta_shares"] == pytest.approx(-0.3 * 300)
    assert by["SPY"]["theta"] == pytest.approx(-0.12 * 300) and by["SPY"]["vega"] == pytest.approx(0.6 * 300)
    assert by["CCC"]["basis"] == "unknown" and by["CCC"]["delta_shares"] is None and by["CCC"]["vega"] is None
    assert by["BBB"]["beta"] is None and by["BBB"]["beta_assumed"] is True
    assert found["unknown"] == {"greeks": 1, "shock": 1}
    known = [r for r in found["rows"] if r["delta_shares"] is not None]
    assert found["totals"]["theta_per_day"] == pytest.approx(sum(r["theta"] for r in known), abs=0.05)
    per_pct = sum(r["spy_dollars_per_1pct"] for r in known)
    assert found["totals"]["spy_dollars_per_1pct"] == pytest.approx(per_pct, abs=0.05)
    assert by["AAA"]["spy_dollars_per_1pct"] == pytest.approx(g.delta * 200 * aaa * 2.0 * 0.01, rel=1e-3)
    assert found["totals"]["spy_equivalent_shares"] == pytest.approx(per_pct * 100 / spy, abs=0.1)
    down, up, vol = (s["change"] for s in found["scenarios"])
    s_down = aaa * (1 - 0.05 * aaa_beta)
    expected = (opt.bs_price("call", s_down, strike, t, 0.40, risk.RATE)
                - opt.bs_price("call", aaa, strike, t, 0.40, risk.RATE)) * 200
    assert by["AAA"]["down"] == pytest.approx(expected, abs=0.01)
    assert by["AAA"]["down"] < 0 < by["AAA"]["up"] and by["SPY"]["down"] > 0     # calls lose, puts gain on a fall
    assert down == pytest.approx(sum(r["down"] for r in known), abs=0.05) and vol > 0 and math.isfinite(up)
    assert found["largest_name"]["name"] in {"AAA", "SPY", "BBB"} and found["largest_sector"] is not None
    assert found["expiries"] == [{"week_of": "2026-12-14", "contracts": 2 + 1 + 1 + 3, "positions": 4}]
    assert found["orders_enabled"] is False


def test_the_risk_page_reads_it_and_an_empty_desk_says_so(tmp_path: Path) -> None:
    from desk.stocks.web import create_app

    paths = world(tmp_path)
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        data = client.get("/api/stocks/risk").json()
        assert data["positions"] == 4 and data["totals"]["vega_per_point"] is not None
    empty = DeskPaths(tmp_path / "empty", REPO / "config", tmp_path / "secrets.env")
    StocksDatabases.open(empty).close()
    dbs = StocksDatabases.open(empty)
    try:
        nothing = risk.portfolio(dbs.ledger, dbs.research, dbs.market, NOW)
    finally:
        dbs.close()
    assert nothing["positions"] == 0 and nothing["totals"]["delta_shares"] is None
    assert [s["change"] for s in nothing["scenarios"]] == [None, None, None]
