from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from desk.core.config import DiscoverySection, LimitsSection, Mode, SizingSection, load_config
from desk.risk.sizing import (
    REQUIRED_DIMENSIONS,
    AccountSnapshot,
    ConfidenceCell,
    ConfidenceReport,
    ConfidenceStatus,
    CostEvidence,
    LiquidityEvidence,
    RiskGate,
    SizingDecision,
    SizingPolicy,
    SizingRequest,
    recommend_paper_ticket,
)

NOW = datetime(2026, 10, 19, 8, tzinfo=UTC)
SOL = 1_000_000_000
MINT = "fixture-mint"
POOL = "fixture-pool"


@pytest.fixture
def sections() -> tuple[SizingSection, LimitsSection]:
    config = load_config(Path("config")).settings
    return config.sizing, config.limits


def _request(**changes: object) -> SizingRequest:
    return replace(SizingRequest("decision-1", "champion-v1", "RISK_ON", MINT, POOL, NOW, Mode.PAPER), **changes)


def _account(**changes: object) -> AccountSnapshot:
    return replace(AccountSnapshot(
        snapshot_id="account-1", ledger_id="paper-ledger-1", as_of=NOW,
        initial_equity_lamports=SOL, current_equity_lamports=1_400_000_000,
        net_closed_realized_profit_lamports=400_000_000, free_capital_lamports=1_400_000_000,
        reserve_lamports=50_000_000, current_exposure_lamports=0,
    ), **changes)


def _liquidity(**changes: object) -> LiquidityEvidence:
    return replace(LiquidityEvidence("verified-1", MINT, POOL, NOW, 100 * SOL, True), **changes)


def _risk(**changes: object) -> RiskGate:
    return replace(RiskGate("risk-1", "drawdown-policy-v1", "decision-1", "account-1", NOW, Decimal(1), True), **changes)


def _confidence(**changes: object) -> ConfidenceReport:
    cells = tuple(ConfidenceCell(
        dimension=name, status=ConfidenceStatus.PASS, estimate=Decimal("0.03"),
        lower_bound=Decimal("0.01"), upper_bound=Decimal("0.06"), evidence_count=300,
        evidence_ids=("registered-experiment-1", "stationary-bootstrap-v1"),
    ) for name in REQUIRED_DIMENSIONS)
    return replace(ConfidenceReport(
        report_id="report-1", dataset_id="closed-reconciled-dataset-1", ledger_id="paper-ledger-1",
        strategy_version="champion-v1", regime="RISK_ON", origin=Mode.PAPER, as_of=NOW,
        first_closed_at=NOW - timedelta(days=60), last_closed_at=NOW,
        closed_trade_count=300, effective_sample_size=Decimal(150), realized_net_profit_lamports=400_000_000,
        confidence_level=Decimal("0.95"), cells=cells, regime_trade_counts=(("RISK_ON", 150), ("NEUTRAL", 150)),
        only_closed_reconciled_trades=True, includes_all_costs=True, deterioration_alarm=False, unresolved_critical_issues=0,
    ), **changes)


def _quote(ticket: int = 140_000_000, **changes: object) -> CostEvidence:
    return replace(CostEvidence(
        quote_id="quote-1", decision_id="decision-1", mint=MINT, pool_address=POOL, strategy_version="champion-v1",
        quoted_at=NOW, ticket_lamports=ticket, swap_loss_lamports=1_000_000, fixed_fees_lamports=100_000,
        upfront_fees_lamports=50_000, refundable_rent_lock_lamports=1_500_000,
        roundtrip_complete=True, fixed_fees_complete=True,
        net_expectancy_lower_bound=Decimal("0.005"), expectancy_report_id="report-1",
    ), **changes)


def _decide(
    sections: tuple[SizingSection, LimitsSection], *, confidence: ConfidenceReport | None = None,
    account: AccountSnapshot | None = None, liquidity: LiquidityEvidence | None = None,
    risk: RiskGate | None = None, quote: CostEvidence | None = None, request: SizingRequest | None = None,
    policy: SizingPolicy | None = None, discovery: DiscoverySection | None = None,
) -> SizingDecision:
    return recommend_paper_ticket(
        request or _request(), account or _account(), liquidity or _liquidity(), risk or _risk(), *sections,
        policy=policy or SizingPolicy(), discovery=discovery or load_config(Path("config")).settings.discovery,
        confidence=confidence, quote=quote,
    )


def test_only_closed_profit_and_complete_current_matrix_can_compound(sections: tuple[SizingSection, LimitsSection]) -> None:
    decision = _decide(sections, confidence=_confidence(), quote=_quote())
    assert decision.effective_capital_lamports == 1_400_000_000
    assert decision.baseline_ticket_lamports == 100_000_000
    assert decision.authorized_ticket_lamports == 140_000_000
    assert decision.scale_allowed and decision.confidence_gate_passed
    assert decision.policy_version == "paper-profit-compounding-v1"
    assert decision.confidence_report_id == "report-1"


@pytest.mark.parametrize("change,reason", [
    ({"closed_trade_count": 299, "regime_trade_counts": (("RISK_ON", 150), ("NEUTRAL", 149))}, "INSUFFICIENT_SAMPLE"),
    ({"effective_sample_size": Decimal("149.999")}, "INSUFFICIENT_SAMPLE"),
    ({"first_closed_at": NOW - timedelta(days=59)}, "INSUFFICIENT_SAMPLE"),
    ({"as_of": NOW - timedelta(days=2), "last_closed_at": NOW - timedelta(days=2)}, "STALE_OR_FUTURE"),
    ({"as_of": NOW + timedelta(seconds=1)}, "STALE_OR_FUTURE"),
    ({"strategy_version": "old-version"}, "WRONG_STRATEGY_VERSION"),
    ({"regime": "RISK_OFF"}, "WRONG_REGIME"),
    ({"ledger_id": "unrelated-account"}, "WRONG_LEDGER"),
    ({"origin": Mode.SHADOW}, "CLOSED_RECONCILED_PAPER"),
    ({"only_closed_reconciled_trades": False}, "CLOSED_RECONCILED_PAPER"),
    ({"includes_all_costs": False}, "NET_PROFIT_NOT_PROVEN"),
    ({"realized_net_profit_lamports": -1}, "NET_PROFIT_NOT_PROVEN"),
    ({"deterioration_alarm": True}, "DETERIORATION_OR_CRITICAL_ISSUE"),
    ({"unresolved_critical_issues": 1}, "DETERIORATION_OR_CRITICAL_ISSUE"),
    ({"confidence_level": Decimal("0.94")}, "INTERVAL_TOO_WEAK"),
    ({"calibration_required": True}, "REQUIRED_CELL_NOT_PASS"),
    ({"regime_trade_counts": (("RISK_ON", 300),)}, "REGIME_ROBUSTNESS_NOT_PROVEN"),
])
def test_evidence_failures_freeze_capital_at_seed_without_blocking_baseline_paper(
    sections: tuple[SizingSection, LimitsSection], change: dict[str, object], reason: str,
) -> None:
    result = _decide(sections, confidence=_confidence(**change), quote=_quote(100_000_000))
    assert result.effective_capital_lamports == SOL
    assert result.authorized_ticket_lamports == 100_000_000
    assert not result.scale_allowed and not result.confidence_gate_passed
    assert any(reason in value for value in result.reasons)


@pytest.mark.parametrize("status", [ConfidenceStatus.FAIL, ConfidenceStatus.INSUFFICIENT])
def test_any_required_cell_blocks_scaling(sections: tuple[SizingSection, LimitsSection], status: ConfidenceStatus) -> None:
    report = _confidence()
    cells = (replace(report.cells[0], status=status), *report.cells[1:])
    result = _decide(sections, confidence=replace(report, cells=cells), quote=_quote(100_000_000))
    assert not result.scale_allowed
    assert "CONFIDENCE_REQUIRED_CELL_NOT_PASS" in result.reasons


@pytest.mark.parametrize("dimension", ["net_expectancy", "predictive_value", "generalization"])
def test_pass_label_cannot_override_nonpositive_lower_bound(
    sections: tuple[SizingSection, LimitsSection], dimension: str,
) -> None:
    report = _confidence()
    cells = tuple(replace(cell, lower_bound=Decimal(0)) if cell.dimension == dimension else cell for cell in report.cells)
    result = _decide(sections, confidence=replace(report, cells=cells), quote=_quote(100_000_000))
    assert not result.confidence_gate_passed
    assert "CONFIDENCE_POSITIVE_LOWER_BOUNDS_NOT_PROVEN" in result.reasons


def test_restricted_strategy_must_prove_its_current_regime(sections: tuple[SizingSection, LimitsSection]) -> None:
    report = _confidence(regime_trade_counts=(("RISK_ON", 300),), formally_restricted_regimes=("RISK_ON",))
    assert _decide(sections, confidence=report, quote=_quote()).scale_allowed
    wrong = replace(report, formally_restricted_regimes=("NEUTRAL",))
    assert not _decide(sections, confidence=wrong, quote=_quote(100_000_000)).scale_allowed


@pytest.mark.parametrize("profit", [0, -100_000_000])
def test_unrealized_gains_or_deposits_do_not_count_as_closed_profit(
    sections: tuple[SizingSection, LimitsSection], profit: int,
) -> None:
    account = _account(current_equity_lamports=2 * SOL, free_capital_lamports=2 * SOL,
                       net_closed_realized_profit_lamports=profit)
    decision = _decide(sections, account=account, confidence=_confidence(), quote=_quote(100_000_000))
    assert decision.authorized_ticket_lamports == 100_000_000
    assert not decision.scale_allowed


def test_deposits_are_excluded_even_with_positive_realized_profit(sections: tuple[SizingSection, LimitsSection]) -> None:
    account = _account(current_equity_lamports=2 * SOL, free_capital_lamports=2 * SOL,
                       net_closed_realized_profit_lamports=200_000_000)
    decision = _decide(sections, account=account, confidence=_confidence(), quote=_quote(120_000_000))
    assert decision.effective_capital_lamports == 1_200_000_000
    assert decision.authorized_ticket_lamports == 120_000_000


def test_equity_losses_reduce_size_despite_earlier_realized_wins(sections: tuple[SizingSection, LimitsSection]) -> None:
    account = _account(current_equity_lamports=700_000_000, free_capital_lamports=700_000_000)
    result = _decide(sections, account=account, confidence=_confidence(), quote=_quote(70_000_000))
    assert result.effective_capital_lamports == 700_000_000
    assert result.authorized_ticket_lamports == 70_000_000
    assert not result.scale_allowed


def test_optional_absolute_liquidity_free_capital_and_remaining_exposure_caps_bind(
    sections: tuple[SizingSection, LimitsSection],
) -> None:
    rich = _account(current_equity_lamports=3 * SOL, free_capital_lamports=3 * SOL,
                    net_closed_realized_profit_lamports=2 * SOL)
    sizing, limits = sections
    legacy = SizingSection.model_validate({**sizing.model_dump(), "max_ticket_sol": Decimal("0.15")})
    assert _decide((legacy, limits), account=rich, confidence=_confidence()).target_ticket_lamports == 150_000_000
    thin = _decide(sections, liquidity=_liquidity(liquidity_lamports=12 * SOL), confidence=_confidence())
    assert thin.target_ticket_lamports == 60_000_000
    cash_poor = _account(free_capital_lamports=450_000_000)
    assert _decide(sections, account=cash_poor, confidence=_confidence()).target_ticket_lamports == 60_000_000
    exposed = _account(current_exposure_lamports=290_000_000)
    assert _decide(sections, account=exposed, confidence=_confidence()).target_ticket_lamports == 60_000_000


def test_relative_cap_scales_with_eligible_account_without_legacy_absolute_limit(
    sections: tuple[SizingSection, LimitsSection],
) -> None:
    account = _account(initial_equity_lamports=10 * SOL, current_equity_lamports=20 * SOL,
                       free_capital_lamports=20 * SOL, net_closed_realized_profit_lamports=10 * SOL)
    liquidity = _liquidity(liquidity_lamports=1000 * SOL)
    decision = _decide(sections, account=account, liquidity=liquidity, confidence=_confidence(), quote=_quote(2 * SOL))
    assert decision.authorized_ticket_lamports == 2 * SOL
    assert decision.scale_allowed
    # The same balance without sufficient evidence still uses the original 10 SOL base.
    baseline = _decide(sections, account=account, liquidity=liquidity, quote=_quote(SOL))
    assert baseline.authorized_ticket_lamports == SOL
    assert not baseline.scale_allowed


def test_tightened_relative_cap_uses_qualified_capital_and_shrinks_with_equity(
    sections: tuple[SizingSection, LimitsSection],
) -> None:
    sizing, limits = sections
    tightened = SizingSection.model_validate({**sizing.model_dump(), "max_ticket_fraction_equity": Decimal("0.06")})
    account = _account(initial_equity_lamports=10 * SOL, current_equity_lamports=20 * SOL,
                       free_capital_lamports=20 * SOL, net_closed_realized_profit_lamports=10 * SOL)
    liquidity = _liquidity(liquidity_lamports=1000 * SOL)
    growth = _decide((tightened, limits), account=account, liquidity=liquidity,
                     confidence=_confidence(), quote=_quote(1_200_000_000))
    assert growth.authorized_ticket_lamports == 1_200_000_000
    unqualified = _decide((tightened, limits), account=account, liquidity=liquidity, quote=_quote(600_000_000))
    assert unqualified.authorized_ticket_lamports == 600_000_000
    losses = replace(account, current_equity_lamports=7 * SOL, free_capital_lamports=7 * SOL)
    shrunken = _decide((tightened, limits), account=losses, liquidity=liquidity,
                       confidence=_confidence(), quote=_quote(420_000_000))
    assert shrunken.authorized_ticket_lamports == 420_000_000 and not shrunken.scale_allowed


def test_drawdown_throttle_never_increases_risk_and_can_halt(sections: tuple[SizingSection, LimitsSection]) -> None:
    throttled = _decide(sections, confidence=_confidence(), risk=_risk(drawdown_throttle=Decimal("0.5")),
                        quote=_quote(70_000_000))
    assert throttled.authorized_ticket_lamports == 70_000_000
    halted = _decide(sections, confidence=_confidence(), risk=_risk(drawdown_throttle=Decimal(0)), quote=_quote(0))
    assert halted.authorized_ticket_lamports == 0


@pytest.mark.parametrize("quote_change", [
    {"ticket_lamports": 100_000_000}, {"mint": "wrong-mint"}, {"pool_address": "wrong-pool"},
    {"decision_id": "old-decision"}, {"strategy_version": "old-version"},
    {"quoted_at": NOW - timedelta(seconds=11)}, {"quoted_at": NOW + timedelta(seconds=1)},
    {"roundtrip_complete": False}, {"fixed_fees_complete": False},
])
def test_larger_target_requires_its_own_fresh_complete_quote(
    sections: tuple[SizingSection, LimitsSection], quote_change: dict[str, object],
) -> None:
    result = _decide(sections, confidence=_confidence(), quote=_quote(**quote_change))
    assert result.target_ticket_lamports == 140_000_000
    assert result.authorized_ticket_lamports == 0
    assert result.requires_requote and not result.scale_allowed


def test_no_quote_returns_target_without_authorizing_and_incomplete_fixed_fees_cannot_pass(
    sections: tuple[SizingSection, LimitsSection],
) -> None:
    target = _decide(sections, confidence=_confidence())
    assert target.target_ticket_lamports == 140_000_000 and target.requires_requote
    assert target.authorized_ticket_lamports == 0


@pytest.mark.parametrize("changes", [
    {"net_expectancy_lower_bound": None}, {"net_expectancy_lower_bound": Decimal(0)},
    {"net_expectancy_lower_bound": Decimal("-0.001")}, {"expectancy_report_id": "unrelated-report"},
    {"net_expectancy_lower_bound": Decimal("0.02")},
])
def test_larger_quote_must_still_have_positive_conservative_expected_net_edge(
    sections: tuple[SizingSection, LimitsSection], changes: dict[str, object],
) -> None:
    result = _decide(sections, confidence=_confidence(), quote=_quote(**changes))
    assert result.target_ticket_lamports == 140_000_000
    assert result.authorized_ticket_lamports == 0 and not result.scale_allowed
    assert "SIZED_NET_EXPECTANCY_NOT_PROVEN" in result.reasons


def test_cost_caps_are_inclusive_and_never_upsize_to_amortize_fees(sections: tuple[SizingSection, LimitsSection]) -> None:
    boundary = _quote(fixed_fees_lamports=1_400_000, swap_loss_lamports=2_800_000)
    assert _decide(sections, confidence=_confidence(), quote=boundary).authorized_ticket_lamports == 140_000_000
    expensive = replace(boundary, fixed_fees_lamports=1_400_001)
    result = _decide(sections, confidence=_confidence(), quote=expensive)
    assert result.target_ticket_lamports == 140_000_000 and result.authorized_ticket_lamports == 0
    assert "FIXED_FEE_FLOOR_FAILED_NO_UPSIZING" in result.reasons
    roundtrip = replace(boundary, swap_loss_lamports=2_800_001)
    assert "ROUNDTRIP_COST_CAP_EXCEEDED" in _decide(sections, confidence=_confidence(), quote=roundtrip).reasons


def test_loose_policy_cannot_relax_configured_quote_age_or_cost(sections: tuple[SizingSection, LimitsSection]) -> None:
    loose = SizingPolicy(max_quote_age_seconds=3600, max_roundtrip_cost_fraction=Decimal("0.10"))
    stale = _decide(sections, confidence=_confidence(), policy=loose,
                    quote=_quote(quoted_at=NOW - timedelta(seconds=11)))
    assert stale.authorized_ticket_lamports == 0 and stale.requires_requote
    expensive = _decide(sections, confidence=_confidence(), policy=loose,
                        quote=_quote(swap_loss_lamports=4_100_001))
    assert expensive.authorized_ticket_lamports == 0
    assert "ROUNDTRIP_COST_CAP_EXCEEDED" in expensive.reasons


def test_tighter_config_and_tighter_policy_each_remain_binding(sections: tuple[SizingSection, LimitsSection]) -> None:
    discovery = load_config(Path("config"), {
        "discovery.max_quote_age_seconds": 3, "discovery.max_roundtrip_cost_fraction": "0.005",
    }).settings.discovery
    old = _decide(sections, confidence=_confidence(), discovery=discovery,
                  quote=_quote(quoted_at=NOW - timedelta(seconds=4)))
    costly = _decide(sections, confidence=_confidence(), discovery=discovery, quote=_quote())
    assert old.authorized_ticket_lamports == 0 and old.requires_requote
    assert costly.authorized_ticket_lamports == 0 and "ROUNDTRIP_COST_CAP_EXCEEDED" in costly.reasons
    strict = SizingPolicy(max_quote_age_seconds=3, max_roundtrip_cost_fraction=Decimal("0.005"))
    assert _decide(sections, confidence=_confidence(), policy=strict, quote=_quote()).authorized_ticket_lamports == 0
    assert _decide(sections, policy=strict, quote=_quote(100_000_000, quoted_at=NOW - timedelta(seconds=4))).requires_requote


def test_policy_hash_records_all_tuning_even_when_policy_version_is_unchanged(
    sections: tuple[SizingSection, LimitsSection],
) -> None:
    original = _decide(sections)
    policy_change = _decide(sections, policy=SizingPolicy(max_quote_age_seconds=9))
    sizing, limits = sections
    sizing_change = _decide((SizingSection.model_validate({**sizing.model_dump(), "fixed_fraction": "0.09"}), limits))
    limits_change = _decide((sizing, LimitsSection.model_validate({**limits.model_dump(), "max_open_positions": 1})))
    discovery = load_config(Path("config"), {"discovery.max_quote_age_seconds": 9}).settings.discovery
    discovery_change = _decide(sections, discovery=discovery)
    decisions = (original, policy_change, sizing_change, limits_change, discovery_change)
    assert len({value.policy_hash for value in decisions}) == 5
    assert len({value.policy_version for value in decisions}) == 1
    assert all(len(value.policy_hash) == 64 for value in decisions)
    assert original.policy_hash == _decide(sections).policy_hash


def test_rent_is_not_a_fee_and_reserve_is_subtracted_exactly_once(sections: tuple[SizingSection, LimitsSection]) -> None:
    account = _account(free_capital_lamports=450_000_000)
    cash_limit = _quote(60_000_000, swap_loss_lamports=100_000, fixed_fees_lamports=100_000,
                        upfront_fees_lamports=100_000, refundable_rent_lock_lamports=339_900_000)
    result = _decide(sections, account=account, confidence=_confidence(), quote=cash_limit)
    assert result.authorized_ticket_lamports == 60_000_000
    one_more = replace(cash_limit, refundable_rent_lock_lamports=339_900_001)
    assert "INSUFFICIENT_CASH_AFTER_PROTECTED_RESERVE" in _decide(
        sections, account=account, confidence=_confidence(), quote=one_more,
    ).reasons
    # Exit fees are also funded outside the protected reserve before entry.
    exit_fee = replace(cash_limit, upfront_fees_lamports=50_000, fixed_fees_lamports=100_001)
    assert "INSUFFICIENT_CASH_AFTER_PROTECTED_RESERVE" in _decide(
        sections, account=account, confidence=_confidence(), quote=exit_fee,
    ).reasons


@pytest.mark.parametrize("changes", [
    {"open_positions": 2}, {"positions_for_dev": 1}, {"positions_for_launchpad": 1},
    {"reserve_lamports": 49_999_999}, {"as_of": NOW - timedelta(seconds=121)},
])
def test_binding_account_entry_constraints(sections: tuple[SizingSection, LimitsSection], changes: dict[str, object]) -> None:
    result = _decide(sections, account=_account(**changes), confidence=_confidence(), quote=_quote())
    assert result.authorized_ticket_lamports == 0


@pytest.mark.parametrize("mode", [Mode.SHADOW, Mode.LIVE])
def test_matrix_never_arms_live_or_activates_paper(sections: tuple[SizingSection, LimitsSection], mode: Mode) -> None:
    result = _decide(sections, request=_request(mode=mode), confidence=_confidence(), quote=_quote())
    assert result.authorized_ticket_lamports == 0 and not result.scale_allowed
    assert "PAPER_ONLY_NO_MODE_AUTHORIZATION" in result.reasons


def test_unverified_liquidity_or_unrelated_risk_gate_cannot_authorize(sections: tuple[SizingSection, LimitsSection]) -> None:
    assert _decide(sections, liquidity=_liquidity(verified=False), quote=_quote(100_000_000)).authorized_ticket_lamports == 0
    unrelated = _decide(sections, risk=_risk(account_snapshot_id="other-account"), quote=_quote(100_000_000))
    assert unrelated.authorized_ticket_lamports == 0
    assert _decide(sections, risk=_risk(new_entries_allowed=False), quote=_quote(100_000_000)).authorized_ticket_lamports == 0


def test_integer_only_contract_and_frozen_evidence() -> None:
    with pytest.raises(ValueError, match="integer"):
        _account(net_closed_realized_profit_lamports=0.1)
    with pytest.raises(ValueError, match="integer"):
        _quote(ticket_lamports=True)
    with pytest.raises(ValueError, match="Decimal"):
        _risk(drawdown_throttle=0.5)
    with pytest.raises(ValueError, match="boolean"):
        _liquidity(verified="false")
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        _risk(drawdown_throttle=Decimal("1.01"))
    with pytest.raises(ValueError, match="timezone-aware"):
        _request(as_of=datetime(2026, 10, 19))
    with pytest.raises(ValueError, match="cannot weaken"):
        SizingPolicy(min_closed_trades=299)
    with pytest.raises(FrozenInstanceError):
        _account().reserve_lamports = 0  # type: ignore[misc]


def test_sizing_rounds_down_exactly_and_does_not_depend_on_decimal_context(
    sections: tuple[SizingSection, LimitsSection],
) -> None:
    account = _account(initial_equity_lamports=1_000_000_009)
    discovery = load_config(Path("config"), {"discovery.max_roundtrip_cost_fraction": "0.012345"}).settings.discovery
    with localcontext() as context:
        context.prec = 2
        first = _decide(sections, account=account, quote=_quote(100_000_000), discovery=discovery)
    second = _decide(sections, account=account, quote=_quote(100_000_000), discovery=discovery)
    assert first == second
    assert first.authorized_ticket_lamports == 100_000_000
