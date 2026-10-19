"""M5 pure rules: fee floor, learning-book sizing, execution profiles, deterministic failures, exit policies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from desk.contracts.quotes import QuoteObservation
from desk.paper.model import (
    SOL_CRASH_LOG_RETURN_1H,
    ExitInputs,
    FeeModel,
    Profiles,
    entry_profiles,
    exit_profiles,
    exit_reason,
    failure_draw,
    impaired,
    learning_ticket,
)
from desk.strategies.specs import paper_specs

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
SOL = 10 ** 9
MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
SPEC = {spec.strategy_id: spec for spec in paper_specs()}["B2"]   # stop 10%, trail +15%/10%, hold 6 h


def quote(amount_in: int, amount_out: int, impact: str = "0.5", *, sell: bool = False) -> QuoteObservation:
    sol = "So11111111111111111111111111111111111111112"
    return QuoteObservation(source="test", input_mint=MINT if sell else sol, output_mint=sol if sell else MINT,
                            in_amount_raw=amount_in, out_amount_raw=amount_out, swap_mode="ExactIn",
                            price_impact_pct=Decimal(impact), requested_at=NOW, received_at=NOW, latency_ms=Decimal(100))


def ticket(**changes: object) -> tuple[int, tuple[str, ...], int]:
    args: dict[str, object] = dict(base_lamports=SOL, realized_pnl_lamports=0, fixed_fraction=Decimal("0.10"),
                                   max_fraction=Decimal("0.15"), min_ticket_lamports=SOL // 20,
                                   max_fixed_cost_fraction=Decimal("0.01"), fixed_costs_lamports=210_000,
                                   rent_lamports=2_039_280, cash_lamports=1000 * SOL, liquidity_cap_lamports=10 * SOL)
    args.update(changes)
    decision = learning_ticket(**args)  # type: ignore[arg-type]
    return decision.lamports, decision.reasons, decision.sizing_capital_lamports


# ── sizing ────────────────────────────────────────────────────────────────────────────────────────────
def test_tickets_are_the_100_dollar_account_size_and_grow_as_a_percentage_of_profit() -> None:
    assert ticket()[0] == SOL // 10                                    # 10% of the $100-account base
    assert ticket(realized_pnl_lamports=SOL // 2)[0] == SOL * 15 // 100  # +50% profit -> 15% bigger base
    assert ticket(realized_pnl_lamports=-SOL // 2)[0] == SOL // 10      # losses never shrink the size


def test_fixed_fraction_cannot_exceed_the_15_percent_cap_or_liquidity_cap() -> None:
    assert ticket(fixed_fraction=Decimal("0.15"))[0] == SOL * 15 // 100
    with pytest.raises(ValueError):
        ticket(fixed_fraction=Decimal("0.20"))
    assert ticket(liquidity_cap_lamports=SOL // 15)[0] == SOL // 15


@pytest.mark.parametrize("changes,reason", [
    ({"fixed_costs_lamports": 1_000_001}, "FEE_FLOOR"),                  # > 1% of a 0.1 SOL ticket
    ({"base_lamports": SOL // 4}, "BELOW_MINIMUM_TICKET"),               # 0.025 SOL < 0.05 SOL minimum
    ({"cash_lamports": SOL // 10}, "INSUFFICIENT_CASH"),                  # rent and fees must also be covered
    ({"liquidity_cap_lamports": None}, "LIQUIDITY_CAP_UNKNOWN"),
])
def test_fee_floor_minimum_cash_and_unknown_liquidity_give_size_zero(changes: dict[str, object], reason: str) -> None:
    size, reasons, _ = ticket(**changes)
    assert size == 0 and reason in reasons


def test_fee_floor_boundary_is_exactly_one_percent() -> None:
    assert ticket(fixed_costs_lamports=1_000_000)[0] == SOL // 10
    assert ticket(fixed_costs_lamports=1_000_001)[0] == 0


# ── profiles and failures ─────────────────────────────────────────────────────────────────────────────
def test_profiles_order_observed_conservative_stress() -> None:
    entry = entry_profiles(quote(SOL // 10, 1_000_000, "1"), quote(SOL // 10, 990_000, "2"))
    assert entry == Profiles(observed=1_000_000, conservative=980_100, stress=950_400)
    exit_ = exit_profiles(quote(980_100, 120_000_000, "1", sell=True), quote(980_100, 118_000_000, "1", sell=True), entry)
    assert exit_.observed >= exit_.conservative >= exit_.stress > 0
    assert exit_.conservative == 117_410_000


def test_failure_draws_are_deterministic_and_roughly_uniform() -> None:
    assert failure_draw("intent-a", "entry", 0) == failure_draw("intent-a", "entry", 0)
    draws = [failure_draw(f"intent-{i}", "entry", 0) for i in range(2000)]
    assert all(Decimal(0) <= d < 1 for d in draws)
    assert 0.07 < sum(d < Decimal("0.10") for d in draws) / 2000 < 0.13


def test_fee_model_prefers_reported_fees_but_never_below_the_conservative_model() -> None:
    fees = FeeModel(5000, 100_000, 2_039_280)
    assert fees.fixed() == 105_000
    reported = quote(1, 1).model_copy(update={"signature_fee_lamports": 5000, "prioritization_fee_lamports": 400_000})
    assert fees.fixed(reported) == 405_000


# ── exit policies (M5-09 boundary table) ──────────────────────────────────────────────────────────────
def facts(**changes: object) -> ExitInputs:
    data: dict[str, object] = dict(ticket_lamports=SOL // 10, value_lamports=SOL // 10, peak_value_lamports=SOL // 10,
                                   opened_at=NOW - timedelta(hours=1), now=NOW, security_rejected=False,
                                   liquidity_usd=Decimal(100_000), entry_liquidity_usd=Decimal(100_000),
                                   sol_return_1h=Decimal(0), volume_decay=Decimal(1), features={})
    data.update(changes)
    return ExitInputs(**data)  # type: ignore[arg-type]


T = SOL // 10


@pytest.mark.parametrize("changes,expected", [
    ({}, None),
    ({"value_lamports": T * 90 // 100}, "HARD_STOP"),                       # exactly -10%
    ({"value_lamports": T * 90 // 100 + 1}, None),
    ({"opened_at": NOW - timedelta(seconds=SPEC.max_hold_seconds)}, "MAX_HOLD"),
    ({"opened_at": NOW - timedelta(seconds=SPEC.max_hold_seconds - 1)}, None),
    ({"peak_value_lamports": T * 115 // 100, "value_lamports": T * 115 // 100 * 90 // 100}, "TRAILING_STOP"),
    ({"peak_value_lamports": T * 115 // 100, "value_lamports": T * 115 // 100 * 90 // 100 + 1}, None),
    ({"peak_value_lamports": T * 114 // 100, "value_lamports": T * 114 // 100 * 90 // 100}, None),   # not armed
    ({"volume_decay": Decimal("0.4999")}, "VOLUME_DECAY"),
    ({"volume_decay": Decimal("0.5")}, None),
    ({"liquidity_usd": Decimal(80_000)}, "LIQUIDITY_DETERIORATION"),          # -20% vs entry
    ({"liquidity_usd": Decimal("80000.01"), "entry_liquidity_usd": Decimal(100_000)}, None),
    ({"liquidity_usd": Decimal("39999.99"), "entry_liquidity_usd": None}, "LIQUIDITY_BELOW_MINIMUM"),
    ({"security_rejected": True}, "SECURITY_CHANGE"),
    ({"sol_return_1h": SOL_CRASH_LOG_RETURN_1H, "value_lamports": T * 95 // 100}, "CATASTROPHIC_MARKET"),
    ({"sol_return_1h": SOL_CRASH_LOG_RETURN_1H, "value_lamports": T * 95 // 100 + 1}, None),
    ({"sol_return_1h": Decimal("-0.08"), "value_lamports": T * 95 // 100}, None),
    ({"value_lamports": None}, None),                                         # no fresh mark: price exits wait
])
def test_each_exit_policy_triggers_exactly_on_its_boundary(changes: dict[str, object], expected: str | None) -> None:
    assert exit_reason(SPEC, facts(**changes)) == expected


def test_strategy_invalidation_uses_the_specs_rejection_conditions() -> None:
    from desk.contracts.strategy import FeatureCondition
    from desk.strategies.specs import builtin_specs, revise_spec

    base = {spec.strategy_id: spec for spec in builtin_specs()}["B2"]
    spec = revise_spec(base, version=3, rejection_conditions=(
        FeatureCondition(feature="log_return_1h", operator="lt", threshold=Decimal("-0.2")),))
    assert exit_reason(spec, facts(features={"log_return_1h": Decimal("-0.21")})) == "STRATEGY_INVALIDATED"
    assert exit_reason(spec, facts(features={"log_return_1h": Decimal("-0.2")})) is None


def test_impairment_on_vanished_route_or_collapsed_quote() -> None:
    assert impaired(T, None, 301) and not impaired(T, None, 300)
    assert impaired(T, T // 2 - 1, 0) and not impaired(T, T // 2, 0)
    assert not impaired(None, 1, 0)


def test_usd_conversion_rounds_down_at_any_live_rate() -> None:
    from desk.paper.model import usd_to_lamports

    rate = Decimal("105.1047")                    # a live-like rate whose quotient has >9 decimal places
    assert usd_to_lamports(Decimal(100_000), rate) == 951_432_238_520   # floor of 951.43... SOL
    assert usd_to_lamports(Decimal(100), Decimal(100)) == 10 ** 9
    assert usd_to_lamports(Decimal(1), Decimal(3)) == 333_333_333
