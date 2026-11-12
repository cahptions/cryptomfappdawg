"""The chain's own distribution (``desk.stocks.chain_odds``): it recovers a known skewed distribution from noisy
quotes better than the lognormal, reduces to the lognormal on a flat smile, and falls back where the chain is too
sparse or too ragged."""

import math

import numpy as np
import pytest

from desk.stocks import chain_odds as co
from desk.stocks import odds as od
from desk.stocks.plays import _ncdf, implied_vols

S, R = 100.0, 0.04


def mixture(t: float, comps: list[tuple[float, float, float]]):  # type: ignore[no-untyped-def]
    """A risk-neutral mixture of lognormals (weight, forward multiple, volatility): known call prices and chances."""
    forward = S * math.exp(R * t)
    assert abs(sum(w * m for w, m, _ in comps) - 1) < 1e-9

    def call(k: np.ndarray) -> np.ndarray:
        out = np.zeros_like(k)
        for w, m, sig in comps:
            sq = sig * math.sqrt(t)
            d1 = (np.log(forward * m / k) + 0.5 * sig * sig * t) / sq
            out = out + w * math.exp(-R * t) * (forward * m * _ncdf(d1) - k * _ncdf(d1 - sq))
        return out

    def above(k: np.ndarray) -> np.ndarray:
        out = np.zeros_like(k)
        for w, m, sig in comps:
            sq = sig * math.sqrt(t)
            out = out + w * _ncdf((np.log(forward * m / k) - 0.5 * sig * sig * t) / sq)
        return out

    return call, above


def chain(t: float, comps: list[tuple[float, float, float]], *, seed: int = 0, step: float = 0.025,
          rel: float = 0.04) -> tuple[np.ndarray, ...]:
    """Calls and puts from 70% to 135% of the price, mids jittered inside a quoted spread (rel × mid, at least 2.5¢)."""
    rng = np.random.default_rng(seed)
    call, _ = mixture(t, comps)
    k = np.arange(0.70, 1.35 + 1e-9, step) * S
    c = call(k)
    p = c - S + k * math.exp(-R * t)
    ks, is_call = np.concatenate([k, k]), np.concatenate([np.ones(len(k), bool), np.zeros(len(k), bool)])
    mid = np.concatenate([c, p])
    half = np.maximum(0.025, rel * mid) / 2
    noisy = mid + rng.uniform(-0.5, 0.5, len(mid)) * half
    keep = noisy > 0.05
    ks, is_call, noisy, half = ks[keep], is_call[keep], noisy[keep], half[keep]
    iv = implied_vols(is_call, np.full(len(ks), S), ks, np.full(len(ks), t), noisy, R)
    return ks, iv, 2 * half / noisy, is_call, noisy


def build(t: float, ks: np.ndarray, iv: np.ndarray, spread: np.ndarray, is_call: np.ndarray, mid: np.ndarray,
          rv: float | None = None) -> co.Expiry:
    return co.expiry(ks, iv, spread, is_call, mid, s=S, t=t, r=R, rv=rv, ic=None, ic_months=0, z=None)


def scores(p: np.ndarray, truth: np.ndarray) -> tuple[float, float]:
    """Expected Brier score and log-loss of chances ``p`` for events that happen with chance ``truth``."""
    q = np.clip(p, 1e-6, 1 - 1e-6)
    brier = truth * (1 - q) ** 2 + (1 - truth) * q * q
    loss = -(truth * np.log(q) + (1 - truth) * np.log(1 - q))
    return float(brier.mean()), float(loss.mean())


SKEWED = [("crash skew, 30 days", 30, [(0.8, 1.02, 0.20), (0.2, 0.92, 0.45)]),
          ("crash skew, 7 days", 7, [(0.85, 1.01, 0.25), (0.15, 0.943333333333, 0.60)]),
          ("crash skew, 90 days", 90, [(0.8, 1.03, 0.22), (0.2, 0.88, 0.40)]),
          ("fat tails, 30 days", 30, [(0.5, 1.0, 0.15), (0.5, 1.0, 0.40)])]


@pytest.mark.parametrize(("name", "days", "comps"), SKEWED, ids=[s[0] for s in SKEWED])
def test_the_chain_recovers_a_known_skewed_distribution_better_than_the_lognormal(name, days, comps) -> None:  # type: ignore[no-untyped-def]
    t = days / 365
    _, above = mixture(t, comps)
    levels = S * np.linspace(0.75, 1.30, 45)          # inside and a little beyond the liquid strikes
    truth = above(levels)
    chain_scores, own_scores, atm_scores, worst = [], [], [], []
    for seed in range(8):
        ks, iv, spread, is_call, mid = chain(t, comps, seed=seed)
        e = build(t, ks, iv, spread, is_call, mid)
        assert e.basis == "chain", e.reason
        assert e.market is not None
        p = e.market.above(S, levels)
        worst.append(float(np.max(np.abs(p - truth))))
        chain_scores.append(scores(p, truth))
        # today's market view: lognormal at the volatility quoted nearest each level (out-of-the-money side)
        forward = S * math.exp(R * t)
        otm = [i for i in range(len(ks)) if (ks[i] >= forward) == bool(is_call[i]) and not np.isnan(iv[i])]
        own = np.interp(levels, ks[otm], iv[otm])
        own_scores.append(scores(_ncdf((np.log(S / levels) + (R - 0.5 * own ** 2) * t) / (own * math.sqrt(t))), truth))
        assert e.atm_iv is not None
        atm = e.atm_iv
        atm_scores.append(scores(_ncdf((np.log(S / levels) + (R - 0.5 * atm ** 2) * t) / (atm * math.sqrt(t))), truth))
    chain_b, chain_l = np.mean(chain_scores, axis=0)
    for label, other in (("own IV", own_scores), ("ATM IV", atm_scores)):
        other_b, other_l = np.mean(other, axis=0)
        assert chain_b < other_b, f"{name}: Brier {chain_b:.5f} not below lognormal at {label} {other_b:.5f}"
        assert chain_l < other_l, f"{name}: log-loss {chain_l:.5f} not below lognormal at {label} {other_l:.5f}"
    assert np.mean(worst) < 0.02, f"{name}: mean worst error {np.mean(worst):.3f}"


def test_a_flat_smile_gives_back_todays_lognormal_for_both_views() -> None:
    t, sigma, rv = 30 / 365, 0.30, 0.20
    ks, iv, spread, is_call, mid = chain(t, [(1.0, 1.0, sigma)], rel=0.0)
    e = co.expiry(ks, iv, spread, is_call, mid, s=S, t=t, r=R, rv=rv, ic=0.08, ic_months=36, z=1.5)
    assert e.basis == "chain" and e.market is not None and e.kestrel is not None
    assert e.sigma == pytest.approx(od.forecast_sigma(sigma, rv), abs=1e-3)
    mu = R + od.tilt(0.08, 36, 1.5, e.sigma, t)
    assert e.drift == pytest.approx(mu, abs=1e-6)
    for level in (80.0, 95.0, 100.0, 105.0, 120.0):
        assert float(e.market.above(S, level)) == pytest.approx(od.above(S, level, t, sigma, R), abs=3e-3)
        assert float(e.kestrel.above(S, level)) == pytest.approx(od.above(S, level, t, e.sigma, mu), abs=3e-3)
        for kind in ("call", "put"):
            want = od.expected_payoff(kind, S, level, t, e.sigma, mu)
            assert float(e.kestrel.expected_payoff(kind == "call", S, level)) == pytest.approx(want, abs=0.02)


def test_put_call_parity_and_the_forecast_mean_hold_on_the_table() -> None:
    t = 45 / 365
    e = build(t, *chain(t, SKEWED[0][2]), rv=0.18)
    assert e.kestrel is not None and e.drift is not None
    k = np.array([80.0, 100.0, 125.0])
    calls, puts = e.kestrel.expected_payoff(True, S, k), e.kestrel.expected_payoff(False, S, k)
    mean = float(e.kestrel.expected_payoff(True, S, 0.0))
    assert mean == pytest.approx(S * math.exp(e.drift * t), rel=2e-3)        # E[S_T] = S·e^{μT}
    assert np.allclose(calls - puts, mean - k)


def test_skew_raises_the_chance_of_finishing_below_a_far_put_strike() -> None:
    t = 30 / 365
    comps = SKEWED[0][2]
    e = build(t, *chain(t, comps))
    assert e.market is not None and e.atm_iv is not None
    far = 85.0
    lognormal = 1 - od.above(S, far, t, e.atm_iv, R)
    assert 1 - float(e.market.above(S, far)) > 1.5 * lognormal          # the crash the lognormal leaves out


def test_sparse_and_wide_chains_fall_back_and_say_why() -> None:
    t = 30 / 365
    ks, iv, spread, is_call, mid = chain(t, SKEWED[0][2])
    keep = np.isin(ks, [95.0, 100.0, 105.0])
    sparse = build(t, ks[keep], iv[keep], spread[keep], is_call[keep], mid[keep])
    assert sparse.basis == "lognormal" and sparse.market is None
    assert "usable strikes" in sparse.reason
    wide = build(t, ks, iv, np.full(len(ks), 0.6), is_call, mid)
    assert wide.basis == "lognormal" and "within 30%" in wide.reason
    rng = np.random.default_rng(3)
    ragged = build(t, ks, iv * rng.uniform(0.5, 1.6, len(iv)), spread, is_call, mid)
    assert ragged.basis == "lognormal"
    assert ragged.describe() == {"basis": "lognormal", "reason": ragged.reason}


def test_sampling_the_stored_quantiles_reproduces_the_distribution() -> None:
    t = 30 / 365
    e = build(t, *chain(t, SKEWED[0][2]), rv=0.25)
    assert e.kestrel is not None
    q = e.kestrel.quantiles()
    assert len(q) == len(co.QUANTILES) and q == sorted(q)
    draws = co.sample(q, np.random.default_rng(1).uniform(size=200_000))
    for level in (85.0, 100.0, 110.0):
        assert float(np.mean(S * np.exp(draws) > level)) == pytest.approx(float(e.kestrel.above(S, level)), abs=0.01)
