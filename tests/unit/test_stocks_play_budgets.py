"""Budget search precedes per-stock selection, without cheapening quality or rewriting closing cohorts."""

import json
import sqlite3
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from desk.stocks import odds, options
from desk.stocks import plays as pl
from desk.stocks.contracts import OptionQuote
from desk.stocks.play_selections import register
from desk.stocks.play_tracking import first_seen, tracked_set
from desk.stocks.universe import option_focus
from tests.stocks_world import make_world, occ

AT = datetime(2026, 10, 28, 15, tzinfo=UTC)


def context():
    return pl.Context(AT.date(),AT,.04,None,0,None,{"itm":odds.Calibration(),"move":odds.Calibration()},[])


def chain_table(spot=1000., symbol="TEST", vol=.1):
    chains = {}
    for days in (7,30,60):
        expiry = AT.date()+timedelta(days=days)
        quotes = []
        for kind in ("call","put"):
            for multiple in (.95,.96,.97,.98,.99,1.,1.005,1.01,1.015,1.02,1.03,1.04,1.05):
                strike = round(spot*multiple,3)
                mid = options.bs_price(kind,spot,strike,days/365,vol,.04)
                if mid<.08:
                    continue
                quotes.append(OptionQuote(occ(symbol,expiry,kind,strike),symbol,kind,strike,expiry,
                    mid*.98,mid*1.02,mid,1000,5000,None,AT))
        chains[expiry] = quotes
    u = pl.Underlying(symbol,spot,AT.isoformat(),"synthetic_budget_fixture",chains,
        detail={"committee":{"score":65,"label":"Bullish lean"},
                "desks":{"momentum":{"score":80}},"price":{"above_sma200":.1}},
        committee=65,rv=vol,iv30=vol)
    tb = pl.table(u,context())
    assert tb is not None
    return tb


def test_lower_cost_contract_is_found_before_one_per_stock_cut():
    tb = chain_table()
    top = pl.finish(pl.likely_itm(tb,context())[0],context())
    assert top["cost"]>500
    affordable = pl.budget_plays("likely_itm",[tb],context(),None)
    low = [p for p in affordable if p["cost"]<=500]
    assert low and all(p["legs"][0]["occ"]!=top["legs"][0]["occ"] for p in low)
    assert all(.25<=abs(p["delta"])<=.55 and p["spread_pct"]<=.15 and p["open_interest"]>=100 for p in low)
    assert pl.finish(pl.likely_itm(tb,context())[0],context())["legs"]==top["legs"]


def test_bands_include_fees_and_have_independent_coverage_not_only_global_top_twelve():
    tables = [chain_table(100.,f"S{i}") for i in range(15)]
    candidates = pl.budget_plays("likely_itm",tables,context(),None)
    counts = Counter(tuple(p["budget_band"]) for p in candidates)
    assert counts[(0.,100.)]==12 and counts[(100.,250.)]==12
    for p in candidates:
        low,high=p["budget_band"]
        assert low<p["cost"]<=high
        assert p["cost"]==pytest.approx(p["legs"][0]["ask"]*100+.65,abs=.01)
    tb=tables[0]
    row=next(i for i in range(len(tb)) if 7<=tb.dte[i]<=60 and .25<=tb.delta[i]<=.55)
    eligible=np.zeros(len(tb),dtype=bool)
    eligible[row]=True
    tb=replace(tb,oi=np.where(eligible,5000,0),ask=np.where(eligible,1.,tb.ask))
    assert pl.likely_itm(tb,replace(context(),budget_range=(0.,100.)))==[]  # $100 premium + fee is over $100


def test_low_budget_does_not_turn_a_trend_pick_into_a_deep_otm_lotto():
    tb=chain_table()
    tb=replace(tb,ask=np.where(np.abs(tb.delta)<.10,.2,tb.ask),
               bid=np.where(np.abs(tb.delta)<.10,.195,tb.bid))
    affordable=pl.budget_plays("trend_calls",[tb],context(),None)
    assert all(abs(p["delta"])>=.35 for p in affordable)


def test_budget_straddle_uses_both_leg_costs_and_can_choose_another_atm_expiry():
    tb=chain_table(40.)
    tb.u.iv_rank=10.
    candidates=pl.budget_plays("cheap_vol",[tb],context(),None)
    assert candidates and any(p["cost"]<=100 for p in candidates)
    for p in candidates:
        assert len(p["legs"])==2
        assert p["legs"][0]["strike"]==p["legs"][1]["strike"]==40
        assert p["cost"]==pytest.approx(sum(leg["ask"]*100+.65 for leg in p["legs"]),abs=.01)
        assert p["budget_band"][0]<p["cost"]<=p["budget_band"][1]


def test_selling_budget_means_maximum_loss_not_the_small_credit_received():
    tb=chain_table(1000.)
    tb.u.iv_rank=80.
    picks=pl.budget_plays("sell_premium",[tb],context(),None)
    assert picks
    for p in picks:
        assert p["action"]=="SELL" and p["cost"] is None
        width=abs(p["legs"][0]["strike"]-p["legs"][1]["strike"])*100
        assert p["max_loss"]==pytest.approx(width-p["credit"],abs=.02)
        assert p["budget_band"][0]<p["max_loss"]<=p["budget_band"][1]


def test_debit_spread_budget_is_the_debit_paid_and_the_gain_is_capped_at_the_width():
    tb=chain_table(100.)                                         # committee 65: bull call spreads
    picks=pl.budget_plays("debit_spreads",[tb],context(),None)
    assert picks
    for p in picks:
        bought,sold=p["legs"]
        assert p["structure"]=="bull_call_debit" and (bought["side"],sold["side"])==("buy","sell")
        assert sold["strike"]>bought["strike"] and bought["expiration"]==sold["expiration"]
        debit=bought["ask"]-sold["bid"]
        assert p["cost"]==pytest.approx(debit*100+1.30,abs=.01)==p["max_loss"]
        assert p["max_gain"]==pytest.approx((sold["strike"]-bought["strike"]-debit)*100-1.30,abs=.01)
        assert p["budget_band"][0]<p["cost"]<=p["budget_band"][1]


def _legs(*spec):
    expiry=AT.date()+timedelta(days=30)
    return [{"occ":occ("TEST",expiry,kind,strike),"kind":kind,"side":side,"strike":strike,
             "expiration":expiry.isoformat()} for kind,strike,side in spec]


@pytest.mark.parametrize("spec,structure,width",[
    ((("call",100.,"buy"),),"long_call",0.),
    ((("put",95.,"sell"),),"cash_secured_put",95.),
    ((("call",100.,"buy"),("put",100.,"buy")),"long_straddle",0.),
    ((("call",105.,"buy"),("put",95.,"buy")),"long_strangle",0.),
    ((("call",100.,"buy"),("call",105.,"sell")),"bull_call_debit",5.),
    ((("put",100.,"buy"),("put",95.,"sell")),"bear_put_debit",5.),
    ((("put",100.,"sell"),("put",95.,"buy")),"bull_put_credit",5.),
    ((("call",100.,"sell"),("call",105.,"buy")),"bear_call_credit",5.),
    ((("put",95.,"sell"),("put",90.,"buy"),("call",105.,"sell"),("call",112.5,"buy")),"iron_condor",7.5),
    ((("put",100.,"sell"),("put",95.,"buy"),("call",100.,"sell"),("call",105.,"buy")),"iron_butterfly",5.),
])
def test_every_offered_shape_is_named_with_its_risk_width(spec,structure,width):
    legs=_legs(*spec)
    assert pl.structure_of(legs)==structure and pl.spread_width(legs)==width
    assert pl.TYPES[structure][0]==("SELL" if structure in ("cash_secured_put","bull_put_credit","bear_call_credit",
                                                            "iron_condor","iron_butterfly") else "BUY")


@pytest.mark.parametrize("spec",[
    (("call",100.,"sell"),),                                             # naked call: never offered
    (("call",100.,"sell"),("put",100.,"sell")),                          # short straddle: undefined risk
    (("call",95.,"buy"),("put",105.,"buy")),                             # call strike below the put: not a strangle
    (("put",95.,"buy"),("put",90.,"sell"),("call",105.,"sell"),("call",112.5,"buy")),   # wings the wrong way round
])
def test_undefined_risk_or_unknown_shapes_are_refused(spec):
    with pytest.raises(ValueError):
        pl.structure_of(_legs(*spec))


@pytest.mark.parametrize("category,structure",[("iron_condors","iron_condor"),("iron_butterflies","iron_butterfly"),
                                               ("strangles","long_strangle"),("cash_secured_puts","cash_secured_put")])
def test_new_strategies_budget_by_their_money_at_risk(category,structure):
    # cash set aside must fit a band (≤ $1,000): a $9 stock with real volatility; the others on a $40 one
    tb=chain_table(9.,vol=.5) if category=="cash_secured_puts" else chain_table(40.)
    tb.u.committee=50. if category!="cash_secured_puts" else 65.        # a quiet stock; a liked one for puts
    tb.u.iv_rank=80. if category.startswith("iron") else 10.
    picks=pl.budget_plays(category,[tb],context(),None)
    assert picks and all(p["structure"]==structure for p in picks)
    for p in picks:
        risk=p["max_loss"]
        assert p["budget_band"][0]<risk<=p["budget_band"][1]
        if p["action"]=="SELL":
            assert risk==pytest.approx((pl.spread_width(p["legs"])-p["entry"])*100+.65*len(p["legs"]),abs=.02)
        else:
            assert risk==p["cost"]


def test_saved_debit_spreads_are_recognised_in_the_archive():
    from desk.stocks.play_archive import recovered_play

    expiry=AT.date()+timedelta(days=30)
    def leg(kind,strike,side):
        return {"occ":occ("TEST",expiry,kind,strike),"kind":kind,"side":side,"strike":strike,
                "expiration":expiry.isoformat()}
    for legs,structure in (([leg("call",100.,"buy"),leg("call",105.,"sell")],"bull_call_debit"),
                           ([leg("put",100.,"buy"),leg("put",95.,"sell")],"bear_put_debit"),
                           ([leg("put",100.,"sell"),leg("put",95.,"buy")],"bull_put_credit")):
        conn=sqlite3.connect(":memory:")
        conn.row_factory=sqlite3.Row
        row=conn.execute("SELECT ? AS symbol, ? AS legs_json, 2.0 AS entry",("TEST",json.dumps(legs))).fetchone()
        play=recovered_play(row)
        assert play["structure"]==structure and play["max_loss"]==(200+1.30 if play["action"]=="BUY" else 300+1.30)


def test_budget_picks_are_automatically_archived_and_tracked_without_entering_closing_cohorts(tmp_path):
    w=make_world(tmp_path,n=12)
    try:
        tb=chain_table()
        picks=pl.budget_plays("likely_itm",[tb],context(),None)
        board={"asof":AT.date().isoformat(),"generated_at":AT.isoformat(),"method":"synthetic_budget_v1",
               "categories":[{"id":"likely_itm","plays":[],"budget_plays":picks}]}
        before=list(w.dbs.ledger.iterdump())
        register(w.dbs.research,board,AT)
        first_seen(w.dbs.research,board,AT)
        register(w.dbs.research,board,AT+timedelta(minutes=1))
        assert w.dbs.research.execute("SELECT COUNT(*) FROM stk_play_ideas").fetchone()[0]==len(picks)
        assert w.dbs.research.execute("SELECT COUNT(*) FROM stk_play_first_seen").fetchone()[0]==len(picks)
        assert not w.dbs.research.execute("SELECT 1 FROM stk_plays").fetchone()
        assert not w.dbs.research.execute("SELECT 1 FROM stk_play_selections").fetchone()
        tracked=tracked_set(w.dbs.research,AT.date(),None)
        assert all(p["legs"][0]["occ"] in tracked and p["idea_id"] for p in picks)
        assert list(w.dbs.ledger.iterdump())==before
    finally:
        w.close()


def test_collection_mixes_liquid_lower_priced_stocks_without_raising_the_request_limit():
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stk_scores_latest(symbol TEXT,detail_json TEXT)")
    for symbol,price,volume in [("BIG",1000,1e10),("HUGE",800,9e9),("MID",500,8e9),
                                ("RETAIL",25,8e7),("RETAIL2",50,7e7),("THIN",10,1e4),("PENNY",1,5e7)]:
        conn.execute("INSERT INTO stk_scores_latest VALUES (?,?)",(symbol,json.dumps({"price":{
            "close":price,"dollar_volume_20d":volume}})))
    assert option_focus(conn,4,1,1e6)==["BIG","RETAIL","HUGE","RETAIL2"]
    assert option_focus(conn,0,1,1e6)==[]
    assert len(option_focus(conn,20,1,1e6))==7  # all leaders retained if the focus budget has room
    conn.close()
