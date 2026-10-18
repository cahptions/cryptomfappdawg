"""Error taxonomy. Every failure the desk can explain has a named type."""

from __future__ import annotations

from enum import StrEnum


class DeskError(Exception):
    """Base class for all desk errors."""


class ConfigError(DeskError):
    """Configuration is invalid, incomplete, or exceeds a safety ceiling."""


class MissingDataError(DeskError):
    """A value was required but is unknown. Unknown is never silently treated as zero."""


class UnitError(DeskError):
    """An amount cannot be represented exactly in the requested unit."""


class SchemaError(DeskError):
    """A provider response did not match the verified schema."""


class ProviderErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SCHEMA = "schema"
    RPC_ERROR = "rpc_error"


class ProviderError(DeskError):
    """A provider request failed after the allowed retries."""

    def __init__(self, provider: str, kind: ProviderErrorKind, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{provider}: {kind.value}: {message}")
        self.provider = provider
        self.kind = kind
        self.message = message
        self.status_code = status_code
