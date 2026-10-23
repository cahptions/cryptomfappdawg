"""Identify the code a Stocks server loaded, independently of files updated while it was running."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

from desk.core.paths import REPO_ROOT

UI_PROTOCOL = 1


def source_revision(root: Path = REPO_ROOT) -> str:
    source = root / "src" / "desk"
    files = sorted({*source.rglob("*.py"), *(source / "stocks" / "static").glob("*")})
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(source)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def server_is_current(url: str, *, timeout: float = 2) -> bool:
    """Bounded read of the fixed loopback runtime endpoint; never read or transmit credentials."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/stocks/runtime", timeout=timeout) as response:  # noqa: S310
            payload = response.read(4097)
        if len(payload) > 4096:
            return False
        value = json.loads(payload)
        return (isinstance(value, dict) and value.get("application") == "kestrel-stocks"
                and value.get("protocol") == UI_PROTOCOL and value.get("running_revision") == source_revision())
    except (urllib.error.URLError, OSError, ValueError):
        return False
