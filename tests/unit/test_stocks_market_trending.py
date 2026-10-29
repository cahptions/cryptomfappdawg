"""Market ranking is invariant to social attention; WSB has its own visible provenance and list specification."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

from desk.stocks.hub import research_lists
from desk.stocks.lists import BY_ID, trending, wsb
from tests.stocks_world import ASOF, NOW, make_world


def day():
    prices={"ACTIVE":{"close":100.,"rel_volume":3.,"ret_1d":.01,"vol_20d":.2},
            "DOWN":{"close":90.,"rel_volume":1.,"ret_1d":-.05,"vol_20d":.2},
            "QUIET":{"close":50.,"rel_volume":1.,"ret_1d":.001,"vol_20d":.2}}
    return SimpleNamespace(asof=ASOF,market_price=prices,ctx=SimpleNamespace(price=prices,options={},crowd={}),results={})


def test_social_mentions_upvotes_and_scores_cannot_qualify_or_rerank_market_trending():
    d=day()
    original=trending(d)
    assert {r["symbol"] for r in original}=={"ACTIVE","DOWN"}
    d.ctx.crowd={"QUIET":{"wsb":{"mentions":10**9,"upvotes":10**12,"rank":1}}}
    d.results={"crowd":{"QUIET":SimpleNamespace(score=100)}}
    assert trending(d)==original
    assert wsb(d)[0]["symbol"]=="QUIET"
    assert all("no social input" in r["reason"] for r in original)
    assert any("-5.0%" in r["reason"] for r in original if r["symbol"]=="DOWN")


def test_missing_or_stale_options_do_not_block_stock_activity_or_boost_a_rank():
    d=day()
    before=trending(d)
    d.ctx.options={"ACTIVE":{"day":(ASOF-timedelta(days=1)).isoformat(),"activity":{
        "session":(ASOF-timedelta(days=1)).isoformat(),"premium_proxy":10**15}}}
    assert trending(d)==before
    d.ctx.options["ACTIVE"]={"day":ASOF.isoformat(),"activity":{
        "session":ASOF.isoformat(),"liquid_contracts":4,"volume":1000,"open_interest":1000,
        "premium_proxy":100_000.,"median_spread":.05}}
    after=trending(d)
    assert "1,000 sampled option contracts" in next(r["reason"] for r in after if r["symbol"]=="ACTIVE")
    d.ctx.price["ACTIVE"]["vol_20d"]=None
    assert "ACTIVE" in {r["symbol"] for r in trending(d)}  # volume alone qualifies; missing volatility is not zero
    d.ctx.price["ACTIVE"]["rel_volume"]=None
    assert "ACTIVE" not in {r["symbol"] for r in trending(d)}  # options alone cannot bypass the stock-activity gate


def test_wsb_uses_mentions_not_upvotes_and_does_not_substitute_general_reddit_counts():
    d=day()
    d.ctx.crowd={"ACTIVE":{"wsb":{"mentions":5,"upvotes":10**6,"rank":2,"mentions_24h_ago":0}},
                 "DOWN":{"wsb":{"mentions":10,"upvotes":1,"rank":1,"mentions_24h_ago":5}},
                 "QUIET":{"all":{"mentions":10**9,"upvotes":10**12,"rank":1}}}
    rows=wsb(d)
    assert [r["symbol"] for r in rows]==["DOWN","ACTIVE"]
    assert rows[0]["mention_change"]==1 and rows[1]["mention_change"] is None
    assert "previously zero mentions" in rows[1]["reason"] and rows[1]["upvotes"]==10**6
    d.ctx.crowd={}
    assert wsb(d)==[]


def test_recorded_wsb_keeps_source_counts_and_observation_time_without_rewriting_a_session(tmp_path):
    w=make_world(tmp_path,n=12)
    try:
        d=day()
        d.ctx.crowd={"ACTIVE":{"wsb":{"mentions":900,"mentions_24h_ago":600,"upvotes":1200,
                                      "rank":1,"observed_hour":498000}}}
        selected=wsb(d)
        assert w.research.save_list(ASOF,"wsb",selected,BY_ID["wsb"].spec_sha256,NOW)
        recorded=next(r for r in research_lists(w.research) if r["id"]=="wsb")["rows"][0]
        for field in ("mentions","mention_change","upvotes","source_rank","observed_hour"):
            assert recorded[field]==selected[0][field]
        assert recorded["rank"]==1
        assert not w.research.save_list(ASOF,"wsb",[],BY_ID["wsb"].spec_sha256,NOW)
        assert next(r for r in research_lists(w.research) if r["id"]=="wsb")["rows"][0]==recorded
    finally:
        w.close()


def test_changed_trending_rule_does_not_relabel_or_rewrite_a_crowd_weighted_snapshot(tmp_path):
    w=make_world(tmp_path,n=12)
    try:
        old=[{"symbol":"SQZ","value":1.,"reason":"Old crowd-weighted method"}]
        w.research.save_list(ASOF,"trending",old,"old-v1-hash",NOW)
        old_preview={"computed_at":(NOW+timedelta(seconds=1)).isoformat(),"lists":[
            {"id":"trending","asof":ASOF.isoformat(),"rows":old,"preview":True}]}
        before=list(w.dbs.research.iterdump())
        assert not next(r for r in research_lists(w.research,old_preview) if r["id"]=="trending")["rows"]
        current=deepcopy(old_preview)
        current["lists"][0].update(spec_sha256=BY_ID["trending"].spec_sha256,rows=[
            {"symbol":"CHEAP","reason":"New market activity method"}])
        assert next(r for r in research_lists(w.research,current) if r["id"]=="trending")["rows"][0]["symbol"]=="CHEAP"
        assert list(w.dbs.research.iterdump())==before
        assert BY_ID["trending"].version==2 and BY_ID["wsb"].history is False
    finally:
        w.close()
