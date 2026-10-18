"""Canonical venue (DEX) identities.

Providers spell DEX ids differently (GeckoTerminal ``pump-fun`` vs DexScreener ``pumpfun``). A provider id maps
to a canonical venue only when the mapping was verified against the pool account's on-chain owner program
(RESEARCH.md §5). Anything else is kept as ``unverified:<provider id>`` until M2 verifies it on-chain.
"""

from __future__ import annotations

# canonical venue -> owner program id, verified via getAccountInfo(pool).owner on 2026-10-18
VENUE_PROGRAMS: dict[str, str] = {
    "pumpfun_bonding_curve": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pumpswap": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "meteora_dbc": "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN",
    "meteora_damm_v2": "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG",
}

_ALIASES: dict[tuple[str, str], str] = {
    ("geckoterminal", "pump-fun"): "pumpfun_bonding_curve",
    ("dexscreener", "pumpfun"): "pumpfun_bonding_curve",
    ("geckoterminal", "pumpswap"): "pumpswap",
    ("dexscreener", "pumpswap"): "pumpswap",
    ("geckoterminal", "meteora-dbc"): "meteora_dbc",
    ("dexscreener", "meteoradbc"): "meteora_dbc",
    ("geckoterminal", "meteora-damm-v2"): "meteora_damm_v2",
}

BONDING_CURVE_VENUES = frozenset({"pumpfun_bonding_curve", "meteora_dbc"})


def canonical_venue(provider: str, provider_dex_id: str) -> str:
    return _ALIASES.get((provider, provider_dex_id), f"unverified:{provider_dex_id}")
