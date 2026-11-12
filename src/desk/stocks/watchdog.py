"""Is Stocks keeping up? A small, read-only check the always-on app runs every few minutes (about once a worker
pass) and the worker runs at the end of each pass.

It looks for three things and nothing else:

- **worker stopped or stuck**: collection and PAPER are switched on (Start PAPER) but the worker has not been running
  for 15 minutes, or it is running but has not moved on to a new step for 90 minutes;
- **a job failing**: a collection job (prices, options, filings...) has been failing for longer than ``job_hours``
  (6 by default), counted from when this check first saw it fail, so a check that starts late never overstates it;
- **research late**: the daily research for the last closed session is not finished ``research_minutes`` (180 by
  default) after that session's close.

What it finds is saved to ``stocks-watchdog.json`` (Evidence → Data health shows it, ``kestrel`` prints it) and, when
phone alerts are on, sent (desk.stocks.alerts). Error texts are never copied: a job's failure is described by an
allowlisted category, because a provider's error can echo a key.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks import calendar as cal
from desk.stocks.repo import MARKET_FILE, RESEARCH_FILE, date_of

WORKER_GRACE = timedelta(minutes=15)
STALLED = timedelta(minutes=90)
JOB_HOURS = 6
RESEARCH_MINUTES = 180
STATE_FILE = "stocks-watchdog.json"


@dataclass(frozen=True)
class Issue:
    id: str            # stable while the same problem lasts: worker_stopped, job:prices, research:2026-11-11
    kind: str          # worker | job | research
    text: str          # one plain sentence; never an error message from a provider
    since: str         # ISO time the problem started (or was first seen)


def clock(stamp: datetime | str) -> str:
    when = cal.new_york(datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp)
    return f"{when:%a %b} {when.day}, {when:%I:%M %p}".replace(" 0", " ") + " ET"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 1_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _readonly(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def observe(paths: DeskPaths, now: datetime, first_seen: dict[str, str], *, job_hours: float = JOB_HOURS,
            research_minutes: int = RESEARCH_MINUTES,
            worker_running: bool | None = None) -> tuple[list[Issue], dict[str, str]]:
    """The problems past their thresholds now, and the updated first-seen times of everything observed."""
    from desk.stocks.diagnostics import error_category

    seen: dict[str, str] = {}
    issues: list[Issue] = []

    def started(ident: str) -> datetime:
        stamp = first_seen.get(ident) or now.isoformat()
        seen[ident] = stamp
        return datetime.fromisoformat(stamp)

    automation = _read_json(paths.home / "stocks-automation.json")
    enabled = automation.get("enabled", True) is not False
    running = WorkerLock(paths, "stocks-worker").held() if worker_running is None else worker_running
    if enabled and not running:
        since = started("worker_stopped")
        if now - since >= WORKER_GRACE:
            issues.append(Issue("worker_stopped", "worker", f"The Stocks worker has been stopped since {clock(since)}: "
                                "no collection or PAPER. Run kestrel to see why, or kestrel restart.",
                                since.isoformat()))
    elif running:
        progress = _read_json(paths.home / "stocks-progress.json")
        try:
            updated = datetime.fromisoformat(str(progress["updated_at"]))
        except (KeyError, ValueError):
            updated = None
        if updated is not None and now - updated >= STALLED:
            phase = str(progress.get("phase", ""))[:40].replace("_", " ")
            issues.append(Issue("worker_stalled", "worker", f"The Stocks worker has not moved on since {clock(updated)} "
                                f"(step: {phase or 'unknown'}). kestrel restart starts it again.",
                                updated.isoformat()))
    market = _readonly(paths.db_dir / MARKET_FILE)
    if market is not None:
        with closing(market):
            try:
                rows = market.execute("SELECT name, last_ok_at, last_error FROM stk_jobs WHERE last_error IS NOT NULL "
                                      "LIMIT 100").fetchall()
            except sqlite3.Error:
                rows = []
        for row in rows:
            name = str(row["name"])
            if not name.replace("_", "").isalnum():
                continue
            since = started(f"job:{name}")
            if now - since >= timedelta(hours=job_hours):
                hours = (now - since).total_seconds() / 3600
                last = f"last worked {clock(row['last_ok_at'])}" if row["last_ok_at"] else "has not worked yet"
                issues.append(Issue(f"job:{name}", "job", f"The {name.replace('_', ' ')} job has been failing for "
                                    f"{hours:.0f} hours ({error_category(str(row['last_error'])).replace('_', ' ')}; "
                                    f"{last}).", since.isoformat()))
    research = _readonly(paths.db_dir / RESEARCH_FILE)
    if research is not None:
        with closing(research):
            try:
                row = research.execute("SELECT MAX(asof) FROM stk_scores_latest").fetchone()
            except sqlite3.Error:
                row = None
        latest = date_of(row[0]) if row and row[0] is not None else None
        delay = timedelta(minutes=research_minutes)
        expected = cal.last_closed_session(now, delay)
        if latest is not None and latest < expected:      # nothing analysed at all is the warm-up, not lateness
            due = cal.session_close(expected) + delay
            ident = f"research:{expected.isoformat()}"
            seen[ident] = due.isoformat()
            issues.append(Issue(ident, "research", f"Research for {expected:%a %b} {expected.day}'s close is not "
                                f"finished (due {clock(due)}; the last analysed close is {latest:%a %b} {latest.day}).",
                                due.isoformat()))
    return issues, seen


def record(paths: DeskPaths, now: datetime, *, job_hours: float = JOB_HOURS, research_minutes: int = RESEARCH_MINUTES,
           worker_running: bool | None = None) -> list[Issue]:
    """Check, then save what was found (with when each problem was first seen) for the health page and ``kestrel``."""
    from desk.stocks.connection import _atomic_private

    path = paths.home / STATE_FILE
    previous = _read_json(path).get("first_seen")
    first_seen = {str(k): str(v) for k, v in previous.items()} if isinstance(previous, dict) else {}
    issues, seen = observe(paths, now, first_seen, job_hours=job_hours, research_minutes=research_minutes,
                           worker_running=worker_running)
    _atomic_private(path, json.dumps({"checked_at": now.isoformat(), "issues": [asdict(i) for i in issues],
                                      "first_seen": seen, "job_hours": job_hours,
                                      "research_minutes": research_minutes}))
    return issues


def saved(paths: DeskPaths) -> dict[str, Any] | None:
    """The last check, for Evidence → Data health; None before the first one."""
    value = _read_json(paths.home / STATE_FILE)
    if not value.get("checked_at"):
        return None
    issues = [{k: str(i.get(k, ""))[:300] for k in ("id", "kind", "text", "since")}
              for i in value.get("issues", []) if isinstance(i, dict)][:20]
    return {"checked_at": str(value["checked_at"]), "issues": issues}


__all__ = ["Issue", "observe", "record", "saved"]
