"""Historical chart context must be dated, bounded and read-only; funding cannot become a profit."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from desk.core.clock import to_iso
from desk.stocks import learning, learning_chart
from desk.stocks.paper.book import MICRO, PaperBook
from desk.stocks.web import create_app
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT, chain


def test_learning_chart_uses_dated_cash_positions_fees_and_keeps_accounts_separate(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        chain(world, AT)
        learning.tick(conn, world.market, AT)
        later = AT + timedelta(minutes=1)
        chain(world, later)
        learning.tick(conn, world.market, later)
        closed = later + timedelta(minutes=121)
        chain(world, closed, bid=5.8, ask=6)
        learning.tick(conn, world.market, closed)
        before = list(conn.iterdump())
        result = learning_chart.history(conn, learning.RESEARCH)
        points = result["points"]
        first, last = points[0], points[-1]
        assert first["cash"] == first["contributed"] == 500_000 and first["pnl"] == 0
        assert first["open_positions"] == first["closed_trades"] == first["fees"] == 0
        assert points[1]["open_positions"] > 0 and points[1]["fees"] > 0
        assert last["closed_trades"] > 0 and last["open_positions"] == 0
        assert last["unrealized_pnl"] == 0, "A fully closed account has no unrealized P/L"
        assert last["cash"] == pytest.approx(PaperBook(conn).cash(learning.RESEARCH))
        assert last["pnl"] == pytest.approx(last["equity"] - last["contributed"])
        assert last["realized_pnl"] + last["unrealized_pnl"] == pytest.approx(last["pnl"])
        assert {e["kind"] for e in result["events"]} == {"entry", "exit", "funding"}
        assert sum(e["amount"] for e in result["events"] if e["kind"] == "funding") == last["contributed"]
        assert all(e["at"] <= result["available_last"] for e in result["events"])
        assert len(learning_chart.history(conn, "reference_1k")["points"]) <= len(points)
        assert before == list(conn.iterdump()), "Chart reads must never submit or mark trades"
    finally:
        conn.close()
        world.close()


def test_retained_history_is_sampled_from_real_rows_preserving_extrema_and_prior_drawdown(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        learning.initialize(conn, AT)
        rows = []
        for i in range(4800):
            pnl = 10000 if i == 111 else -1234 if i == 3333 else i / 100
            rows.append((to_iso(AT + timedelta(minutes=i)), learning.RESEARCH, 500000 + pnl, 500000, pnl))
        conn.executemany("INSERT INTO stk_learning_nav(at,account,equity,contributed,pnl) VALUES (?,?,?,?,?)", rows)
        result = learning_chart.history(conn, learning.RESEARCH)
        assert result["observations"] == result["available_observations"] == 4800
        assert result["sampled"] and len(result["points"]) <= learning_chart.MAX_POINTS
        assert result["points"][0]["at"] == rows[0][0] and result["points"][-1]["at"] == rows[-1][0]
        expected = {r[0]: r[4] for r in rows}
        assert all(expected[p["at"]] == p["pnl"] for p in result["points"])
        assert max(p["pnl"] for p in result["points"]) == 10000
        assert min(p["pnl"] for p in result["points"]) == -1234
        today = learning_chart.history(conn, learning.RESEARCH, "1D")
        assert today["observations"] < result["observations"]
        assert today["points"][-1]["drawdown"] == pytest.approx(rows[-1][4] - 10000)
        assert all(p["cash"] == 500000 for p in result["points"])
        # Funding after the last mark must not leak into historical tooltips.
        PaperBook(conn).add_cash(learning.RESEARCH, AT + timedelta(days=20), AT.date(), MICRO * 1000, "DEPOSIT")
        assert learning_chart.history(conn, learning.RESEARCH)["points"][-1]["cash"] == 500000
    finally:
        conn.close()
        world.close()


def test_learning_chart_endpoint_empty_validation_account_isolation_and_readonly(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        with TestClient(create_app(world.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
            empty = client.get("/api/stocks/learning/chart").json()
            assert empty["points"] == [] and empty["available_observations"] == 0
            assert client.get("/api/stocks/learning/chart?window=invalid").status_code == 422
            assert client.get("/api/stocks/learning/chart?account=not_an_account").status_code == 422
            assert client.get("/api/stocks/learning/chart?account=reference_1k&window=1D").json()["account"] == "reference_1k"
    finally:
        world.close()
