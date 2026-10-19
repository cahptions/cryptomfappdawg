"""Small, fixed-command process supervisor for the local dashboard."""

from __future__ import annotations

import contextlib
import math
import os
import sqlite3
import subprocess
import sys
import threading
from collections import deque
from typing import Any

from desk.core.clock import TimestampError, parse_iso8601, utc_now
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.paper.ledger import PaperLedger
from desk.storage.databases import Databases


class ControlError(ValueError):
    pass


class WorkerManager:
    """Only named research commands are accepted; clients cannot supply argv or paths."""

    def __init__(self, paths: DeskPaths) -> None:
        self.paths = paths
        self._lock = threading.Lock()
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._jobs: deque[dict[str, Any]] = deque(maxlen=20)

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(DESK_HOME=str(self.paths.home), DESK_CONFIG_DIR=str(self.paths.config_dir),
                           DESK_SECRETS_FILE=str(self.paths.secrets_file))
        return environment

    def _fresh_worker(self) -> bool:
        """Check the persisted heartbeat as well as processes started by this dashboard."""
        database = self.paths.db_dir / "runtime.db"
        if not database.exists():
            return False
        try:
            with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2)) as conn:
                rows = conn.execute("SELECT process, at FROM heartbeats WHERE process != 'guardian'").fetchall()
            # A future timestamp can reflect a wall-clock correction, not an idle worker.
            return any((utc_now() - parse_iso8601(row[1])).total_seconds() <= 90 for row in rows)
        except (sqlite3.Error, TimestampError) as exc:
            raise ControlError("cannot verify existing workers; check desk status in the terminal") from exc

    def _paper_halted(self) -> bool:
        database = self.paths.db_dir / "ledger-paper.db"
        if not database.exists():
            return False
        try:
            with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2)) as conn:
                conn.row_factory = sqlite3.Row
                return PaperLedger(conn).halted() is not None
        except sqlite3.Error as exc:
            raise ControlError("cannot verify PAPER halt state; check desk status in the terminal") from exc

    def _refresh_jobs(self) -> None:
        for job in self._jobs:
            child = self._children.get(job["name"])
            if child and child.pid == job["pid"]:
                job["returncode"] = child.poll()

    def start(self, kind: str, minutes: float = 4320) -> dict[str, Any]:
        if not isinstance(kind, str) or kind not in {"paper", "shadow", "report"}:
            raise ControlError("unsupported worker")
        if isinstance(minutes, bool) or not isinstance(minutes, int | float) or not 0 < minutes <= 10080 \
                or not math.isfinite(minutes):
            raise ControlError("duration must be between zero and 10080 minutes")
        with self._lock:
            self._refresh_jobs()
            if kind in self._children and self._children[kind].poll() is None:
                raise ControlError(f"{kind} is already running")
            if kind != "report":
                if self.paths.stop_file.exists() or (kind == "paper" and self._paper_halted()):
                    raise ControlError("entries are stopped; use desk resume in the terminal first")
                if any(p.poll() is None for name, p in self._children.items() if name != "report") \
                        or WorkerLock(self.paths).held() or self._fresh_worker():
                    raise ControlError("an entry or collection worker is already active")
            command = [sys.executable, "-m", "desk"]
            command += ["confidence", "--json"] if kind == "report" else [kind, "--minutes", str(minutes)]
            self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
            output_path = self.paths.logs_dir / f"dashboard-{kind}.log"
            descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "ab") as output:
                process = subprocess.Popen(  # noqa: S603 - argv is fixed above, never supplied by the client
                    command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    env=self._environment(), start_new_session=True, shell=False, close_fds=True,
                )
            self._children[kind] = process
            job = {"name": kind, "pid": process.pid, "started_at": utc_now().isoformat(), "returncode": None}
            self._jobs.append(job)
            return dict(job)

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_jobs()
            return [dict(job) for job in self._jobs]

    def stop_entries(self) -> dict[str, Any]:
        # STOP is deliberately written first. Neither this action nor dashboard exit kills the guardian.
        self.paths.home.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.paths.stop_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(f"stopped {utc_now().isoformat()}\n")
            output.flush()
            os.fsync(output.fileno())
        dbs = Databases.open(self.paths)
        try:
            PaperLedger(dbs.paper_ledger).halt("STOP", utc_now())
        finally:
            dbs.close()
        return {"halted": True, "reason": "STOP", "guardian": "continues supervising open positions"}
