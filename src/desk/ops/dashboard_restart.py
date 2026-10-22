"""Gracefully restart only the current user's dashboard that owns the exact kernel flock.

Linux Mint supports pidfds. No PID file, broad process-name signal, lock unlink, or forced kill is used.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock


def lock_owner(path: Path) -> int | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    for line in Path('/proc/locks').read_text().splitlines():
        words = line.split()
        if len(words) < 6 or words[1:4] != ['FLOCK', 'ADVISORY', 'WRITE']:
            continue
        try:
            major, minor, inode = words[5].split(':')
            if (int(major, 16), int(minor, 16), int(inode)) == (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino):
                return int(words[4])
        except ValueError:
            continue
    return None


def is_dashboard(words: list[bytes], desk: str) -> bool:
    target = desk.encode()
    return (len(words) >= 5 and words[1:5] == [b'-m', b'desk', target, b'dashboard']) or (
        len(words) >= 4 and Path(os.fsdecode(words[1])).name == 'desk' and words[2:4] == [target, b'dashboard'])


def restart_dashboard(paths: DeskPaths, desk: str = 'stocks', *, timeout: float = 20) -> None:
    if desk not in {'stocks', 'arbitrage'}:
        raise DeskError('Only Stocks and Arbitrage dashboards support this restart command')
    label = 'Stocks' if desk == 'stocks' else 'Arbitrage'
    lock = WorkerLock(paths, 'stocks-dashboard' if desk == 'stocks' else 'dashboard')
    try:
        pid = lock_owner(lock.path)
        if pid is None:
            if lock.held():
                raise DeskError('Dashboard owner could not be identified; no process was stopped')
            return
        proc = Path(f'/proc/{pid}')
        if proc.stat().st_uid != os.getuid() or not is_dashboard(proc.joinpath('cmdline').read_bytes().split(b'\0'), desk):
            raise DeskError(f'The lock owner is not this user\'s {label} dashboard; no process was stopped')
        descriptor = os.pidfd_open(pid)
        try:
            if lock_owner(lock.path) != pid:
                raise DeskError('Dashboard changed during restart; no process was stopped. Retry the command')
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        finally:
            os.close(descriptor)
    except ProcessLookupError:
        pass  # The exact dashboard exited while its identity was being checked.
    except OSError as exc:
        raise DeskError(f'Could not safely restart {label}; its process and existing data were preserved') from exc
    until = time.monotonic() + timeout
    while lock.held():
        if time.monotonic() >= until:
            raise DeskError(f'{label} is still shutting down. No forced stop was used; retry after it exits')
        time.sleep(0.1)
