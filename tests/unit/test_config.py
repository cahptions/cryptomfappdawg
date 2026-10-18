"""M1-03 (single definition + snapshot hash) and M1-04 (safety ceilings)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from desk.core.config import Mode, load_config
from desk.core.errors import ConfigError


def test_real_config_loads_and_defaults_to_shadow(config_dir: Path) -> None:
    loaded = load_config(config_dir)
    assert loaded.settings.desk.mode is Mode.SHADOW
    assert str(loaded.settings.paper.starting_capital_usd) == "100"
    assert len(loaded.snapshot_hash) == 64


def test_same_file_twice_gives_same_hash(config_dir: Path) -> None:
    assert load_config(config_dir).snapshot_hash == load_config(config_dir).snapshot_hash


def test_changing_any_threshold_changes_hash(config_dir: Path) -> None:
    base = load_config(config_dir).snapshot_hash
    changed = load_config(config_dir, overrides={"limits.max_trades_per_day": 9}).snapshot_hash
    assert changed != base


def test_cosmetic_decimal_formatting_does_not_change_hash(config_dir: Path) -> None:
    a = load_config(config_dir, overrides={"sizing.fixed_fraction": "0.10"}).snapshot_hash
    b = load_config(config_dir, overrides={"sizing.fixed_fraction": "0.1"}).snapshot_hash
    assert a == b


def test_unknown_key_rejected(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        load_config(config_dir, overrides={"limits.max_leverage": 5})


def test_wrong_type_rejected(config_dir: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(config_dir, overrides={"limits.max_open_positions": "lots"})


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("sizing.fixed_fraction", "0.5"),
        ("sizing.max_ticket_sol", "5"),
        ("limits.max_open_positions", 10),
        ("limits.cumulative_drawdown_fraction", "0.9"),
        ("limits.stale_data_halt_seconds", 3600),
    ],
)
def test_value_above_ceiling_fails_with_clear_error(config_dir: Path, setting: str, value: object) -> None:
    with pytest.raises(ConfigError, match=re.escape(setting) + " = .* exceeds safety ceiling"):
        load_config(config_dir, overrides={setting: value})


@pytest.mark.parametrize("setting", ["sizing.min_ticket_sol", "limits.live_sol_reserve"])
def test_value_below_floor_fails(config_dir: Path, setting: str) -> None:
    with pytest.raises(ConfigError, match="below safety floor"):
        load_config(config_dir, overrides={setting: "0.001"})


def test_stricter_than_ceiling_is_allowed(config_dir: Path) -> None:
    loaded = load_config(config_dir, overrides={"limits.max_open_positions": 1, "sizing.fixed_fraction": "0.05"})
    assert loaded.settings.limits.max_open_positions == 1


def test_paid_provider_cost_fails_ceiling(config_dir: Path) -> None:
    providers = config_dir / "providers.toml"
    text = providers.read_text().replace("cost_usd_month = 0", "cost_usd_month = 49", 1)
    providers.write_text(text)
    with pytest.raises(ConfigError, match=re.escape("providers.max_paid_cost_usd_month")):
        load_config(config_dir)


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path)
