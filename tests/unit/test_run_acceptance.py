"""Recorded run acceptance must reject short, interrupted, or zero-work reports."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from desk.core.paths import DeskPaths
from desk.ops.acceptance import discovery_run_failures, segmented_progress
from desk.ops.launcher import desktop_entry, install_launchers


def complete_run():
    return {"started_at": "2026-10-18T00:00:00+00:00", "ended_at": "2026-10-19T00:00:00+00:00", "stats": {
        "discovery": {"orphans": 0}, "unhandled_exceptions": 0, "suspend_gaps": 0, "peak_rss_mb": 60,
        "completed_requested_duration": True,
        "tasks": {name: {"scheduled": 1440, "succeeded": 1440} for name in (
            "geckoterminal_new_pools", "jupiter_recent", "dexscreener_refresh", "discovery_evaluation")}}}


def test_complete_recorded_run_meets_duration_gate() -> None:
    assert discovery_run_failures(complete_run()) == []


def test_short_smoke_test_does_not_close_m2() -> None:
    run = complete_run()
    run["ended_at"] = "2026-10-18T00:02:00+00:00"
    assert any("below 24 hours" in reason for reason in discovery_run_failures(run))


def test_small_ntp_adjustment_does_not_shorten_measured_monotonic_runtime() -> None:
    run = complete_run()
    run["ended_at"] = "2026-10-18T23:59:59+00:00"
    run["stats"]["elapsed_monotonic_seconds"] = 86400.1
    assert discovery_run_failures(run) == []


@pytest.mark.parametrize("case", ["missing", "active", "not_m2", "exceptions", "orphans", "zero_work", "sleep"])
def test_incomplete_or_broken_evidence_is_rejected(case: str) -> None:
    run = deepcopy(complete_run())
    if case == "missing":
        run = None
    elif case == "active":
        run["ended_at"] = None
    elif case == "not_m2":
        run["stats"]["discovery"] = None
    elif case == "exceptions":
        run["stats"]["unhandled_exceptions"] = 1
    elif case == "orphans":
        run["stats"]["discovery"]["orphans"] = 1
    elif case == "zero_work":
        run["stats"]["tasks"] = {}
    elif case == "sleep":
        run["stats"]["suspend_gaps"] = 1
    assert discovery_run_failures(run)


# ── segmented test (operator decision 2026-10-20) ──────────────────────────────────────────────────
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def piece(run_id: str, start_hours_ago: float, hours: float | None, **stats: object) -> dict:
    started = NOW - timedelta(hours=start_hours_ago)
    run = deepcopy(complete_run())
    run.update(id=run_id, started_at=started.isoformat(),
               ended_at=None if hours is None else (started + timedelta(hours=hours)).isoformat())
    run["stats"]["completed_requested_duration"] = False
    run["stats"]["elapsed_monotonic_seconds"] = None if hours is None else hours * 3600
    run["stats"].update(stats)
    return run


def test_pieces_add_up_to_the_24_hour_test() -> None:
    runs = [piece(f"r{i}", 30 * (i + 1), 4) for i in range(5)] + [piece("short", 200, 3.5)]
    result = segmented_progress(runs, NOW)
    assert result["counted_hours"] == 23.5 and not result["passed"]
    result = segmented_progress([*runs, piece("last", 10, 0.5)], NOW)
    assert result["passed"] and result["counted_hours"] == 24.0 and result["longest_segment_hours"] == 4


def test_a_crash_loses_only_its_own_piece_and_sleep_is_not_counted() -> None:
    runs = [piece("ok", 50, 4), piece("crash", 40, None), piece("slept", 30, 2.5, suspend_gaps=1)]
    result = segmented_progress(runs, NOW)
    states = {s["run_id"]: s["state"] for s in result["segments"]}
    assert states == {"ok": "COUNTED", "crash": "INTERRUPTED", "slept": "COUNTED"}
    assert result["counted_hours"] == 6.5                         # active (monotonic) time only
    running = segmented_progress([*runs, piece("now", 1, None)], NOW, running_run_id="now")
    assert {s["run_id"]: s["state"] for s in running["segments"]}["now"] == "RUNNING"
    assert running["counted_hours"] == 6.5                       # a running piece counts once it ends cleanly


def test_broken_old_or_short_pieces_cannot_pass() -> None:
    assert not segmented_progress([piece(f"r{i}", 10 * (i + 1), 3.9) for i in range(8)], NOW)["passed"]  # no 4 h piece
    old = segmented_progress([piece(f"r{i}", 24 * 15 + i * 5, 4) for i in range(7)], NOW)
    assert old["counted_hours"] == 0                             # older than the 14-day window
    bad = segmented_progress([piece("x", 10, 4, unhandled_exceptions=2)], NOW)
    assert bad["segments"][0]["state"] == "NOT_COUNTED" and "unhandled exceptions" in bad["segments"][0]["problems"]
    shadow = segmented_progress([piece("s", 10, 4, discovery=None)], NOW)
    assert shadow["segments"] == []                              # not a discovery run: not part of the test


def test_launchers_open_the_dashboards_without_a_terminal(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "desk data", tmp_path / "config", tmp_path / "secrets.env")
    written = install_launchers(paths, "/home/neo/kestrel/.venv/bin/python", data_home=tmp_path / "share")
    names = sorted(p.name for p in written)
    assert names == ["kestrel-crypto.desktop", "kestrel-crypto.svg", "kestrel.desktop", "kestrel.svg"]
    entry = (tmp_path / "share" / "applications" / "kestrel-crypto.desktop").read_text()
    assert "Terminal=false" in entry and "-m desk open --desk robinhood --port 8766" in entry
    assert f'"DESK_HOME={tmp_path / "desk data"}"' in entry          # spaces quoted the Desktop Entry way
    with pytest.raises(ValueError, match="launcher"):
        desktop_entry(DeskPaths(Path("/x/$HOME"), tmp_path, tmp_path / "s.env"), "/usr/bin/python3", "solana", 8765)
