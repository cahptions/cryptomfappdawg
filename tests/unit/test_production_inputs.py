"""Decisions from the real feature builder, not hand-made contexts: the gap that hid an always-NO_TRADE desk.

The builder labels the age-adjusted volume decay UNVERIFIED (token age is a provider claim). Under evaluator v3
the H7 overlay therefore vetoed every strategy on every candidate. v4 lets an overlay act on an UNVERIFIED value
(it can only remove eligibility); MISSING inputs still veto.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from desk.contracts.discovery import DiscoveryReason, Outcome, SecurityAssessment
from desk.contracts.observed import DataStatus
from desk.contracts.strategy import DecisionContext
from desk.features import FeatureBuilder
from desk.strategies import evaluate_decision
from desk.strategies.evaluate import EVALUATOR_VERSION
from desk.strategies.specs import paper_specs
from tests.unit.test_features import conn, persist_assessment, seed_claim, seed_series  # noqa: F401  (fixture)
from tests.unit.test_security import MINT, NOW, POOL, holder_metrics, pool_verification, quotes

UNVERIFIED_VETO = "OVERLAY_H7:FEATURE_UNVERIFIED:volume_decay_adj"


def _context(connection: sqlite3.Connection, claim_age_hours: int | None) -> DecisionContext:
    seed_series(connection)
    if claim_age_hours is not None:
        seed_claim(connection, claim_age_hours)
    buy, sell = quotes()
    persist_assessment(connection, SecurityAssessment(mint=MINT, assessed_at=NOW, outcome=Outcome.PASS, reasons=(),
                                                      pool_verification=pool_verification(), buy_quote=buy,
                                                      sell_quote=sell, holders=holder_metrics()))
    features = FeatureBuilder(connection).build(MINT, POOL, NOW)
    return DecisionContext(features=features, security_outcome=Outcome.PASS, security_assessed_at=NOW,
                           discovery_reason=DiscoveryReason.NEW_POOL, regime_label=None,
                           regime_status=DataStatus.MISSING)


def test_the_builder_marks_age_adjusted_decay_unverified(conn: sqlite3.Connection) -> None:  # noqa: F811
    features = _context(conn, 24).features
    assert features.values["volume_decay_adj"].status is DataStatus.UNVERIFIED
    assert features.usable("roundtrip_cost_fraction") is not None and features.usable("verified_liquidity_usd")


def test_v3_vetoed_every_strategy_on_real_builder_output(conn: sqlite3.Connection) -> None:  # noqa: F811
    decision = evaluate_decision("c1", _context(conn, 24), paper_specs(), evaluator_version="shadow-rules-v3")
    assert decision.chosen == "NO_TRADE"
    traders = [row for row in decision.ranking if row.strategy_id not in ("NO_TRADE", "H7")]
    assert traders and all(UNVERIFIED_VETO in row.reasons for row in traders)


def test_v4_overlay_acts_on_the_claim_and_lets_the_desk_trade(conn: sqlite3.Connection) -> None:  # noqa: F811
    decision = evaluate_decision("c1", _context(conn, 24), paper_specs())
    assert EVALUATOR_VERSION == "shadow-rules-v4"
    assert decision.chosen != "NO_TRADE"
    assert not any(UNVERIFIED_VETO in row.reasons for row in decision.ranking)


@pytest.mark.parametrize("age,veto", [
    (7, "OVERLAY_H7:REJECTION_CONDITION_TRUE:volume_decay_adj:lt:0.5"),      # fading volume: a real exhaustion signal
    (None, "OVERLAY_H7:FEATURE_MISSING:volume_decay_adj"),                    # no age claim at all: still fail-closed
])
def test_v4_still_vetoes_exhaustion_and_missing_inputs(conn: sqlite3.Connection, age: int | None,  # noqa: F811
                                                        veto: str) -> None:
    decision = evaluate_decision("c1", _context(conn, age), paper_specs())
    assert decision.chosen == "NO_TRADE"
    traders = [row for row in decision.ranking if row.strategy_id not in ("NO_TRADE", "H7")]
    assert all(veto in row.reasons for row in traders)


def test_production_availability_of_each_paper_strategy(conn: sqlite3.Connection) -> None:  # noqa: F811
    """Which strategies' required inputs the real builder can supply as OK (H7 judged under v4)."""
    features = _context(conn, 24).features
    blocked = {}
    for spec in paper_specs():
        if spec.strategy_id in ("B0", "H7"):
            continue
        bad = sorted(f.name for f in spec.features if features.values[f.name].status is not DataStatus.OK)
        if bad:
            blocked[spec.strategy_id] = bad
    # Documented gaps (PROGRESS): claims are unverified and event-chronology features are not produced yet.
    # B1, B2, H3, H4 and H8 can qualify on real inputs.
    assert set(blocked) == {"H1", "H2", "H5", "H6"}, blocked
    assert blocked["H1"] == ["organic_volume_share"]
    assert blocked["H6"] == ["token_age_seconds"]
    assert Decimal(0) < features.usable("log_return_1h")  # the seeded series rises
