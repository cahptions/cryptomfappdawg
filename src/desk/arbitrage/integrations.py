"""Independent owner-only Arbitrage credentials. Never return values or borrow another desk's authorization."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field

from desk.arbitrage.contracts import Record
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths


class Credentials(Record):
    client_id: str = Field(default="", max_length=2000)
    client_secret: str = Field(default="", max_length=2000)
    enabled: bool = False
    free_access_confirmed: bool = False


def load(paths: DeskPaths) -> Credentials:
    try:
        descriptor = os.open(paths.secrets_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return Credentials()
    except OSError as exc:
        raise DeskError("Cannot open private Arbitrage connection file; symlinks are refused") from exc
    try:
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise DeskError("Arbitrage credentials must be owned by you and readable only by you (mode 600)")
            raw = handle.read(16385)
            if len(raw) > 16384:
                raise DeskError("Arbitrage credentials file exceeds its size limit")
            return Credentials.model_validate_json(raw)
    except ValueError as exc:
        raise DeskError("Arbitrage connection file is invalid; credential values were suppressed") from exc


def status(paths: DeskPaths) -> dict[str, Any]:
    try:
        value = load(paths)
        return {
            "app_key_set": bool(value.client_id),
            "app_secret_set": bool(value.client_secret),
            "enabled": value.enabled,
            "free_access_confirmed": value.free_access_confirmed,
            "connected": None,
            "note": "Presence is not a successful production API test",
        }
    except DeskError as exc:
        return {"error": str(exc), "enabled": False, "connected": None}


def save(paths: DeskPaths, value: Credentials) -> None:
    if any(any(c in field for c in "\r\n\x00") for field in (value.client_id, value.client_secret)):
        raise DeskError("Credentials must be single-line values")
    paths.secrets_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = paths.secrets_file.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    temporary: Path | None = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        previous = load(paths)
        merged = Credentials(
            client_id=value.client_id.strip() or previous.client_id,
            client_secret=value.client_secret.strip() or previous.client_secret,
            enabled=value.enabled,
            free_access_confirmed=value.free_access_confirmed,
        )
        if merged.enabled and (not merged.free_access_confirmed or not merged.client_id or not merged.client_secret):
            raise DeskError("Both production app credentials and confirmation of no added API charges are required to enable")
        with tempfile.NamedTemporaryFile(dir=paths.secrets_file.parent, prefix=".arbitrage-", mode="w", delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(merged.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(paths.secrets_file)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        os.close(descriptor)
