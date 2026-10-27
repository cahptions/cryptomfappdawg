"""Browser API confirmation, exact archived calculator context and CSRF isolation."""

import uuid

from fastapi.testclient import TestClient

from desk.core.paths import DeskPaths
from desk.stocks import play_selections, web
from desk.stocks.repo import StocksDatabases
from tests.conftest import REPO
from tests.unit.test_stocks_personal_plays import OCC, START, play


def test_personal_selection_api_is_idempotent_and_exact_contract_calculator_survives_no_chain(tmp_path, monkeypatch):
    paths = DeskPaths(tmp_path/"home", REPO/"config", tmp_path/"secrets.env")
    dbs = StocksDatabases.open(paths)
    board = {"asof":"2026-10-26", "method":"fixture", "categories":[{"id":"puts", "plays":[play()]}]}
    play_selections.register(dbs.research,board,START)
    ident = board["categories"][0]["plays"][0]["idea_id"]
    dbs.close()
    monkeypatch.setattr(web,"utc_now",lambda:START)
    with TestClient(web.create_app(paths,auto_start=False),base_url="http://127.0.0.1:8767") as client:
        prefix = "/api/stocks/"
        data = client.get(prefix+f"plays/idea/{ident}").json()
        assert data["plan"]["eligible_at"].startswith("2026-10-27T13:30")
        payload = {"idea_id":ident, "quantity":5, "request_id":str(uuid.uuid4())}
        assert client.post(prefix+"plays/select",json=payload).status_code == 403
        headers = {"X-CSRF-Token":client.get(prefix+"session").json()["csrf_token"]}
        selected = client.post(prefix+"plays/select",json=payload,headers=headers)
        assert selected.status_code == 200, selected.text
        assert client.post(prefix+"plays/select",json=payload,headers=headers).json() == selected.json()
        ticket = selected.json()["id"]
        assert client.get(prefix+"plays/mine").json()["total"] == 1
        assert client.get(prefix+f"plays/mine/{ticket}").json()["chart"] is None
        chain = client.get(prefix+f"stock/SPY/options?idea_id={ident}").json()
        assert chain["contracts"][0]["occ"] == OCC and chain["contracts"][0]["kind"] == "put"
        scenario = {"idea_id":ident, "legs":[{"occ":OCC}], "valuation_date":"2026-11-20", "underlying_price":90}
        result = client.post(prefix+"stock/SPY/calculator",json=scenario,headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["scenario_pl"] == 798.7  # correct put, frozen $2 ask, both-way fees
        assert client.post(prefix+"stock/QQQ/calculator",json=scenario,headers=headers).status_code == 400
        cancelled = client.post(prefix+f"plays/mine/{ticket}/action",json={"action":"cancel"},headers=headers)
        assert cancelled.json()["state"] == "cancelled"
        assert client.get(prefix+"plays/history").status_code == 200
