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
