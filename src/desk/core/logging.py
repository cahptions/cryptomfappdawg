"""Structured logging with secret redaction.

* Console: short human-readable lines.
* File: one JSON object per line in ``<DESK_HOME>/logs/desk.jsonl`` (rotated).
* A redaction filter replaces every known secret value, and anything that looks like ``api-key=...`` in a URL,
  before a record reaches any handler.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACTED = "***REDACTED***"
_URL_KEY_PATTERN = re.compile(r"(?i)((?:api[-_]?key|apikey|token|key)=)[^&\s\"']+")
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class RedactionFilter(logging.Filter):
    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: list[str] = []
        self.update(secret_values)

    def update(self, secret_values: Iterable[str]) -> None:
        merged = set(self._secrets) | {s for s in secret_values if s and len(s) >= 4}
        self._secrets = sorted(merged, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return _URL_KEY_PATTERN.sub(lambda m: m.group(1) + REDACTED, text)

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {k: self._redact_value(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return type(value)(self._redact_value(v) for v in value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = self.redact(message)
        record.args = None
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS:
                setattr(record, key, self._redact_value(value))
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


_FILTER = RedactionFilter()


def redaction_filter() -> RedactionFilter:
    return _FILTER


def setup_logging(logs_dir: Path | None, secret_values: Iterable[str] = (), level: int = logging.INFO) -> None:
    """Configure root logging once per process. Safe to call again (handlers are replaced)."""
    _FILTER.update(secret_values)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    console.addFilter(_FILTER)
    root.addHandler(console)
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "desk.jsonl", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(_FILTER)
        root.addHandler(file_handler)
    # third-party HTTP libraries log full URLs (which can contain keys) at INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
