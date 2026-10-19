"""Recorded run acceptance must reject short, interrupted, or zero-work reports."""

from __future__ import annotations

from copy import deepcopy

import pytest

from desk.ops.acceptance import discovery_run_failures


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
