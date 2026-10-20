"""The two council advisors, frozen (research/council/STRATEGIES.md and specifications.json, profile solana_meme_15m).

Exactly two identities exist. Every parameter is an unvalidated starting hypothesis; a change is a new version
with a new hash, never an edit. ``CODE_SHA256`` identifies the calculation code that produced an advisory.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

from desk.core.hashing import canonical_json, fingerprint

PROFILE = "solana_meme_15m"
BAR_SECONDS = 900                      # decision bars: 15 minutes
TOKEN_NATIVE_SECONDS = 300             # three contiguous closed native 5-minute token bars per decision bar
BENCHMARK_NATIVE_SECONDS = 60          # fifteen aligned closed SOL/USD minutes per decision bar
PROVIDER_CACHE_SECONDS = 60            # documented response cache: a younger "closed" bar may still be partial

COMMON: dict[str, Any] = {
    "profile": PROFILE,
    "mode": "SHADOW",
    "influence": "NONE",
    "reference_action": "PROSPECTIVE_LONG",
    "bar": "UTC_15M_FROM_THREE_CONTIGUOUS_NATIVE_5M",
    "bar_time": "INTERVAL_START",
    "benchmark": "SOL_USD_FROM_FIFTEEN_ALIGNED_CLOSED_1M",
    "account_numeraire": "SOL",
    "price_currency": "USD",
    "universe_max": 20,
    "first_observed_min_hours": 96,
    "verified_liquidity_min_usd": "100000",
    "required_security_outcome": "PASS",
    "cost_ceiling_fraction": "0.03",
    "final_quote_max_age_seconds": 10,
    "quote_pair_max_receipt_separation_seconds": 5,
    "cost_formula": "(Q - exact_token_sell_quote_proceeds + nonembedded_network_fees) / Q",
    "max_latest_bar_age_seconds": 900,
    "min_benchmark_stddev_per_bar": "0.0005",
    "min_robust_residual_scale_per_bar": "0.001",
    "forward_fill_allowed": False,
    "pool_splicing_allowed": False,
    "additional_network_requests": 0,
    "sizing_authority": False,
    "new_entry_authority": False,
    "exit_authority": False,
}

BRC: dict[str, Any] = {
    "advisor_id": "C1_BRC",
    "version": 1,
    "name": "Benchmark-residual continuation",
    "common": COMMON,
    "train_returns": 288,
    "signal_returns": 16,
    "skip_returns": 1,
    "required_closed_prices": 306,
    "max_hold_seconds": 28800,
    "beta_min": "-1",
    "beta_max": "4",
    "max_half_beta_difference": "1.5",
    "support_score_min": "2",
    "oppose_score_max": "-2",
    "support_requires_latest_skipped_account_numeraire_return_nonnegative": True,
    "score_is_probability_or_p_value": False,
}

ECDR: dict[str, Any] = {
    "advisor_id": "C2_ECDR",
    "version": 1,
    "name": "Execution-confirmed dislocation reversal",
    "common": COMMON,
    "experimental": True,
    "relative_return_lookback": 96,
    "volume_lookback": 20,
    "max_abs_benchmark_event_return": "0.01",
    "shock_z_max": "-3",
    "reported_bar_volume_multiple_min": "2",
    "event_expiry_seconds": 1800,
    "max_hold_seconds": 5400,
    "pre_enrollment_required": True,
    "baseline_pair_count": 6,
    "baseline_cadence_seconds": 900,
    "baseline_lookback_seconds": 5400,
    "depth_adapter": "SUPPORTED_SIMPLE_REAL_RESERVE_CONSTANT_PRODUCT",
    "depth_ratio_min": "0.95",
    # minimum version: no watcher, no extra request; an event without its pre-event panel is ABSTAIN
    "intensive_watch_enabled": False,
}

SPECS = {"C1_BRC": BRC, "C2_ECDR": ECDR}


def spec_sha256(spec: dict[str, Any]) -> str:
    return fingerprint(spec)


def spec_json(spec: dict[str, Any]) -> str:
    return canonical_json(spec)


def _code_sha256() -> str:
    """Hash of the council calculation code and its advisory contract, in a fixed file order."""
    root = Path(__file__).resolve().parent
    files = [*sorted(root.glob("*.py")), root.parent / "contracts" / "advisory.py"]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


CODE_SHA256 = _code_sha256()


def decimal(spec: dict[str, Any], key: str) -> Decimal:
    return Decimal(str(spec[key]))


__all__ = ["BAR_SECONDS", "BENCHMARK_NATIVE_SECONDS", "BRC", "CODE_SHA256", "COMMON", "ECDR", "PROFILE",
           "PROVIDER_CACHE_SECONDS", "SPECS", "TOKEN_NATIVE_SECONDS", "decimal", "spec_json", "spec_sha256"]
