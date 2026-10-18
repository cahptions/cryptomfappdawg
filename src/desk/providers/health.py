"""Provider health state machine (OK → DEGRADED → DOWN → OK), persisted in ``runtime.db``.

* ``degraded_after_failures`` consecutive failures: OK → DEGRADED
* ``down_after_failures`` consecutive failures: → DOWN
* ``recover_after_successes`` consecutive successes: DEGRADED/DOWN → OK
Every transition is recorded with its time and reason so the dashboard can explain it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from desk.core.clock import to_iso, utc_now
from desk.core.config import HealthSection


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class HealthRecord:
    provider: str
    state: HealthState
    consecutive_failures: int
    consecutive_successes: int
    last_success_at: str | None
    last_failure_at: str | None
    last_error: str | None


class HealthTracker:
    def __init__(self, conn: sqlite3.Connection, settings: HealthSection,
                 clock: Callable[[], datetime] = utc_now) -> None:
        self.conn = conn
        self.settings = settings
        self.clock = clock

    def get(self, provider: str) -> HealthRecord:
        row = self.conn.execute("SELECT * FROM provider_health WHERE provider = ?", (provider,)).fetchone()
        if row is None:
            return HealthRecord(provider, HealthState.OK, 0, 0, None, None, None)
        return HealthRecord(row["provider"], HealthState(row["state"]), row["consecutive_failures"],
                            row["consecutive_successes"], row["last_success_at"], row["last_failure_at"],
                            row["last_error"])

    def record_success(self, provider: str) -> HealthState:
        rec = self.get(provider)
        successes = rec.consecutive_successes + 1
        state = rec.state
        if state is not HealthState.OK and successes >= self.settings.recover_after_successes:
            state = HealthState.OK
        now = to_iso(self.clock())
        self._save(provider, state, 0, successes, now, rec.last_failure_at, rec.last_error)
        if state is not rec.state:
            self._transition(provider, rec.state, state, f"{successes} consecutive successes")
        return state

    def record_failure(self, provider: str, kind: str, message: str) -> HealthState:
        rec = self.get(provider)
        failures = rec.consecutive_failures + 1
        state = rec.state
        if failures >= self.settings.down_after_failures:
            state = HealthState.DOWN
        elif failures >= self.settings.degraded_after_failures and state is HealthState.OK:
            state = HealthState.DEGRADED
        now = to_iso(self.clock())
        error = f"{kind}: {message}"[:500]
        self._save(provider, state, failures, 0, rec.last_success_at, now, error)
        if state is not rec.state:
            self._transition(provider, rec.state, state, f"{failures} consecutive failures; last: {error}")
        return state

    def all(self) -> list[HealthRecord]:
        names = [row["provider"] for row in self.conn.execute("SELECT provider FROM provider_health ORDER BY provider")]
        return [self.get(name) for name in names]

    def transitions(self, provider: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT from_state, to_state, at, reason FROM provider_health_events WHERE provider = ? ORDER BY id",
            (provider,),
        ).fetchall()

    def _save(self, provider: str, state: HealthState, failures: int, successes: int, last_ok: str | None,
              last_fail: str | None, last_error: str | None) -> None:
        self.conn.execute(
            "INSERT INTO provider_health(provider, state, consecutive_failures, consecutive_successes, last_success_at,"
            " last_failure_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET state = excluded.state,"
            " consecutive_failures = excluded.consecutive_failures,"
            " consecutive_successes = excluded.consecutive_successes, last_success_at = excluded.last_success_at,"
            " last_failure_at = excluded.last_failure_at, last_error = excluded.last_error,"
            " updated_at = excluded.updated_at",
            (provider, state.value, failures, successes, last_ok, last_fail, last_error, to_iso(self.clock())),
        )

    def _transition(self, provider: str, old: HealthState, new: HealthState, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO provider_health_events(provider, from_state, to_state, at, reason) VALUES (?, ?, ?, ?, ?)",
            (provider, old.value, new.value, to_iso(self.clock()), reason),
        )
