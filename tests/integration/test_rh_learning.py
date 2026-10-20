"""The learning lab inside the PAPER engine: learned entries and exits, record/off modes, benching, scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from desk.core.hashing import canonical_json
from desk.rh import learn, learning
from desk.rh.contracts import Candle
from desk.rh.view import RhView
from tests.integration.test_rh_paper import NOW, SESSION, D, World

TREND = "TREND:n=20,sma=0,x=10,k=3.0,hold=60,regime=False"


def promote(world: World, procedure: str | None = "LEARN_BEST", best: tuple[str, ...] = (TREND,)) -> str:
    report = {"selection": {"best": list(best), "top3": list(best), "model": None},
              "procedures": {name: {"label": name, "passed": name == procedure} for name in learn.PROCEDURES},
              "folds": [], "symbols": 2, "days": 221, "candidates": 255, "leaderboard": [], "cost_model": {}}
    selection = "sel-" + (procedure or "none")
    world.dbs.market.execute(
        "INSERT INTO rh_learning_runs(created_at, data_end, lab_sha256, selection_sha256, promoted, report_json)"
        " VALUES (?, ?, ?, ?, ?, ?)", (NOW.isoformat(), (SESSION - timedelta(days=1)).isoformat(), learn.lab_sha256(),
                                       selection, procedure, canonical_json(report)))
    return selection


@pytest.fixture
async def world(tmp_path: Path) -> World:
    w = World(tmp_path)
    await w.prime()
    yield w
    await w.suite.aclose()
    w.dbs.close()


def learned(world: World, state: str = "OPEN") -> list:
    return [p for p in world.ledger.positions(state) if p.strategy_id == learn.STRATEGY_ID]


async def test_a_promoted_choice_trades_paper_with_its_own_version(world: World) -> None:
    selection = promote(world)
    await world.desk.learned_session(SESSION, NOW)
    [pos] = learned(world)
    assert pos.symbol == "ALT-USD"                                    # the 20-day breakout; BTC has none
    assert pos.spec_sha256 == selection
    assert pos.plan["features"]["learned_candidate"] == TREND
    assert pos.stop_price is not None and pos.stop_price < pos.entry_unit_price
    decision = world.dbs.market.execute("SELECT * FROM rh_decisions WHERE strategy_id = ? AND action = 'ENTER'",
                                        (learn.STRATEGY_ID,)).fetchone()
    assert decision["spec_sha256"] == selection and "LEARNED" in decision["reasons"]
    await world.desk.learned_session(SESSION, NOW)                    # one position per coin
    assert len(learned(world)) == 1


async def test_learned_positions_exit_by_the_rule_that_opened_them(world: World) -> None:
    promote(world)
    await world.desk.learned_session(SESSION, NOW)
    [pos] = learned(world)
    today = datetime(SESSION.year, SESSION.month, SESSION.day, tzinfo=UTC)          # the bar of the 20th
    world.market.save_candles("coinbase", [
        Candle("ALT-USD", 86400, today, D(60), D(61), D(39), D(40), D(1_000_000)),     # far below the 10-day low
        Candle("BTC-USD", 86400, today, D(320), D(324), D(319), D(321), D(1_000_000))], NOW + timedelta(days=1))
    world.clock.at = NOW + timedelta(days=1)                       # quotes untouched: only the daily rule can act
    await world.desk.learned_session(SESSION + timedelta(days=1), NOW + timedelta(days=1))
    closed = {p.id: p for p in learned(world, "CLOSED")}
    assert pos.id in closed and closed[pos.id].exit_reason == "LOW_BREAK"   # closed below the prior 10-day low
    exit_decision = world.dbs.market.execute("SELECT reasons FROM rh_decisions WHERE strategy_id = ? AND action ="
                                             " 'EXIT'", (learn.STRATEGY_ID,)).fetchone()
    assert exit_decision["reasons"] == "LOW_BREAK"


async def test_nothing_promoted_record_mode_and_benching_mean_no_learned_entries(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    promote(world, procedure=None)
    await world.desk.learned_session(SESSION, NOW)
    assert learned(world) == []
    promote(world)
    world.desk.settings = world.desk.settings.model_copy(update={"learning": "record"})
    await world.desk.learned_session(SESSION, NOW)
    assert learned(world) == []
    world.desk.settings = world.desk.settings.model_copy(update={"learning": "paper"})
    monkeypatch.setattr(learning, "forward", lambda *_a, **_k: {"benched": True})
    await world.desk.learned_session(SESSION, NOW)
    assert learned(world) == []


async def test_the_lab_runs_when_due_and_waits_a_month_after(world: World) -> None:
    world.desk._backfilled = True                                     # no network in tests
    await world.desk.learning_step()
    run = learning.latest(world.dbs.market)
    assert run is not None and run.promoted is None                   # 221 days cannot fill a single unseen month
    assert not learning.due(world.dbs.market, NOW + timedelta(hours=2))
    assert not learning.due(world.dbs.market, NOW + timedelta(days=5))
    assert learning.due(world.dbs.market, NOW + timedelta(days=learn.TEST_DAYS + 2))
    events = [r["kind"] for r in world.dbs.ledger.execute("SELECT kind FROM rh_events")]
    assert "LEARNING_RUN" in events
    page = RhView(world.paths, world.loaded).learning()
    assert page["latest"]["id"] == run.id and page["mode"] == "paper" and page["forward"]["benched"] is False
    with pytest.raises(Exception, match="append-only"):
        world.dbs.market.execute("UPDATE rh_learning_runs SET promoted = 'LEARN_BEST'")


async def test_off_mode_does_not_learn(world: World) -> None:
    world.desk.settings = world.desk.settings.model_copy(update={"learning": "off"})
    await world.desk.learning_step()
    assert learning.latest(world.dbs.market) is None
