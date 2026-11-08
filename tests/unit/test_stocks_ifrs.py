"""Foreign private issuers filing 20-F under IFRS get fundamentals from the ifrs-full taxonomy, read like US GAAP and
point in time; values in another currency are left out (nothing converts currencies). Synthetic facts only."""

from __future__ import annotations

import json
from datetime import date

from desk.stocks.fundamentals import CONCEPTS, Fundamentals, base
from desk.stocks.sources.sec import parse_companyfacts


def annual(_concept: str, value: float, unit: str = "USD", year: int = 2025, start: bool = True) -> dict[str, object]:
    return {"start": f"{year}-01-01" if start else None, "end": f"{year}-12-31", "val": value, "form": "20-F",
            "filed": f"{year + 1}-04-15", "fy": year, "fp": "FY", "accn": f"acc-{year}"}


def facts(unit: str) -> dict[str, object]:
    flows = {"Revenue": 5e9, "ProfitLossAttributableToOwnersOfParent": 6e8, "ProfitLossFromOperatingActivities": 9e8,
             "CashFlowsFromUsedInOperatingActivities": 1e9,
             "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": 2e8}
    instants = {"Assets": 8e9, "EquityAttributableToOwnersOfParent": 4e9, "CashAndCashEquivalents": 1e9}
    body = {name: {"units": {unit: [annual(name, v, unit, y) for y in (2024, 2025)]}} for name, v in flows.items()}
    body |= {name: {"units": {unit: [annual(name, v, unit, y, start=False) for y in (2024, 2025)]}}
             for name, v in instants.items()}
    return {"facts": {"ifrs-full": body}}


def test_ifrs_filers_get_the_same_fundamentals_point_in_time() -> None:
    assert "ifrs-full:Revenue" in CONCEPTS
    parsed = parse_companyfacts(json.dumps(facts("USD")).encode(), CONCEPTS)
    f = Fundamentals(parsed)
    known = base(f, date(2026, 5, 1))
    assert known["revenue"] == 5e9 and known["net_income"] == 6e8 and known["equity"] == 4e9
    assert known["fcf"] == 8e8 and abs(known["operating_margin"] - 0.18) < 1e-9          # type: ignore[operator]
    before = base(f, date(2026, 4, 10))                                                     # 2025's 20-F not filed yet
    assert before["revenue"] == 5e9 and before.get("ttm_end") == float(date(2024, 12, 31).toordinal())


def test_other_currencies_are_left_out_rather_than_mixed_with_dollar_prices() -> None:
    f = Fundamentals(parse_companyfacts(json.dumps(facts("EUR")).encode(), CONCEPTS))
    known = base(f, date(2026, 5, 1))
    assert known["revenue"] is None and known["equity"] is None
