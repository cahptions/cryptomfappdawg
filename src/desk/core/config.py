"""Configuration: one authoritative TOML file, validated, hashed, and checked against safety ceilings.

``load_config`` returns a :class:`LoadedConfig` holding the validated settings and their snapshot hash.
Any unknown key, wrong type, or value beyond a ceiling stops the desk with a clear message.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from desk.core.errors import ConfigError
from desk.core.hashing import fingerprint


class Mode(StrEnum):
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE = "LIVE"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Fraction = Decimal  # documented alias: values in [0, 1]


class DeskSection(_Section):
    mode: Mode = Mode.SHADOW


class PaperSection(_Section):
    starting_capital_usd: Decimal = Field(gt=0)


class SizingSection(_Section):
    fixed_fraction: Fraction = Field(gt=0, le=1)
    max_ticket_fraction_free_capital: Fraction = Field(gt=0, le=1)
    max_ticket_fraction_pool_liquidity: Fraction = Field(gt=0, le=1)
    max_ticket_fraction_equity: Fraction = Field(default=Decimal("0.15"), gt=0, le=1)
    # Retained only as an optional additional tightening limit for older/operator profiles.
    max_ticket_sol: Decimal | None = Field(default=None, gt=0)
    min_ticket_sol: Decimal = Field(gt=0)
    max_fixed_cost_fraction: Fraction = Field(gt=0, le=1)


class LimitsSection(_Section):
    max_open_positions: int = Field(ge=0)
    max_total_exposure_fraction: Fraction = Field(ge=0, le=1)
    max_positions_per_dev: int = Field(ge=0)
    max_positions_per_launchpad: int = Field(ge=0)
    daily_realized_loss_fraction: Fraction = Field(gt=0, le=1)
    daily_equity_drawdown_fraction: Fraction = Field(gt=0, le=1)
    cumulative_drawdown_fraction: Fraction = Field(gt=0, le=1)
    max_trades_per_day: int = Field(ge=0)
    max_order_submissions_per_min: int = Field(ge=0)
    max_consecutive_failed_tx: int = Field(ge=1)
    stale_data_halt_seconds: int = Field(gt=0)
    live_sol_reserve: Decimal = Field(ge=0)


class HttpSection(_Section):
    timeout_seconds: float = Field(gt=0, le=120)
    max_retries: int = Field(ge=0, le=10)
    backoff_base_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)
    user_agent: str


class HealthSection(_Section):
    degraded_after_failures: int = Field(ge=1)
    down_after_failures: int = Field(ge=1)
    recover_after_successes: int = Field(ge=1)


class CollectorSection(_Section):
    geckoterminal_new_pools_interval_seconds: int = Field(ge=60)
    geckoterminal_new_pools_pages: int = Field(ge=1, le=10)
    geckoterminal_trending_interval_seconds: int = Field(ge=60)
    jupiter_recent_interval_seconds: int = Field(ge=30)
    jupiter_trending_interval_seconds: int = Field(ge=60)
    dexscreener_refresh_interval_seconds: int = Field(ge=30)
    dexscreener_promotions_interval_seconds: int = Field(ge=300)
    rpc_slot_interval_seconds: int = Field(ge=10)
    max_tracked_tokens: int = Field(ge=1, le=5000)


class StorageSection(_Section):
    raw_retention_days: int = Field(ge=1)


class DiscoverySection(_Section):
    evaluation_interval_seconds: int = Field(ge=10)
    max_assessments_per_cycle: int = Field(ge=1, le=10)
    reassess_after_seconds: int = Field(ge=60)
    min_pool_age_seconds: int = Field(ge=0)
    min_liquidity_usd: Decimal = Field(gt=0)
    min_volume_h1_usd: Decimal = Field(ge=0)
    min_fdv_usd: Decimal = Field(gt=0)
    max_fdv_usd: Decimal = Field(gt=0)
    max_snapshot_age_seconds: int = Field(gt=0)
    min_distinct_traders_h1: int = Field(ge=1)
    ticket_sol: Decimal = Field(gt=0)
    max_quote_age_seconds: int = Field(gt=0)
    max_roundtrip_cost_fraction: Decimal = Field(gt=0, le=1)
    max_top1_pct: Decimal = Field(gt=0, le=100)
    max_top10_pct: Decimal = Field(gt=0, le=100)
    max_dev_pct: Decimal = Field(gt=0, le=100)
    max_holder_conflict_pp: Decimal = Field(gt=0, le=100)
    max_source_conflict_fraction: Decimal = Field(gt=0, le=1)
    extreme_dev_mints: int = Field(ge=1)
    min_dev_migration_fraction: Decimal = Field(ge=0, le=1)
    volume_expansion_ratio: Decimal = Field(gt=1)
    acceleration_ratio: Decimal = Field(gt=1)
    renewed_after_seconds: int = Field(ge=60)


class DeskConfig(_Section):
    desk: DeskSection
    paper: PaperSection
    sizing: SizingSection
    limits: LimitsSection
    http: HttpSection
    health: HealthSection
    collector: CollectorSection
    storage: StorageSection
    discovery: DiscoverySection


@dataclass(frozen=True)
class LoadedConfig:
    settings: DeskConfig
    snapshot_hash: str
    ceilings_hash: str
    source: Path


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def _lookup(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def check_ceilings(config_data: dict[str, Any], ceilings: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    """Raise :class:`ConfigError` listing every config value that is looser than its ceiling/floor."""
    merged = dict(config_data)
    if extra:
        merged.update(extra)
    problems: list[str] = []
    unknown = set(ceilings) - {"max", "min"}
    if unknown:
        problems.append(f"unknown ceiling sections: {sorted(unknown)}")
    for direction in ("max", "min"):
        for dotted, limit in ceilings.get(direction, {}).items():
            try:
                actual = Decimal(str(_lookup(merged, dotted)))
            except KeyError:
                problems.append(f"{dotted}: ceiling defined but the setting is missing")
                continue
            bound = Decimal(str(limit))
            if direction == "max" and actual > bound:
                problems.append(f"{dotted} = {actual} exceeds safety ceiling {bound}")
            if direction == "min" and actual < bound:
                problems.append(f"{dotted} = {actual} is below safety floor {bound}")
    if problems:
        raise ConfigError("safety ceiling check failed:\n  - " + "\n  - ".join(problems))


def load_config(config_dir: Path, overrides: dict[str, Any] | None = None) -> LoadedConfig:
    """Load ``default.toml`` (+ optional overrides for tests), validate, check ceilings and hash."""
    source = config_dir / "default.toml"
    data = _read_toml(source)
    if overrides:
        for dotted, value in overrides.items():
            section, _, key = dotted.partition(".")
            data.setdefault(section, {})[key] = value
    try:
        settings = DeskConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {source}:\n{exc}") from exc

    ceilings = _read_toml(config_dir / "safety_ceilings.toml")
    providers = _read_toml(config_dir / "providers.toml")
    max_paid = max((Decimal(str(p.get("cost_usd_month", 0))) for p in providers.get("providers", {}).values()),
                   default=Decimal(0))
    check_ceilings(settings.model_dump(mode="json"), ceilings, {"providers": {"max_paid_cost_usd_month": str(max_paid)}})

    return LoadedConfig(
        settings=settings,
        snapshot_hash=fingerprint(settings.model_dump(mode="python")),
        ceilings_hash=fingerprint(ceilings),
        source=source,
    )
