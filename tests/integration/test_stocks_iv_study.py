from datetime import timedelta

import pytest

from desk.stocks import brain_study as study
from desk.stocks import calendar as cal
from desk.stocks.contracts import Bar
from tests.stocks_world import ASOF, NOW, make_world
from tests.unit.test_stocks_option_signals import store


def test_study_freezes_future_comparator_and_grades_only_after_receipts(tmp_path):
    world = make_world(tmp_path, n=30)
    market, research = world.market.conn, world.dbs.research
    try:
        market.execute("UPDATE stk_companies SET sector='fixture'")
        for symbol, in market.execute("SELECT symbol FROM stk_listings WHERE symbol LIKE 'S__'"):
            store(world.market, symbol, ASOF, call=.4)
        candidates, _ = study._features(market, ASOF, NOW)
        assert len(candidates) >= 8
        leader = next(c for c in candidates if any(
            c["symbol"] != p["symbol"] and abs(p["momentum"] - c["momentum"]) <= .02 for p in candidates
        ))
        peer = next(p for p in candidates if p["symbol"] != leader["symbol"] and abs(p["momentum"] - leader["momentum"]) <= .02)
        store(world.market, leader["symbol"], ASOF, call=.3)
        store(world.market, peer["symbol"], ASOF, call=.8)
        study.advance(market, research, NOW, enabled=True)
        case = study.summary(research, enabled=True)["recent"][0]
        assert case["challenger"] == peer["symbol"] and case["comparator"] == leader["symbol"]
        store(world.market, peer["symbol"], ASOF, call=.05, computed=NOW + timedelta(minutes=5))
        study.advance(market, research, NOW + timedelta(minutes=10), enabled=True)
        assert study.summary(research, enabled=True)["recent"][0] == case
        assert study.summary(research, enabled=True)["comparison"]["one_sided_p"] is None
        entry = cal.next_trading_day(ASOF)
        exit_day = study._shift(ASOF, study.HORIZON)
        names = [peer["symbol"], leader["symbol"], "SPY"]
        for day, ret in ((entry, 0), (exit_day, .02)):
            bars = [Bar(s, day, 100, 110, 90, 100 * (1 + ret if s == names[0] else 1), 1000) for s in names]
            world.market.save_bars(day, bars, "synthetic", cal.session_close(day) + timedelta(minutes=20))
        grade_at = cal.session_close(exit_day) + timedelta(minutes=25)
        study.advance(market, research, grade_at, enabled=False)
        report = study.summary(research, enabled=False)
        assert report["counts"] == {"proposed": 1, "graded": 1}
        assert report["comparison"]["mean_excess_bps"] == pytest.approx(200)
        assert report["comparison"]["one_sided_p"] is None
        study.advance(market, research, grade_at + timedelta(minutes=5), enabled=False)
        assert study.summary(research, enabled=False) == report
    finally:
        world.close()


def test_missed_window_is_counted_without_historical_enrollment(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        study.advance(world.market.conn, world.dbs.research, NOW, enabled=False)
        assert study.summary(world.dbs.research, enabled=False)["registered_at"] is None
        study.advance(world.market.conn, world.dbs.research, NOW, enabled=True)
        study.advance(world.market.conn, world.dbs.research, study._open(cal.next_trading_day(ASOF)), enabled=True)
        report = study.summary(world.dbs.research, enabled=True)
        assert report["counts"] == {"abstained": 1, "missed": 1}
        assert report["recent"][0]["session"] == ASOF.isoformat()
    finally:
        world.close()


def test_quote_trial_keeps_core_and_book_state_and_records_unavailable_outcomes(tmp_path):
    from desk.core.clock import to_iso
    from desk.stocks import learning, learning_retail, quote_trials
    from desk.stocks.paper.book import PaperBook
    from tests.unit.test_stocks_learning import AT

    world = make_world(tmp_path, n=12)
    book_db = learning.open_book(world.paths)
    try:
        learning.initialize(book_db, AT - timedelta(days=1))
        book = PaperBook(book_db)
        account = learning.SMALL_ACCOUNTS[0]
        book_db.execute("INSERT INTO stk_learning_sources VALUES ('source','S00',?,X'00')", (to_iso(AT),))
        learning_retail.record_wait(book, account, AT, "cash", {"mode": "starter", "phase": "Insufficient cash"},
                                    {"cash": 1000, "one_unit": 1050, "observation": {"source_key": "source"}}, symbol="S00")
        research = world.dbs.research
        for symbol in ("S00", "S07"):
            research.execute("INSERT INTO stk_scores_latest VALUES (?,?,?,?)",
                             (symbol, AT.date().toordinal(), to_iso(AT),
                              '{"price":{"close":100,"dollar_volume_20d":9000000}}'))
        before = list(book_db.iterdump())
        args = world.market, research, book_db, AT, ["SPY", "S07", "QQQ"], {"SPY", "QQQ"}
        chosen = quote_trials.assign(*args, "schwab")
        assert chosen and chosen[1] == "S07" and chosen[2] in {"S07", "S00"}
        assert quote_trials.assign(*args, "schwab") is None
        day, _, actual = chosen
        interrupted = quote_trials.summary(world.market.conn, AT + timedelta(minutes=16), enabled=True)
        assert sum(a["unknown"] for a in interrupted["arms"].values()) == 1
        quote_trials.finish(world.market, day, actual, AT + timedelta(minutes=1), {
            "errors": {actual: "fixture forbidden"}, "provider_calls": {actual: 1}, "symbol_seconds": {actual: .25},
        })
        quote_trials.finish(world.market, day, actual, AT + timedelta(minutes=1), {})
        report = quote_trials.summary(world.market.conn, AT, enabled=True)
        assert report["latest"]["result"]["status"] == "provider failure"
        assert report["latest"]["result"]["provider_calls"] == 1
        assert list(book_db.iterdump()) == before
    finally:
        book_db.close()
        world.close()
