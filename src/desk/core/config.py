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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from desk.core.base58 import is_solana_address
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
    starting_capital_usd: Decimal = Field(gt=0)        # reference account: sizing base and replica capital
    # PLAN §13.1.1 learning book (operator decisions 2026-10-19)
    learning_cash_usd: Decimal = Field(gt=0)
    max_learning_positions: int = Field(ge=1)
    learning_max_cumulative_loss_usd: Decimal = Field(gt=0)
    mark_interval_seconds: int = Field(ge=10, le=60)
    guardian_loop_seconds: int = Field(ge=1, le=10)
    guardian_heartbeat_max_age_seconds: int = Field(ge=5, le=60)
    conservative_wait_seconds: float = Field(ge=0, le=10)
    simulated_failure_rate: Fraction = Field(ge=0, le=1)
    stress_failure_rate: Fraction = Field(ge=0, le=1)
    signature_fee_lamports: int = Field(ge=0)
    priority_fee_lamports: int = Field(ge=0)
    token_account_rent_lamports: int = Field(ge=0)
    # read-only taker used to measure real network fees (nothing is signed or sent); a public funded address
    fee_probe_taker: str = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
    fee_probe_interval_seconds: int = Field(default=600, ge=300, le=3600)

    @field_validator("fee_probe_taker")
    @classmethod
    def _probe_taker(cls, value: str) -> str:
        if not is_solana_address(value):
            raise ValueError("fee_probe_taker must be a Solana address")
        return value


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


class ContextSection(_Section):
    sol_reference_pool: str
    interval_seconds: int = Field(ge=30, le=90)                     # regime inputs must be <= 120 s old
    hazard_followup_interval_seconds: int = Field(ge=60, le=600)    # within the 20-minute follow-up window

    @field_validator("sol_reference_pool")
    @classmethod
    def _address(cls, value: str) -> str:
        if not is_solana_address(value):
            raise ValueError("sol_reference_pool must be a Solana address")
        return value


class LiveSection(_Section):
    """M8 signer settings. ``max_capital_sol = 0`` keeps LIVE impossible (gate 4)."""

    max_capital_sol: Decimal = Field(ge=0)
    max_priority_fee_lamports: int = Field(ge=0)
    intent_max_age_seconds: int = Field(ge=5, le=120)
    entry_slippage_bps: int = Field(default=100, ge=1, le=300)    # BUY min-out below the fresh quote
    exit_slippage_bps: int = Field(default=300, ge=1, le=300)     # SELL: exiting matters more than price
    withdraw_address: str = ""                       # empty = withdrawals impossible
    allowed_fee_destinations: tuple[str, ...] = ()

    @field_validator("withdraw_address")
    @classmethod
    def _withdraw(cls, value: str) -> str:
        if value and not is_solana_address(value):
            raise ValueError("withdraw_address must be empty or a Solana address")
        return value


class ResearchSection(_Section):
    # M6-14: local model scores have zero influence until they beat the baselines out of sample AND a new,
    # operator-approved configuration value exists; today "none" is the only accepted value.
    model_influence: Literal["none"] = "none"
    # research/council: two record-only advisors with zero influence and zero added requests ("off" stops recording)
    council: Literal["record", "off"] = "record"


class RobinhoodSection(_Section):
    """The Robinhood desk (docs/ROBINHOOD.md). PAPER only: the desk has no code that places orders."""

    starting_capital_usd: Decimal = Decimal("500")         # reference account used for PAPER ticket sizing
    learning_cash_usd: Decimal = Decimal("100000")         # paper cushion, same idea as the Solana learning book
    fixed_fraction: Fraction = Decimal("0.10")             # ticket = 10% of (capital + realized profit) ...
    max_ticket_fraction: Fraction = Decimal("0.15")        # ... never above 15%
    max_open_positions: int = Field(default=10, ge=1)
    learning_max_cumulative_loss_usd: Decimal = Decimal("1000")
    max_roundtrip_cost_fraction: Fraction = Decimal("0.02")  # measured buy + sell at the exact ticket size
    estimate_max_age_seconds: int = Field(default=10, ge=1)
    quote_interval_seconds: int = Field(default=60, ge=15)
    backtest_roundtrip_cost_fraction: Fraction = Decimal("0.02")  # until a coin has measured Robinhood estimates
    # learning lab (docs/ROBINHOOD_LEARNING.md; operator decision 2026-10-20): "paper" lets a procedure that passes
    # on unseen months trade the PAPER book as L1_LEARNED; "record" only learns and reports; "off" does nothing.
    learning: Literal["paper", "record", "off"] = "paper"
    dashboard_port: int = Field(default=8766, ge=1024, le=65535)


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
    context: ContextSection
    research: ResearchSection = ResearchSection()
    live: LiveSection
    robinhood: RobinhoodSection = RobinhoodSection()


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
