"""Paper GUI read APIs expose real paged trades and reasons without changing the ledger."""

from datetime import timedelta

from fastapi.testclient import TestClient

from desk.stocks.paper.book import PaperBook
from desk.stocks.web import create_app
from tests.stocks_world import NOW, make_world


def test_trade_pages_are_account_scoped_read_only_and_show_the_original_entry_reason(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        book = PaperBook(world.dbs.ledger)
        book.open_account("gui_test", 10000, NOW, NOW.date())
        for index in range(12):
            did = book.decide(NOW-timedelta(days=1), NOW.date()-timedelta(days=1), "gui_test", "a"*64,
                              f"S{index:02}", "ENTER", [f"Original reason {index}"], {})
            book.open_position(account="gui_test", spec_sha256="a"*64, asset="stock", symbol=f"S{index:02}",
                structure="long_stock", legs=[{"kind":"stock", "quantity":10, "entry_price":10}], decision_id=did,
                at=NOW, day=NOW.date(), cost=100, fees=.1, max_loss=None, stop_price=8, expires=None, plan={})
        for position in book.positions("gui_test")[:4]:
            book.close_position(position, at=NOW+timedelta(days=1), day=NOW.date()+timedelta(days=1),
                                credit=110, fees=.1, reason="Synthetic exit", evidence={})
        for day in world.days[-405:]:
            book.snapshot(day, "gui_test", NOW, 10000, 0)
        before = list(world.dbs.ledger.iterdump())
        with TestClient(create_app(world.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
            def page(offset):
                return client.get(f"/api/stocks/paper/trades?account=gui_test&state=OPEN&limit=3&offset={offset}").json()
            first, second = page(0), page(3)
            assert first["total"] == second["total"] == 8
            assert len(first["trades"]) == len(second["trades"]) == 3
            assert not {p["id"] for p in first["trades"]} & {p["id"] for p in second["trades"]}
            for position in first["trades"]:
                assert position["entry_reason"] == f"Original reason {int(position['symbol'][1:])}"
                assert position["unrealized_pnl"] == -.1
            closed = client.get("/api/stocks/paper/trades?account=gui_test&state=CLOSED").json()
            assert closed["total"] == 4 and all(p["pnl"] > 0 for p in closed["trades"])
            assert client.get("/api/stocks/paper/trades?account=another_account").json()["total"] == 0
            assert client.get("/api/stocks/paper/trades?account=gui_test&limit=9999").status_code == 422
            assert len(client.get("/api/stocks/paper/equity?account=gui_test").json()["curve"]) == 405
            assert not client.get("/api/stocks/paper/equity?account=another_account").json()["curve"]
            csrf = client.get("/api/stocks/session").json()["csrf_token"]
            assert client.post("/api/stocks/backtest/automatic", json={"enabled":False,"sessions":63},
                               headers={"X-CSRF-Token":csrf}).json()["auto_enabled"] is False
            assert client.get("/api/stocks/backtest").json()["job"]["requested_sessions"] == 63
        assert list(world.dbs.ledger.iterdump()) == before
    finally:
        world.close()
