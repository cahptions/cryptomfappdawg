"""Definitions are part of the feature-set hash; changes create a new version identifier."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from desk.core.hashing import canonical_json, sha256_hex


class MissingPolicy(StrEnum):
    REJECT_CANDIDATE = "REJECT_CANDIDATE"
    EXCLUDE_FEATURE = "EXCLUDE_FEATURE"
    IMPUTE_FORBIDDEN = "IMPUTE_FORBIDDEN"


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    sources: tuple[str, ...] = Field(min_length=1)
    max_age_seconds: int = Field(gt=0)
    missing_policy: MissingPolicy
    version: str = Field(min_length=1)


def _definition(name: str, definition: str, unit: str = "ratio", *, age: int = 120,
                sources: tuple[str, ...] = ("market_snapshots",),
                policy: MissingPolicy = MissingPolicy.IMPUTE_FORBIDDEN) -> FeatureDefinition:
    return FeatureDefinition(name=name, definition=definition, unit=unit, sources=sources,
                             max_age_seconds=age, missing_policy=policy, version="1")


_DEFINITIONS = [
    *[_definition(f"log_return_{label}", f"Natural log of latest price / same-source price at or before {seconds}s ago; "
                  "anchor must be within 120s of target", "log_ratio")
      for label, seconds in (("5m", 300), ("15m", 900), ("1h", 3600), ("6h", 21600))],
    _definition("price_usd", "Latest same-pool observed USD price", "USD/token"),
    _definition("reported_liquidity_usd", "Provider-reported liquidity; not independently verified reserves", "USD"),
    _definition("fdv_usd", "Provider-reported fully diluted valuation", "USD"),
    _definition("market_cap_usd", "Provider-reported market capitalization", "USD"),
    *[_definition(f"volume_usd_{label}", f"Provider rolling {window} USD volume, not cumulative event volume", "USD")
      for label, window in (("5m", "m5"), ("1h", "h1"), ("6h", "h6"), ("24h", "h24"))],
    _definition("return_acceleration", "Latest 5m log return minus preceding non-overlapping 5m log return", "log_ratio"),
    _definition("realized_volatility_1h", "Population standard deviation of adjacent observed log returns over 1h; "
                "cadence-dependent, gap <=180s", "log_ratio"),
    _definition("volatility_adjusted_momentum", "1h log return / population stddev of adjacent 1h sample log returns"),
    _definition("distance_from_high_6h", "Latest price / maximum observed same-source 6h price minus 1; gap <=180s"),
    _definition("consolidation_range_1h", "(Maximum observed 1h price - minimum) / minimum; gap <=180s"),
    _definition("volume_acceleration", "Latest rolling 5m USD volume / (rolling 1h USD volume / 12)"),
    _definition("buy_sell_ratio", "Rolling 1h buy transaction count / sell count; not distinct-wallet counts"),
    _definition("buy_transactions_1h", "Provider rolling 1h buy transaction count", "transactions"),
    _definition("sell_transactions_1h", "Provider rolling 1h sell transaction count", "transactions"),
    _definition("distinct_buyers_1h", "Provider count of distinct 1h buyer addresses; never substitutes transactions",
                "addresses"),
    _definition("distinct_sellers_1h", "Provider count of distinct 1h seller addresses; overlap with buyers is unknown",
                "addresses"),
    _definition("transaction_frequency_acceleration", "Latest rolling 5m buy+sell count / (rolling 1h count / 12)"),
    _definition("mean_transaction_size_usd", "Rolling 1h USD volume / 1h buy+sell transaction count", "USD/transaction"),
    _definition("liquidity_change_1h", "Latest same-source reported liquidity / 1h anchor liquidity minus 1"),
    _definition("liquidity_to_market_cap", "Latest reported liquidity / latest reported market capitalization"),
    _definition("volume_decay_orig", "V6h / (V24h / 4), the original documented six-hour volume-decay ratio"),
    _definition("volume_decay_adj", "V6h / (V24h * 6 / clamp(token age in hours, 6, 24)); age remains provider claim",
                sources=("market_snapshots", "token_claims")),
    _definition("token_age_seconds", "As-of minus provider token creation timestamp; UNVERIFIED, not first-seen age",
                "seconds", age=86400, sources=("token_claims", "discovery_observations")),
    _definition("pool_age_seconds", "As-of minus append-only provider pool creation timestamp; UNVERIFIED",
                "seconds", age=86400, sources=("discovery_observations", "token_claims")),
    _definition("first_observed_age_seconds", "As-of minus earliest recorded observation of this mint; lower bound on age",
                "seconds", age=2147483647, sources=("market_snapshots", "token_claims")),
    _definition("organic_volume_share", "Jupiter claimed (organic buy+sell USD volume)/(buy+sell USD volume) over1h; UNVERIFIED",
                sources=("token_claims",)),
    _definition("holder_count", "Jupiter claimed holder count; UNVERIFIED", "addresses", sources=("token_claims",)),
    _definition("dev_mints", "Provider claimed creator mint count; UNVERIFIED", "mints", sources=("token_claims",)),
    _definition("dev_migrations", "Provider claimed creator migration count; UNVERIFIED", "migrations",
                sources=("token_claims",)),
    _definition("verified_liquidity_usd", "Independently verified pool reserves valued in USD; unavailable until recorded",
                "USD", sources=("security_assessments",), policy=MissingPolicy.REJECT_CANDIDATE),
    _definition("roundtrip_cost_fraction", "Recorded sequential exact-ticket quote-implied cost plus reported fixed fees; "
                "not a complete execution cost model", age=10, sources=("security_assessments",),
                policy=MissingPolicy.REJECT_CANDIDATE),
    *[_definition(name, definition, "percent", sources=("security_assessments",), policy=MissingPolicy.REJECT_CANDIDATE)
      for name, definition in (
          ("top1_pct", "Largest classified owner's adjusted holdings / total mint supply; top20-account lower bound"),
          ("top10_pct", "Largest ten classified owners' adjusted holdings / total mint supply; top20-account lower bound"),
          ("dev_holdings_pct", "Observed creator owner's holdings / mint supply; absent creator holdings remain unknown"))],
    _definition("holder_count_growth_1h", "Latest provider holder count / same-source 1h anchor holder count minus1; UNVERIFIED",
                sources=("token_claims",)),
    _definition("initial_spike_return", "Return from verified migration event to first validated spike peak; "
                "missing until event chronology is recorded", sources=("event_chronology",)),
    _definition("spike_retracement_fraction", "Fraction of validated initial spike retraced after its peak; "
                "requires ordered migration, spike, and pullback events", sources=("event_chronology",)),
    _definition("breakout_retest_confirmed", "Continuous retest-distance score from verified breakout and subsequent retest "
                "chronology; missing until these events are recorded", sources=("event_chronology",)),
    *[_definition(name, definition, unit, sources=("market_context",)) for name, definition, unit in (
        ("sol_return_1h", "SOL 1h log return from a separate SOL reference series", "log_ratio"),
        ("sol_volatility_1h", "SOL 1h realized volatility from a separate reference series", "log_ratio"),
        ("watchlist_volume_usd_1h", "Point-in-time sum of tracked token 1h USD volume with defined universe", "USD"),
        ("migrations_per_hour", "Unique verified migrations received during preceding1h", "migrations/hour"),
        ("hazard_rate_24h", "Share of tracked tokens losing >80% liquidity in24h with documented cohort", "fraction"),
        ("quote_impact_slope", "Change of quote price impact per change in exact ticket size", "fraction/SOL"),
        ("quote_variability", "Stddev of contemporaneous same-ticket route quotes", "fraction"),
        ("exit_capacity_sol", "Largest verified sell quote ticket meeting configured impact bound", "SOL"),
    )],
]

if len({item.name for item in _DEFINITIONS}) != len(_DEFINITIONS):
    raise ValueError("duplicate feature definition")

FEATURE_REGISTRY = MappingProxyType({item.name: item for item in _DEFINITIONS})
FEATURE_SET_VERSION = "features-v1-" + sha256_hex(canonical_json(
    [item.model_dump(mode="json") for item in _DEFINITIONS]))[:16]
