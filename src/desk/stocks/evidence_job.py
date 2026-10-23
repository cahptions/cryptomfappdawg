"""One local, cancellable evaluation process. No network, orders or adoption of learned weights."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import suppress
from datetime import timedelta
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks.connection import _atomic_private


def document(paths: DeskPaths, name: str) -> dict[str, Any] | None:
    if name not in {"state", "report"}:
        raise ValueError("unknown evidence document")
    path = paths.home / f"stocks-evidence-{name}.json"
    try:
        if path.stat().st_size > 4_000_000:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def publish(paths: DeskPaths, **value: Any) -> None:
    _atomic_private(paths.home / "stocks-evidence-state.json", json.dumps({
        **value, "updated_at": utc_now().isoformat(), "advisory_only": True}, allow_nan=False))


class EvidenceWorker:
    def __init__(self, paths: DeskPaths) -> None:
        self.paths = paths
        self.process: subprocess.Popen[bytes] | None = None
        self.lock = threading.RLock()
        self.stopping = False

    def status(self) -> dict[str, Any]:
        with self.lock:
            state = document(self.paths, "state") or {"status": "idle", "phase": "Ready when you are"}
            running = self.process is not None and self.process.poll() is None
            external = not running and WorkerLock(self.paths, "stocks-evidence").held()
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
            if self.status()["running"]:
                raise DeskError("An evaluation is already running. Its progress is shown here.")
            self.paths.ensure()
            publish(self.paths, status="running", phase="Preparing saved market data", completed=0, total=0)
            path = self.paths.logs_dir / "stocks-evidence.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix(".previous.log"))
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "ab") as log:
                    self.process = subprocess.Popen(
                        [sys.executable, "-m", "desk.stocks.evidence_job"], cwd=REPO_ROOT,
                        env={**os.environ, "DESK_HOME": str(self.paths.home), "DESK_CONFIG_DIR": str(self.paths.config_dir),
                             "DESK_SECRETS_FILE": str(self.paths.secrets_file), "OPENBLAS_NUM_THREADS": "1",
                             "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            except OSError as exc:
                publish(self.paths, status="failed", phase="Could not start. Check Data health for storage problems.")
                raise DeskError("Could not start the evaluation; collection is unaffected.") from exc
            self.stopping = False
            return self.status()

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.stopping = True
                with suppress(ProcessLookupError):
                    self.process.terminate()  # exact owned child only; no signals from a PID file
                publish(self.paths, status="stopping", phase="Stopping evaluation; collected observations are preserved")
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
    from desk.stocks.validation import rebuild, report

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
                panel = load_panel(market, days[-1] - timedelta(days=loaded.settings.stocks.history_days), days[-1])
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
