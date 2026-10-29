"""Same statistical hurdles, separate evidence: historical grades cannot age or promote the forward record."""

import json
import math
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from desk.core.config import load_config
from desk.stocks.backtest import account_views, memory_db, replay
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.report import report
from desk.stocks.paper.strategies import BY_STRATEGY
from desk.stocks.replay_grades import VERSION, compute, current
from desk.stocks.web import create_app
from desk.storage.db import DbKind
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


@pytest.fixture
def books(tmp_path):
    w=make_world(tmp_path,n=12)
    conn=memory_db(DbKind.STOCKS_LEDGER)
    book=PaperBook(conn)
    strategies=tuple(BY_STRATEGY[s] for s in ("momentum","value_momentum","random_10","spy_hold"))
    for s in strategies:
        book.open_account(s.id,10000,NOW,w.days[-126])
        book.decide(NOW,w.days[-126],s.id,s.spec_sha256,"SPY","SKIP",["Synthetic statistical fixture"],{})
        equity=10000.
        for i,day in enumerate(w.days[-126:]):
            if not s.baseline:
                equity *= 1.002+.0002*math.sin(i)
            book.snapshot(day,s.id,NOW,equity,0.)
    yield w,conn,book,strategies
    conn.close()
    w.close()


def test_historical_grade_uses_replay_sessions_and_same_adjusted_family_without_touching_forward(books):
    w,_conn,book,strategies=books
    forward=PaperBook(w.dbs.ledger)
    for s in strategies:
        forward.open_account(s.id,10000,NOW,w.days[-1])
        forward.snapshot(w.days[-1],s.id,NOW,10000,0)
    before=list(w.dbs.ledger.iterdump())
    original=report(forward,w.market,strategies)
    grades=compute(book,w.market,strategies)
    momentum=grades["momentum"]
    assert momentum["sessions"]==126 and momentum["grade"]["grade"]=="working"
    assert momentum["vs_baseline"]["n"]==125 and momentum["vs_baseline"]["p_holm"]>=momentum["vs_baseline"]["p"]
    assert momentum["holm_family"]==["momentum","value_momentum"]
    assert momentum["first_day"]==w.days[-126].isoformat() and momentum["last_day"]==w.days[-1].isoformat()
    assert momentum["can_promote"] is False and momentum["basis"]=="historical_reconstruction"
    assert report(forward,w.market,strategies)==original
    assert all(a["sessions"]==1 for a in original["accounts"])
    assert list(w.dbs.ledger.iterdump())==before


def test_replay_grade_is_current_only_for_the_exact_strategy_and_rejects_old_or_missing_grades(books):
    w,_conn,book,strategies=books
    grades=compute(book,w.market,strategies)
    accounts=account_views(book,strategies)
    for a in accounts:
        a["historical_grade"]=grades[a["id"]]
    result={"run_id":"a"*32,"first_day":w.days[-126].isoformat(),"last_day":w.days[-1].isoformat(),"accounts":accounts}
    original=deepcopy(result)
    matched=current(result,strategies)
    assert result==original
    assert matched["momentum"]["status"]=="current"
    changed=replace(BY_STRATEGY["momentum"],version=99)
    assert current(result,(changed,))["momentum"]["status"]=="stale"
    assert "grade" not in current(result,(changed,))["momentum"]
    next(a for a in result["accounts"] if a["id"]=="momentum").pop("historical_grade")
    assert current(result,strategies)["momentum"]["status"]=="missing"
    assert current({"accounts":None},strategies)["momentum"]["status"]=="missing"
    assert original["accounts"][0]["historical_grade"]  # matching never mutates the stored report
    assert current(None)["cheap_iv_calls"]["status"]=="unsupported"


def test_event_replay_still_needs_closed_trades_and_mixed_versions_cannot_get_a_current_grade(books):
    w,_conn,book,strategies=books
    event=BY_STRATEGY["insider_cluster"]
    book.open_account(event.id,10000,NOW,w.days[-126])
    for day in w.days[-126:]:
        book.snapshot(day,event.id,NOW,10000.,0.)
    grades=compute(book,w.market,(*strategies,event))
    assert grades[event.id]["grade"]["text"]=="Too early: 0 of 20 closed trades"
    book.decide(NOW,w.days[-1],"momentum","old-specification","SPY","SKIP",["Synthetic old version"],{})
    mixed=compute(book,w.market,strategies)["momentum"]
    assert mixed["mixed_specifications"] and mixed["grade"]["grade"]=="early"
    historical={"run_id":"a"*32,"accounts":[{"id":"momentum","spec_sha256":BY_STRATEGY["momentum"].spec_sha256,
        "sessions":126,"historical_grade":mixed}]}
    assert current(historical,strategies)["momentum"]["status"]=="stale"


def test_completed_replay_keeps_statistical_grades_with_its_immutable_archive(tmp_path):
    w=make_world(tmp_path,n=12)
    before=list(w.dbs.ledger.iterdump())
    try:
        result=replay(w.paths,load_config(REPO/"config").settings.stocks,8)
        assert result["grade_version"]==VERSION
        from desk.stocks.replay_archive import reader

        with reader(w.paths,result["run_id"]) as conn:
            stored=json.loads(conn.execute("SELECT value FROM replay_meta WHERE key='grades'").fetchone()[0])
        a=next(a for a in result["accounts"] if a["id"]=="momentum")
        assert stored["momentum"]==a["historical_grade"]
        assert a["historical_grade"]["sessions"]==8
        assert a["historical_grade"]["grade"]["text"]=="Too early: 8 of 126 sessions"
        assert a["historical_grade"]["spec_sha256"]==BY_STRATEGY["momentum"].spec_sha256
        forward=report(PaperBook(w.dbs.ledger),w.market,(BY_STRATEGY["momentum"],))
        from desk.stocks.paper import lab

        learned=lab.as_strategy("valuation+quality/10",{"variants":1,"why":"Synthetic learned-policy fixture"})
        lab.promote(w.research,learned,{},NOW)
        (w.paths.home/"stocks-paper.json").write_text(json.dumps(forward))
        (w.paths.home/"stocks-backtest-report.json").write_text(json.dumps(result))
        with TestClient(create_app(w.paths,auto_start=False),base_url="http://127.0.0.1:8767") as client:
            payload=client.get("/api/stocks/paper").json()
            assert payload["report"]==forward and payload["historical_replays"]["momentum"]["sessions"]==8
            assert payload["historical_replays"]["momentum"]["status"]=="current"
            assert payload["historical_replays"][learned.id]["status"]=="unsupported"
            a["spec_sha256"]="old-specification"
            (w.paths.home/"stocks-backtest-report.json").write_text(json.dumps(result))
            stale=client.get("/api/stocks/paper").json()["historical_replays"]["momentum"]
            assert stale["status"]=="stale" and "grade" not in stale
        assert list(w.dbs.ledger.iterdump())==before
    finally:
        w.close()
