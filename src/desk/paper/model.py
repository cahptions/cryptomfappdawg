"""Pure PAPER rules: costs, execution profiles, deterministic failure draws, sizing and exit policies.

Nothing here reads a clock, the network or a database, so every rule is table-testable (M5-02/03/05/09).
All money is integer lamports; token quantities are integer raw units. Rounding always favours the desk's
caution: sizes and proceeds round down.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from desk.contracts.quotes import QuoteObservation
from desk.contracts.strategy import FeatureCondition, StrategySpec

SOL_CRASH_LOG_RETURN_1H = Decimal("0.92").ln()      # PLAN §13.3 policy 7 (SOL -8% in one hour)
CATASTROPHIC_STOP_FRACTION = Decimal("0.05")         # tightened stop / trail while the market is in crash state
IMPAIRED_VALUE_FRACTION = Decimal("0.5")             # PLAN §12.4: best quote < 50% of last executable mark
NO_ROUTE_IMPAIR_SECONDS = 300                        # PLAN §12.4: no sell route for > 5 minutes
VOLUME_DECAY_EXIT_BELOW = Decimal("0.5")             # same boundary as the H7 overlay's rejection


def floor_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def usd_to_lamports(usd: Decimal, sol_usd: Decimal) -> int:
    """USD amount at a SOL/USD rate, rounded down to whole lamports (a rate quotient has many decimals)."""
    if sol_usd <= 0:
        raise ValueError("SOL/USD rate must be positive")
    return floor_int(Decimal(usd) / sol_usd * Decimal(10 ** 9))


@dataclass(frozen=True)
class FeeModel:
    """Documented conservative fixed costs until R-06 measures real priority fees with a read-only taker."""

    signature_fee_lamports: int
    priority_fee_lamports: int
    token_account_rent_lamports: int

    @property
    def per_transaction(self) -> int:
        return self.signature_fee_lamports + self.priority_fee_lamports

    def fixed(self, quote: QuoteObservation | None = None) -> int:
        """Per-transaction fee, preferring fees reported by the quote when present."""
        if quote is not None and quote.signature_fee_lamports is not None and quote.prioritization_fee_lamports is not None:
            return max(quote.signature_fee_lamports + quote.prioritization_fee_lamports, self.per_transaction)
        return self.per_transaction


def failure_draw(intent_id: str, leg: str, attempt: int) -> Decimal:
    """Deterministic uniform draw in [0, 1): the same intent always meets the same simulated failures."""
    digest = hashlib.sha256(f"{intent_id}:{leg}:{attempt}".encode()).digest()
    return Decimal(int.from_bytes(digest[:8], "big")) / Decimal(2 ** 64)


def impact_fraction(*quotes: QuoteObservation) -> Decimal:
    """Largest absolute price impact among quotes, as a fraction (Jupiter reports percentage points)."""
    impacts = [abs(q.price_impact_pct) / 100 for q in quotes if q.price_impact_pct is not None]
    return max(impacts, default=Decimal(0))


@dataclass(frozen=True)
class Profiles:
    """Parallel accountings of one leg. Portfolio state always follows ``conservative``."""

    observed: int
    conservative: int
    stress: int

    def __post_init__(self) -> None:
        if not self.observed >= self.conservative >= self.stress >= 0:
            raise ValueError("profiles must satisfy observed >= conservative >= stress >= 0")


def entry_profiles(first: QuoteObservation, second: QuoteObservation) -> Profiles:
    """Tokens received: observed = first quote; conservative = worse quote less half the impact; stress = 2x impact."""
    worse = min(first.out_amount_raw, second.out_amount_raw)
    impact = impact_fraction(first, second)
    conservative = floor_int(Decimal(worse) * (1 - impact / 2))
    stress = floor_int(Decimal(worse) * max(Decimal(0), 1 - 2 * impact))
    return Profiles(observed=max(first.out_amount_raw, conservative), conservative=conservative, stress=min(stress, conservative))


def exit_profiles(first: QuoteObservation, second: QuoteObservation, entry: Profiles) -> Profiles:
    """Lamports received when selling. Quotes are for the conservative quantity; other profiles scale linearly.

    Stress also assumes exit liquidity 30% lower, i.e. impact / 0.7, doubled.
    """
    held = Decimal(entry.conservative)
    worse = Decimal(min(first.out_amount_raw, second.out_amount_raw))
    impact = impact_fraction(first, second)
    conservative = floor_int(worse * (1 - impact / 2))
    observed = floor_int(Decimal(first.out_amount_raw) * Decimal(entry.observed) / held)
    stress = floor_int(worse * Decimal(entry.stress) / held * max(Decimal(0), 1 - 2 * impact / Decimal("0.7")))
    return Profiles(observed=max(observed, conservative), conservative=conservative, stress=min(stress, conservative))


# ── sizing (PLAN §13.1.1 learning book) ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TicketDecision:
    lamports: int
    reasons: tuple[str, ...]
    sizing_capital_lamports: int


def learning_ticket(*, base_lamports: int, realized_pnl_lamports: int, fixed_fraction: Decimal,
                    max_fraction: Decimal, min_ticket_lamports: int, max_fixed_cost_fraction: Decimal,
                    fixed_costs_lamports: int, rent_lamports: int, cash_lamports: int,
                    liquidity_cap_lamports: int | None) -> TicketDecision:
    """Ticket = fraction x ($100-account base + realized profit); losses never shrink the base.

    Returns 0 with reasons when the fee floor, minimum ticket, liquidity cap or cash would be violated.
    """
    if base_lamports <= 0 or not Decimal(0) < fixed_fraction <= max_fraction <= 1:
        raise ValueError("positive base and 0 < fixed_fraction <= max_fraction <= 1 required")
    capital = base_lamports + max(0, realized_pnl_lamports)
    ticket = min(floor_int(Decimal(capital) * fixed_fraction), floor_int(Decimal(capital) * max_fraction))
    reasons: list[str] = []
    if liquidity_cap_lamports is None:
        reasons.append("LIQUIDITY_CAP_UNKNOWN")
    else:
        ticket = min(ticket, liquidity_cap_lamports)
    if ticket < min_ticket_lamports:
        reasons.append("BELOW_MINIMUM_TICKET")
    if Decimal(fixed_costs_lamports) > Decimal(ticket) * max_fixed_cost_fraction:
        reasons.append("FEE_FLOOR")
    if ticket + fixed_costs_lamports + rent_lamports > cash_lamports:
        reasons.append("INSUFFICIENT_CASH")
    return TicketDecision(0 if reasons else ticket, tuple(reasons), capital)


# ── exits (PLAN §13.3) ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ExitInputs:
    ticket_lamports: int
    value_lamports: int | None          # latest EXECUTABLE sell quote for the exact quantity; None = no fresh mark
    peak_value_lamports: int | None
    opened_at: datetime
    now: datetime
    security_rejected: bool             # latest security re-assessment after entry is REJECT
    liquidity_usd: Decimal | None       # latest usable liquidity feature
    entry_liquidity_usd: Decimal | None
    sol_return_1h: Decimal | None
    volume_decay: Decimal | None        # the variant the strategy uses (VD-adj for the built-in specs)
    features: dict[str, Decimal]        # usable features at ``now`` for invalidation conditions


def _holds(value: Decimal, condition: FeatureCondition) -> bool:
    threshold = condition.threshold
    return {"gte": value >= threshold, "gt": value > threshold, "lte": value <= threshold,
            "lt": value < threshold, "eq": value == threshold}[condition.operator]


def exit_reason(spec: StrategySpec, facts: ExitInputs) -> str | None:
    """The first exit policy that fires, in a fixed priority order; None keeps the position."""
    if facts.security_rejected:
        return "SECURITY_CHANGE"
    liquidity = spec.liquidity_exits
    if facts.liquidity_usd is not None:
        if facts.liquidity_usd < liquidity.minimum_verified_liquidity_usd:
            return "LIQUIDITY_BELOW_MINIMUM"
        if (facts.entry_liquidity_usd and facts.entry_liquidity_usd > 0
                and facts.liquidity_usd <= facts.entry_liquidity_usd * (1 - liquidity.drop_fraction)):
            return "LIQUIDITY_DETERIORATION"
    if (facts.now - facts.opened_at).total_seconds() >= spec.max_hold_seconds:
        return "MAX_HOLD"
    value, ticket = facts.value_lamports, Decimal(facts.ticket_lamports)
    if value is not None:
        peak = Decimal(max(facts.peak_value_lamports or 0, value))
        if facts.sol_return_1h is not None and facts.sol_return_1h <= SOL_CRASH_LOG_RETURN_1H and (
                value <= ticket * (1 - CATASTROPHIC_STOP_FRACTION) or value <= peak * (1 - CATASTROPHIC_STOP_FRACTION)):
            return "CATASTROPHIC_MARKET"
        if value <= ticket * (1 - spec.initial_stop.loss_fraction):
            return "HARD_STOP"
        trailing = spec.trailing
        if peak >= ticket * (1 + trailing.activation_gain_fraction) and value <= peak * (1 - trailing.drawdown_fraction):
            return "TRAILING_STOP"
    if facts.volume_decay is not None and facts.volume_decay < VOLUME_DECAY_EXIT_BELOW:
        return "VOLUME_DECAY"
    for condition in spec.rejection_conditions:
        current = facts.features.get(condition.feature)
        if current is not None and _holds(current, condition):
            return "STRATEGY_INVALIDATED"
    return None


def impaired(last_mark_lamports: int | None, quote_lamports: int | None, seconds_without_route: float) -> bool:
    if quote_lamports is None:
        return seconds_without_route > NO_ROUTE_IMPAIR_SECONDS
    return last_mark_lamports is not None and Decimal(quote_lamports) < Decimal(last_mark_lamports) * IMPAIRED_VALUE_FRACTION
