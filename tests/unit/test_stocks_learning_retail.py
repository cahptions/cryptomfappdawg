"""Finite transfer accounts use prior research, their own capital, real later quotes and independent results."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from desk.core.clock import to_iso
from desk.stocks import learning as lab
from desk.stocks import learning_retail as retail
from desk.stocks.contracts import OptionQuote
from desk.stocks.paper.book import PaperBook
from desk.stocks.web import create_app
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT, EXP, chain


@pytest.fixture
def world(tmp_path):
    w=make_world(tmp_path,n=12)
    conn=lab.open_book(w.paths)
    lab.initialize(conn,AT-timedelta(days=30))
    yield w,conn
    conn.close()
    w.close()


def outcome(conn,structure,net,entered,*,account=lab.RESEARCH):
    cfg=conn.execute("SELECT * FROM stk_learning_experiments WHERE json_extract(config_json,'$.structure')=? "
        "AND json_extract(config_json,'$.hold_minutes')=30 AND json_extract(config_json,'$.repeat_minutes')=30 LIMIT 1",
        (structure,)).fetchone()
    book=PaperBook(conn)
    did=book.decide(entered,entered.date(),account,cfg["spec_sha256"],"SPY","ENTER",["Synthetic prior research"],{})
    pid=book.open_position(account=account,spec_sha256=cfg["spec_sha256"],asset="option",symbol="SPY",structure=structure,
        legs=[],decision_id=did,at=entered,day=entered.date(),cost=100.,fees=0.,max_loss=100.,stop_price=None,expires=EXP,plan={})
    position=next(p for p in book.positions(account) if p.id==pid)
    mature=entered+timedelta(minutes=30)
    book.close_position(position,at=mature,day=mature.date(),credit=100*(1+net),fees=0.,reason="Synthetic maturity",evidence={})
    conn.execute("INSERT INTO stk_learning_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
        (pid,cfg["id"],to_iso(entered),to_iso(mature),entered.date().isoformat()+":SPY",net,100*net,1,1))


def small_accounts(conn):
    return {a["id"]:a for a in lab.report(conn)["accounts"] if a["small_account"]}


def test_restart_preserves_finite_balances_losses_and_one_time_funding(world):
    w,conn=world
    forward=list(w.dbs.ledger.iterdump())
    book=PaperBook(conn)
    assert [a["starting_capital"] for a in small_accounts(conn).values()]==[1000,5000]
    book.add_cash(lab.SMALL_ACCOUNTS[0],AT,AT.date(),-100_000_000,"FEE")
    lab.initialize(conn,AT)
    assert not lab.fund(book,lab.SMALL_ACCOUNTS[0],100_000,AT)
    one=small_accounts(conn)[lab.SMALL_ACCOUNTS[0]]
    assert one["cash"]==900 and one["trading_pnl"]==-100 and one["return_on_start"]==-.1
    assert one["contributed"]==1000 and one["additional_funding"]==0 and not one["funding_allowed"]
    assert list(w.dbs.ledger.iterdump())==forward


def test_small_accounts_fill_later_and_are_not_starved_by_a_large_research_queue(world):
    w,conn=world
    for i in range(130):
        lab.add_experiment(conn,AT,"long_call",100_000+i,40+i)
    chain(w,AT)
    lab.tick(conn,w.market,AT)
    orders=conn.execute("SELECT id FROM stk_pending WHERE account IN (?,?)",lab.SMALL_ACCOUNTS).fetchall()
    assert len(orders)==2 and min(r[0] for r in orders)>200
    assert not PaperBook(conn).positions(lab.SMALL_ACCOUNTS[0])
    fresh=AT+timedelta(minutes=1)
    chain(w,fresh)
    lab.tick(conn,w.market,fresh)
    for a in small_accounts(conn).values():
        assert a["open_positions"]>0 and a["trading_pnl"]<0 and a["cash"]>=0
        assert a["additional_funding"]==0
    p=PaperBook(conn).positions(lab.SMALL_ACCOUNTS[0])[0]
    assert p.plan["transfer_policy"]["mode"]=="starter"
    assert p.plan["transfer_policy"]["expected_net_return"] is None


def test_affordability_can_choose_different_contracts_for_the_two_balances(world):
    w,conn=world
    quotes=[OptionQuote(f"SPY261120C{strike*1000:08d}","SPY","call",strike,EXP,bid,ask,(bid+ask)/2,
                        50_000,2_000_000,None,AT) for strike,bid,ask in [(100,4.8,5.),(105,.95,1.05)]]
    w.market.save_option_chain("SPY",AT.date(),EXP,AT,100,quotes,source="synthetic_retail")
    lab.tick(conn,w.market,AT)
    plans={r["account"]:json.loads(r["order_json"]) for r in conn.execute(
        "SELECT account,order_json FROM stk_pending WHERE account IN (?,?)",lab.SMALL_ACCOUNTS)}
    assert plans[lab.SMALL_ACCOUNTS[0]]["legs"][0]["strike"]==105
    assert plans[lab.SMALL_ACCOUNTS[1]]["legs"][0]["strike"]==100
    assert all(p["desired_units"]>=1 and isinstance(p["desired_units"],int) for p in plans.values())


def test_policy_only_uses_prior_uncapped_outcomes_and_does_not_count_repeated_fills_as_independent(world):
    _w,conn=world
    for _ in range(12):
        outcome(conn,"long_put",.2,AT-timedelta(days=2))
    outcome(conn,"long_call",10.,AT+timedelta(days=1))
    outcome(conn,"long_call",5.,AT-timedelta(days=2),account=lab.SMALL_ACCOUNTS[1])
    choices=retail.policies(conn,AT)
    assert len(choices)==1 and choices[0]["config"]["structure"]=="long_put"
    evidence=choices[0]["evidence"]
    assert evidence["blocks"]==1 and evidence["days"]==1 and evidence["expected_net_return"]==pytest.approx(.2/11)
    assert evidence["mode"]=="provisional" and evidence["matured_through"]<to_iso(AT)
    assert retail.policies(conn,AT-timedelta(days=5))[0]["evidence"]["mode"]=="starter"


def test_unfavorable_research_keeps_small_accounts_in_cash_while_research_continues(world):
    w,conn=world
    for structure in lab.STRUCTURES:
        outcome(conn,structure,-.2,AT-timedelta(days=2))
    chain(w,AT)
    result=lab.tick(conn,w.market,AT)
    assert result["orders"]>0  # uncapped experiments still run
    for a in small_accounts(conn).values():
        assert a["pending_orders"]==0 and a["cash"]==a["starting_capital"]
        assert a["policy"]["mode"]=="waiting" and "holding cash" in a["policy"]["phase"]


def test_positive_research_selects_puts_and_small_trades_get_their_own_realized_results(world):
    w,conn=world
    outcome(conn,"long_call",-.1,AT-timedelta(days=2))
    outcome(conn,"long_put",.2,AT-timedelta(days=2))
    chain(w,AT)
    lab.tick(conn,w.market,AT)
    plans=[json.loads(r[0]) for r in conn.execute("SELECT order_json FROM stk_pending WHERE account IN (?,?)",lab.SMALL_ACCOUNTS)]
    assert len(plans)==2 and all(p["structure"]=="long_put" for p in plans)
    assert all(p["forecast"]["blocks"]==1 for p in plans)
    entered=AT+timedelta(minutes=1)
    chain(w,entered)
    lab.tick(conn,w.market,entered)
    end=entered+timedelta(minutes=31)
    chain(w,end,bid=6.,ask=6.2)
    lab.tick(conn,w.market,end)
    accounts=small_accounts(conn)
    assert all(a["closed_trades"]>0 and a["realized_pnl"]>0 and a["additional_funding"]==0 for a in accounts.values())
    assert accounts[lab.SMALL_ACCOUNTS[0]]["realized_pnl"]!=accounts[lab.SMALL_ACCOUNTS[1]]["realized_pnl"]
    assert conn.execute("SELECT COUNT(*) FROM stk_learning_outcomes o JOIN stk_positions p ON p.id=o.position_id "
                        "WHERE p.account IN (?,?)",lab.SMALL_ACCOUNTS).fetchone()[0]==0


def test_price_jump_cancels_unaffordable_entry_and_resizes_other_account_without_overdraft(world):
    w,conn=world
    chain(w,AT,bid=1.4,ask=1.5)
    lab.tick(conn,w.market,AT)
    later=AT+timedelta(minutes=1)
    chain(w,later,bid=11.9,ask=12.)
    lab.tick(conn,w.market,later)
    one=small_accounts(conn)[lab.SMALL_ACCOUNTS[0]]
    assert one["open_positions"]==0 and one["cash"]==1000
    for a in small_accounts(conn).values():
        assert a["cash"]>=0 and a["additional_funding"]==0
    assert conn.execute("SELECT state FROM stk_pending WHERE account=? ORDER BY id LIMIT 1",
                        (lab.SMALL_ACCOUNTS[0],)).fetchone()[0]=="CANCELLED"


def test_small_account_routes_are_read_only_and_include_separate_orders_and_positions(world):
    w,conn=world
    chain(w,AT)
    lab.tick(conn,w.market,AT)
    with TestClient(create_app(w.paths,auto_start=False),base_url="http://127.0.0.1:8767") as client:
        before=list(conn.iterdump())
        for account in lab.SMALL_ACCOUNTS:
            assert client.get(f"/api/stocks/learning/trades?account={account}").status_code==200
            data=client.get(f"/api/stocks/learning/orders?account={account}").json()
            assert data["total"]==1 and all(r["account"]==account for r in data["orders"])
        assert list(conn.iterdump())==before
        assert len(client.get("/api/stocks/learning").json()["accounts"])==4
