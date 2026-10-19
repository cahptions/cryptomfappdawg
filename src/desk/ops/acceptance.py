"""Evaluate recorded M2 run evidence without confusing a smoke test with the 24-hour requirement."""

from __future__ import annotations

import math
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
