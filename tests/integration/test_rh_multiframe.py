"""Offline timing, availability, restart, accounting and STOP boundaries for experimental v2."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from desk.rh import multiframe as mf
from desk.rh import strategies as st
from desk.rh.engine import RhDesk
from desk.rh.history import sync_candles
from desk.rh.view import RhView
from tests.integration.test_rh_paper import NOW, World, intraday


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = World(tmp_path)
    monkeypatch.setattr("desk.rh.view.utc_now", w.clock)
    await w.prime()
    yield w
    await w.suite.aclose()
    w.dbs.close()


def test_closed_bucket_boundary_and_grace() -> None:
    at = NOW.replace(hour=14, minute=15, second=0)
    assert mf.cutoff(at).minute == 0
    assert mf.cutoff(at + timedelta(seconds=29)).minute == 0
    assert mf.cutoff(at + timedelta(seconds=30)) == at
    assert mf.floor_time(at, mf.FOUR_HOURS).hour == 12


def test_four_hour_aggregation_requires_exact_hourly_components() -> None:
    end = NOW.replace(hour=12, minute=0)
    bars = intraday("ALT-USD", [Decimal(10), Decimal(11), Decimal(12), Decimal(13)], mf.HOUR, end)
    result = mf.aggregate(bars)
    assert len(result) == 1 and result[0].close == 13 and result[0].volume == 4_000_000
    assert mf.aggregate(bars[:-1]) == []
    assert mf.aggregate([*bars, bars[0]]) == []
    assert mf.aggregate(bars[1:]) == []


async def test_new_candles_are_evaluated_in_the_afternoon_without_forcing_trades(world: World) -> None:
    # Carry an aligned daily close and build a fresh intraday afternoon window; no daily-session call.
    world.clock.at = NOW + timedelta(hours=14)
    end = mf.cutoff(world.clock())
    world.market.save_candles("coinbase", intraday("BTC-USD", [Decimal(300) + i / Decimal(10) for i in range(336)],
                                                  mf.HOUR, end), world.clock())
    world.market.save_candles("coinbase", intraday("BTC-USD", [Decimal(320)] * 61, mf.QUARTER, end), world.clock())
    # Existing immutable overlapping buckets prevail. Both latest frames are complete and current.
    await world.desk.intraday_session()
    rows = world.dbs.market.execute("SELECT * FROM rh_frame_checks").fetchall()
    assert rows and rows[0]["bar_end"].startswith("2026-10-20T14:00")
    assert world.ledger.positions() == []                         # collecting/analysing does not imply a buy
    assert mf.load(world.market, "BTC-USD", world.clock()).ready


async def test_restart_and_repeated_polls_cannot_replay_a_candle_check(world: World) -> None:
    await world.desk.intraday_session()
    assert len(world.ledger.positions("OPEN")) == 1
    first = world.ledger.positions()[0]
    rows = world.dbs.market.execute("SELECT * FROM rh_frame_checks").fetchall()
    evidence = json.loads(rows[0]["evidence_json"])
    assert evidence["universe_inputs"] and evidence["conditions"] and evidence["frames"]
    restarted = RhDesk(world.paths, world.loaded, world.suite, world.dbs, clock=world.clock,
                       sleep=world._sleep, wait_seconds=0)
    await restarted.intraday_session()
    assert [p.id for p in world.ledger.positions()] == [first.id]
    assert world.dbs.market.execute("SELECT COUNT(*) FROM rh_frame_checks").fetchone()[0] == len(rows)
    assert world.market.setup_entered(st.Signal(first.strategy_id, first.symbol, "ENTER", (),
                                              {"spec_version": "2", "bar_end": mf.cutoff(NOW).isoformat()}))


async def test_stale_or_gapped_frames_abstain_and_late_data_is_not_visible_early(world: World) -> None:
    state = mf.load(world.market, "ALT-USD", NOW)
    assert state.ready
    later = NOW + timedelta(minutes=15)
    assert not mf.load(world.market, "ALT-USD", later).ready
    # Only the next 15m bar arrives, leaving the 00:00..00:15 gap permanently visible.
    end = mf.cutoff(later) + timedelta(minutes=15)
    bar = intraday("ALT-USD", [Decimal(61)], mf.QUARTER, end)
    received = end + timedelta(minutes=1)
    world.market.save_candles("coinbase", bar, received)
    assert bar[0] not in world.market.candles("ALT-USD", mf.QUARTER, as_of=received - timedelta(seconds=1))
    assert not mf.load(world.market, "ALT-USD", received).ready
    # A late real missing bucket repairs the gap; no interpolation, and first receipt is respected.
    missing = intraday("ALT-USD", [Decimal(60)], mf.QUARTER, end - timedelta(minutes=15))
    world.market.save_candles("coinbase", missing, received + timedelta(seconds=1))
    assert not mf.load(world.market, "ALT-USD", received).ready
    assert mf.load(world.market, "ALT-USD", received + timedelta(seconds=1)).ready


async def test_stop_created_during_estimate_wait_prevents_entry(world: World) -> None:
    async def stop_during_wait(_: float) -> None:
        world.paths.stop_file.touch()
    world.desk.sleep = stop_during_wait
    await world.desk.intraday_session()
    assert world.ledger.positions() == []
    assert any("ENTRY_STOPPED_DURING_ESTIMATE" in r["reasons"] for r in world.decisions())


async def test_price_jump_during_wait_cannot_exceed_the_account_relative_cap(world: World) -> None:
    async def jump(_: float) -> None:
        world.mids["ALT-USD"] *= 2
    world.desk.sleep = jump
    await world.desk.intraday_session()
    assert world.ledger.positions() == []
    assert any("ACCOUNT_RELATIVE_TICKET_CAP" in r["reasons"] for r in world.decisions())


async def test_new_15m_data_cannot_reenter_a_closed_position_on_the_same_hourly_setup(world: World) -> None:
    await world.desk.intraday_session()
    pos = world.ledger.positions("OPEN")[0]
    assert await world.desk.exit(pos, "TEST_EXIT")
    world.clock.at = NOW + timedelta(minutes=15)
    for symbol, close in (("BTC-USD", Decimal(320)), ("ALT-USD", Decimal(61))):
        world.market.save_candles("coinbase", intraday(symbol, [close], mf.QUARTER, mf.cutoff(world.clock())),
                                 world.clock())
    await world.desk.poll_quotes()
    states = {s: mf.load(world.market, s, world.clock()) for s in world.mids}
    assert any(s.strategy_id == "R1_TREND" for s in mf.entries(states, "RISK_ON", []))
    await world.desk.intraday_session()
    assert world.ledger.positions("OPEN") == []
    assert len(world.ledger.positions("CLOSED")) == 1


async def test_hourly_pullback_uses_15m_confirmation_in_a_four_hour_uptrend(world: World) -> None:
    symbol = "DIP-USD"
    hours = [Decimal(20) + i * Decimal("0.24") for i in range(333)] + [Decimal(99), Decimal(98), Decimal(97)]
    world.market.save_candles("coinbase", intraday(symbol, hours, mf.HOUR, mf.cutoff(NOW))
                             + intraday(symbol, [Decimal(97)] * 60 + [Decimal("97.2")], mf.QUARTER, mf.cutoff(NOW)), NOW)
    from tests.integration.test_rh_paper import daily
    world.market.save_candles("coinbase", daily(symbol, [Decimal(50) + i for i in range(120)]), NOW)
    states = {s: mf.load(world.market, s, NOW) for s in (symbol, "BTC-USD")}
    signals = mf.entries(states, "RISK_ON", [])
    assert any(s.strategy_id == "R3_PULLBACK" and s.symbol == symbol for s in signals)
    assert Decimal(states[symbol].features["rsi2"]) < 10
    assert not mf.entries(states, "RISK_OFF", [])


async def test_24h_momentum_reclaim_ranks_ready_assets_and_abstains_without_enough_data(world: World) -> None:
    from tests.integration.test_rh_paper import daily
    for symbol in ("FAST-USD", "SLOW-USD"):
        hours = [Decimal(10) * (Decimal("1.005") ** i) for i in range(336)]
        quarters = [hours[-1]] * 59 + [hours[-1] * Decimal("0.98"), hours[-1] * Decimal("1.01")]
        world.market.save_candles("coinbase", intraday(symbol, hours, mf.HOUR, mf.cutoff(NOW))
                                 + intraday(symbol, quarters, mf.QUARTER, mf.cutoff(NOW))
                                 + daily(symbol, [Decimal(30)] * 120), NOW)
    states = {s: mf.load(world.market, s, NOW) for s in ("BTC-USD", "FAST-USD", "SLOW-USD")}
    signals = mf.entries(states, "RISK_ON", [])
    assert {s.symbol for s in signals if s.strategy_id == "R2_XMOM"} == {"FAST-USD", "SLOW-USD"}
    assert not any(s.strategy_id == "R2_XMOM" for s in mf.entries(
        {s: state for s, state in states.items() if s != "SLOW-USD"}, "RISK_ON", []))


async def test_v1_position_keeps_its_daily_exit_rules(world: World) -> None:
    # v1 is not upgraded in place: the v2 72h time exit cannot close a legacy 4-day position.
    first = await world.desk._estimates("ALT-USD", Decimal("0.1"), "ENTRY")
    buy = next(e for e in first if e.side == "BUY")
    at = NOW - timedelta(days=4)
    signal = st.Signal("R1_TREND", "ALT-USD", "ENTER", ("LEGACY_FIXTURE",))
    digest = world.market.record_decision(at, at.date(), signal, "ENTER", signal.reasons, {})
    pid = world.ledger.open_position(at=at, day=at.date(), strategy_id="R1_TREND", symbol="ALT-USD",
                                     decision_hash=digest, buy=buy, roundtrip=Decimal("0.01"),
                                     stop_price=Decimal(10), plan={})
    await world.desk.intraday_session()
    assert pid in {p.id for p in world.ledger.positions("OPEN")}
    trend = next(s for s in RhView(world.paths, world.loaded).snapshot()["strategies"] if s["id"] == "R1_TREND")
    assert trend["legacy_open"] == 1


async def test_v2_metrics_do_not_relabel_legacy_v1_as_new_evidence(world: World) -> None:
    await world.desk.session()
    snap = RhView(world.paths, world.loaded).snapshot()
    trend = next(s for s in snap["strategies"] if s["id"] == "R1_TREND")
    assert trend["spec_version"] == 2 and trend["backtest"] is None
    assert snap["session"]["next_at"].startswith("2026-10-20T00:15:30")
    for interval in ("15m", "1h", "4h", "1d"):
        coin = RhView(world.paths, world.loaded).coin("ALT-USD", "1M", interval)
        assert coin and coin["points"] and len(coin["frames"]) == 4


async def test_history_sync_uses_quarter_hour_not_hour_floor(world: World) -> None:
    calls = []
    class History:
        async def candles(self, product: str, seconds: int, start: object, end: object) -> list[object]:
            calls.append((product, seconds, start, end))
            return []
    at = NOW.replace(hour=14, minute=47)
    await sync_candles(History(), world.market, ["NEW-USD"], granularity=900, days=3,  # type: ignore[arg-type]
                       now=at, clock=world.clock)
    assert calls[0][3] == at.replace(minute=45, second=0, microsecond=0)


async def test_rotating_bootstrap_does_not_finish_daily_baselines_against_only_its_first_batch(world: World) -> None:
    from desk.rh.contracts import Pair
    world.market.save_pairs([Pair(f"NEW{i}-USD", f"NEW{i}", "USD", Decimal("0.01"), Decimal("0.01"),
                                  Decimal(10000), Decimal(1), "tradable", True) for i in range(8)], NOW)
    assert world.desk.session_ready(NOW) is None
    class MissingHistory:
        async def candles(self, *_: object) -> list[object]:
            return []
    world.suite.coinbase = MissingHistory()  # type: ignore[assignment]
    await world.desk.sync_history()
    assert world.desk.session_ready(NOW) is None          # more assets remain beyond the first batch
    await world.desk.sync_history()
    assert world.desk.session_ready(NOW) == NOW.date()    # attempted missing product is an explicit exclusion
