"""Reproduce the user's two-LEAPS-only history and recover real saved sightings without any manual selection."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import play_archive as archive
from desk.stocks import play_marks, play_tracking, plays, web
from desk.stocks import play_selections as personal
from desk.stocks.contracts import Bar
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch
from tests.conftest import REPO

DAY = date(2026,10,27)
IDENTIFIED = datetime(2026,10,27,10,tzinfo=cal.NEW_YORK)
LATER = IDENTIFIED+timedelta(days=1)


def pick(symbol="SPY", kind="call", strike=100.):
    expiration = "2026-11-20"
    leg = {"occ":f"{symbol}261120{kind[0].upper()}{round(strike*1000):08d}","kind":kind,"strike":strike,
           "expiration":expiration,"side":"buy","bid":1.9,"ask":2.,"iv":.3,"underlying":100.}
    return {"symbol":symbol,"type":kind.upper(),"action":"BUY","structure":"long_"+kind,"rank":1,
            "expiration":expiration,"strike":strike,"entry":2.,"max_loss":200.65,"cost":200.65,
            "event":"itm","event_kind":kind,"event_strike":strike,"event_width":0.,"legs":[leg],
            "underlying_price":100.,"itm_market":.5,"itm_history":None,"itm_kestrel":.5,
            "itm_kestrel_raw":.5,"profit_kestrel":.4,"source":"synthetic_archive_fixture"}


def board(at, category, picks):
    return {"asof":DAY.isoformat(),"generated_at":at.isoformat(),"method":"fixture",
            "categories":[{"id":category,"plays":picks}]}


@pytest.fixture
def world(tmp_path):
    paths = DeskPaths(tmp_path/"home",REPO/"config",tmp_path/"secrets.env")
    dbs = StocksDatabases.open(paths)
    yield paths,dbs
    dbs.close()


def initial_two(dbs):
    a,b = pick(),pick("QQQ")
    b["rank"] = 2
    categories = {c.id:[] for c in plays.CATEGORIES}
    categories["leaps"] = [a,b]
    plays.record(StocksResearch(dbs.research),DAY,categories,IDENTIFIED+timedelta(hours=7),"fixture")
    return a,b


def test_all_identified_survive_empty_frozen_categories_and_rank_changes_without_clicks(world, monkeypatch):
    paths,dbs = world
    initial_two(dbs)
    missing = pick("AAPL","put")
    vanished = pick("MSFT")
    personal.register(dbs.research,board(IDENTIFIED,"likely_itm",[missing,vanished]),IDENTIFIED)
    # A later build ranks something else. Earlier picks must remain in history and quote rotation.
    replacement = pick("NVDA")
    personal.register(dbs.research,board(IDENTIFIED+timedelta(hours=2),"likely_itm",[replacement]),IDENTIFIED+timedelta(hours=2))
    assert plays.record(StocksResearch(dbs.research),DAY,{"likely_itm":[missing]},LATER,"fixture") == []
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_plays").fetchone()[0] == 2
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_play_selections").fetchone()[0] == 0
    tracked = play_tracking.tracked_set(dbs.research,LATER.date(),None)
    assert vanished["legs"][0]["occ"] in tracked and replacement["legs"][0]["occ"] in tracked
    dbs.research.execute("INSERT INTO stk_track_ticks(occ,at,session,bid,ask,underlying,quoted_at,source) "
        "VALUES (?,?,?,?,?,?,?,?)", (missing["legs"][0]["occ"],LATER.isoformat(),LATER.date().toordinal(),
                                   4.,4.1,102.,LATER.isoformat(),"synthetic_archive_fixture"))
    monkeypatch.setattr(web,"utc_now",lambda:LATER)
    with TestClient(web.create_app(paths,auto_start=False),base_url="http://127.0.0.1:8767") as client:
        data = client.get("/api/stocks/plays/history?when=yesterday").json()
        assert data["coverage"]["identified"] == 5 and data["coverage"]["closing"] == 2
        rows = [r for c in data["categories"] for r in c["plays"]]
        assert {r["symbol"] for r in rows} == {"SPY","QQQ","AAPL","MSFT","NVDA"}
        current = next(r for r in rows if r["symbol"] == "AAPL")
        assert current["latest"]["pnl"] == 198.7 and current["archive_id"]
        assert client.get("/api/stocks/plays/history?when=yesterday&scope=closing").json()["summary"] is None
        older = client.get("/api/stocks/plays/history?when=yesterday&scope=closing").json()
        assert sum(len(c["plays"]) for c in older["categories"]) == 2
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_plays").fetchone()[0] == 2


def test_startup_recovers_legacy_sightings_preserving_original_entry_and_without_model_reconstruction(world, monkeypatch):
    paths,dbs = world
    initial_two(dbs)
    original = pick("AAPL","put")
    original["entry"] = 3.
    play_tracking.first_seen(dbs.research,board(IDENTIFIED,"bear_puts",[original]),IDENTIFIED)
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_play_ideas").fetchone()[0] == 0
    monkeypatch.setattr(web,"utc_now",lambda:LATER)
    with TestClient(web.create_app(paths,auto_start=False),base_url="http://127.0.0.1:8767") as client:
        data = client.get("/api/stocks/plays/history?when=yesterday").json()
        recovered = next(r for c in data["categories"] for r in c["plays"] if r["symbol"] == "AAPL")
        assert recovered["entry"] == 3. and recovered["chance"] is None and recovered["rank"] is None
        detail = client.get("/api/stocks/plays/idea/"+recovered["archive_id"]).json()
        assert detail["idea"]["play"]["recovered_sighting"]
        assert datetime.fromisoformat(detail["idea"]["identified_at"]) == IDENTIFIED
        assert data["coverage"]["recovery_pending"] == 0
        assert client.get("/api/stocks/plays/mine").json()["total"] == 0
        assert archive.RECOVERY_METHOD not in data["methods"]
    assert archive.recover(dbs.research,LATER) == {"recovered":0,"skipped":0}


def test_identification_day_uses_new_york_and_stale_board_does_not_backdate_new_ideas(world):
    _paths,dbs = world
    actual = datetime(2026,10,28,1,tzinfo=UTC)  # still Oct 27 in New York
    a = pick("AAPL")
    personal.register(dbs.research,board(actual,"likely_itm",[a]),actual)
    later = actual+timedelta(hours=10)
    personal.register(dbs.research,board(later,"likely_itm",[pick("MSFT")]),later)
    assert [s["session"] for s in archive.sessions(dbs.research)["sessions"]] == ["2026-10-28","2026-10-27"]
    old = archive.session_view(dbs.research,DAY,later)
    assert old["coverage"]["identified"] == 1
    assert old["categories"][0]["plays"][0]["symbol"] == "AAPL"


async def test_archived_play_gets_daily_marks_and_settlement_without_becoming_a_closing_cohort(world):
    _paths,dbs = world
    a = pick("AAPL")
    b = board(IDENTIFIED,"likely_itm",[a])
    personal.register(dbs.research,b,IDENTIFIED)
    expiry = date(2026,11,20)
    StocksMarket(dbs.market).save_bars(expiry,[Bar("AAPL",expiry,105.,105.,105.,105.,1e6)],"fixture",LATER)
    end = cal.session_close(expiry)+timedelta(hours=1)
    await play_marks.update(dbs.market,dbs.research,end,history=None,rate=lambda _: .04,budget=0)
    history = personal.view(dbs.research,a["idea_id"],end)["chart"]
    assert history["daily"][-1]["status"] == "settled" and history["daily"][-1]["close"]["pnl"] == 299.35
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_plays").fetchone()[0] == 0
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_play_outcomes").fetchone()[0] == 0
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_play_days").fetchone()[0] == 0


async def test_intraday_sighting_never_becomes_a_closing_quote_or_an_entry_on_an_earlier_day(world):
    _paths,dbs = world
    personal.register(dbs.research,board(LATER,"likely_itm",[pick("AAPL")]),LATER)
    before = cal.session_close(DAY)+timedelta(hours=1)
    await play_marks.update(dbs.market,dbs.research,before,history=None,rate=lambda _: .04,budget=0)
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_play_marks").fetchone()[0] == 0
    await play_marks.update(dbs.market,dbs.research,LATER+timedelta(hours=7),history=None,rate=lambda _: .04,budget=0)
    assert dbs.research.execute("SELECT COUNT(*) FROM stk_track_days WHERE source='recorded play'").fetchone()[0] == 0


def test_unreadable_legacy_sighting_is_reported_once_and_cannot_block_recovery(world):
    _paths,dbs = world
    dbs.research.execute("INSERT INTO stk_play_first_seen VALUES (?,?,?,?,?,?,?)",
        (DAY.toordinal(),"likely_itm","bad","BAD",IDENTIFIED.isoformat(),2.,"invalid-json"))
    assert archive.recover(dbs.research,LATER) == {"recovered":0,"skipped":1}
    assert archive.recover(dbs.research,LATER) == {"recovered":0,"skipped":0}
    assert archive.recovery_pending(dbs.research) == 0
