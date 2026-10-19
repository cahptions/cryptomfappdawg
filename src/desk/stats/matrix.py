"""Confidence matrix (PLAN §14.1) and readiness (§14.3), computed deterministically from PAPER evidence.

Each cell has an estimate, an uncertainty, an evidence count and a status. No weighted score exists and no
model output enters. ``ELIGIBLE FOR MANUAL LIVE REVIEW`` only permits the operator to review; it never arms LIVE.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from desk.stats.core import (
    Array,
    as_array,
    bootstrap_drawdown_quantile,
    cusum_alarm,
    effective_sample_size,
    expected_shortfall,
    max_drawdown,
    mean_interval,
    stationary_bootstrap_means,
    walk_forward_folds,
)

PASS, FAIL, INSUFFICIENT, NOT_APPLICABLE = "PASS", "FAIL", "INSUFFICIENT", "NOT_APPLICABLE"
BASELINES = ("B0", "B1", "B2")
MATRIX_VERSION = "matrix-v1"
# §14.1 thresholds (proposed, operator-approved with the plan); a change is a new MATRIX_VERSION
RULES: dict[str, float] = {
    "min_trades": 300, "min_ess": 150, "min_days": 60, "min_trades_for_estimate": 30,
    "max_drawdown": 0.20, "max_drawdown_p95": 0.30, "min_es95": -0.35, "max_share_below_minus_50": 0.02,
    "regime_min_trades": 50, "regimes_required": 2, "min_quote_availability": 0.98, "max_cost_gap": 0.01,
    "max_exit_problem_share": 0.015, "fold_positive_share": 0.70, "folds": 5, "stability_window": 100,
    "stability_p_value": 0.10, "max_operating_cost_share": 0.25, "min_replay_sample": 200,
    "strong_negative_stress": -0.05, "cusum_k": 0.12, "cusum_h": 20.0,
}


@dataclass(frozen=True)
class Trade:
    strategy_id: str
    strategy_version: str
    opened_at: datetime
    closed_at: datetime
    ret: float                    # conservative realized return on the ticket (all costs)
    observed_ret: float
    stress_ret: float
    regime_label: str | None      # None = uncalibrated at decision time
    exit_problem: bool            # was ever IMPAIRED or EXIT_BLOCKED
    pnl_lamports: int
    max_hold_seconds: float


@dataclass(frozen=True)
class Evidence:
    """Book-level inputs that are not per-trade."""

    entry_attempts: int = 0
    entries_without_route: int = 0
    unreconciled_lamports: int = 0
    replay_checked: int = 0
    replay_identical: int = 0
    operating_cost_lamports: int = 0           # paid services: none, so 0 (M1-20)
    open_exit_problems: int = 0
    sizing_fraction: float = 0.10
    baseline_returns: dict[str, list[float]] = field(default_factory=dict)


@dataclass(frozen=True)
class Cell:
    dimension: str
    status: str
    estimate: float | None
    lower: float | None
    upper: float | None
    evidence: int
    detail: str


def _cell(dimension: str, status: str, estimate: float | None = None, lower: float | None = None,
          upper: float | None = None, evidence: int = 0, detail: str = "") -> Cell:
    def clean(v: float | None) -> float | None:
        return None if v is None or not math.isfinite(v) else round(v, 6)
    return Cell(dimension, status, clean(estimate), clean(lower), clean(upper), evidence, detail)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _cluster_means(trades: list[Trade]) -> Array:
    """Trades opened in the same hour are correlated (same market moment): one observation per hour."""
    clusters: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        clusters[t.opened_at.strftime("%Y-%m-%dT%H")].append(t.ret)
    return as_array([sum(v) / len(v) for _, v in sorted(clusters.items())])


def _daily_account_returns(trades: list[Trade], fraction: float) -> Array:
    days: dict[str, float] = defaultdict(float)
    for t in trades:
        days[t.closed_at.date().isoformat()] += t.ret * fraction
    return as_array([days[d] for d in sorted(days)])


def build_matrix(trades: list[Trade], evidence: Evidence, *, seed: int = 0, resamples: int = 2000,
                 only: frozenset[str] | None = None) -> dict[str, Any]:
    """The matrix for one strategy version. ``trades`` must all belong to it.

    ``only`` limits the result to the named dimensions (used by large simulation tests); readiness then is None.
    """

    def want(name: str) -> bool:
        return only is None or name in only

    trades = sorted(trades, key=lambda t: (t.opened_at, t.closed_at))
    n = len(trades)
    r = RULES
    cells: list[Cell] = []
    returns = as_array([t.ret for t in trades]) if n else np.zeros(0)
    enough = n >= r["min_trades_for_estimate"]
    strategy = trades[0].strategy_id if trades else "?"

    # net expectancy (one-sided 95% stationary-block-bootstrap lower bound)
    if enough and want("net_expectancy"):
        one = mean_interval(returns, one_sided=True, resamples=resamples, seed=seed)
        two = mean_interval(returns, resamples=resamples, seed=seed + 1)
        cells.append(_cell("net_expectancy", PASS if one.lower > 0 else FAIL, one.estimate, one.lower, two.upper, n,
                           "mean conservative net return per trade"))
    else:
        two = None
        cells.append(_cell("net_expectancy", INSUFFICIENT, float(returns.mean()) if n else None, evidence=n,
                           detail=f"needs >= {int(r['min_trades_for_estimate'])} closed trades"))

    # predictive value vs the random-eligible baseline
    baseline = evidence.baseline_returns.get("B1", [])
    if strategy in BASELINES:
        cells.append(_cell("predictive_value", NOT_APPLICABLE, detail="baselines are the comparison, not candidates"))
    elif enough and len(baseline) >= r["min_trades_for_estimate"] and want("predictive_value"):
        b = as_array(baseline)
        delta = (stationary_bootstrap_means(returns, resamples=resamples, seed=seed + 4)
                 - stationary_bootstrap_means(b, resamples=resamples, seed=seed + 5))
        lower = float(np.quantile(delta, 0.05))
        cells.append(_cell("predictive_value", PASS if lower > 0 else FAIL, float(returns.mean() - b.mean()),
                           lower, None, n, f"expectancy minus B1's ({len(baseline)} trades); one-sided 95% bound"))
    else:
        cells.append(_cell("predictive_value", INSUFFICIENT, evidence=n, detail="needs >= 30 trades here and in B1"))

    # statistical reliability
    days = (trades[-1].closed_at - trades[0].opened_at).total_seconds() / 86400 if n else 0.0
    ess = effective_sample_size(_cluster_means(trades)) if n else 0.0
    reliable = n >= r["min_trades"] and ess >= r["min_ess"] and days >= r["min_days"]
    cells.append(_cell("statistical_reliability", PASS if reliable else INSUFFICIENT, float(n), ess, days, n,
                       f"n>={int(r['min_trades'])}, ESS>={int(r['min_ess'])} (hour clusters), >={int(r['min_days'])} days"))

    # drawdown (account level, daily)
    daily = _daily_account_returns(trades, evidence.sizing_fraction) if n else np.zeros(0)
    observed_dd = max_drawdown(daily) if n else 0.0
    if observed_dd > r["max_drawdown"]:
        cells.append(_cell("drawdown", FAIL, observed_dd, evidence=len(daily), detail="observed max drawdown > 20%"))
    elif len(daily) >= 30 and want("drawdown"):
        p95 = bootstrap_drawdown_quantile(daily, resamples=min(resamples, 1000), seed=seed + 6)
        cells.append(_cell("drawdown", PASS if p95 <= r["max_drawdown_p95"] else FAIL, observed_dd, None, p95,
                           len(daily), "observed <= 20% and bootstrap p95 <= 30%"))
    else:
        cells.append(_cell("drawdown", INSUFFICIENT, observed_dd, evidence=len(daily), detail="needs >= 30 trading days"))

    # downside tail
    if n:
        es = expected_shortfall(returns)
        share = float(np.mean(returns < -0.5))
        failed = share > r["max_share_below_minus_50"] and n >= 50
        status = FAIL if failed else (PASS if n >= 100 and es >= r["min_es95"] else FAIL if n >= 100 else INSUFFICIENT)
        cells.append(_cell("downside_tail", status, es, None, share, n, "ES95 >= -35% and <= 2% of trades below -50%"))
    else:
        cells.append(_cell("downside_tail", INSUFFICIENT))

    # market robustness (calibrated regimes only)
    regimes: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        if t.regime_label:
            regimes[t.regime_label].append(t.ret)
    big = {k: v for k, v in regimes.items() if len(v) >= r["regime_min_trades"]}
    negative = [k for k, v in big.items() if sum(v) / len(v) <= 0]
    uncalibrated = sum(1 for t in trades if not t.regime_label)
    status = FAIL if negative else PASS if len(big) >= r["regimes_required"] else INSUFFICIENT
    cells.append(_cell("market_robustness", status, float(len(big)), evidence=n,
                       detail=f"regimes with >=50 trades: {sorted(big)}; negative: {negative}; "
                              f"uncalibrated trades: {uncalibrated}"))

    # execution reliability
    attempts = evidence.entry_attempts
    if attempts >= 100 and enough:
        availability = 1 - evidence.entries_without_route / attempts
        gap = float(np.mean([t.observed_ret - t.ret for t in trades]))
        ok = availability >= r["min_quote_availability"] and gap <= r["max_cost_gap"]
        cells.append(_cell("execution_reliability", PASS if ok else FAIL, availability, None, gap, attempts,
                           "quote availability >= 98%; observed-vs-conservative gap <= 1.0 pp"))
    else:
        cells.append(_cell("execution_reliability", INSUFFICIENT, evidence=attempts, detail="needs >= 100 entry attempts"))

    # exit reliability
    problems = sum(t.exit_problem for t in trades) + evidence.open_exit_problems
    if evidence.unreconciled_lamports:
        cells.append(_cell("exit_reliability", FAIL, detail="ledger does not reconcile"))
    elif n >= 100:
        share = problems / (n + evidence.open_exit_problems)
        cells.append(_cell("exit_reliability", PASS if share <= r["max_exit_problem_share"] else FAIL, share,
                           evidence=n, detail="IMPAIRED/EXIT_BLOCKED share <= 1.5%; zero unreconciled"))
    else:
        cells.append(_cell("exit_reliability", INSUFFICIENT, evidence=n, detail="needs >= 100 closed trades"))

    # stress
    stress = float(np.mean([t.stress_ret for t in trades])) if n else 0.0
    cells.append(_cell("stress", (PASS if stress > 0 else FAIL) if enough else INSUFFICIENT, stress if n else None,
                       evidence=n, detail="stress-profile expectancy > 0"))

    cells.append(_cell("calibration", NOT_APPLICABLE, detail="no local model scores are used (model_influence = none)"))

    # generalization: walk-forward test segments (rules are fixed, so no training happens)
    if reliable and want("generalization"):
        times = [t.opened_at.timestamp() for t in trades]
        hold = max(t.max_hold_seconds for t in trades)
        folds, _ = walk_forward_folds(times, folds=int(r["folds"]), embargo_seconds=hold, max_hold_seconds=hold,
                                      holdout_fraction=0.0)
        segments = [returns[f.test[0]:f.test[1]] for f in folds]
        pooled = np.concatenate(segments) if segments else returns
        lower = mean_interval(pooled, one_sided=True, resamples=resamples, seed=seed + 7).lower
        positive = sum(1 for s in segments if len(s) and s.mean() > 0) / max(1, len(segments))
        ok = lower > 0 and positive >= r["fold_positive_share"]
        cells.append(_cell("generalization", PASS if ok else FAIL, positive, lower, None, len(pooled),
                           f"{len(segments)} forward folds; pooled lower bound > 0 and >= 70% positive"))
    else:
        cells.append(_cell("generalization", INSUFFICIENT, evidence=n, detail="needs the reliability sample"))

    # stability
    if n >= 150:
        head = returns[: 2 * n // 3]
        tail = returns[2 * n // 3:]
        sigma = float(head.std(ddof=1)) or 1e-9
        alarm = cusum_alarm(returns, reference_mean=float(head.mean()), sigma=sigma, k=r["cusum_k"], h=r["cusum_h"])
        recent_alarm = alarm is not None and alarm >= n - r["stability_window"]
        se = math.sqrt(head.var(ddof=1) / len(head) + tail.var(ddof=1) / len(tail))
        p_decline = _normal_cdf((tail.mean() - head.mean()) / se) if se > 0 else 0.5
        ok = not recent_alarm and p_decline > r["stability_p_value"]
        cells.append(_cell("stability", PASS if ok else FAIL, float(tail.mean() - head.mean()), p_decline, None, n,
                           f"CUSUM alarm at {alarm}; one-sided decline p = {p_decline:.3f}"))
    else:
        cells.append(_cell("stability", INSUFFICIENT, evidence=n, detail="needs >= 150 trades"))

    # cost efficiency
    if enough:
        net = sum(t.pnl_lamports for t in trades) - evidence.operating_cost_lamports
        gross = sum(t.pnl_lamports for t in trades if t.pnl_lamports > 0)
        ok = net > 0 and evidence.operating_cost_lamports <= r["max_operating_cost_share"] * max(gross, 1)
        cells.append(_cell("cost_efficiency", PASS if ok else FAIL, float(net), evidence=n,
                           detail=f"operating costs {evidence.operating_cost_lamports} lamports (no paid services)"))
    else:
        cells.append(_cell("cost_efficiency", INSUFFICIENT, evidence=n))

    # data integrity
    if evidence.unreconciled_lamports:
        cells.append(_cell("data_integrity", FAIL, detail="ledger does not reconcile"))
    elif evidence.replay_checked >= r["min_replay_sample"]:
        agreement = evidence.replay_identical / evidence.replay_checked
        cells.append(_cell("data_integrity", PASS if agreement == 1 else FAIL, agreement, evidence=evidence.replay_checked,
                           detail="recorded decisions re-evaluated from stored inputs"))
    else:
        cells.append(_cell("data_integrity", INSUFFICIENT, evidence=evidence.replay_checked,
                           detail=f"needs >= {int(r['min_replay_sample'])} recorded decisions"))

    if only is not None:
        cells = [c for c in cells if c.dimension in only]
    readiness = None if only is not None else _readiness(strategy, {c.dimension: c for c in cells}, two, stress)
    return {"matrix_version": MATRIX_VERSION, "strategy_id": strategy,
            "strategy_version": trades[0].strategy_version if trades else None, "trades": n,
            "readiness": readiness, "cells": [asdict(c) for c in cells], "rules": RULES}


def _readiness(strategy: str, cells: dict[str, Cell], expectancy: Any, stress: float) -> str:
    if strategy in BASELINES:
        return "BASELINE (comparison only; never promoted)"
    if cells["statistical_reliability"].status != PASS:
        return "INSUFFICIENT EVIDENCE"
    failed = (
        (expectancy is not None and expectancy.upper < 0)
        or stress < RULES["strong_negative_stress"]
        or any(cells[d].status == FAIL for d in ("drawdown", "downside_tail", "exit_reliability"))
    )
    if failed:
        return "FAILED VALIDATION"
    if all(c.status in (PASS, NOT_APPLICABLE) for c in cells.values()):
        return "ELIGIBLE FOR MANUAL LIVE REVIEW"
    return "CONTINUE PAPER TESTING"
