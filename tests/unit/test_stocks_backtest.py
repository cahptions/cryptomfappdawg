"""Replay isolation, chronological signals/fills, split timing and held-out selection boundaries."""

from datetime import date
from unittest.mock import Mock

import numpy as np
import pytest

from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.stocks.backtest import historical_panel, replay, walk_forward
from desk.stocks.backtest_job import BacktestWorker
from desk.stocks.contracts import Split
from desk.stocks.prices import Panel
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def test_replay_records_actual_simulated_positions_without_mutating_source_research_or_forward_paper(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        before = [list(conn.iterdump()) for conn in (world.dbs.market, world.dbs.research, world.dbs.ledger)]
        result = replay(world.paths, load_config(REPO / "config").settings.stocks, 63)
        assert result["record"] == "historical" and result["requested_sessions"] == 63
        assert result["first_day"] == world.days[-63].isoformat() and result["last_day"] == world.days[-1].isoformat()
        assert result["trades"] and result["walk_forward"]["windows"]
        assert all(p["entry_reason"] for p in result["trades"])
        assert all(a["sessions"] == 63 and a["asset"] == "stock" for a in result["accounts"])
        assert all(p["entry_day"] > result["first_day"] for p in result["trades"])
        decisions = {d["id"]: d for d in result["decisions"]}
        for position in result["trades"]:
            if position["decision_id"] in decisions:
                assert position["entry_day"] > decisions[position["decision_id"]]["asof"]
        assert [list(conn.iterdump()) for conn in (world.dbs.market, world.dbs.research, world.dbs.ledger)] == before
    finally:
        world.close()


def test_future_price_volume_and_split_changes_cannot_change_earlier_decisions(tmp_path):
    world = make_world(tmp_path, n=12)
    settings = load_config(REPO / "config").settings.stocks
    try:
        before = replay(world.paths, settings, 8)
        future = world.days[-2]
        # This disposable counterfactual fixture may alter its future. Production append-only triggers stay intact.
        world.dbs.market.execute("DROP TRIGGER stk_bars_no_update")
        world.dbs.market.execute(
            "UPDATE stk_bars SET open=open*3,high=high*3,low=low*3,close=close*3,volume=volume*1000 "
            "WHERE symbol='CHEAP' AND day>=?",
            (future.toordinal(),),
        )
        world.market.save_splits([Split("CHEAP", future, 1, 3)], NOW)
        after = replay(world.paths, settings, 8)

        def decisions(result):
            return [
                (d["account"], d["symbol"], d["asof"], d["action"], d["reasons"])
                for d in result["decisions"]
                if d["asof"] < future.isoformat()
            ]

        assert decisions(before) == decisions(after)

        def entries(result):
            return sorted(
                (p["account"], p["symbol"], p["entry_day"], p["entry_cost"], p["entry_fees"])
                for p in result["trades"]
                if p["entry_day"] < future.isoformat()
            )

        assert entries(before) == entries(after)
    finally:
        world.close()


def test_historical_panel_has_no_future_columns_or_future_split_adjustment():
    days = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    values = np.array([[100.0, 101.0, 51.0]])
    raw = Panel(["ABC"], days, values, values, values, values, np.array([[1000.0, 1000.0, 2000.0]]))
    splits = [Split("ABC", days[2], 1, 2)]
    early = historical_panel(raw, days[1], splits)
    late = historical_panel(raw, days[2], splits)
    assert early.days == days[:2] and early.close[0, 0] == 100
    assert late.close[0, 0] == 50 and late.volume[0, 0] == 2000
    assert raw.close[0, 0] == 100


def test_walk_forward_selects_before_outcome_and_held_out_windows_do_not_overlap():
    curves = [
        {"day": date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + i).isoformat(), "equity": 10000 + i * 10}
        for i in range(63)
    ]
    accounts = [
        {"id": "a", "title": "A", "baseline": False, "curve": curves},
        {"id": "b", "title": "B", "baseline": False, "curve": [{**r, "equity": 10000} for r in curves]},
        {"id": "spy_hold", "title": "SPY", "baseline": True, "curve": curves},
    ]
    initial = walk_forward(accounts)
    assert initial["windows"][0]["strategy"] == "a"
    for left, right in zip(initial["windows"], initial["windows"][1:], strict=False):
        assert left["test_end"] < right["test_start"] and right["train_through"] == left["test_end"]
    accounts[0]["curve"] = [{**r, "equity": r["equity"] if i < 21 else 1} for i, r in enumerate(curves)]
    assert walk_forward(accounts)["windows"][0]["strategy"] == "a"
    assert walk_forward(accounts)["windows"][0]["test_return"] < 0


def test_automatic_replay_is_once_per_input_and_only_cancels_owned_fixed_module(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    child = Mock()
    child.poll.return_value = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr("desk.stocks.evidence_job.subprocess.Popen", spawn)
    try:
        worker = BacktestWorker(world.paths)
        worker.maintain()
        worker.maintain()
        assert spawn.call_count == 1
        assert spawn.call_args.args[0][1:] == ["-m", "desk.stocks.backtest_job"]
        assert spawn.call_args.kwargs["env"]["DESK_STOCKS_BACKTEST_SESSIONS"] == "63"
        worker.cancel()
        child.terminate.assert_called_once()
        child.poll.return_value = -15
        worker.maintain()
        assert spawn.call_count == 1
        assert not worker.automatic(False, 63)["auto_enabled"]
        with pytest.raises(DeskError, match="Choose"):
            worker.start(999)
    finally:
        world.close()


def test_paper_pause_stops_only_owned_automatic_replay_and_can_resume_it(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    child = Mock()
    child.poll.return_value = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr("desk.stocks.evidence_job.subprocess.Popen", spawn)
    try:
        worker = BacktestWorker(world.paths)
        worker.maintain()
        assert worker.status()["auto_started"]
        worker.pause_automatic()
        child.terminate.assert_called_once()
        assert worker.preferences()["last_input"] is None
        child.poll.return_value = -15
        worker.maintain()
        assert spawn.call_count == 2
        # A deliberately started offline replay is independent of the PAPER pause control.
        child.poll.return_value = -15
        worker.start(63)
        child.poll.return_value = None
        before = child.terminate.call_count
        worker.pause_automatic()
        assert child.terminate.call_count == before
    finally:
        world.close()
