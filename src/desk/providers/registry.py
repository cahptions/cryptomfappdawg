"""The provider registry (``config/providers.toml``): endpoints, verified fields, limits, costs, fallbacks."""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from desk.core.errors import ConfigError

Window = tuple[int, float]  # (max requests, window seconds)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BucketSpec(_Strict):
    windows: tuple[Window, ...] = Field(min_length=1)
    windows_with_key: tuple[Window, ...] | None = None

    @model_validator(mode="after")
    def _positive(self) -> Self:
        for count, seconds in (*self.windows, *(self.windows_with_key or ())):
            if count < 1 or seconds <= 0:
                raise ValueError(f"invalid window ({count}, {seconds})")
        return self


class EndpointSpec(_Strict):
    path: str
    status: Literal["verified", "to_verify"]
    verified_fields: tuple[str, ...]
    bucket: str = "default"
    max_addresses: int | None = None
    page_size: int | None = None
    notes: str | None = None


class ProviderSpec(_Strict):
    display_name: str
    base_url: str
    keyless_base_url: str | None = None
    auth: Literal["none", "header", "url"]
    key_env: str | None = None
    key_header: str | None = None
    cost_usd_month: Decimal = Field(ge=0)
    documented_limit: str
    measured_limit: str
    freshness_seconds: int = Field(ge=0)
    failure_handling: str
    fallback: str
    allowed_usage: str
    evidence: tuple[str, ...] = Field(min_length=1)
    default_headers: dict[str, str] = Field(default_factory=dict)
    buckets: dict[str, BucketSpec]
    endpoints: dict[str, EndpointSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.auth == "header" and not (self.key_env and self.key_header):
            raise ValueError("header auth needs key_env and key_header")
        if self.auth == "url" and not self.key_env:
            raise ValueError("url auth needs key_env")
        for name, endpoint in self.endpoints.items():
            if endpoint.bucket not in self.buckets:
                raise ValueError(f"endpoint {name} uses unknown bucket {endpoint.bucket!r}")
        return self


class ProviderRegistry(_Strict):
    schema_version: Literal[1]
    providers: dict[str, ProviderSpec]

    def get(self, name: str) -> ProviderSpec:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise ConfigError(f"provider {name!r} is not in the registry") from exc


def load_registry(config_dir: Path, repo_root: Path | None = None) -> ProviderRegistry:
    path = config_dir / "providers.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        registry = ProviderRegistry.model_validate(data)
    except FileNotFoundError as exc:
        raise ConfigError(f"provider registry not found: {path}") from exc
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigError(f"invalid provider registry {path}:\n{exc}") from exc
    if repo_root is not None:
        missing = [f"{name}: {ev}" for name, spec in registry.providers.items() for ev in spec.evidence
                   if not (repo_root / ev).exists()]
        if missing:
            raise ConfigError("provider registry references missing evidence files:\n  " + "\n  ".join(missing))
    return registry
