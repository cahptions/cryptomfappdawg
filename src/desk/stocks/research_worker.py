"""Dashboard-owned collection/PAPER worker. Startup is automatic; pause persists; GET never starts work."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks.connection import _atomic_private


class ResearchWorker:
    def __init__(self, paths: DeskPaths) -> None:
        self.paths = paths
        self.process: subprocess.Popen[bytes] | None = None
        self.stopping = False
        self.lock = threading.RLock()
        self.preference = paths.home / "stocks-automation.json"
        self.error: str | None = None
        self.blocked = False
        try:
            self.enabled = self._enabled()
        except DeskError as exc:
            self.enabled, self.blocked, self.error = False, True, str(exc)

    def _enabled(self) -> bool:
        if not self.preference.exists():
            return True
        try:
            if self.preference.stat().st_size > 4096:
                raise ValueError
            value = json.loads(self.preference.read_text())
            if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
                raise ValueError
            return bool(value["enabled"])
        except (OSError, ValueError) as exc:
            raise DeskError("Saved PAPER automation setting is unreadable. Use Start PAPER to save it again.") from exc

    def status(self) -> dict[str, Any]:
        with self.lock:
            code = self.process.poll() if self.process is not None else None
            owned = self.process is not None and code is None
            external = not owned and WorkerLock(self.paths, "stocks-worker").held()
            managed = owned or (external and self._managed_pid())
            return {"owned": owned, "running": owned or external, "managed": managed,
                    "stopping": owned and self.stopping, "exit_code": code, "enabled": self.enabled,
                    "mode": "PAPER" if managed else None, "error": self.error}

    def _managed_pid(self) -> bool:
        """Observe a surviving app worker; never signal a PID obtained from this metadata."""
        try:
            path = self.paths.home / "stocks-progress.json"
            if path.stat().st_size > 4096:
                return False
            value = json.loads(path.read_text())
            pid = value.get("pid")
            if not value.get("managed") or not isinstance(pid, int) or pid <= 0:
                return False
            words = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            return all(word in words for word in (b"-m", b"desk", b"stocks", b"paper", b"--managed"))
        except (OSError, ValueError, AttributeError):
            return False

    def maintain(self) -> dict[str, Any]:
        """Called by dashboard lifespan, not by requests. Wait for external workers; surface crashes without loops."""
        with self.lock:
            if not self.enabled or self.blocked or self.status()["running"]:
                return self.status()
            if self.process is not None:
                self.error = f"PAPER worker exited (code {self.process.poll()}). Check stocks-research.log, then Start PAPER."
                self.blocked = True
                return self.status()
            try:
                return self.start()
            except DeskError as exc:
                self.error, self.blocked = str(exc), True
                return self.status()

    def start(self) -> dict[str, Any]:
        try:
            return self._start()
        except OSError as exc:
            raise DeskError("Could not start PAPER. Check available disk space and write access to desk-data.") from exc

    def _start(self) -> dict[str, Any]:
        with self.lock:
            if self.status()["running"]:
                raise DeskError("A Stocks worker is already running. Use its existing collection; do not start a second one.")
            self.paths.ensure()
            _atomic_private(self.preference, json.dumps({"enabled": True}))
            self.enabled, self.blocked, self.error = True, False, None
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
                        [sys.executable, "-m", "desk", "stocks", "paper", "--continuous", "--price-days", "20", "--managed"],
                        cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            except OSError as exc:
                raise DeskError("Could not start PAPER. Check the laptop's available disk space.") from exc
            self.stopping = False
            return self.status()

    def stop(self, *, pause: bool = True) -> dict[str, Any]:
        with self.lock:
            if pause:
                self.paths.ensure()
                _atomic_private(self.preference, json.dumps({"enabled": False}))
                self.enabled = False
            if self.process is not None and self.process.poll() is None and not self.stopping:
                # Only this exact Popen child: no PID-file lookup and no signalling an unrelated terminal worker.
                with suppress(ProcessLookupError):
                    self.process.send_signal(signal.SIGTERM)
                self.stopping = True
            return self.status()
