"""GUI actions stay bounded and do not turn a research check into a trading/model-change action."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from desk.core.errors import DeskError
from desk.stocks.evidence_job import EvidenceWorker, document, evaluate
from desk.stocks.explore import option_setups
from desk.stocks.options_view import build
from desk.stocks.web import create_app
from tests.stocks_world import make_world
from tests.unit.test_stocks_options_population import CAPTURE, StandardSchwab


async def test_contract_shortlist_uses_quotes_costs_and_bounded_liquidity_screen(tmp_path):
    from desk.stocks.collect import option_chains

    w = make_world(tmp_path, n=12)
    try:
        ledger = list(w.dbs.ledger.iterdump())
        await option_chains(SimpleNamespace(tradier=StandardSchwab(), option_source="synthetic"), w.market,
                            CAPTURE, ["SPY", "QQQ"], rate=.04, last_prices={}, clock=lambda: CAPTURE)
        result = option_setups(w.dbs.market, CAPTURE)
        assert len(result["rows"]) == 4
        for row in result["rows"]:
            assert row["entry_cost"] == pytest.approx(420.65)
            assert row["max_premium_loss"] == row["entry_cost"]
            assert row["strike"] == 100 and row["spread_fraction"] <= .15
            assert row["breakeven"] == pytest.approx(104.2065 if row["kind"] == "call" else 95.7935)
        assert "not expected profit" in result["basis"]
        assert list(w.dbs.ledger.iterdump()) == ledger
    finally:
        w.close()


def test_evaluation_publishes_readable_report_without_adopting_weights_or_changing_paper(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        # A short genuine-shaped history can produce an honest early report; no made-up forward outcomes.
        cut = w.days[-100].toordinal()
        w.dbs.market.execute("DELETE FROM stk_bars WHERE day<?", (cut,))
        w.dbs.market.execute("DELETE FROM stk_bar_days WHERE day<?", (cut,))
        ledger = list(w.dbs.ledger.iterdump())
        previous_validation = w.research.latest_validation()
        evaluate(w.paths)
        state, report = document(w.paths, "state"), document(w.paths, "report")
        assert state["status"] == "completed" and report["advisory_only"]
        assert report["rebuild"]["months"] == 0 and report["live"]["days"] == 0
        assert w.research.latest_validation() == previous_validation
        assert list(w.dbs.ledger.iterdump()) == ledger
    finally:
        w.close()


async def test_no_positive_daily_volume_blocks_activity_not_quote_based_research(tmp_path):
    from desk.stocks.collect import option_chains

    class ZeroVolume(StandardSchwab):
        async def chain(self, symbol, expiration):
            return [replace(q, volume=0) for q in await super().chain(symbol, expiration)]

    w = make_world(tmp_path, n=12)
    try:
        await option_chains(SimpleNamespace(tradier=ZeroVolume(), option_source="synthetic"), w.market,
                            CAPTURE, ["SPY"], rate=.04, last_prices={}, clock=lambda: CAPTURE)
        view = build(w.market, CAPTURE)
        assert view["coverage"]["quality_pass"] == 0 and view["coverage"]["quote_quality_pass"] == 1
        assert not view["rows"]["options_activity"] and not view["rows"]["unusual_options"]
        assert view["rows"]["big_moves"]
        setups = option_setups(w.dbs.market, CAPTURE)
        assert len(setups["rows"]) == 2 and all(row["volume"] == 0 for row in setups["rows"])
    finally:
        w.close()


def test_evidence_worker_fixed_command_duplicate_guard_and_exact_child_cancellation(tmp_path, monkeypatch):
    w = make_world(tmp_path, n=12)
    child = Mock()
    child.poll.return_value = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr("desk.stocks.evidence_job.subprocess.Popen", spawn)
    try:
        worker = EvidenceWorker(w.paths)
        assert worker.start()["running"]
        argv = spawn.call_args.args[0]
        assert argv[1:] == ["-m", "desk.stocks.evidence_job"]
        assert spawn.call_args.kwargs["env"]["OPENBLAS_NUM_THREADS"] == "1"
        with pytest.raises(DeskError, match="already running"):
            worker.start()
        worker.cancel()
        child.terminate.assert_called_once()
        child.poll.return_value = -15
        assert worker.status()["status"] == "cancelled"
    finally:
        w.close()


def test_gui_evaluation_and_connection_test_require_valid_session(tmp_path, monkeypatch):
    w = make_world(tmp_path, n=12)
    start = Mock(return_value={"running": True, "owned": True, "phase": "Testing"})
    monkeypatch.setattr(EvidenceWorker, "start", start)
    try:
        with TestClient(create_app(w.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
            for path in ("proof/start", "proof/cancel", "connection/test", "stock/SPY/history/request", "stock/SPY/calculator"):
                assert client.post("/api/stocks/" + path, json={}).status_code == 403
            assert not start.called
            headers = {"X-CSRF-Token": client.get("/api/stocks/session").json()["csrf_token"]}
            assert client.post("/api/stocks/proof/start", json={}, headers=headers).json()["running"]
            assert client.get("/api/stocks/proof/health").json()["read_only"]
            assert client.get("/api/stocks/explore").status_code == 200
            # GET remains read-only. A CSRF-protected POST registers demand, without touching complete bulk days.
            days = w.market.bar_days()
            client.get("/api/stocks/stock/SPY")
            assert w.dbs.market.execute("SELECT COUNT(*) FROM stk_stock_views").fetchone()[0] == 0
            assert client.post("/api/stocks/stock/SPY/history/request", json={}, headers=headers).json()["queued"]
            assert w.market.bar_days() == days
            assert client.post("/api/stocks/stock/INVALID/history/request", json={}, headers=headers).status_code == 404
    finally:
        w.close()
