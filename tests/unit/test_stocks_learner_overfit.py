"""The overfitting statistics (desk.stocks.learner.overfit): the deflated Sharpe ratio matches the paper's worked
example, the probability of backtest overfitting is near one half on noise and near zero with a planted edge, and the
trial counter is persistent and append-only. Synthetic data only."""

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from desk.stocks.learner import overfit as of


def test_the_deflated_sharpe_ratio_matches_the_worked_example() -> None:
    # Bailey & Lopez de Prado (2014), section 4: annualised SR 2.5 over 5 years of daily returns (T = 1250), skew -3,
    # kurtosis 10, chosen among N = 100 trials whose annualised Sharpe ratios vary by 0.5: DSR = 0.9004.
    sr, var = 2.5 / math.sqrt(250), 0.5 / 250
    assert of.expected_max_sharpe(100, var) * math.sqrt(250) == pytest.approx(1.7894, abs=1e-3)
    assert of.deflated_sharpe(sr, 1250, -3, 10, 100, var) == pytest.approx(0.9004, abs=5e-4)
    # one trial: the probabilistic Sharpe ratio against zero; more trials always deflate
    assert of.deflated_sharpe(sr, 1250, -3, 10, 1, var) > of.deflated_sharpe(sr, 1250, -3, 10, 1000, var)
    rng = np.random.default_rng(1)
    strong = of.dsr_of(rng.normal(0.3, 1.0, 250), 10, 0.004)
    weak = of.dsr_of(rng.normal(0.0, 1.0, 250), 10, 0.004)
    assert strong and weak and strong["passes"] and not weak["passes"]
    assert of.dsr_of([0.1, 0.1], 5, 0.01) is None


def test_pbo_is_about_a_half_on_noise_and_near_zero_with_a_planted_edge() -> None:
    noise = [of.pbo(np.random.default_rng(s).normal(size=(200, 40)))["pbo"] for s in range(12)]   # type: ignore[index]
    assert 0.35 < float(np.mean(noise)) < 0.65
    m = np.random.default_rng(4).normal(size=(200, 40))
    m[:, 11] += 0.4
    planted = of.pbo(m)
    assert planted is not None and planted["pbo"] < 0.1 and planted["trials"] == 40
    assert of.pbo(m[:, :1]) is None and of.pbo(m[:10]) is None


def test_the_trial_counter_only_grows(tmp_path: Path) -> None:
    from desk.storage.db import DbKind, connect, migrate

    conn = connect(tmp_path / "r.db", DbKind.STOCKS_RESEARCH)
    migrate(conn, DbKind.STOCKS_RESEARCH)
    now = datetime(2026, 11, 18, tzinfo=UTC)
    assert of.counter(conn, "model") == (0, 0.0)
    of.record(conn, "model", [{"name": "gbt", "sharpe": 0.2, "observations": 40},
                              {"name": "ridge", "sharpe": 0.05, "observations": 40}], "m1", now)
    of.record(conn, "model", [{"name": "gbt", "sharpe": 0.1, "observations": 50}], "m2", now)
    n, var = of.counter(conn, "model")
    assert n == 3 and var == pytest.approx(np.var([0.2, 0.05, 0.1], ddof=1))
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM stk_learn_trials")
