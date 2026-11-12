"""The odds of an option: market, history and KESTREL views agree with closed forms and simulation, the committee's
tilt is bounded by its measured evidence, and calibration only moves predictions once outcomes exist."""

from __future__ import annotations

import math
import random

import pytest

from desk.stocks import odds as od
from desk.stocks import options as opt


def simulate(s: float, t: float, sigma: float, mu: float, n: int = 20000, steps: int = 60, seed: int = 3) -> list[list[float]]:
    rng = random.Random(seed)
    dt = t / steps
    paths = []
    for _ in range(n):
        x, path = math.log(s), []
        for _ in range(steps):
            x += (mu - 0.5 * sigma * sigma) * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1)
            path.append(math.exp(x))
        paths.append(path)
    return paths


def test_market_odds_match_black_scholes_n_d2() -> None:
    s, k, t, iv, r = 100.0, 105.0, 30 / 365, 0.35, 0.04
    d2 = (math.log(s / k) + (r - iv * iv / 2) * t) / (iv * math.sqrt(t))
    assert od.finishes_beyond("call", s, k, t, iv, r) == pytest.approx(opt.norm_cdf(d2))
    assert od.finishes_beyond("put", s, k, t, iv, r) == pytest.approx(1 - opt.norm_cdf(d2))
    # under the pricing measure the expected payoff is the forward value of the option's price
    assert od.expected_payoff("call", s, k, t, iv, r) == pytest.approx(opt.bs_price("call", s, k, t, iv, r) * math.exp(r * t))
    assert od.expected_payoff("put", s, k, t, iv, r) == pytest.approx(opt.bs_price("put", s, k, t, iv, r) * math.exp(r * t))


def test_touch_and_expiry_probabilities_match_simulation() -> None:
    s, t, sigma, mu = 100.0, 0.25, 0.4, 0.08
    paths = simulate(s, t, sigma, mu)
    shift = math.exp(0.5826 * sigma * math.sqrt(t / 60))   # Broadie-Glasserman-Kou: checking 60 times misses touches
    for level in (115.0, 88.0):
        hit = sum(1 for p in paths if (max(p) >= level if level > s else min(p) <= level)) / len(paths)
        moved = level * shift if level > s else level / shift
        assert od.touch(s, moved, t, sigma, mu) == pytest.approx(hit, abs=0.015)
        assert od.touch(s, level, t, sigma, mu) > hit                       # continuous monitoring sees more
    ends = sum(1 for p in paths if p[-1] > 110) / len(paths)
    assert od.above(s, 110, t, sigma, mu) == pytest.approx(ends, abs=0.015)
    assert od.touch(s, s, t, sigma, mu) == 1.0
    # roughly twice the chance of finishing beyond, for a driftless out-of-the-money level
    assert od.touch(s, 120, t, sigma, 0.0) == pytest.approx(2 * od.above(s, 120, t, sigma, 0.0), rel=0.15)


def test_history_keeps_the_shape_and_rescales_to_todays_volatility() -> None:
    rng = random.Random(1)
    closes, x = [], 100.0
    for _ in range(800):
        x *= math.exp(rng.gauss(0.0005, 0.02))
        closes.append(x)
    h = od.History.build(closes, 21)
    assert h is not None and h.sessions == 21 and len(h.z) == 800 - 21
    assert sum(h.z) / len(h.z) == pytest.approx(0.0, abs=1e-9)
    sigma = 0.3
    # a strike one standard deviation of the 21-session move above the price: about 16% on a normal-ish history
    k = 100 * math.exp(sigma * math.sqrt(21 / 252))
    assert h.beyond("call", 100, k, sigma) == pytest.approx(0.16, abs=0.05)
    assert h.beyond("put", 100, k, sigma) == pytest.approx(0.84, abs=0.05)
    assert od.History.build(closes[:100], 21) is None                     # too little history


def test_the_committee_tilt_is_bounded_by_evidence_and_horizon() -> None:
    assert od.tilt(None, 12, 2.0, 0.4, 0.1) == 0.0
    assert od.tilt(-0.05, 12, 2.0, 0.4, 0.1) == 0.0                        # a negative IC never tilts
    t = 30 / 365
    small = od.tilt(0.05, 6, 1.5, 0.4, t)
    big = od.tilt(0.05, 60, 1.5, 0.4, t)
    assert 0 < small < big                                                 # more evidence, more tilt
    assert od.tilt(0.05, 60, 10.0, 0.4, t) == pytest.approx(od.tilt(0.05, 60, 3.0, 0.4, t))   # z capped at 3
    total = od.tilt(0.05, 60, 1.5, 0.4, 1.0) * 1.0                         # a year-long option
    month = od.tilt(0.05, 60, 1.5, 0.4, od.IC_HORIZON_YEARS) * od.IC_HORIZON_YEARS
    assert total == pytest.approx(month)                                   # never extrapolated past the IC's horizon
    assert od.tilt(0.05, 60, -1.5, 0.4, t) == pytest.approx(-big)


def test_odds_for_buyers_and_sellers() -> None:
    kw = {"s": 100.0, "k": 105.0, "t": 30 / 365, "iv": 0.4, "rv": 0.3, "r": 0.04, "history": None, "ic": None,
          "ic_months": 0, "z": None}
    premium = opt.bs_price("call", 100, 105, 30 / 365, 0.4, 0.04)
    long = od.odds(kind="call", action="buy", premium=premium, **kw)
    short = od.odds(kind="call", action="sell", premium=premium, **kw)
    assert long.itm_kestrel < long.itm_market                              # forecast vol below implied: less OTM finish
    assert long.expected_value < 0 < short.expected_value                  # implied vol above realized: sellers' edge
    assert long.expected_value == pytest.approx(-short.expected_value)
    assert long.profit_kestrel + short.profit_kestrel == pytest.approx(1.0)
    assert long.profit_kestrel < long.itm_kestrel                          # break-even is beyond the strike
    assert long.sigma == pytest.approx(math.sqrt(0.7 * 0.16 + 0.3 * 0.09))
    assert set(long.as_json()) >= {"itm_market", "itm_kestrel", "profit_kestrel", "touch_kestrel", "expected_value"}


def test_calibration_learns_only_from_enough_outcomes() -> None:
    assert od.Calibration.learn([0.3] * 10, [True] * 10, sessions=range(10)).apply(0.3) == 0.3   # too few: identity
    rng = random.Random(2)
    predicted = [rng.uniform(0.05, 0.95) for _ in range(4000)]
    outcomes = [rng.random() < p * 0.6 for p in predicted]                 # overconfident by 40%
    sessions = [i % 200 for i in range(4000)]                              # 200 sessions, independent outcomes
    cal = od.Calibration.learn(predicted, outcomes, sessions=sessions)
    assert cal.resolved == 4000 and cal.sessions == 200
    for p in (0.25, 0.5, 0.75):
        assert cal.apply(p) == pytest.approx(0.6 * p, abs=0.05)
    assert od.brier([cal.apply(p) for p in predicted], outcomes) < od.brier(predicted, outcomes)
    honest = od.Calibration.learn(predicted, [rng.random() < p for p in predicted], sessions=sessions)
    assert honest.apply(0.5) == pytest.approx(0.5, abs=0.05)
    assert 0.0 <= cal.apply(0.0) <= cal.apply(1.0) <= 1.0


def test_calibration_needs_ten_separate_sessions_not_just_thirty_plays() -> None:
    predicted, outcomes = [0.6] * 30, [i % 3 == 0 for i in range(30)]     # said 60%, a third happened
    two = od.Calibration.learn(predicted, outcomes, sessions=[i % 2 for i in range(30)])
    assert not two.buckets and two.apply(0.6) == 0.6 and two.sessions == 2 and two.resolved == 30
    twelve = od.Calibration.learn(predicted, outcomes, sessions=[i % 12 for i in range(30)])
    assert twelve.buckets and twelve.sessions == 12 and twelve.apply(0.6) < 0.6
    assert od.MIN_CALIBRATION == 30 and od.MIN_CALIBRATION_SESSIONS == 10


def test_clustered_outcomes_count_as_less_evidence_and_shrink_more() -> None:
    """Same plays, sessions, predictions and hit rate (a third): mixed within each session, or whole sessions hitting
    or missing together. The clustered record is closer to one observation a session, so it moves the chance less."""
    sessions = [i // 5 for i in range(60)]                                 # 12 sessions of 5 plays
    predicted = [0.6] * 60
    mixed = [i % 3 == 0 for i in range(60)]
    clustered = [s % 3 == 0 for s in sessions]
    assert sum(mixed) == sum(clustered) == 20
    spread = od.Calibration.learn(predicted, mixed, sessions=sessions)
    together = od.Calibration.learn(predicted, clustered, sessions=sessions)
    assert od.session_correlation(od.cells(predicted, mixed, sessions)) < 0.1
    assert od.session_correlation(od.cells(predicted, clustered, sessions)) == pytest.approx(1.0)
    assert spread.effective > 50 and together.effective == pytest.approx(12, abs=0.01)
    assert spread.apply(0.6) < together.apply(0.6) < 0.6                   # both learn; the clustered one less
    # independent plays, one per session, keep their full weight
    alone = od.Calibration.learn(predicted[:30], mixed[:30], sessions=range(30))
    assert alone.effective == pytest.approx(30)
