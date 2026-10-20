"""H8 moonshot hypothesis, hypotheses-before-baselines ranking and evidence-gated conviction sizing."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json
from desk.paper import engine as engine_module
from desk.paper.conviction import POLICY_VERSION, assess
from desk.paper.ledger import PaperLedger
from desk.strategies import evaluate_decision
from desk.strategies.specs import builtin_specs, paper_specs
from tests.integration.test_paper_lifecycle import MINT, POOL, _setup, dbs  # noqa: F401  (fixture)
from tests.unit.test_strategies import NOW, _context, _feature, _replace_feature

SPECS = {spec.strategy_id: spec for spec in paper_specs()}
H8 = SPECS["H8"]
RUNNER = {"log_return_1h": "0.55", "log_return_15m": "0.12", "log_return_5m": "0.03", "return_acceleration": "0.01",
          "volume_acceleration": "6", "buy_transactions_1h": "900", "buy_sell_ratio": "1.8",
          "liquidity_change_1h": "0.4", "top10_pct": "18", "top1_pct": "6"}


def runner(**changes: str) -> Any:
    context = _context(regime_status=_context().regime_status)
    for name, value in {**RUNNER, **changes}.items():
        context = _replace_feature(context, name, _feature(value))
    return context


def test_h8_fires_only_on_a_fully_confirmed_runner_and_outranks_momentum() -> None:
    decision = evaluate_decision("c1", runner(), paper_specs())
    assert decision.chosen == "H8"
    # B2 scores 100 x log return (55 here) but a qualifying hypothesis outranks any baseline
    b2 = next(r for r in decision.ranking if r.strategy_id == "B2")
    assert b2.eligible and b2.score > 2
    for name, weak in (("log_return_1h", "0.30"), ("volume_acceleration", "2.9"), ("liquidity_change_1h", "0.1"),
                       ("top10_pct", "30"), ("top1_pct", "12"), ("buy_sell_ratio", "1.1"), ("log_return_5m", "-0.01")):
        assert evaluate_decision("c1", runner(**{name: weak}), paper_specs()).chosen != "H8", name


def test_baselines_still_trade_when_no_hypothesis_wants_the_candidate() -> None:
    quiet = _replace_feature(_context(), "log_return_1h", _feature("0.02"))
    only_baselines = (SPECS["B2"], builtin_specs()[0])
    assert evaluate_decision("c1", quiet, only_baselines).chosen == "B2"
    flat = _replace_feature(_context(), "log_return_1h", _feature("0"))
    assert evaluate_decision("c1", flat, only_baselines).chosen == "NO_TRADE"     # ties still go to NO_TRADE


def test_h8_exits_are_tuned_to_bank_the_run_before_the_dump() -> None:
    assert H8.initial_stop.loss_fraction == Decimal("0.15")
    assert (H8.trailing.activation_gain_fraction, H8.trailing.drawdown_fraction) == (Decimal("0.25"), Decimal("0.12"))
    assert H8.max_hold_seconds == 7200 and "PAPER" in H8.allowed_modes


def _report(databases: Any, status: str, at: Any) -> None:
    payload = {"kind": "CONFIDENCE_MATRIX", "matrices": [{"strategy_id": "H8", "strategy_version": H8.version_id, "cells": [
        {"dimension": "net_expectancy", "status": status, "lower": "0.01", "evidence": 60}]}]}
    databases.research.execute(
        "INSERT INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, 'CONFIDENCE_MATRIX', ?)",
        (f"conf-{status}-{to_iso(at)}", to_iso(at), canonical_json(payload)))


def test_conviction_needs_the_strategys_own_evidence_and_extra_confirmation(dbs: Any) -> None:  # noqa: F811
    features = runner().features
    base, cap = Decimal("0.10"), Decimal("0.15")
    assert assess(dbs.research, H8, features, NOW).evidence == "NO_REPORT"
    _report(dbs, "INSUFFICIENT", NOW - timedelta(hours=1))
    assert not assess(dbs.research, H8, features, NOW).applied
    _report(dbs, "PASS", NOW - timedelta(minutes=5))
    conviction = assess(dbs.research, H8, features, NOW)
    assert conviction.applied and conviction.fraction(base, cap) == Decimal("0.15")      # 1.5x, at the 15% cap
    assert conviction.fraction(Decimal("0.12"), cap) == cap                              # never above the cap
    weak_signal = runner(volume_acceleration="4", buy_sell_ratio="1.4").features
    assert not assess(dbs.research, H8, weak_signal, NOW).applied
    assert assess(dbs.research, H8, features, NOW + timedelta(days=8)).evidence == "STALE_REPORT"
    other = assess(dbs.research, SPECS["H3"], features, NOW)
    assert not other.applied and other.evidence == "NOT_APPLICABLE"


@pytest.mark.parametrize("evidence,expected_ticket", [("PASS", 75 * 10 ** 7), (None, 5 * 10 ** 8)])
def test_engine_sizes_h8_up_only_with_evidence(dbs: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                               evidence: str | None, expected_ticket: int) -> None:
    engine, _, _, clock, candidate = _setup(dbs, monkeypatch)
    context = runner()
    features = context.features.model_copy(update={"mint": MINT, "pool_address": POOL})

    def shifted(*_: object, **__: object) -> object:
        delta = clock.now - NOW
        values = {n: v.model_copy(update={"observed_at": v.observed_at + delta}) for n, v in features.values.items()}
        return context.model_copy(update={"security_assessed_at": NOW + delta, "regime_label": None,
                                          "features": features.model_copy(update={"as_of": clock.now, "values": values})})

    monkeypatch.setattr(engine_module, "decision_context", shifted)
    if evidence:
        _report(dbs, evidence, NOW - timedelta(minutes=1))
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    position = PaperLedger(dbs.paper_ledger).open_positions()[0]
    assert position["strategy_id"] == "H8" and position["ticket_lamports"] == expected_ticket
    record = json.loads(position["entry_json"])["conviction"]
    assert record["policy"] == POLICY_VERSION and record["applied"] is bool(evidence)
    assert Decimal(record["conviction_fraction"]) == Decimal("0.15")
