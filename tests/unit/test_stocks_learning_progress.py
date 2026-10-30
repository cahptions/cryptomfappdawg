"""Collected outcomes, formal validation and live collection are separate stages, not a misleading zero counter."""

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from desk.core.clock import to_iso
from desk.core.config import load_config
from desk.stocks import learning as lab
from desk.stocks import learning_service
from desk.stocks.paper.book import PaperBook
from desk.stocks.web import create_app
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT, EXP, chain


@pytest.fixture
def world(tmp_path):
    w = make_world(tmp_path,n=12)
    conn = lab.open_book(w.paths)
    lab.initialize(conn,AT-timedelta(days=20))
    yield w,conn
    conn.close()
    w.close()


def finish(conn, entered, *, symbol="SPY", net=-.1, account=lab.RESEARCH, matured=None, record=True):
    cfg = conn.execute("SELECT id,spec_sha256 FROM stk_learning_experiments "
        "WHERE json_extract(config_json,'$.structure')='long_call' AND json_extract(config_json,'$.hold_minutes')=30 "
        "AND json_extract(config_json,'$.repeat_minutes')=30 LIMIT 1").fetchone()
    book = PaperBook(conn)
    did = book.decide(entered,entered.date(),account,cfg["spec_sha256"],symbol,"ENTER",["Synthetic progress fixture"],{})
    pid = book.open_position(account=account,spec_sha256=cfg["spec_sha256"],asset="option",symbol=symbol,
        structure="long_call",legs=[],decision_id=did,at=entered,day=entered.date(),cost=100.,fees=0.,max_loss=100.,
        stop_price=None,expires=EXP,plan={"experiment_id":cfg["id"],"hold_minutes":30})
    position = next(p for p in book.positions(account) if p.id==pid)
    end = matured or entered+timedelta(minutes=30)
    book.close_position(position,at=end,day=end.date(),credit=100*(1+net),fees=0.,reason="Synthetic progress fixture",evidence={})
    if record:
        conn.execute("INSERT INTO stk_learning_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
            (pid,cfg["id"],to_iso(entered),to_iso(end),entered.date().isoformat()+":"+symbol,net,net*100,1,1))
    return cfg["id"]


def test_52_closed_trades_show_52_outcomes_while_formal_model_is_still_zero(world):
    _w,conn = world
    for _ in range(52):
        experiment = finish(conn,AT)
    now = AT+timedelta(hours=2)
    lab.check_in(conn,now)
    before = list(conn.iterdump())
    data = lab.report(conn,now)
    evidence = data["evidence"]
    assert data["accounts"][0]["closed_trades"]==52 and data["model"]["version"]==0
    assert evidence["matured_outcomes"]==52 and evidence["closed_research_trades"]==52
    assert evidence["underlying_day_blocks"]==1 and evidence["entry_days"]==1
    assert evidence["min_entry_days"]==4 and evidence["min_blocks"]==12
    assert not evidence["ready_for_comparison"] and evidence["closed_without_outcome"]==0
    assert len(evidence["blockers"])==2
    assert evidence["families"][0]["outcomes"]==52 and evidence["families"][0]["blocks"]==1
    assert evidence["families"][0]["mean_net_return"]==pytest.approx(-.1)
    assert lab.forecast(conn,experiment,now)["expected_net_return"] < 0
    assert data["check_ins"][0]["report"]["matured_outcomes"]==52
    assert list(conn.iterdump())==before


def test_reference_and_future_results_do_not_inflate_research_evidence_or_its_forecast(world):
    _w,conn = world
    experiment = finish(conn,AT,net=.1)
    for account in (lab.REFERENCE,*lab.SMALL_ACCOUNTS):
        finish(conn,AT,net=10.,account=account)
    finish(conn,AT+timedelta(days=1),net=100.)
    now = AT+timedelta(hours=2)
    evidence = lab.evidence_progress(conn,now)
    assert evidence["matured_outcomes"]==1 and evidence["closed_research_trades"]==1
    assert evidence["entry_days"]==1 and evidence["potential_test_samples"]==0
    assert lab.forecast(conn,experiment,now)["expected_net_return"]==pytest.approx(.1/11)
    lab.check_in(conn,now)
    assert lab.report(conn,now)["check_ins"][0]["report"]["week_outcomes"]==1


def test_missing_outcome_is_explained_without_inventing_a_label_or_writing_on_get(world):
    w,conn = world
    finish(conn,AT,record=False)
    now = AT+timedelta(hours=2)
    evidence = lab.evidence_progress(conn,now)
    assert evidence["closed_research_trades"]==1 and evidence["matured_outcomes"]==0
    assert evidence["closed_without_outcome"]==1 and any("lack an outcome record" in b for b in evidence["blockers"])
    with TestClient(create_app(w.paths,auto_start=False),base_url="http://127.0.0.1:8767") as client:
        before = list(conn.iterdump())
        response = client.get("/api/stocks/learning").json()
        assert response["evidence"]["closed_without_outcome"]==1
        assert list(conn.iterdump())==before


def test_thresholds_and_actual_chronological_split_match_the_grader_after_hours(world):
    w,conn = world
    for day in (0,1,2,5):
        for symbol in ("SPY","QQQ","AAPL"):
            finish(conn,AT+timedelta(days=day),symbol=symbol,net=.1)
    now = AT+timedelta(days=6,hours=8)  # closed market, all outcomes already stored
    evidence = lab.evidence_progress(conn,now)
    assert evidence["entry_days"]==4 and evidence["underlying_day_blocks"]==12 and evidence["ready_for_comparison"]
    before = conn.execute("SELECT COUNT(*) FROM stk_pending").fetchone()[0]
    lab.tick(conn,w.market,now)
    model = lab.state(conn,"model")
    assert model["version"]==1 and model["training_outcomes"]==6 and model["test_outcomes"]==6
    assert model["test_blocks"]==6 and model["promoted"] is False
    assert conn.execute("SELECT COUNT(*) FROM stk_pending").fetchone()[0]==before
    assert conn.execute("SELECT COUNT(*) FROM stk_learning_outcomes").fetchone()[0]==12


def test_matured_labels_that_overlap_later_entries_explain_the_embargo_instead_of_claiming_ready(world):
    _w,conn = world
    for day in (0,1,2,5):
        for symbol in ("SPY","QQQ","AAPL"):
            finish(conn,AT+timedelta(days=day),symbol=symbol,matured=AT+timedelta(days=7))
    evidence = lab.evidence_progress(conn,AT+timedelta(days=8))
    assert evidence["matured_outcomes"]==12 and evidence["entry_days"]==4
    assert not evidence["ready_for_comparison"] and evidence["potential_training_samples"]==0
    assert any("finish before held-out entries" in b for b in evidence["blockers"])


def test_old_check_in_is_refreshed_once_after_upgrade_even_without_new_outcomes(world):
    w,conn = world
    finish(conn,AT)
    now = AT+timedelta(hours=8)
    key = "check:"+now.date().isoformat()
    lab.set_state(conn,key,1)
    lab.set_state(conn,key+":exists",True)
    lab.tick(conn,w.market,now)
    assert lab.state(conn,"check_method")==lab.CHECK_METHOD_VERSION
    count = conn.execute("SELECT COUNT(*) FROM stk_learning_checks").fetchone()[0]
    assert count==1
    lab.tick(conn,w.market,now+timedelta(minutes=1))
    assert conn.execute("SELECT COUNT(*) FROM stk_learning_checks").fetchone()[0]==count
    payload = json.loads(conn.execute("SELECT payload_json FROM stk_learning_checks").fetchone()[0])
    assert payload["evidence"]["matured_outcomes"]==1


async def test_saved_learning_is_processed_before_a_provider_call_that_times_out(world, monkeypatch):
    w,conn = world
    chain(w,AT)
    monkeypatch.setattr(learning_service,"utc_now",lambda:AT)
    started = asyncio.Event()
    async def stuck_collection(*_args):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr(learning_service,"collect",stuck_collection)
    before_forward = list(w.dbs.ledger.iterdump())
    task = asyncio.create_task(learning_service.cycle(w.paths,load_config(w.paths.config_dir)))
    try:
        await asyncio.wait_for(started.wait(),2)
        assert conn.execute("SELECT COUNT(*) FROM stk_pending").fetchone()[0]>0
        assert lab.state(conn,"progress")["orders"]>0
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_checks").fetchone()[0]==1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert list(w.dbs.ledger.iterdump())==before_forward
    learning_service.control(w.paths,False)
    before = list(conn.iterdump())
    await learning_service.cycle(w.paths,load_config(w.paths.config_dir))
    assert list(conn.iterdump())==before
