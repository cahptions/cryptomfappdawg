"""KESTREL Stocks analysis on a synthetic market: desks find the planted traits, lists are built and appended once,
and nothing that became public after the day can change that day's analysis."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from desk.core.config import load_config
from desk.stocks import desks as dk
from desk.stocks.analysis import Analyzer, compact, stock_detail
from desk.stocks.contracts import Bar, Filing, Company, InsiderTrade
from desk.stocks.lists import BY_ID, LISTS, build_lists
from desk.stocks.prices import load_panel
from tests.conftest import REPO
from tests.stocks_world import ASOF, NOW, StocksWorld, make_world

SETTINGS = load_config(REPO / "config").settings.stocks


@pytest.fixture
def world(tmp_path: Path) -> StocksWorld:
    w = make_world(tmp_path)
    yield w
    w.close()


def analyse(world: StocksWorld, asof: date = ASOF):
    analyzer = Analyzer(world.market, world.research, SETTINGS)
    panel = load_panel(world.market, asof - timedelta(days=760), asof)
    return analyzer.build(panel, asof, now=NOW)


def test_every_desk_scores_the_universe_and_finds_the_planted_traits(world: StocksWorld) -> None:
    day = analyse(world)
    assert day.universe_size == 60 and "SPY" not in day.ctx.symbols
    for desk in dk.DESKS:
        scored = [s for s, r in day.results[desk].items() if r.score is not None]
        assert len(scored) >= (3 if desk == "options" else 0), desk
        assert all(0 <= (r.score or 0) <= 100 for r in day.results[desk].values()), desk
    val = day.results["valuation"]
    assert val["CHEAP"].score > 70 and val["PRICEY"].score < 30
    assert day.results["insiders"]["INSDR"].verdict == "Cluster buying"
    assert "Cluster buying" in day.results["insiders"]["INSDR"].reasons[0]
    assert day.results["shorts"]["SQZ"].facts["squeeze"] >= 50
    assert day.results["quality"]["TRAP"].score < day.results["quality"]["CHEAP"].score
    assert day.results["quality"]["DILUT"].facts["dilution"] > 0.15
    assert day.results["crowd"]["SQZ"].verdict in ("Heating up", "Talked about") and day.results["crowd"]["S20"].verdict == "Quiet"
    assert all(r.reasons for r in val.values() if r.score is not None)
    detail = stock_detail(day, "CHEAP")
    assert detail["committee"]["score"] is not None and detail["desks"]["valuation"]["verdict"] in ("Cheap", "Reasonable")
    assert detail["bull"] and detail["desks"]["valuation"]["facts"]["fair_value"]["base"] > 0
    assert set(compact(day, "CHEAP")) >= {"c", "valuation", "px"}


def test_lists_pick_the_planted_names_and_are_appended_once(world: StocksWorld) -> None:
    day = analyse(world)
    lists = build_lists(day)
    assert set(lists) == {spec.id for spec in LISTS}
    assert "CHEAP" in [r["symbol"] for r in lists["undervalued"]]
    assert "PRICEY" in [r["symbol"] for r in lists["overvalued"]]
    assert [r["symbol"] for r in lists["insider_buying"]][0] == "INSDR"
    assert "SQZ" in [r["symbol"] for r in lists["squeeze"]]
    assert "DILUT" in [r["symbol"] for r in lists["dilution"]]
    assert "TRAP" not in [r["symbol"] for r in lists["undervalued"]]
    assert all(r["reason"] for rows in lists.values() for r in rows)
    assert all(len(rows) <= 25 for rows in lists.values())
    spec = BY_ID["undervalued"]
    assert world.research.save_list(ASOF, spec.id, lists["undervalued"], spec.spec_sha256, NOW)
    assert not world.research.save_list(ASOF, spec.id, [], spec.spec_sha256, NOW)


def test_a_past_day_is_not_changed_by_anything_published_after_it(world: StocksWorld) -> None:
    asof = date(2026, 9, 15)
    before = analyse(world, asof)
    snapshot = {s: (before.committee[s]["score"], {d: before.results[d][s].score for d in dk.DESKS}) for s in before.ctx.symbols}
    lists_before = build_lists(before)
    # news after the day: a huge rally, a new 10-Q, insider buying, a red-flag 8-K
    later = asof + timedelta(days=3)
    world.market.save_bars(later, [Bar(s, later, 999.0, 999.0, 999.0, 999.0, 9e9) for s in ("S10", "CHEAP")], "test", NOW)
    world.market.save_insider_trades([(InsiderTrade("late", 1010, "S10", "Late", 77, True, False, None, False, asof, "P",
                                                    1e6, 10.0, True, 1e6, True, False), asof)])          # filed on the day itself
    sic = next(sp.sic for sp in world.specs if sp.symbol == "S10")
    world.market.save_company(Company(1010, "S10 Corp", sic, "x", ("S10",), ("NYSE",), "1231", None, (
        Filing(1010, "late-8k", "8-K", asof, datetime(2026, 9, 15, 23, 0, tzinfo=UTC), asof, ("4.02",), "x.htm"),)),
        None, NOW)
    after = analyse(world, asof)
    assert {s: (after.committee[s]["score"], {d: after.results[d][s].score for d in dk.DESKS}) for s in after.ctx.symbols} == snapshot
    assert build_lists(after) == lists_before


def test_the_methodology_is_versioned() -> None:
    assert dk.method_sha256() == dk.method_sha256()
    assert len({spec.spec_sha256 for spec in LISTS}) == len(LISTS)
    content = dk.method_content()
    assert content["default_weights"]["crowd"] == 0 and content["version"] == dk.METHOD_VERSION
