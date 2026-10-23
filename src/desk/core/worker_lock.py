"""One entry/collection worker per data directory, shared by terminals and dashboards.

The worker must retain this lock for its entire run. Never unlink the lock file: doing so
would allow different processes to lock different inodes at the same path. The kernel
releases the lock automatically on process exit, including crashes.
"""

from __future__ import annotations

import fcntl
import os
from types import TracebackType

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths


class WorkerBusy(DeskError):
    """Another collection or entry worker already owns this data directory."""


class WorkerLock:
    def __init__(self, paths: DeskPaths, name: str = "entry-worker") -> None:
        if name not in {"entry-worker", "dashboard", "guardian", "guardian-live", "telegram", "rh-worker", "rh-dashboard",
                        "stocks-worker", "stocks-dashboard", "stocks-evidence", "stocks-backtest"}:
            raise ValueError("unsupported worker lock")
        self.paths, self.name = paths, name
        self.path = paths.home / "run" / f"{name}.lock"
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise WorkerBusy("this worker already holds the entry lock")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise WorkerBusy("an entry or collection worker is already active") from exc
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is not None:
            descriptor, self._descriptor = self._descriptor, None
            os.close(descriptor)

    def __enter__(self) -> WorkerLock:
        self.acquire()
        return self

    def __exit__(self, _kind: type[BaseException] | None, _value: BaseException | None,
                 _traceback: TracebackType | None) -> None:
        self.release()

    def held(self) -> bool:
        try:
            with WorkerLock(self.paths, self.name):
                return False
        except WorkerBusy:
            return True
