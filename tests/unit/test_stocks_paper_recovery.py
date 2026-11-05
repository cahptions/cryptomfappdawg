"""Recovery through missed sessions is simulation, and the evaluation says which sessions were recovered."""

from datetime import timedelta

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks.analysis import Analyzer
from desk.stocks.backtest import memory_db
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.engine import PaperEngine
from desk.stocks.paper.report import recovery_sessions, report
from desk.stocks.paper.strategies import BY_STRATEGY
from desk.stocks.prices import load_panel
from desk.storage.db import DbKind
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world

SETTINGS = load_config(REPO / "config").settings.stocks


def test_sessions_stepped_without_their_analysis_are_counted_and_flagged_per_account(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        strategies = (BY_STRATEGY["momentum"], BY_STRATEGY["random_10"], BY_STRATEGY["spy_hold"])
        book = PaperBook(memory_db(DbKind.STOCKS_LEDGER))
        engine = PaperEngine(w.market, book, SETTINGS, strategies)
        analyzer = Analyzer(w.market, w.research, SETTINGS)
        first, missed, target = w.days[-4], w.days[-3], w.days[-2]

        def analysed(d):
            return analyzer.build(load_panel(w.market, w.days[0], d), d, now=cal.session_close(d) + timedelta(hours=1))

        engine.step(first, cal.session_close(first) + timedelta(hours=1), analysed(first))
        later = cal.session_close(target) + timedelta(hours=1)          # the app was off: one pass catches up
        engine.step(missed, later, None, allow_entries=False)
        engine.step(target, later, analysed(target))
        assert recovery_sessions(book) == {missed}
        result = report(book, w.market, strategies)
        assert result["recovery_sessions"] == 1
        for account in result["accounts"]:
            assert account["recovery_sessions"] == 1 and account["recovery_days"] == [missed.isoformat()]
            assert account["recovery_note"].startswith("1 of 3 sessions were recovery simulation")
        assert not [d for d in book.decisions() if d["asof"] == missed.isoformat() and d["action"] == "ENTER"]
    finally:
        w.close()


def test_a_ledger_from_before_recovery_events_still_identifies_its_catch_up_sessions(tmp_path):
    w = make_world(tmp_path, n=8)
    try:
        st = BY_STRATEGY["momentum"]
        book = PaperBook(memory_db(DbKind.STOCKS_LEDGER))
        book.open_account(st.id, 10000, NOW, w.days[-5])
        for d in w.days[-5:-3]:
            book.snapshot(d, st.id, cal.session_close(d) + timedelta(hours=1), 10000.0, 0.0)
        catch_up = cal.session_close(w.days[-1]) + timedelta(hours=1)    # two missed sessions and the target, together
        for d in w.days[-3:]:
            book.snapshot(d, st.id, catch_up, 10000.0, 0.0)
        assert recovery_sessions(book) == set(w.days[-3:-1])
        account = report(book, w.market, (st,))["accounts"][0]
        assert account["recovery_sessions"] == 2 and "2 of 5 sessions" in account["recovery_note"]
        clean = PaperBook(memory_db(DbKind.STOCKS_LEDGER))
        clean.open_account(st.id, 10000, NOW, w.days[-5])
        for d in w.days[-5:]:
            clean.snapshot(d, st.id, cal.session_close(d) + timedelta(hours=1), 10000.0, 0.0)
        assert report(clean, w.market, (st,))["accounts"][0]["recovery_note"] is None
    finally:
        w.close()
