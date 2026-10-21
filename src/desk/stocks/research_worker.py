"""Explicit dashboard-owned research process. Never starts on GET or controls a terminal-owned worker."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from typing import Any

from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.worker_lock import WorkerLock


class ResearchWorker:
    def __init__(self, paths: DeskPaths) -> None:
        self.paths = paths
        self.process: subprocess.Popen[bytes] | None = None
        self.stopping = False
        self.lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self.lock:
            code = self.process.poll() if self.process is not None else None
            owned = self.process is not None and code is None
            return {"owned": owned, "running": owned or WorkerLock(self.paths, "stocks-worker").held(),
                    "stopping": owned and self.stopping, "exit_code": code}

    def start(self) -> dict[str, Any]:
        with self.lock:
            if self.status()["running"]:
                raise DeskError("A Stocks worker is already running. Use its existing collection; do not start a second one.")
            self.paths.ensure()
            path = self.paths.logs_dir / "stocks-research.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix(".previous.log"))
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            env = {**os.environ, "DESK_HOME": str(self.paths.home), "DESK_CONFIG_DIR": str(self.paths.config_dir),
                   "DESK_SECRETS_FILE": str(self.paths.secrets_file), "PYTHONUNBUFFERED": "1"}
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "ab") as log:
                    self.process = subprocess.Popen(  # fixed argv; credentials never enter the command
                        [sys.executable, "-m", "desk", "stocks", "collect", "--minutes", "1440"],
                        cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            except OSError as exc:
                raise DeskError("Could not start research collection. Check the laptop's available disk space.") from exc
            self.stopping = False
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if self.process is not None and self.process.poll() is None and not self.stopping:
                # Only this exact Popen child: no PID-file lookup and no signalling an unrelated terminal worker.
                with suppress(ProcessLookupError):
                    self.process.send_signal(signal.SIGTERM)
                self.stopping = True
            return self.status()
