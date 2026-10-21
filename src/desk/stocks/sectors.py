"""Sectors from the SEC's SIC code: the Fama-French 12-industry classification (Kenneth French's data library),
plus the sub-types valuation treats differently (banks, insurers, REITs)."""

from __future__ import annotations

# (sector, label, SIC ranges inclusive)
FF12: tuple[tuple[str, str, tuple[tuple[int, int], ...]], ...] = (
    ("NoDur", "Consumer staples", ((100, 999), (2000, 2399), (2700, 2749), (2770, 2799), (3100, 3199), (3940, 3989))),
    ("Durbl", "Consumer durables", ((2500, 2519), (2590, 2599), (3630, 3659), (3710, 3711), (3714, 3714), (3716, 3716),
                                    (3750, 3751), (3792, 3792), (3900, 3939), (3990, 3999))),
    ("Manuf", "Industrials", ((2520, 2589), (2600, 2699), (2750, 2769), (3000, 3099), (3200, 3569), (3580, 3629),
                              (3700, 3709), (3712, 3713), (3715, 3715), (3717, 3749), (3752, 3791), (3793, 3799),
                              (3830, 3839), (3860, 3899))),
    ("Enrgy", "Energy", ((1200, 1399), (2900, 2999))),
    ("Chems", "Chemicals", ((2800, 2829), (2840, 2899))),
    ("BusEq", "Technology", ((3570, 3579), (3660, 3692), (3694, 3699), (3810, 3829), (7370, 7379))),
    ("Telcm", "Telecom and media", ((4800, 4899),)),
    ("Utils", "Utilities", ((4900, 4949),)),
    ("Shops", "Retail and services", ((5000, 5999), (7200, 7299), (7600, 7699))),
    ("Hlth", "Health care", ((2830, 2839), (3693, 3693), (3840, 3859), (8000, 8099))),
    ("Money", "Financials", ((6000, 6999),)),
)
LABELS = {code: label for code, label, _ in FF12} | {"Other": "Other"}


def sector(sic: str | int | None) -> str:
    """Fama-French 12 code; unknown or unclassified SIC is 'Other'."""
    try:
        code = int(sic) if sic is not None else -1
    except ValueError:
        return "Other"
    for name, _, ranges in FF12:
        if any(lo <= code <= hi for lo, hi in ranges):
            return name
    return "Other"


def financial_type(sic: str | int | None) -> str | None:
    """bank, insurer or reit (valued on book value and ROE, or on funds from operations); None otherwise."""
    try:
        code = int(sic) if sic is not None else -1
    except ValueError:
        return None
    if code == 6798:
        return "reit"
    if 6000 <= code <= 6199:
        return "bank"
    if 6300 <= code <= 6411:
        return "insurer"
    return None


__all__ = ["FF12", "LABELS", "financial_type", "sector"]
