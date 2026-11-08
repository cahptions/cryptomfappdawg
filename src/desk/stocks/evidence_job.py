"""One local, cancellable evaluation process. No network, orders or adoption of learned weights.

It also runs by itself once a week (``weekly``) while collection is on, so the Evidence page always has a recent
result; the weights it proposes are shown and never adopted: adopting them stays the user's explicit choice."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks.connection import _atomic_private


def document(paths: DeskPaths, name: str, *, kind: str = "evidence") -> dict[str, Any] | None:
    if name not in {"state", "report"} or kind not in {"evidence", "backtest"}:
        raise ValueError("unknown evidence document")
    path = paths.home / f"stocks-{kind}-{name}.json"
    try:
        if path.stat().st_size > 4_000_000:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


WEEK = timedelta(days=7)
RETRY = timedelta(hours=6)
MIN_SESSIONS = 60            # the analysis' own minimum history


def schedule(paths: DeskPaths, now: datetime) -> dict[str, Any]:
    """When the weekly evaluation last finished and is next due (now, when it never has)."""
    report = document(paths, "report")
    last = report.get("generated_at") if report else None
    due = datetime.fromisoformat(last) + WEEK if last else now
    return {"weekly": True, "last": last, "next": due.isoformat(), "due": now >= due}


def publish(paths: DeskPaths, *, kind: str = "evidence", **value: Any) -> None:
    if kind not in {"evidence", "backtest"}:
        raise ValueError("unknown evaluation kind")
    _atomic_private(paths.home / f"stocks-{kind}-state.json", json.dumps({
        **value, "updated_at": utc_now().isoformat(), "advisory_only": True}, allow_nan=False))


class EvidenceWorker:
    kind = "evidence"
    module = "desk.stocks.evidence_job"

    def __init__(self, paths: DeskPaths) -> None:
        self.paths = paths
        self.process: subprocess.Popen[bytes] | None = None
        self.lock = threading.RLock()
        self.stopping = False

    def status(self) -> dict[str, Any]:
        with self.lock:
            state = document(self.paths, "state", kind=self.kind) or {"status": "idle", "phase": "Ready when you are"}
            running = self.process is not None and self.process.poll() is None
            external = not running and WorkerLock(self.paths, f"stocks-{self.kind}").held()
            if not running and not external and state.get("status") in {"running", "stopping"}:
                state = {"status": "cancelled" if self.stopping else "interrupted",
                         "phase": "Evaluation stopped. Completed comparisons stay saved; you can run it again."}
            return {**state, "running": running or external, "owned": running, "stopping": running and self.stopping}

    def start(self) -> dict[str, Any]:
        try:
            return self._start()
        except OSError as exc:
            raise DeskError("Could not start the evaluation. Check Data health for storage problems.") from exc

    def _start(self) -> dict[str, Any]:
        with self.lock:
            if self.module not in {"desk.stocks.evidence_job", "desk.stocks.backtest_job"}:
                raise ValueError("Unsupported local evaluation module")
            if self.status()["running"]:
                raise DeskError("An evaluation is already running. Its progress is shown here.")
            self.paths.ensure()
            publish(self.paths, kind=self.kind, status="running", phase="Preparing saved market data", completed=0, total=0)
            path = self.paths.logs_dir / f"stocks-{self.kind}.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix(".previous.log"))
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "ab") as log:
                    self.process = subprocess.Popen(  # noqa: S603 -- fixed allowlisted module, no browser-supplied argv
                        [sys.executable, "-m", self.module], cwd=REPO_ROOT,
                        env={**os.environ, "DESK_HOME": str(self.paths.home), "DESK_CONFIG_DIR": str(self.paths.config_dir),
                             "DESK_SECRETS_FILE": str(self.paths.secrets_file), "OPENBLAS_NUM_THREADS": "1",
                             "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", **self.extra_env()},
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            except OSError as exc:
                publish(self.paths, kind=self.kind, status="failed",
                        phase="Could not start. Check Data health for storage problems.")
                raise DeskError("Could not start the evaluation; collection is unaffected.") from exc
            self.stopping = False
            return self.status()

    def extra_env(self) -> dict[str, str]:
        return {}

    def sessions(self) -> int:
        """Stored closing sessions (the evaluation needs prices; an empty data folder waits for them)."""
        import sqlite3

        from desk.stocks.repo import MARKET_FILE

        path = self.paths.db_dir / MARKET_FILE
        if not path.exists():
            return 0
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM stk_bar_days WHERE rows>0").fetchone()[0])
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def weekly(self, now: datetime | None = None) -> bool:
        """Start the evaluation when a week has passed since the last one finished (not after a failure within six
        hours, and never beside a running evaluation or replay). Returns whether it started."""
        now = now or utc_now()
        with self.lock:
            state = self.status()
            attempted = state.get("updated_at") if state.get("status") in {"failed", "interrupted"} else None
            if (state["running"] or WorkerLock(self.paths, "stocks-backtest").held()
                    or not schedule(self.paths, now)["due"] or self.sessions() < MIN_SESSIONS
                    or (attempted and now - datetime.fromisoformat(attempted) < RETRY)):
                return False
            self.start()
            publish(self.paths, status="running", phase="Weekly evaluation: preparing saved market data",
                    completed=0, total=0, automatic=True)
            return True

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.stopping = True
                with suppress(ProcessLookupError):
                    self.process.terminate()  # exact owned child only; no signals from a PID file
                publish(self.paths, kind=self.kind, status="stopping",
                        phase="Stopping evaluation; collected observations are preserved")
            return self.status()

    def close(self) -> None:
        self.cancel()
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def evaluate(paths: DeskPaths) -> None:
    from desk.stocks.analysis import Analyzer
    from desk.stocks.prices import load_panel
    from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch
    from desk.stocks.runner import _memory_guard
    from desk.stocks.validation import evaluation_window, rebuild, report

    with WorkerLock(paths, "stocks-evidence"):
        try:
            _memory_guard()
            loaded, now = load_config(paths.config_dir), utc_now()
            dbs = StocksDatabases.open(paths)
            try:
                market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
                days = market.bar_days()
                if not days:
                    raise DeskError("No closing prices are saved yet. Connect stock history in Settings and resume collection.")
                since, limited = evaluation_window(market, loaded.settings.stocks)
                _memory_guard()
                panel = load_panel(market, since, days[-1])
                publish(paths, status="running", phase="Building historical comparisons from saved observations",
                        completed=0, total=0)

                def progress(day: Any, done: int, total: int) -> None:
                    _memory_guard()
                    publish(paths, status="running", phase=f"Checked historical month ending {day}", completed=done, total=total)

                rebuild(Analyzer(market, research, loaded.settings.stocks), panel, now=now, progress=progress)
                publish(paths, status="running", phase="Comparing research lists, baselines and uncertainty",
                        completed=0, total=0)
                result = report(market, research, panel, today=days[-1])
                result.update(advisory_only=True, generated_at=utc_now().isoformat(),
                              note="Evaluation only. Proposed model weights are not adopted by this action.")
                result["prices"]["memory_note"] = limited
                # Separate from stk_validation: viewing evidence must not change Analyzer.weights().
                _atomic_private(paths.home / "stocks-evidence-report.json", json.dumps(result, allow_nan=False))
                publish(paths, status="completed", phase="Evaluation complete", completed=1, total=1)
            finally:
                dbs.close()
        except DeskError as exc:
            publish(paths, status="failed", phase=str(exc))
        except Exception:
            publish(paths, status="failed", phase="Evaluation could not finish. Run Check data health here, then retry.")
            raise


if __name__ == "__main__":
    os.nice(10)
    evaluate(DeskPaths.from_env())
