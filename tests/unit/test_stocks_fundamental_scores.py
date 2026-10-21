"""Undefined accounting ratios must not crash research or become neutral/failed observations."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest

from desk.stocks.contracts import Fact
from desk.stocks.fundamentals import Fundamentals, metrics, scores

ASOF = date(2026, 10, 21)


def company() -> Fundamentals:
    flows = {"Revenues": (100, 120), "NetIncomeLoss": (10, 14),
             "NetCashProvidedByUsedInOperatingActivities": (12, 18), "GrossProfit": (30, 45),
             "CostOfRevenue": (70, 75), "WeightedAverageNumberOfDilutedSharesOutstanding": (100, 100),
             "OperatingIncomeLoss": (15, 20), "DepreciationDepletionAndAmortization": (5, 6),
             "SellingGeneralAndAdministrativeExpense": (10, 12)}
    balances = {"Assets": (150, 160), "LongTermDebtNoncurrent": (20, 18), "AssetsCurrent": (45, 55),
                "LiabilitiesCurrent": (25, 25), "Liabilities": (60, 55), "RetainedEarningsAccumulatedDeficit": (25, 35),
                "AccountsReceivableNetCurrent": (10, 13), "PropertyPlantAndEquipmentNet": (35, 40)}
    rows = {}
    for name, values in {**flows, **balances}.items():
        concept = "us-gaap:" + name
        rows[concept] = [Fact(concept, "shares" if "Shares" in name else "USD",
                              date(year, 1, 1) if name in flows else None, date(year, 12, 31), float(value), "10-K",
                              date(year + 1, 2, 15), year, "FY", f"synthetic-{year}")
                         for year, value in zip((2024, 2025), values, strict=True)]
    return Fundamentals(rows)


def change(f: Fundamentals, concept: str, index: int, value: float) -> None:
    rows = f.facts["us-gaap:" + concept]
    rows[index] = replace(rows[index], value=value)


def test_complete_scores_keep_the_financial_definitions() -> None:
    result = scores(company(), ASOF)
    assert result["piotroski"] == 9
    assert result["altman_base"] == pytest.approx(1.2 * 30 / 160 + 1.4 * 35 / 160 + 3.3 * 20 / 160 + 120 / 160)
    # Independently written Beneish components for the complete synthetic statements.
    expected = (-4.84 + 0.92 * ((13 / 120) / (10 / 100)) + 0.528 * ((30 / 100) / (45 / 120))
                + 0.404 * ((1 - 95 / 160) / (1 - 80 / 150)) + 0.892 * 1.2
                + 0.115 * ((5 / 40) / (6 / 46)) - 0.172 * 1 + 4.679 * ((14 - 18) / 160)
                - 0.327 * ((55 / 160) / (60 / 150)))
    assert result["beneish_m"] == pytest.approx(expected)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("reported_gross", [False, True])
@pytest.mark.parametrize("revenue", [0.0, -1.0])
def test_nonpositive_revenue_preserves_unknown_margin_scores(index: int, reported_gross: bool, revenue: float) -> None:
    f = company()
    if not reported_gross:
        del f.facts["us-gaap:GrossProfit"]  # reproduce the operator's cost-of-revenue branch as well
    change(f, "Revenues", index, revenue)
    result = metrics(f, ASOF, 1.0)
    assert result["piotroski"] is None and result["beneish_m"] is None
    assert result["altman_base"] is not None  # unaffected accounting measures remain usable
    assert all(v is None or math.isfinite(v) for v in result.values())


@pytest.mark.parametrize("concept,index,value,missing_score", [
    ("Assets", 0, 0, "piotroski"), ("Assets", 1, 0, "piotroski"),
    ("LiabilitiesCurrent", 0, 0, "piotroski"), ("LiabilitiesCurrent", 1, 0, "piotroski"),
    ("AssetsCurrent", 0, 115, "beneish_m"),  # asset-quality denominator is zero
    ("DepreciationDepletionAndAmortization", 1, 0, "beneish_m"),
    ("SellingGeneralAndAdministrativeExpense", 0, 0, "beneish_m"),
    ("Liabilities", 0, 0, "beneish_m"),
])
def test_zero_composite_denominators_are_missing(concept: str, index: int, value: float, missing_score: str) -> None:
    f = company()
    change(f, concept, index, value)
    assert scores(f, ASOF)[missing_score] is None


def test_missing_cash_flow_is_not_a_failed_piotroski_test() -> None:
    f = company()
    del f.facts["us-gaap:NetCashProvidedByUsedInOperatingActivities"]
    assert scores(f, ASOF)["piotroski"] is None
