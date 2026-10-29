"""Yesterday's estimate must give way to today's actual option quote, without rewriting historical evidence."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import play_history as ph
from desk.stocks import play_marks, play_selections, web
from desk.stocks import play_tracking as pt
from desk.stocks.contracts import OptionTick
from desk.stocks.repo import StocksDatabases
from tests.conftest import REPO
from tests.integration.test_stocks_play_archive import DAY, IDENTIFIED, board, initial_two, pick

NOW = datetime(2026,10,29,11,tzinfo=cal.NEW_YORK)
YESTERDAY = NOW-timedelta(days=1)


@pytest.fixture
def saved(tmp_path):
    paths = DeskPaths(tmp_path/"home",REPO/"config",tmp_path/"secrets.env")
    dbs = StocksDatabases.open(paths)
    play,_ = initial_two(dbs)
    play_selections.register(dbs.research,board(IDENTIFIED,"leaps",[play]),IDENTIFIED)
    got = ph.pnl("BUY",2.,play["legs"],1.85,False)
    dbs.research.execute("INSERT INTO stk_play_marks(session,category,rank,twin,day,status,value,pnl,pnl_pct,recorded_at) "
                        "VALUES (?,'leaps',1,0,?,'estimated',1.85,?,?,?)",
                        (DAY.toordinal(),YESTERDAY.date().toordinal(),got["pnl"],got["pct"],YESTERDAY.isoformat()))
    yield paths,dbs,play
    dbs.close()


def tick(research, play, *, received=NOW, quoted=NOW-timedelta(seconds=20)):
    item = OptionTick(play["legs"][0]["occ"],3.,3.1,None,None,50,100,.3,.5,.02,-.02,.1,105.,quoted)
    pt.save_ticks(research,pt.tick_rows([item],received,received.date(),"synthetic_freshness_fixture"))


def test_28th_estimate_becomes_29th_quote_in_both_detail_paths_and_api_without_rewriting_marks(saved, monkeypatch):
    paths,dbs,play = saved
    old = ph.play_view(dbs.research,DAY,"leaps",1,NOW)
    assert old["freshness"]["state"] == "stale" and old["freshness"]["market_open"]
    assert "today's option quotes" in old["freshness"]["message"]
    before = [tuple(r) for r in dbs.research.execute("SELECT * FROM stk_play_marks")]
    tick(dbs.research,play)
    monkeypatch.setattr(web,"utc_now",lambda:NOW)
    with TestClient(web.create_app(paths,auto_start=False),base_url="http://127.0.0.1:8767") as client:
        result = client.get(f"/api/stocks/plays/play?session={DAY}&category=leaps&rank=1").json()
        assert result["freshness"]["state"] == "current"
        assert datetime.fromisoformat(result["exits"]["close"]["latest"]["at"]) == NOW-timedelta(seconds=20)
        assert result["exits"]["close"]["latest"]["pnl"] == pytest.approx(98.7)
        archived = client.get("/api/stocks/plays/idea/"+play["idea_id"]).json()["chart"]
        assert archived["freshness"]["state"] == "current"
        assert archived["exits"]["close"]["latest"]["value"] == 3.
    assert [tuple(r) for r in dbs.research.execute("SELECT * FROM stk_play_marks")] == before
    latest = ph.session_view(dbs.research,DAY,NOW)["categories"][0]["plays"][0]["latest"]
    assert latest["day"] == "2026-10-29" and latest["status"] == "live"


def test_retrieving_a_yesterdays_quote_today_does_not_make_it_current_or_fold_it_as_a_new_close(saved):
    _paths,dbs,play = saved
    tick(dbs.research,play,quoted=YESTERDAY)
    data = ph.play_view(dbs.research,DAY,"leaps",1,NOW)
    assert data["freshness"]["state"] == "stale"
    assert data["exits"]["close"]["latest"]["t"] == "2026-10-28"
    assert pt.last_ticks(dbs.research,NOW.date()) == {}
    pt.fold(dbs.research,NOW.date(),NOW.replace(hour=17),lambda _: .04)
    assert not dbs.research.execute("SELECT 1 FROM stk_track_days WHERE session=?", (NOW.date().toordinal(),)).fetchone()


@pytest.mark.parametrize("quoted,state",[(NOW-timedelta(minutes=20),"stale"),(None,"unverified")])
def test_old_or_unknown_provider_quote_times_are_not_labeled_current(saved, quoted, state):
    _paths,dbs,play = saved
    tick(dbs.research,play,quoted=quoted)
    data = ph.play_view(dbs.research,DAY,"leaps",1,NOW)
    assert data["freshness"]["state"] == state
    assert data["exits"]["close"]["latest"]["value"] == 3.
    if quoted:
        assert datetime.fromisoformat(data["exits"]["close"]["latest"]["at"]) == quoted


def test_a_spread_requires_contemporaneous_quote_times_for_every_leg(saved):
    _paths,dbs,long = saved
    short = pick("SPY",strike=105.)
    tick(dbs.research,long,quoted=NOW-timedelta(minutes=6))
    tick(dbs.research,short,quoted=NOW-timedelta(seconds=10))
    legs = [*long["legs"],{**short["legs"][0],"side":"sell"}]
    exits = {"close":{"latest":{"t":NOW.isoformat(),"at":NOW.isoformat(),"status":"live"}}}
    assert ph.valuation_status(dbs.research,legs,NOW,exits)["state"] == "stale"


async def test_live_quotes_are_saved_before_historical_repair_even_when_repair_fails(saved, monkeypatch):
    _paths,dbs,_play = saved
    class Quotes:
        configured = True
        async def option_quotes(self, occs):
            return [OptionTick(o,3.,3.1,None,None,50,100,.3,.5,.02,-.02,.1,105.,NOW) for o in occs]
    async def failed_repair(*args, **kwargs):
        assert dbs.research.execute("SELECT COUNT(*) FROM stk_track_ticks").fetchone()[0] == 2
        assert pt.get_state(dbs.research,"pass")["quoted"] == 2
        raise DeskError("Synthetic history download failure")
    monkeypatch.setattr(play_marks,"update",failed_repair)
    result = await pt.run_pass(dbs.market,dbs.research,NOW,board=None,schwab=Quotes(),chains=None,
                               rate=lambda _: .04,clock=lambda:NOW)
    assert result["quoted"] == 2 and result["maintenance_error"]
    assert ph.play_view(dbs.research,DAY,"leaps",1,NOW)["freshness"]["state"] == "current"


def test_tracking_uses_the_shared_session_close_instead_of_its_own_fixed_4pm(monkeypatch):
    early = datetime(2026,11,27,12,59,tzinfo=cal.NEW_YORK)
    monkeypatch.setattr(cal,"session_close",lambda day:datetime.combine(day,early.replace(hour=13,minute=0).timetz()))
    assert pt.market_open(early)
    assert not pt.market_open(early.replace(hour=13,minute=2))
