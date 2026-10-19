"""Concentration from top-20 token accounts, aggregated by their on-chain token owner.

This is a lower bound on wallet holdings, not a complete beneficial-owner census. Only proofs constructed
by the on-chain verifier may populate ``verified_exclusions``; provider labels are never accepted as proof.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from desk.contracts.discovery import HolderAccount, HolderMetrics


def concentration(accounts: list[HolderAccount], supply_raw: int, verified_exclusions: dict[str, str],
                  verified_dev: str | None = None) -> HolderMetrics:
    if supply_raw <= 0 or not accounts or len(accounts) > 20:
        raise ValueError("positive supply and 1-20 largest token accounts required")
    if (len({a.address for a in accounts}) != len(accounts)
            or not 0 < sum(a.amount_raw for a in accounts) <= supply_raw):
        raise ValueError("duplicate holder accounts or holdings exceeding supply")
    raw: dict[str, int] = defaultdict(int)
    adjusted: dict[str, int] = defaultdict(int)
    excluded: dict[str, str] = {}
    for account in accounts:
        raw[account.owner] += account.amount_raw
        if account.address in verified_exclusions:
            excluded[account.address] = verified_exclusions[account.address]
        else:
            adjusted[account.owner] += account.amount_raw
    raw_values, adjusted_values = sorted(raw.values(), reverse=True), sorted(adjusted.values(), reverse=True)

    def pct(amount: int) -> Decimal:
        return Decimal(amount) * 100 / Decimal(supply_raw)

    return HolderMetrics(
        raw_top1_pct=pct(raw_values[0]), raw_top10_pct=pct(sum(raw_values[:10])),
        adjusted_top1_pct=pct(adjusted_values[0] if adjusted_values else 0),
        adjusted_top10_pct=pct(sum(adjusted_values[:10])),
        dev_pct=None if verified_dev is None or verified_dev not in raw else pct(raw[verified_dev]),
        unclassified_pct=pct(sum(adjusted_values)), observed_supply_pct=pct(sum(raw_values)), excluded=excluded,
    )
