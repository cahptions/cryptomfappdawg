"""Market regimes, from point-in-time market features only (docs/STOCKS_LEARNER.md → Regimes).

A regime is three plain labels read from the market features a candidate already carries (``features.MARKET``: every
one of them as it stood at the previous session's close, or the decision session's own SPY chain):

* **Trend**: SPY against its 200-session average. Above it is an **uptrend**, at or below it a **downtrend**.
* **Volatility**: the VIX level and SPY's own option term structure (90-day minus 30-day IV, the VIX term structure's
  stand-in). **High** when the VIX is at or above ``VIX_HIGH``, or at or above ``VIX_CALM`` with the term structure
  inverted (near-dated options dearer than later ones: the market pays up for protection now). **Calm** when the VIX is
  below ``VIX_CALM`` and the term structure is not inverted. **Elevated** otherwise.
* **Credit**: the high-yield spread's change over 21 observations. **Widening** when it rose by ``HY_WIDENING`` points
  (30 basis points) or more, **steady** otherwise.

A missing input makes that label **unknown**; it is never guessed. The regime codes are also model features
(``FEATURES``), so the outcome model can learn that something works in one regime and not another, and every
held-out result and finding is reported per regime. ``content`` is the frozen rule; ``version_hash`` its fingerprint.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from desk.core.hashing import canonical_json, sha256_hex

NAME = "stocks_learner_regimes"
VERSION = 1
VIX_CALM = 20.0
VIX_HIGH = 30.0
HY_WIDENING = 0.30                  # percentage points over 21 observations
FEATURES = ("regime_trend", "regime_vol", "regime_credit")
ABOUT = {"regime_trend": "Market trend: 1 when SPY is above its 200-session average, 0 at or below",
         "regime_vol": "Market volatility: 0 calm, 1 elevated, 2 high (VIX level and SPY's term structure)",
         "regime_credit": "Credit: 1 when the high-yield spread widened 0.30 points or more over 21 observations"}
TREND = {1: "uptrend", 0: "downtrend"}
VOL = {0: "calm", 1: "elevated", 2: "high-vol"}
CREDIT = {0: "credit steady", 1: "credit widening"}


def content() -> dict[str, Any]:
    return {"version": VERSION, "vix_calm": VIX_CALM, "vix_high": VIX_HIGH, "hy_widening": HY_WIDENING,
            "trend": "spy_vs_200d > 0", "vol": "high if vix >= vix_high or (vix >= vix_calm and spy_term_slope < 0); "
            "calm if vix < vix_calm and not spy_term_slope < 0; else elevated",
            "credit": "widening if hy_change_21d >= hy_widening", "features": list(FEATURES)}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def _num(v: Any) -> float | None:
    if v is None:
        return None
    x = float(v)
    return x if math.isfinite(x) else None


def codes(market: Mapping[str, Any]) -> dict[str, float | None]:
    """The regime codes of one session's market features (None = unknown)."""
    trend_x, vix, slope, hy = (_num(market.get(k)) for k in ("spy_vs_200d", "vix", "spy_term_slope", "hy_change_21d"))
    trend = None if trend_x is None else (1.0 if trend_x > 0 else 0.0)
    if vix is None:
        vol = None
    else:
        inverted = slope is not None and slope < 0
        vol = 2.0 if vix >= VIX_HIGH or (vix >= VIX_CALM and inverted) else 0.0 if vix < VIX_CALM and not inverted else 1.0
    credit = None if hy is None else (1.0 if hy >= HY_WIDENING else 0.0)
    return {"regime_trend": trend, "regime_vol": vol, "regime_credit": credit}


def label(c: Mapping[str, Any]) -> str:
    """Plain words for a regime: "calm uptrend", "high-vol downtrend, credit widening", "unknown"."""
    trend, vol, credit = (c.get(k) for k in FEATURES)
    if trend is None and vol is None:
        return "unknown"
    words = f"{VOL.get(int(vol), '') if vol is not None and math.isfinite(vol) else 'volatility unknown'} " \
            f"{TREND.get(int(trend), '') if trend is not None and math.isfinite(trend) else 'trend unknown'}"
    if credit is not None and math.isfinite(credit) and int(credit) == 1:
        words += ", credit widening"
    return words.strip()


def of(market: Mapping[str, Any]) -> dict[str, Any]:
    c = codes(market)
    return {**c, "label": label(c)}


__all__ = ["ABOUT", "FEATURES", "HY_WIDENING", "NAME", "VERSION", "VIX_CALM", "VIX_HIGH", "codes", "content", "label",
           "of", "version_hash"]
