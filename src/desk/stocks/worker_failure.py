"""Retain bounded crash context without exception messages, source lines, locals or credentials."""

from __future__ import annotations

import json
import os
import re
import traceback
from pathlib import Path
from typing import Any

from desk.core.clock import utc_now
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.stocks.connection import _atomic_private

FILE = "stocks-paper-failure.json"
TYPES = frozenset({"Exception", "ArithmeticError", "ZeroDivisionError", "OverflowError", "ValueError", "TypeError",
                   "KeyError", "IndexError", "AttributeError", "NameError", "UnboundLocalError", "RuntimeError",
                   "MemoryError", "OSError", "PermissionError", "FileNotFoundError", "TimeoutError", "ImportError",
                   "ModuleNotFoundError", "OperationalError", "IntegrityError", "DatabaseError", "ProgrammingError",
                   "DeskError", "ConfigError", "SchemaError", "StorageError", "MissingDataError", "WorkerBusy",
                   "SchwabAuthError", "MemoryPressure"})


def record(paths: DeskPaths, exc: Exception) -> None:
    """Best effort at the CLI boundary; callers must still propagate the original failure."""
    frames = []
    for frame in traceback.extract_tb(exc.__traceback__):
        try:
            relative = Path(frame.filename).resolve().relative_to(REPO_ROOT / "src" / "desk").as_posix()
        except ValueError:
            continue
        if re.fullmatch(r"[a-zA-Z0-9_/]+\.py", relative):
            frames.append({"file": relative, "line": frame.lineno})
    value = {"at": utc_now().isoformat(), "pid": os.getpid(),
             "exception_type": type(exc).__name__ if type(exc).__name__ in TYPES else "Exception",
             "frames": frames[-8:]}
    _atomic_private(paths.home / FILE, json.dumps(value, allow_nan=False))


def read(paths: DeskPaths) -> dict[str, Any] | None:
    """Validate retained context again before it reaches browser responses or exports."""
    try:
        with (paths.home / FILE).open("rb") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            return None
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get("exception_type") not in TYPES
                or not isinstance(value.get("at"), str) or not re.fullmatch(r"[0-9T:+.Z-]{1,40}", value["at"])
                or type(value.get("pid")) is not int or value["pid"] <= 0
                or not isinstance(value.get("frames"), list) or len(value["frames"]) > 8):
            return None
        frames = []
        for frame in value["frames"]:
            if (not isinstance(frame, dict) or not isinstance(frame.get("file"), str)
                    or not re.fullmatch(r"[a-zA-Z0-9_/]+\.py", frame["file"])
                    or type(frame.get("line")) is not int or frame["line"] <= 0):
                return None
            frames.append({"file": frame["file"], "line": frame["line"]})
        return {"at": value["at"], "pid": value["pid"], "exception_type": value["exception_type"], "frames": frames}
    except (OSError, ValueError, TypeError):
        return None


def summary(value: dict[str, Any]) -> str:
    frame = value["frames"][-1] if value["frames"] else None
    location = f" at {frame['file']}:{frame['line']}" if frame else ""
    return (f"{value['exception_type']}{location}. Open Evidence → Check data health to download the failure details. "
            "Saved data is kept. Use Start PAPER to retry after addressing the failure.")
