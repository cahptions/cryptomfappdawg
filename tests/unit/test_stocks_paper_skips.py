"""A candidate the engine passes over leaves its reason in the decision timeline: once per stock, a few per session."""

from dataclasses import replace
from datetime import timedelta

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks.analysis import Analyzer
from desk.stocks.backtest import memory_db
from desk.stocks.paper import engine as eng
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.strategies import BY_STRATEGY
from desk.stocks.prices import load_panel
from desk.storage.db import DbKind
from tests.conftest import REPO
from tests.stocks_world import make_world

SETTINGS = load_config(REPO / "config").settings.stocks


def test_option_candidates_without_a_chain_record_why_once_per_stock_and_session(tmp_path, monkeypatch):
    w = make_world(tmp_path, n=12)
    try:
        base = BY_STRATEGY["random_calls"]
        st = replace(base, params=tuple(sorted({**base.p, "n": 4}.items())))       # four random picks a day
        book = PaperBook(memory_db(DbKind.STOCKS_LEDGER))
        engine = eng.PaperEngine(w.market, book, SETTINGS, (st,))
        analyzer = Analyzer(w.market, w.research, SETTINGS)
        first, second = w.days[-3], w.days[-2]
        for d in (first, second):
            day = analyzer.build(load_panel(w.market, w.days[0], d), d, now=cal.session_close(d) + timedelta(hours=1))
            assert [s for s, o in day.ctx.options.items() if o.get("iv30")]              # optioned, but no chains
            engine.step(d, cal.session_close(d) + timedelta(hours=1), day)
        skips = [x for x in book.decisions(st.id) if x["action"] == "SKIP"]
        assert skips and all("no option chain was collected for this stock today" in x["reasons"] for x in skips)
        for d in (first, second):
            symbols = [x["symbol"] for x in skips if x["asof"] == d.isoformat()]
            assert len(symbols) == len(set(symbols)) == 4 <= eng.MAX_SKIPS
        monkeypatch.setattr(eng, "MAX_SKIPS", 2)
        third = w.days[-1]
        day = analyzer.build(load_panel(w.market, w.days[0], third), third, now=cal.session_close(third) + timedelta(hours=1))
        engine.step(third, cal.session_close(third) + timedelta(hours=1), day)
        assert len([x for x in book.decisions(st.id) if x["action"] == "SKIP" and x["asof"] == third.isoformat()]) == 2
    finally:
        w.close()
