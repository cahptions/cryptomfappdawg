"""KESTREL Stocks proof on a synthetic market: rebuilt months match what the desk would have said on the day, the
daily record is appended once, and the report grades lists and desks and learns committee weights."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from desk.core.config import load_config
from desk.stocks import desks as dk
from desk.stocks import validation as val
from desk.stocks.analysis import Analyzer
from desk.stocks.daily import refresh, register_specs
from desk.stocks.lists import LISTS
from desk.stocks.prices import load_panel
from desk.storage.db import StorageError
from tests.conftest import REPO
from tests.stocks_world import ASOF, NOW, StocksWorld, make_world

SETTINGS = load_config(REPO / "config").settings.stocks


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> StocksWorld:
    w = make_world(tmp_path_factory.mktemp("validation"))
    yield w
    w.close()


def full_panel(world: StocksWorld):
    return load_panel(world.market, world.days[0])


def test_rebuild_is_point_in_time_and_done_once(world: StocksWorld) -> None:
    analyzer = Analyzer(world.market, world.research, SETTINGS)
    panel = full_panel(world)
    built = val.rebuild(analyzer, panel, now=NOW)
    assert len(built) == len(val.rebuild_days(panel)) >= 10
    assert val.rebuild(analyzer, panel, now=NOW) == []                    # nothing new to rebuild
    months = val.months(world.research)
    assert [m.asof for m in months] == built
    # what the rebuild stored for a month equals an analysis run with no prices after that month
    m = months[3]
    alone = Analyzer(world.market, world.research, SETTINGS).build(load_panel(world.market, world.days[0], m.asof), m.asof,
                                                                     now=NOW, crowd_and_options=False,
                                                                     weights=dict(dk.DEFAULT_WEIGHTS))
    for d in dk.DESKS:
        expect = {s: round(r.score, 2) for s, r in alone.results[d].items() if r.score is not None}
        assert {s: v[0] for s, v in m.scores[d].items()} == expect, d
    assert set(m.lists) == {spec.id for spec in LISTS if spec.history}
    assert not m.scores["crowd"] and not m.scores["options"]               # no crowd or options history


def test_daily_refresh_waits_for_prices_and_records_once(world: StocksWorld) -> None:
    analyzer = Analyzer(world.market, world.research, SETTINGS)
    early = load_panel(world.market, world.days[0], world.days[-2])
    waiting = refresh(analyzer, early, ASOF, NOW)
    assert waiting.skipped and "waiting" in waiting.skipped and not world.research.snapshot_days()
    # a live record over the last months: each day refreshed with only the prices it had
    for d in world.days[-116::5]:               # ends on ASOF
        out = refresh(analyzer, load_panel(world.market, world.days[0], d), d, NOW)
        assert out.skipped is None and set(out.recorded) == {spec.id for spec in LISTS}
    again = refresh(analyzer, full_panel(world), ASOF, NOW + timedelta(hours=2))
    assert again.recorded == [] and again.universe == 60                   # pages refreshed, the record untouched
    assert world.research.latest_asof() == ASOF and world.research.latest("CHEAP") is not None
    assert len(world.research.snapshot_days()) == 24


def test_report_grades_lists_and_desks_and_learns_weights(world: StocksWorld) -> None:
    panel = full_panel(world)
    out = val.run(world.market, world.research, panel, today=ASOF, now=NOW)
    assert out["id"] >= 1 and out["method_sha256"] == val.method_sha256()
    assert out["rebuild"]["months"] >= 10 and out["rebuild"]["caveats"]
    undervalued = out["lists"]["undervalued"]
    assert undervalued["claim"] == "outperform" and undervalued["months_rebuilt"] >= 10
    assert undervalued["rebuilt"]["1m"]["n"] >= 9 and undervalued["live"]["1w"]["n"] >= 20
    assert undervalued["grade"]["grade"] in ("early", "working", "promising", "not_working", "backwards")
    assert out["lists"]["trending"]["rebuilt"] is None and out["lists"]["trending"]["history"] is False
    for name in ("valuation", "momentum", "risk", "committee"):
        assert out["desks"][name]["rebuilt"]["ic"]["n"] >= 9, name
    assert out["desks"]["crowd"]["rebuilt"] is None
    tested = [v["rebuilt"]["1m"] for v in out["lists"].values() if v["claim"] in val.TESTED_CLAIMS and v["rebuilt"]]
    assert all(s["p_holm"] is None or s["p_holm"] >= s["p"] for s in tested)
    learning = out["committee"]
    assert learning["months"] >= 9 and learning["why"]
    analyzer = Analyzer(world.market, world.research, SETTINGS)
    expected = out["committee_weights"] or dk.DEFAULT_WEIGHTS
    assert analyzer.weights() == expected
    curve = undervalued["curve"]["rebuilt"]
    assert curve and curve[0]["list"] == 1.0
    assert world.research.latest_validation()["id"] == out["id"]
    assert "Research, not financial advice." in val.render(out)
    with pytest.raises(Exception, match="append-only"):
        world.research.conn.execute("DELETE FROM stk_validation")


def test_rules_are_frozen_by_version(world: StocksWorld) -> None:
    register_specs(world.market, NOW)
    with pytest.raises(StorageError, match="changed without a new version"):
        world.market.register_spec("stocks_validation", val.VERSION, {**val.content(), "cost_round_trip": 0},
                                   datetime(2026, 10, 22, tzinfo=UTC))
