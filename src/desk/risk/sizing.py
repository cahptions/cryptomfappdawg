"""Pure PAPER sizing recommendations with conditional realized-profit compounding.

This is an unconnected M5/M6 foundation, not a trading engine, statistics producer,
or LIVE authorization. Callers must first supply the independent risk gate and
measured, attributable evidence. No score, deposit, mark-to-market gain, or winning
streak is an input. The fixed risk fraction and configured ceilings never increase.

The two-step interface deliberately returns a target before an exact-sized quote
exists. Obtain a new quote for that target and call again; a smaller quote cannot
authorize the larger ticket. All monetary values are integer lamports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum

from desk.core.config import DiscoverySection, LimitsSection, Mode, SizingSection
from desk.core.hashing import fingerprint
from desk.core.units import sol_to_lamports

REQUIRED_DIMENSIONS = (
    "predictive_value", "net_expectancy", "statistical_reliability", "drawdown",
    "downside_tail", "market_robustness", "execution_reliability", "exit_reliability",
    "stress", "generalization", "stability", "cost_efficiency", "data_integrity",
)


def _integer(value: int, name: str, *, signed: bool = False) -> None:
    if type(value) is not int or (not signed and value < 0):
        raise ValueError(f"{name} must be an {'signed ' if signed else 'nonnegative '}integer")


def _decimal(value: Decimal, name: str, *, fraction: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal; binary floats are forbidden")
    if fraction and not Decimal(0) <= value <= Decimal(1):
        raise ValueError(f"{name} must be in [0, 1]")


def _time(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty evidence identifier")


def _boolean(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def _floor_fraction(amount: int, fraction: Decimal) -> int:
    """Exact rational multiplication, independent of ambient Decimal precision."""
    numerator, denominator = fraction.as_integer_ratio()
    return amount * numerator // denominator


def _fresh(observed_at: datetime, now: datetime, seconds: int) -> bool:
    return timedelta(0) <= now - observed_at <= timedelta(seconds=seconds)


class ConfidenceStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 - matrix status, not a credential
    FAIL = "FAIL"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class ConfidenceCell:
    """Research-produced cell; estimates use the metric's documented native unit."""

    dimension: str
    status: ConfidenceStatus
    estimate: Decimal
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    evidence_count: int
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.dimension, "dimension")
        if not isinstance(self.status, ConfidenceStatus):
            raise ValueError("status must be a ConfidenceStatus")
        _decimal(self.estimate, "estimate")
        for name, value in (("lower_bound", self.lower_bound), ("upper_bound", self.upper_bound)):
            if value is not None:
                _decimal(value, name)
        if self.lower_bound is not None and self.upper_bound is not None and self.lower_bound > self.upper_bound:
            raise ValueError("confidence interval bounds are reversed")
        _integer(self.evidence_count, "evidence_count")
        if type(self.evidence_ids) is not tuple or not self.evidence_ids:
            raise ValueError("evidence_ids must be a nonempty immutable tuple")
        for evidence_id in self.evidence_ids:
            _identifier(evidence_id, "evidence_id")


@dataclass(frozen=True, slots=True)
class ConfidenceReport:
    """Closed, reconciled ledger evidence for one strategy version and regime.

    A research job, not this module, must calculate the matrix using PLAN §14.2.
    PASS statuses assert the corresponding §14.1 rules; explicit sample, span,
    regime, positive-bound and integrity checks below cannot be replaced by PASS.
    ``realized_net_profit_lamports`` includes all trading and operating costs.
    No rows derived solely from SHADOW/replay or open positions may qualify.
    """

    report_id: str
    dataset_id: str
    ledger_id: str
    strategy_version: str
    regime: str
    origin: Mode
    as_of: datetime
    first_closed_at: datetime
    last_closed_at: datetime
    closed_trade_count: int
    effective_sample_size: Decimal
    realized_net_profit_lamports: int
    confidence_level: Decimal
    cells: tuple[ConfidenceCell, ...]
    regime_trade_counts: tuple[tuple[str, int], ...]
    formally_restricted_regimes: tuple[str, ...] = ()
    only_closed_reconciled_trades: bool = False
    includes_all_costs: bool = False
    deterioration_alarm: bool = True
    unresolved_critical_issues: int = 1
    calibration_required: bool = False

    def __post_init__(self) -> None:
        for name in ("report_id", "dataset_id", "ledger_id", "strategy_version", "regime"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.origin, Mode):
            raise ValueError("origin must be a Mode")
        for name in ("as_of", "first_closed_at", "last_closed_at"):
            _time(getattr(self, name), name)
        if not self.first_closed_at <= self.last_closed_at <= self.as_of:
            raise ValueError("closed-trade evidence timestamps are inconsistent")
        _integer(self.closed_trade_count, "closed_trade_count")
        _integer(self.realized_net_profit_lamports, "realized_net_profit_lamports", signed=True)
        _integer(self.unresolved_critical_issues, "unresolved_critical_issues")
        for name in (
            "only_closed_reconciled_trades", "includes_all_costs", "deterioration_alarm", "calibration_required",
        ):
            _boolean(getattr(self, name), name)
        _decimal(self.effective_sample_size, "effective_sample_size")
        _decimal(self.confidence_level, "confidence_level", fraction=True)
        if self.effective_sample_size < 0 or self.effective_sample_size > self.closed_trade_count:
            raise ValueError("effective sample size must be between zero and the closed trade count")
        if type(self.cells) is not tuple or len({cell.dimension for cell in self.cells}) != len(self.cells):
            raise ValueError("cells must be an immutable tuple with unique dimensions")
        if type(self.regime_trade_counts) is not tuple or type(self.formally_restricted_regimes) is not tuple:
            raise ValueError("regime evidence must use immutable tuples")
        if len({name for name, _ in self.regime_trade_counts}) != len(self.regime_trade_counts):
            raise ValueError("regime names must be unique")
        for name, count in self.regime_trade_counts:
            _identifier(name, "regime name")
            _integer(count, "regime trade count")
        if sum(count for _, count in self.regime_trade_counts) > self.closed_trade_count:
            raise ValueError("regime trade counts exceed the closed trade count")
        for name in self.formally_restricted_regimes:
            _identifier(name, "restricted regime")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Reconciled account state; deposits are excluded from realized net profit.

    ``free_capital_lamports`` is gross spendable cash BEFORE the one reserve
    deduction below. ``current_exposure_lamports`` is the independent risk
    engine's conservatively valued aggregate open exposure, not free cash.
    """

    snapshot_id: str
    ledger_id: str
    as_of: datetime
    initial_equity_lamports: int
    current_equity_lamports: int
    net_closed_realized_profit_lamports: int
    free_capital_lamports: int
    reserve_lamports: int
    current_exposure_lamports: int
    open_positions: int = 0
    positions_for_dev: int = 0
    positions_for_launchpad: int = 0

    def __post_init__(self) -> None:
        _identifier(self.snapshot_id, "snapshot_id")
        _identifier(self.ledger_id, "ledger_id")
        _time(self.as_of, "as_of")
        for name in (
            "initial_equity_lamports", "current_equity_lamports", "free_capital_lamports",
            "reserve_lamports", "current_exposure_lamports", "open_positions", "positions_for_dev",
            "positions_for_launchpad",
        ):
            _integer(getattr(self, name), name)
        _integer(self.net_closed_realized_profit_lamports, "net_closed_realized_profit_lamports", signed=True)
        if self.initial_equity_lamports == 0:
            raise ValueError("initial equity must be positive")
        if self.free_capital_lamports > self.current_equity_lamports:
            raise ValueError("free capital cannot exceed current equity")


@dataclass(frozen=True, slots=True)
class LiquidityEvidence:
    verification_id: str
    mint: str
    pool_address: str
    as_of: datetime
    liquidity_lamports: int
    verified: bool = False

    def __post_init__(self) -> None:
        for name in ("verification_id", "mint", "pool_address"):
            _identifier(getattr(self, name), name)
        _time(self.as_of, "as_of")
        _integer(self.liquidity_lamports, "liquidity_lamports")
        _boolean(self.verified, "verified")


@dataclass(frozen=True, slots=True)
class RiskGate:
    """Independent M5 gate, including halts, daily losses, drawdowns and rate caps.

    The throttle must come from an already declared deterministic policy, never a
    model's confidence score. This helper does not calculate those other limits.
    """

    evaluation_id: str
    throttle_policy_id: str
    decision_id: str
    account_snapshot_id: str
    as_of: datetime
    drawdown_throttle: Decimal
    new_entries_allowed: bool = False

    def __post_init__(self) -> None:
        _identifier(self.evaluation_id, "evaluation_id")
        _identifier(self.throttle_policy_id, "throttle_policy_id")
        _identifier(self.decision_id, "decision_id")
        _identifier(self.account_snapshot_id, "account_snapshot_id")
        _time(self.as_of, "as_of")
        _decimal(self.drawdown_throttle, "drawdown_throttle", fraction=True)
        _boolean(self.new_entries_allowed, "new_entries_allowed")


@dataclass(frozen=True, slots=True)
class CostEvidence:
    """Fresh round trip for exactly one proposed ticket, with explicit fee scope.

    ``swap_loss_lamports`` includes DEX/route fees, impact and conservative
    slippage, but EXCLUDES fixed transaction fees. ``fixed_fees_lamports`` is the
    complete nonrefundable round-trip signature/priority cost. Upfront fees are
    a subset of that total. Refundable rent is a cash lock, never a P&L fee.
    All fixed fees are funded separately from the protected account reserve;
    that reserve is subtracted from free capital exactly once.

    Compounding also needs a conservative expectancy lower bound for THIS size
    after measured costs, linked to the current confidence report. A future
    research/cost model must produce it; a quote alone does not measure edge.
    """

    quote_id: str
    decision_id: str
    mint: str
    pool_address: str
    strategy_version: str
    quoted_at: datetime
    ticket_lamports: int
    swap_loss_lamports: int
    fixed_fees_lamports: int
    upfront_fees_lamports: int
    refundable_rent_lock_lamports: int
    roundtrip_complete: bool = False
    fixed_fees_complete: bool = False
    net_expectancy_lower_bound: Decimal | None = None
    expectancy_report_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("quote_id", "decision_id", "mint", "pool_address", "strategy_version"):
            _identifier(getattr(self, name), name)
        _time(self.quoted_at, "quoted_at")
        for name in (
            "ticket_lamports", "swap_loss_lamports", "fixed_fees_lamports", "upfront_fees_lamports",
            "refundable_rent_lock_lamports",
        ):
            _integer(getattr(self, name), name)
        if self.upfront_fees_lamports > self.fixed_fees_lamports:
            raise ValueError("upfront fees cannot exceed complete round-trip fixed fees")
        _boolean(self.roundtrip_complete, "roundtrip_complete")
        _boolean(self.fixed_fees_complete, "fixed_fees_complete")
        if self.net_expectancy_lower_bound is not None:
            _decimal(self.net_expectancy_lower_bound, "net_expectancy_lower_bound")
        if self.expectancy_report_id is not None:
            _identifier(self.expectancy_report_id, "expectancy_report_id")


@dataclass(frozen=True, slots=True)
class SizingPolicy:
    """Explicit proposed policy for this foundation; not new runtime settings.

    Default evidence thresholds are PLAN §14.1, not an invented confidence
    percentage. Runtime integration and any changes require separate review.
    This object cannot weaken those evidence floors or increase configured caps.
    """

    version: str = "paper-profit-compounding-v1"
    min_closed_trades: int = 300
    min_effective_sample_size: Decimal = Decimal(150)
    min_calendar_days: int = 60
    min_trades_per_regime: int = 50
    max_matrix_age_seconds: int = 86400
    max_quote_age_seconds: int = 10
    max_roundtrip_cost_fraction: Decimal = Decimal("0.03")

    def __post_init__(self) -> None:
        _identifier(self.version, "version")
        for name in (
            "min_closed_trades", "min_calendar_days", "min_trades_per_regime", "max_matrix_age_seconds",
            "max_quote_age_seconds",
        ):
            _integer(getattr(self, name), name)
        _decimal(self.min_effective_sample_size, "min_effective_sample_size")
        _decimal(self.max_roundtrip_cost_fraction, "max_roundtrip_cost_fraction", fraction=True)
        if (self.min_closed_trades < 300 or self.min_effective_sample_size < 150
                or self.min_calendar_days < 60 or self.min_trades_per_regime < 50):
            raise ValueError("policy cannot weaken PLAN §14.1 evidence floors")
        if self.max_matrix_age_seconds == 0 or self.max_quote_age_seconds == 0:
            raise ValueError("freshness windows must be positive")


@dataclass(frozen=True, slots=True)
class SizingRequest:
    decision_id: str
    strategy_version: str
    regime: str
    mint: str
    pool_address: str
    as_of: datetime
    mode: Mode = Mode.SHADOW

    def __post_init__(self) -> None:
        for name in ("decision_id", "strategy_version", "regime", "mint", "pool_address"):
            _identifier(getattr(self, name), name)
        _time(self.as_of, "as_of")
        if not isinstance(self.mode, Mode):
            raise ValueError("mode must be a Mode")


@dataclass(frozen=True, slots=True)
class SizingDecision:
    target_ticket_lamports: int
    authorized_ticket_lamports: int
    effective_capital_lamports: int
    baseline_ticket_lamports: int
    scale_allowed: bool
    confidence_gate_passed: bool
    requires_requote: bool
    reasons: tuple[str, ...]
    policy_version: str
    policy_hash: str
    confidence_report_id: str | None
    quote_id: str | None


def _confidence_reasons(
    report: ConfidenceReport | None, request: SizingRequest, account: AccountSnapshot, policy: SizingPolicy,
) -> list[str]:
    if report is None:
        return ["CONFIDENCE_MISSING"]
    reasons: list[str] = []
    if report.ledger_id != account.ledger_id:
        reasons.append("CONFIDENCE_WRONG_LEDGER")
    if report.origin != Mode.PAPER or not report.only_closed_reconciled_trades:
        reasons.append("CONFIDENCE_REQUIRES_CLOSED_RECONCILED_PAPER")
    if not report.includes_all_costs or report.realized_net_profit_lamports <= 0:
        reasons.append("CONFIDENCE_NET_PROFIT_NOT_PROVEN")
    if report.strategy_version != request.strategy_version:
        reasons.append("CONFIDENCE_WRONG_STRATEGY_VERSION")
    if report.regime != request.regime:
        reasons.append("CONFIDENCE_WRONG_REGIME")
    if not _fresh(report.as_of, request.as_of, policy.max_matrix_age_seconds):
        reasons.append("CONFIDENCE_STALE_OR_FUTURE")
    if (report.closed_trade_count < policy.min_closed_trades
            or report.effective_sample_size < policy.min_effective_sample_size
            or report.last_closed_at - report.first_closed_at < timedelta(days=policy.min_calendar_days)):
        reasons.append("CONFIDENCE_INSUFFICIENT_SAMPLE")
    if report.confidence_level < Decimal("0.95"):
        reasons.append("CONFIDENCE_INTERVAL_TOO_WEAK")
    if report.deterioration_alarm or report.unresolved_critical_issues:
        reasons.append("CONFIDENCE_DETERIORATION_OR_CRITICAL_ISSUE")
    cells = {cell.dimension: cell for cell in report.cells}
    required = (*REQUIRED_DIMENSIONS, "calibration") if report.calibration_required else REQUIRED_DIMENSIONS
    if any(
        name not in cells or cells[name].status != ConfidenceStatus.PASS or cells[name].evidence_count == 0
        for name in required
    ):
        reasons.append("CONFIDENCE_REQUIRED_CELL_NOT_PASS")
    positive_bounds = [cells[name].lower_bound if name in cells else None for name in (
        "predictive_value", "net_expectancy", "generalization",
    )]
    if any(bound is None or bound <= 0 for bound in positive_bounds):
        reasons.append("CONFIDENCE_POSITIVE_LOWER_BOUNDS_NOT_PROVEN")
    if "stress" not in cells or cells["stress"].estimate <= 0:
        reasons.append("CONFIDENCE_STRESS_NOT_POSITIVE")
    qualified_regimes = {name for name, count in report.regime_trade_counts if count >= policy.min_trades_per_regime}
    if report.formally_restricted_regimes:
        if (request.regime not in report.formally_restricted_regimes
                or not set(report.formally_restricted_regimes) <= qualified_regimes):
            reasons.append("CONFIDENCE_REGIME_ROBUSTNESS_NOT_PROVEN")
    elif request.regime not in qualified_regimes or len(qualified_regimes) < 2:
        reasons.append("CONFIDENCE_REGIME_ROBUSTNESS_NOT_PROVEN")
    return reasons


def recommend_paper_ticket(
    request: SizingRequest,
    account: AccountSnapshot,
    liquidity: LiquidityEvidence,
    risk_gate: RiskGate,
    sizing: SizingSection,
    limits: LimitsSection,
    *,
    policy: SizingPolicy,
    discovery: DiscoverySection,
    confidence: ConfidenceReport | None = None,
    quote: CostEvidence | None = None,
) -> SizingDecision:
    """Return an auditable recommendation; never submit, fill, or arm anything.

    Confidence failure prevents compounding; it does not invent a higher fraction
    or turn ordinary PAPER data collection off. Other entry gates still apply.
    ``authorized_ticket_lamports`` is strictly zero outside PAPER and without a
    complete, fresh quote of the final target. It is not execution authorization.
    The loaded discovery configuration bounds quote age and round-trip cost;
    a caller's policy may tighten those bounds but cannot relax them.
    """
    max_quote_age = min(policy.max_quote_age_seconds, discovery.max_quote_age_seconds)
    max_roundtrip_cost = min(policy.max_roundtrip_cost_fraction, discovery.max_roundtrip_cost_fraction)
    policy_snapshot = {
        "policy": asdict(policy), "sizing": sizing.model_dump(mode="python"),
        "limits": limits.model_dump(mode="python"), "discovery": discovery.model_dump(mode="python"),
    }
    # fingerprint canonicalizes Decimals with normalize(), so give every input
    # enough precision even if an unrelated caller changed its Decimal context.
    decimal_precision = max(
        (len(value.as_tuple().digits) for section in policy_snapshot.values() for value in section.values()
         if isinstance(value, Decimal)), default=28,
    )
    with localcontext() as context:
        context.prec = max(28, decimal_precision)
        policy_hash = fingerprint(policy_snapshot)
    reasons = _confidence_reasons(confidence, request, account, policy)
    confidence_passed = not reasons
    profitable = account.net_closed_realized_profit_lamports > 0
    if not profitable:
        reasons.append("NO_POSITIVE_CLOSED_NET_PROFIT")
    baseline_capital = min(account.initial_equity_lamports, account.current_equity_lamports)
    effective_capital = baseline_capital
    if confidence_passed and profitable:
        effective_capital = min(
            account.current_equity_lamports,
            account.initial_equity_lamports + account.net_closed_realized_profit_lamports,
        )
    deployable = max(0, account.free_capital_lamports - account.reserve_lamports)
    exposure_remaining = max(
        0,
        _floor_fraction(account.current_equity_lamports, limits.max_total_exposure_fraction)
        - account.current_exposure_lamports,
    )
    common_cap = min(
        _floor_fraction(deployable, sizing.max_ticket_fraction_free_capital),
        _floor_fraction(liquidity.liquidity_lamports, sizing.max_ticket_fraction_pool_liquidity),
        exposure_remaining, deployable,
    )
    if sizing.max_ticket_sol is not None:
        common_cap = min(common_cap, sol_to_lamports(sizing.max_ticket_sol))
    baseline_cap = min(common_cap, _floor_fraction(baseline_capital, sizing.max_ticket_fraction_equity))
    target_cap = min(common_cap, _floor_fraction(effective_capital, sizing.max_ticket_fraction_equity))
    baseline_ticket = _floor_fraction(
        min(_floor_fraction(baseline_capital, sizing.fixed_fraction), baseline_cap), risk_gate.drawdown_throttle,
    )
    target = _floor_fraction(
        min(_floor_fraction(effective_capital, sizing.fixed_fraction), target_cap), risk_gate.drawdown_throttle,
    )
    blocked = False
    if request.mode != Mode.PAPER:
        reasons.append("PAPER_ONLY_NO_MODE_AUTHORIZATION")
        blocked = True
    if (not risk_gate.new_entries_allowed or risk_gate.decision_id != request.decision_id
            or risk_gate.account_snapshot_id != account.snapshot_id):
        reasons.append("INDEPENDENT_RISK_GATE_BLOCKED")
        blocked = True
    if not all(_fresh(value, request.as_of, limits.stale_data_halt_seconds) for value in (
        account.as_of, risk_gate.as_of, liquidity.as_of,
    )):
        reasons.append("CRITICAL_EVIDENCE_STALE_OR_FUTURE")
        blocked = True
    if not liquidity.verified or (liquidity.mint, liquidity.pool_address) != (request.mint, request.pool_address):
        reasons.append("LIQUIDITY_NOT_VERIFIED_FOR_POOL")
        blocked = True
    if account.reserve_lamports < sol_to_lamports(limits.live_sol_reserve):
        reasons.append("RESERVE_BELOW_CONFIGURED_MINIMUM")
        blocked = True
    if (account.open_positions >= limits.max_open_positions or account.positions_for_dev >= limits.max_positions_per_dev
            or account.positions_for_launchpad >= limits.max_positions_per_launchpad):
        reasons.append("POSITION_LIMIT_REACHED")
        blocked = True
    if target == 0 or target < sol_to_lamports(sizing.min_ticket_sol):
        reasons.append("TICKET_BELOW_MINIMUM_OR_RISK_BUDGET_EXHAUSTED")
        blocked = True
    requires_requote = False
    if quote is None:
        reasons.append("REQUIRES_EXACT_TICKET_QUOTE")
        requires_requote = True
    elif (
        (quote.decision_id, quote.mint, quote.pool_address, quote.strategy_version, quote.ticket_lamports)
        != (request.decision_id, request.mint, request.pool_address, request.strategy_version, target)
        or not _fresh(quote.quoted_at, request.as_of, max_quote_age)
        or not quote.roundtrip_complete or not quote.fixed_fees_complete
    ):
        reasons.append("REQUIRES_FRESH_COMPLETE_EXACT_TICKET_QUOTE")
        requires_requote = True
    else:
        if quote.fixed_fees_lamports > _floor_fraction(target, sizing.max_fixed_cost_fraction):
            reasons.append("FIXED_FEE_FLOOR_FAILED_NO_UPSIZING")
            blocked = True
        if quote.swap_loss_lamports + quote.fixed_fees_lamports > _floor_fraction(target, max_roundtrip_cost):
            reasons.append("ROUNDTRIP_COST_CAP_EXCEEDED")
            blocked = True
        if target + quote.fixed_fees_lamports + quote.refundable_rent_lock_lamports > deployable:
            reasons.append("INSUFFICIENT_CASH_AFTER_PROTECTED_RESERVE")
            blocked = True
        if target > baseline_ticket:
            net_cell = (
                next((cell for cell in confidence.cells if cell.dimension == "net_expectancy"), None) if confidence else None
            )
            historical_bound = net_cell.lower_bound if net_cell is not None else None
            if (confidence is None or quote.expectancy_report_id != confidence.report_id
                    or quote.net_expectancy_lower_bound is None or quote.net_expectancy_lower_bound <= 0
                    or historical_bound is None or quote.net_expectancy_lower_bound > historical_bound):
                reasons.append("SIZED_NET_EXPECTANCY_NOT_PROVEN")
                blocked = True
    authorized = 0 if blocked or requires_requote else target
    scale_allowed = confidence_passed and profitable and authorized > baseline_ticket
    if scale_allowed:
        reasons.append("REALIZED_PROFIT_COMPOUNDING_WITHIN_EXISTING_LIMITS")
    elif confidence_passed and profitable and target <= baseline_ticket:
        reasons.append("NO_SIZE_INCREASE_WITHIN_CURRENT_CAPS_OR_EQUITY")
    return SizingDecision(
        target_ticket_lamports=target, authorized_ticket_lamports=authorized,
        effective_capital_lamports=effective_capital, baseline_ticket_lamports=baseline_ticket,
        scale_allowed=scale_allowed, confidence_gate_passed=confidence_passed, requires_requote=requires_requote,
        reasons=tuple(reasons), policy_version=policy.version, policy_hash=policy_hash,
        confidence_report_id=confidence.report_id if confidence is not None else None,
        quote_id=quote.quote_id if quote is not None else None,
    )
