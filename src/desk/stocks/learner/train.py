"""The Learner's weekly retraining, on the evidence worker's cadence (docs/STOCKS_LEARNER.md → How it learns).

``Trainer.weekly`` runs from the app's maintenance loop beside ``EvidenceWorker.weekly``, under the same guards (never
beside a running evaluation or replay): it starts one niced, ``model.THREADS``-thread subprocess
(``python -m desk.stocks.learner.train``) when a week has passed since the last registered model, or when none exists
yet and enough labels do; after a failure it waits six hours. The subprocess trains, freezes and registers a model and
then refreshes the shadow view. Nothing it produces is adopted by any trading: the model is shown in shadow only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from typing import Any

from desk.core.clock import utc_now
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.worker_lock import WorkerBusy, WorkerLock
from desk.stocks.connection import _atomic_private

WEEK = timedelta(days=7)
RETRY = timedelta(hours=6)
LOCK = "stocks-learner"
STATE = "stocks-learner-train.json"


def state(paths: DeskPaths) -> dict[str, Any]:
    try:
        value = json.loads((paths.home / STATE).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def publish(paths: DeskPaths, **value: Any) -> None:
    _atomic_private(paths.home / STATE, json.dumps({**value, "updated_at": utc_now().isoformat()}, allow_nan=False))


def _registry(paths: DeskPaths) -> tuple[str | None, int]:
    """(when the newest model of the current code was trained, labelled candidate groups)."""
    from desk.stocks.learner import model
    from desk.stocks.repo import RESEARCH_FILE

    path = paths.db_dir / RESEARCH_FILE
    if not path.exists():
        return None, 0
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        conn.row_factory = sqlite3.Row
        row = model.latest(conn)
        groups = conn.execute("SELECT COUNT(DISTINCT source || session || symbol) FROM stk_learn_labels").fetchone()[0]
        return (None if row is None else str(row["trained_at"])), int(groups or 0)
    except sqlite3.Error:
        return None, 0
    finally:
        conn.close()


MIN_GROUPS = 40                    # labelled stock-sessions before the first model is worth training


def due(paths: DeskPaths, now: datetime) -> bool:
    last, groups = _registry(paths)
    if groups < MIN_GROUPS:
        return False
    s = state(paths)
    if s.get("status") == "failed" and s.get("updated_at") and now - datetime.fromisoformat(s["updated_at"]) < RETRY:
        return False
    return last is None or now - datetime.fromisoformat(last) >= WEEK


class Trainer:
    def __init__(self, paths: DeskPaths) -> None:
        self.paths = paths
        self.process: subprocess.Popen[bytes] | None = None
        self.lock = threading.RLock()

    def running(self) -> bool:
        with self.lock:
            return (self.process is not None and self.process.poll() is None) or WorkerLock(self.paths, LOCK).held()

    def start(self) -> bool:
        with self.lock:
            if self.running():
                return False
            from desk.stocks.learner.model import THREADS

            self.paths.ensure()
            log = self.paths.logs_dir / "stocks-learner.log"
            if log.exists() and log.stat().st_size > 2_000_000:
                log.replace(log.with_suffix(".previous.log"))
            descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            threads = str(THREADS)
            with os.fdopen(descriptor, "ab") as out:
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "desk.stocks.learner.train"], cwd=REPO_ROOT,
                    env={**os.environ, "DESK_HOME": str(self.paths.home), "DESK_CONFIG_DIR": str(self.paths.config_dir),
                         "DESK_SECRETS_FILE": str(self.paths.secrets_file), "OMP_NUM_THREADS": threads,
                         "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
                    stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
            publish(self.paths, status="running", phase="Training the Learner (shadow)")
            return True

    def weekly(self, now: datetime | None = None) -> bool:
        """Start a training when one is due (see the module docstring). Returns whether it started."""
        from desk.stocks.evidence_job import document

        now = now or utc_now()
        evidence = document(self.paths, "state") or {}
        if (self.running() or evidence.get("status") == "running" or WorkerLock(self.paths, "stocks-backtest").held()
                or not due(self.paths, now)):
            return False
        return self.start()

    def close(self) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()


def main(paths: DeskPaths) -> dict[str, Any]:
    """Train, freeze and register; then refresh the shadow view. Run in the subprocess (or ``desk stocks
    learner-train``)."""
    from desk.stocks.learner import model, view
    from desk.stocks.repo import StocksDatabases

    try:
        with WorkerLock(paths, LOCK):
            dbs = StocksDatabases.open(paths)
            try:
                now = utc_now()

                def progress(text: str) -> None:
                    publish(paths, status="running", phase=f"Training the Learner (shadow): {text}")

                result = model.train(dbs.research, paths.home, now, progress=progress)
                if result.get("trained"):
                    view.refresh(paths, dbs, utc_now(), force=True)
                publish(paths, status="completed" if result.get("trained") else "waiting", phase=result.get(
                    "reason") or "Trained", result=result)
                return result
            finally:
                dbs.close()
    except WorkerBusy:
        return {"trained": False, "reason": "a training is already running"}
    except Exception as exc:
        publish(paths, status="failed", phase=f"Training failed ({type(exc).__name__}); retried in six hours")
        raise


if __name__ == "__main__":
    os.nice(10)
    print(json.dumps(main(DeskPaths.from_env()), default=str))
