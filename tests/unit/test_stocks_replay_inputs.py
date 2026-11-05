"""What the historical replay reads on each day: only inputs it can show as they were known then."""

from datetime import date, timedelta

import pytest

from desk.core.config import load_config
from desk.stocks import backtest
from desk.stocks.backtest import replay, short_history
from desk.stocks.contracts import ShortInterest
from desk.stocks.replay_grades import current
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world

SETTINGS = load_config(REPO / "config").settings.stocks


def settlements(start: date, end: date) -> list[date]:
    """FINRA's twice-monthly settlement dates (the 15th and the month's last day, close enough for a fixture)."""
    out, d = [], start
    while d <= end:
        last = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        out += [x for x in (d.replace(day=15), last) if start <= x <= end]
        d = last + timedelta(days=1)
    return out


def store_short_history(world, days: list[date], symbols=None) -> None:
    rows = [ShortInterest(sp.symbol, d, sp.shares * (0.3 if sp.symbol == "SQZ" else 0.02), sp.shares * 0.02, 2e6,
                          9.0 if sp.symbol == "SQZ" else 1.5)
            for d in days for sp in world.specs if symbols is None or sp.symbol in symbols]
    world.market.save_short_interest(rows, NOW)


@pytest.fixture
def seen(monkeypatch):
    days = []
    original = backtest.ReplayEngine.step

    def step(self, asof, now, day, **kwargs):
        days.append(day)
        return original(self, asof, now, day, **kwargs)

    monkeypatch.setattr(backtest.ReplayEngine, "step", step)
    return days


def test_macro_is_read_point_in_time_and_short_interest_waits_for_a_covering_history(tmp_path, seen):
    w = make_world(tmp_path, n=12)
    try:
        result = replay(w.paths, SETTINGS, 8)
        assert all(d.ctx.macro.get("ust10y") == 4.3 and d.ctx.macro["rates_as_of"] < d.asof.isoformat() for d in seen)
        assert all(d.ctx.shorts == {} for d in seen)
        assert result["short_interest"]["replayed"] is False and result["short_interest"]["settlements"] == 1
        ids = {a["id"] for a in result["accounts"]}
        assert not ids & {"squeeze_runner", "squeeze_calls"}
        assert any("short interest and the squeeze rules wait" in x for x in result["limitations"])
        assert not any("non-vintage" in x for x in result["limitations"])
        graded = current(result)
        assert graded["squeeze_runner"]["status"] == graded["squeeze_calls"]["status"] == "unsupported"
        assert "1 FINRA settlements are stored" in graded["squeeze_calls"]["message"]
    finally:
        w.close()


def test_a_covering_short_interest_history_replays_the_squeeze_rules(tmp_path, seen):
    w = make_world(tmp_path, n=12)
    try:
        store_short_history(w, settlements(date(2025, 6, 1), date(2026, 10, 15)))
        result = replay(w.paths, SETTINGS, 8)
        assert result["short_interest"]["replayed"] is True
        ids = {a["id"] for a in result["accounts"]}
        assert {"squeeze_runner", "squeeze_calls"} <= ids
        graded = current(result)
        assert graded["squeeze_runner"]["status"] == "current" and graded["squeeze_calls"]["status"] == "current"
        for d in seen:   # each day sees only settlements published 12 days before it
            assert d.ctx.shorts and all(date.fromisoformat(v["settlement"]) <= d.asof - timedelta(days=12)
                                        for v in d.ctx.shorts.values() if v["settlement"])
    finally:
        w.close()


def test_short_history_needs_a_year_without_gaps_or_partial_backfills(tmp_path):
    w = make_world(tmp_path, n=12)
    first, last = date(2026, 3, 2), date(2026, 10, 21)
    try:
        assert not short_history(w.market, first, last)["replayed"]           # one stored settlement
        dates = settlements(date(2025, 6, 1), date(2026, 10, 15))
        store_short_history(w, [d for d in dates if not date(2026, 5, 1) <= d <= date(2026, 6, 20)])
        gap = short_history(w.market, first, last)
        assert not gap["replayed"] and gap["settlements"] >= 24
        store_short_history(w, [d for d in dates if date(2026, 5, 1) <= d <= date(2026, 6, 20)], symbols={"SQZ"})
        assert not short_history(w.market, first, last)["replayed"]            # thin settlements are partial
        store_short_history(w, [d for d in dates if date(2026, 5, 1) <= d <= date(2026, 6, 20)])
        assert short_history(w.market, first, last)["replayed"]
        assert not short_history(w.market, date(2025, 6, 10), last)["replayed"]  # nothing published by then
    finally:
        w.close()


def test_a_past_day_universe_is_the_listings_as_they_stood_that_day(tmp_path):
    from desk.stocks.analysis import Analyzer
    from desk.stocks.contracts import Listing
    from desk.stocks.prices import load_panel
    from desk.stocks.universe import listed_on, research_listings

    w = make_world(tmp_path, n=12)
    try:
        keep = [Listing(sp.symbol, f"{sp.symbol} Corp", "NYSE", False, False, "common") for sp in w.specs
                if sp.symbol != "S10"]
        w.market.save_listings([*keep, Listing("NEWCO", "Newco Common Stock", "NYSE", False, False, "common")], {},
                               NOW + timedelta(days=10))
        assert "S10" not in {r["symbol"] for r in research_listings(w.market)}   # delisted today
        past, later = w.days[-60], (NOW + timedelta(days=20)).date()
        assert {"S10", "CHEAP"} <= {r["symbol"] for r in listed_on(w.market, past)}
        assert "NEWCO" not in {r["symbol"] for r in listed_on(w.market, past)}       # appeared later
        now_listed = {r["symbol"] for r in listed_on(w.market, later)}
        assert "NEWCO" in now_listed and "S10" not in now_listed and "CHEAP" in now_listed
        analyzer = Analyzer(w.market, w.research, SETTINGS)
        panel = load_panel(w.market, w.days[0], past)
        assert "S10" in analyzer.build(panel, past, now=NOW, historical=True).ctx.symbols
        assert "S10" not in analyzer.build(panel, past, now=NOW).ctx.symbols
    finally:
        w.close()


def test_a_held_stock_that_stops_trading_after_delisting_sells_at_its_last_close(tmp_path):
    from desk.core.clock import to_iso
    from desk.stocks import calendar as cal
    from desk.stocks.backtest import memory_db
    from desk.stocks.contracts import Bar
    from desk.stocks.paper.book import PaperBook
    from desk.stocks.paper.engine import PaperEngine
    from desk.stocks.paper.strategies import BY_STRATEGY
    from desk.storage.db import DbKind

    w = make_world(tmp_path, n=12)
    try:
        for i, d in enumerate(w.days[-40:-10]):
            w.market.save_bars(d, [Bar("GONE", d, 20 + i, 21 + i, 19 + i, 20.5 + i, 1e6)], "synthetic", NOW)
        last_bar = w.days[-11]
        w.market.conn.execute("INSERT INTO stk_listings VALUES ('GONE','Gone Inc','NYSE','common',0,0,NULL,0,?,?)",
                              (to_iso(NOW), to_iso(cal.session_close(last_bar))))
        st = BY_STRATEGY["momentum"]
        book = PaperBook(memory_db(DbKind.STOCKS_LEDGER))
        engine = PaperEngine(w.market, book, SETTINGS, (st,))
        book.open_account(st.id, 10000, NOW, w.days[-20])
        did = book.decide(NOW, w.days[-21], st.id, st.spec_sha256, "GONE", "ENTER", ["fixture"], {})
        book.open_position(account=st.id, spec_sha256=st.spec_sha256, asset="stock", symbol="GONE", structure="long_stock",
                           legs=[{"kind": "stock", "symbol": "GONE", "quantity": 10, "entry_price": 30.0}],
                           decision_id=did, at=NOW, day=w.days[-20], cost=300.0, fees=0.0, max_loss=None,
                           stop_price=None, expires=None, plan={})
        position = book.positions(st.id)[0]
        exit_id = book.decide(NOW, w.days[-6], st.id, st.spec_sha256, "GONE", "EXIT", ["left the top 20"], {})
        book.place(exit_id, st.id, cal.session_close(w.days[-6]), w.days[-6],
                   {"asset": "stock", "action": "EXIT", "symbol": "GONE", "position_id": position.id,
                    "reason": "left the top 20"})
        engine.step(w.days[-5], cal.session_close(w.days[-5]), None, allow_entries=False)
        closed = book.positions(st.id, state="CLOSED")
        assert len(closed) == 1 and "delisted" in (closed[0].exit_reason or "")
        assert closed[0].exit_credit == pytest.approx(10 * (20.5 + 29) * (1 - 0.0015), rel=1e-6)
        assert not book.pending()
    finally:
        w.close()
