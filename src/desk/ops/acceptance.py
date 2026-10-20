"""Evaluate recorded M2 run evidence without confusing a smoke test with the 24-hour requirement."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from desk.core.clock import parse_iso8601


def discovery_run_failures(run: dict[str, Any] | None, required_hours: float = 24) -> list[str]:
    if run is None:
        return ["No collection run is recorded"]
    if run.get("ended_at") is None:
        return ["The run is still active or did not finish cleanly"]
    failures = []
    stats = run.get("stats") or {}
    duration = stats.get("elapsed_monotonic_seconds")
    if duration is None:
        duration = (parse_iso8601(run["ended_at"]) - parse_iso8601(run["started_at"])).total_seconds()
    if not isinstance(duration, int | float) or not math.isfinite(duration) or duration < 0:
        return ["Run duration is not a valid finite measurement"]
    if duration < required_hours * 3600:
        failures.append(f"Run duration {duration / 3600:.4f} hours is below {required_hours:g} hours")
    if stats.get("completed_requested_duration") is not True:
        failures.append("Completion of the requested collection duration is not verified")
    discovery = stats.get("discovery")
    if discovery is None:
        failures.append("This is not an M2 discovery run")
    elif discovery.get("orphans") != 0:
        failures.append("Candidate audit integrity is not verified")
    if stats.get("unhandled_exceptions") != 0:
        failures.append("Unhandled exceptions were recorded or not measured")
    if stats.get("suspend_gaps") != 0:
        failures.append("Suspend gaps were recorded or not measured")
    for job in ("geckoterminal_new_pools", "jupiter_recent", "dexscreener_refresh", "discovery_evaluation"):
        task = stats.get("tasks", {}).get(job, {})
        if task.get("scheduled", 0) <= 0 or task.get("succeeded", 0) <= 0:
            failures.append(f"No successful scheduled work recorded for {job}")
    if stats.get("peak_rss_mb") is None:
        failures.append("Peak memory was not measured")
    return failures


# ── segmented 24-hour test (operator decision 2026-10-20) ────────────────────────────────────────
# The 24 hours may be collected in pieces so the laptop can be switched off: every discovery run that ENDED
# CLEANLY counts with its measured active time (monotonic, so sleep is excluded), whether it reached its planned
# 4 hours or was stopped by a shutdown. A crash or power loss loses only that piece. At least one piece must run
# the full 4 hours, and only pieces from the last 14 days count, so the evidence stays recent and still shows
# hours of uninterrupted operation.
SEGMENT_HOURS = 4
SEGMENT_WINDOW_DAYS = 14
REQUIRED_JOBS = ("geckoterminal_new_pools", "jupiter_recent", "dexscreener_refresh", "discovery_evaluation")


def _active_seconds(run: dict[str, Any]) -> float | None:
    stats = run.get("stats") or {}
    duration = stats.get("elapsed_monotonic_seconds")
    if duration is None and run.get("ended_at"):
        duration = (parse_iso8601(run["ended_at"]) - parse_iso8601(run["started_at"])).total_seconds()
    if not isinstance(duration, int | float) or not math.isfinite(duration) or duration < 0:
        return None
    return float(duration)


def segment_problems(run: dict[str, Any]) -> list[str]:
    """Why a finished run cannot count as a segment (sleep is allowed: active time excludes it)."""
    stats = run.get("stats") or {}
    problems = []
    if stats.get("discovery") is None:
        return ["not a discovery run"]
    if _active_seconds(run) is None:
        problems.append("run time not measured")
    if stats.get("unhandled_exceptions") != 0:
        problems.append("unhandled exceptions")
    if (stats.get("discovery") or {}).get("orphans") != 0:
        problems.append("candidate audit integrity not verified")
    for job in REQUIRED_JOBS:
        task = stats.get("tasks", {}).get(job, {})
        if task.get("scheduled", 0) <= 0 or task.get("succeeded", 0) <= 0:
            problems.append(f"no successful {job}")
    if stats.get("peak_rss_mb") is None:
        problems.append("peak memory not measured")
    return problems


def segmented_progress(runs: list[dict[str, Any]], now: datetime, *, running_run_id: str | None = None,
                       required_hours: float = 24, segment_hours: float = SEGMENT_HOURS,
                       window_days: int = SEGMENT_WINDOW_DAYS) -> dict[str, Any]:
    """Progress of the segmented test from recorded runs (newest first in the result)."""
    since = now - timedelta(days=window_days)
    segments = []
    for run in sorted(runs, key=lambda r: r["started_at"], reverse=True):
        started = parse_iso8601(run["started_at"])
        if started < since:
            continue
        if run.get("ended_at") is None:
            state = "RUNNING" if run["id"] == running_run_id else "INTERRUPTED"
            problems = [] if state == "RUNNING" else ["did not finish (crash, power loss or forced stop)"]
            hours = (now - started).total_seconds() / 3600 if state == "RUNNING" else None
        else:
            problems = segment_problems(run)
            if problems == ["not a discovery run"]:
                continue
            seconds = _active_seconds(run)
            hours = None if seconds is None else seconds / 3600
            state = "COUNTED" if not problems else "NOT_COUNTED"
        segments.append({"run_id": run["id"], "started_at": run["started_at"], "ended_at": run.get("ended_at"),
                         "hours": None if hours is None else round(hours, 3), "state": state, "problems": problems,
                         "sleep_gaps": (run.get("stats") or {}).get("suspend_gaps", 0)})
    counted = [s for s in segments if s["state"] == "COUNTED"]
    total = sum(s["hours"] or 0 for s in counted)
    longest = max((s["hours"] or 0 for s in counted), default=0.0)
    failures = []
    if total < required_hours:
        failures.append(f"{total:.2f} of {required_hours:g} hours counted")
    if longest < segment_hours:
        failures.append(f"no counted piece has run {segment_hours:g} hours yet (longest {longest:.2f} h)")
    return {"passed": not failures, "counted_hours": round(total, 3), "required_hours": required_hours,
            "longest_segment_hours": round(longest, 3), "segment_hours": segment_hours, "window_days": window_days,
            "segments": segments, "failures": failures,
            "rule": f"{required_hours:g} h of cleanly finished discovery runs within {window_days} days, at least one "
                    f"lasting {segment_hours:g} h; sleep does not count; a crash loses only its own piece"}


def discovery_test(market: Any, runtime: Any, now: datetime) -> dict[str, Any]:
    """Load the window's runs (market.db) and the collector heartbeat (runtime.db) and evaluate the test."""
    import json

    from desk.core.clock import to_iso

    rows = market.execute("SELECT id, started_at, ended_at, stats_json FROM collector_runs WHERE started_at >= ?",
                          (to_iso(now - timedelta(days=SEGMENT_WINDOW_DAYS + 1)),)).fetchall()
    runs = [{"id": r["id"], "started_at": r["started_at"], "ended_at": r["ended_at"],
             "stats": json.loads(r["stats_json"] or "{}")} for r in rows]
    beat = runtime.execute("SELECT at FROM heartbeats WHERE process = 'collector'").fetchone()
    alive = beat is not None and 0 <= (now - parse_iso8601(beat["at"])).total_seconds() <= 90
    unfinished = sorted((r for r in runs if r["ended_at"] is None), key=lambda r: r["started_at"])
    return segmented_progress(runs, now, running_run_id=unfinished[-1]["id"] if alive and unfinished else None)
