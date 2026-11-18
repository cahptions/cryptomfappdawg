"""How much of a result is luck from trying many things (docs/STOCKS_LEARNER.md → After accounting for what it tried).

**The deflated Sharpe ratio** (Bailey & López de Prado, "The Deflated Sharpe Ratio", 2014). A Sharpe ratio ``SR``
estimated on ``T`` observations with skewness ``γ3`` and kurtosis ``γ4`` is compared with the best Sharpe ratio expected
from ``N`` unskilled trials whose Sharpe ratios vary by ``V``:

    SR0 = sqrt(V) × ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))         (γ: the Euler–Mascheroni constant)
    DSR = Φ( (SR − SR0) √(T − 1) / √(1 − γ3 SR + (γ4 − 1)/4 SR²) )

DSR is the probability that the true Sharpe ratio is above zero after the selection among ``N`` trials. Anything
chosen among many (the model variants and ensemble members, the do / don't rules) is judged by it; ``REQUIRED`` (0.95)
is the bar. With one trial it is the probabilistic Sharpe ratio against zero.

**The probability of backtest overfitting** (Bailey, Borwein, López de Prado & Zhu, 2017; combinatorially symmetric
cross-validation). The trials' per-period results (``T`` × ``N``) are cut into ``S`` blocks; for every way of choosing
half the blocks as "in sample", the trial with the best in-sample Sharpe ratio is ranked out of sample; PBO is the
share of those choices where it lands at or below the median. Near 0.5: picking the best was no better than chance.

**The trial counter** (``stk_learn_trials``) is persistent and append-only: every model variant, ensemble member and
rule ever evaluated is a row, so ``N`` only grows, across trainings.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import combinations
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex

NAME = "stocks_learner_overfit"
VERSION = 1
REQUIRED = 0.95                     # the deflated Sharpe ratio anything chosen among many trials must reach
BLOCKS = 16                         # CSCV blocks (fewer, and even, when there are few observations)
MIN_BLOCK = 4                       # observations a block needs at least
EULER = 0.5772156649015329


def content() -> dict[str, Any]:
    return {"version": VERSION, "required_dsr": REQUIRED, "cscv_blocks": BLOCKS, "min_block": MIN_BLOCK,
            "dsr": "Bailey & Lopez de Prado 2014: Phi((SR - SR0) sqrt(T-1) / sqrt(1 - skew SR + (kurt-1)/4 SR^2)), "
                   "SR0 = sqrt(V) ((1-g) Z(1-1/N) + g Z(1-1/(N e))); per-period Sharpe ratios; N and V from the "
                   "persistent trial counter",
            "pbo": "Bailey, Borwein, Lopez de Prado & Zhu 2017, CSCV: share of half-block splits where the in-sample "
                   "best trial ranks at or below the out-of-sample median (logit <= 0)",
            "trials": "every model variant, ensemble member and finding rule evaluated, append-only"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _phi_inv(p: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(p))


def moments(values: Sequence[float]) -> tuple[float, int, float, float] | None:
    """(per-period Sharpe ratio, observations, skewness, kurtosis — 3 for a normal); None when undefined."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return None
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return None
    z = (x - x.mean()) / x.std()
    return float(x.mean() / sd), len(x), float((z ** 3).mean()), float((z ** 4).mean())


def expected_max_sharpe(trials: int, variance: float) -> float:
    """The best Sharpe ratio expected among ``trials`` unskilled trials whose Sharpe ratios vary by ``variance``."""
    if trials <= 1 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * ((1 - EULER) * _phi_inv(1 - 1 / trials) + EULER * _phi_inv(1 - 1 / (trials * math.e)))


def probabilistic_sharpe(sr: float, t: int, skew: float, kurt: float, benchmark: float = 0.0) -> float:
    """The probability that the true Sharpe ratio exceeds ``benchmark`` (Bailey & López de Prado 2012)."""
    denominator = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if t < 2 or denominator <= 0:
        return float("nan")
    return _phi((sr - benchmark) * math.sqrt(t - 1) / math.sqrt(denominator))


def deflated_sharpe(sr: float, t: int, skew: float, kurt: float, trials: int, variance: float) -> float:
    return probabilistic_sharpe(sr, t, skew, kurt, expected_max_sharpe(trials, variance))


def dsr_of(values: Sequence[float], trials: int, variance: float) -> dict[str, Any] | None:
    """The deflated Sharpe ratio of a per-period series after ``trials`` trials with Sharpe variance ``variance``."""
    m = moments(values)
    if m is None:
        return None
    sr, t, skew, kurt = m
    value = deflated_sharpe(sr, t, skew, kurt, max(1, trials), variance)
    if not math.isfinite(value):
        return None
    return {"dsr": round(value, 4), "sharpe": round(sr, 4), "observations": t, "skew": round(skew, 3),
            "kurtosis": round(kurt, 3), "trials": max(1, trials), "sharpe_variance": round(variance, 6),
            "benchmark": round(expected_max_sharpe(max(1, trials), variance), 4), "passes": value >= REQUIRED}


def pbo(matrix: np.ndarray, blocks: int = BLOCKS) -> dict[str, Any] | None:
    """The probability of backtest overfitting of choosing the best column of ``matrix`` (periods × trials) by its
    Sharpe ratio (CSCV, see the module docstring); None with fewer than two trials or too few periods."""
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        return None
    s = min(blocks, m.shape[0] // MIN_BLOCK)
    s -= s % 2
    if s < 4:
        return None
    parts = np.array_split(np.arange(m.shape[0]), s)
    sums = np.stack([m[p].sum(axis=0) for p in parts])                    # blocks × trials
    squares = np.stack([(m[p] ** 2).sum(axis=0) for p in parts])
    counts = np.asarray([len(p) for p in parts], dtype=float)
    choose = np.zeros((math.comb(s, s // 2), s))
    for i, c in enumerate(combinations(range(s), s // 2)):
        choose[i, list(c)] = 1.0

    def sharpe(mask: np.ndarray) -> np.ndarray:
        n = (mask @ counts)[:, None]
        mean = (mask @ sums) / n
        var = np.maximum((mask @ squares) / n - mean ** 2, 1e-18) * n / np.maximum(n - 1, 1)
        out: np.ndarray = mean / np.sqrt(var)
        return out

    inside, outside = sharpe(choose), sharpe(1.0 - choose)
    best = np.argmax(inside, axis=1)
    chosen = outside[np.arange(len(best)), best]
    rank = (outside < chosen[:, None]).sum(axis=1) + 0.5 * ((outside == chosen[:, None]).sum(axis=1) - 1) + 1
    omega = rank / (m.shape[1] + 1)
    logit = np.log(omega / (1 - omega))
    return {"pbo": round(float((logit <= 0).mean()), 4), "splits": len(best), "blocks": s, "trials": int(m.shape[1]),
            "periods": int(m.shape[0]), "median_logit": round(float(np.median(logit)), 4)}


# ── the trial counter ────────────────────────────────────────────────────────────────────────────
def record(conn: sqlite3.Connection, kind: str, trials: Sequence[Mapping[str, Any]], model_id: str | None,
           now: datetime) -> int:
    """Append trials (each ``{name, sharpe, observations}``) to the persistent counter."""
    try:
        conn.executemany("INSERT INTO stk_learn_trials(at, model_id, kind, name, sharpe, observations, detail_json) "
                         "VALUES (?,?,?,?,?,?,?)",
                         [(to_iso(now), model_id, kind, str(t["name"]), t.get("sharpe"), t.get("observations"),
                           json.dumps({k: v for k, v in t.items() if k not in ("name", "sharpe", "observations")},
                                      allow_nan=False, default=str)) for t in trials])
    except sqlite3.OperationalError:
        return 0
    return len(trials)


def counter(conn: sqlite3.Connection, kind: str) -> tuple[int, float]:
    """(trials of ``kind`` ever recorded, the variance of their Sharpe ratios; 0 with fewer than two)."""
    try:
        rows = [r[0] for r in conn.execute("SELECT sharpe FROM stk_learn_trials WHERE kind=?", (kind,))]
    except sqlite3.OperationalError:
        return 0, 0.0
    sharpes = [float(x) for x in rows if x is not None and math.isfinite(float(x))]
    return len(rows), float(np.var(sharpes, ddof=1)) if len(sharpes) >= 2 else 0.0


def totals(conn: sqlite3.Connection) -> dict[str, int]:
    try:
        return {str(k): int(n) for k, n in conn.execute("SELECT kind, COUNT(*) FROM stk_learn_trials GROUP BY kind")}
    except sqlite3.OperationalError:
        return {}


__all__ = ["NAME", "REQUIRED", "VERSION", "content", "counter", "deflated_sharpe", "dsr_of", "expected_max_sharpe",
           "moments", "pbo", "probabilistic_sharpe", "record", "totals", "version_hash"]
