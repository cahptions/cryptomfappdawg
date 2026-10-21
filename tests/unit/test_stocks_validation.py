"""KESTREL Stocks validation: forward returns are next-open to close, statistics are honest about overlap, claims are
graded only with enough evidence, and committee weights are learned only from the past."""

from __future__ import annotations

import math
import random
from datetime import date

import numpy as np
import pytest

from desk.stocks import calendar as cal
from desk.stocks import desks as dk
from desk.stocks import validation as val
from desk.stocks.prices import Panel

DAYS = cal.trading_days(date(2025, 1, 2), date(2026, 6, 30))


def panel(series: dict[str, list[float]], days: list[date] = DAYS) -> Panel:
    """Closes per symbol; opens equal the previous close (no gaps) unless the close is missing."""
    symbols = sorted(series)
    close = np.array([series[s] for s in symbols], dtype=float)
    open_ = np.concatenate([close[:, :1], close[:, :-1]], axis=1)
    open_[np.isnan(close)] = np.nan
    return Panel(symbols, days[:close.shape[1]], open_, close * 1.01, close * 0.99, close, np.full(close.shape, 1e6))


def growth(rate: float, n: int = len(DAYS), start: float = 100.0) -> list[float]:
    return [start * (1 + rate) ** k for k in range(n)]


def test_ranks_and_spearman() -> None:
    assert list(val.average_ranks(np.array([10.0, 30.0, 20.0, 20.0]))) == [0.0, 3.0, 1.5, 1.5]
    x = np.arange(20, dtype=float)
    assert val.spearman(x, x ** 3) == pytest.approx(1.0)
    assert val.spearman(x, -x) == pytest.approx(-1.0)
    assert val.spearman(x[:5], x[:5]) is None                      # too few names
    assert val.spearman(x, np.full(20, 1.0)) is None               # no spread


def test_one_sided_p_and_independent_periods() -> None:
    rng = np.random.default_rng(1)
    up = rng.normal(0.02, 0.01, 60)
    assert val.one_sided_p(up) < 0.01
    assert val.one_sided_p(-up) > 0.99
    assert val.one_sided_p(np.array([1.0, 1.0, 1.0])) is None
    daily = val.summarize(list(up), horizon=21, step=1)
    assert daily["independent"] == pytest.approx(60 / 21, abs=0.05)   # 60 overlapping daily months ≈ 3 independent
    monthly = val.summarize(list(up), horizon=21, step=21)
    assert monthly["independent"] == 60 and monthly["ci"][0] < monthly["mean"] < monthly["ci"][1]


def test_forward_returns_enter_at_the_next_open_and_exit_at_the_close() -> None:
    n = 40
    a = growth(0.01, n)
    gone = [*growth(0.0, 10, 50.0), *[math.nan] * (n - 10)]          # stops trading after 10 sessions
    p = panel({"A": a, "GONE": gone, "SPY": growth(0.0, n)})
    fw = val.Forward(p)
    asof = p.days[4]
    entry, exit_ = fw.window(asof, 5) or (0, 0)
    assert (entry, exit_) == (5, 9)
    r = fw.returns(asof, 5)
    assert r is not None
    assert r[p.index["A"]] == pytest.approx(a[9] / a[4] - 1)       # open of session 5 = close of session 4
    assert fw.bench(asof, 5) == pytest.approx(0.0)
    late = fw.returns(p.days[8], 10)
    assert late is not None and late[p.index["GONE"]] == pytest.approx(0.0)   # sold at its last close
    assert fw.returns(p.days[n - 3], 5) is None                    # the window hasn't closed yet
    assert fw.returns(date(2024, 1, 1), 5) is None                 # before the first price
    dd = fw.drawdowns(asof, 5)
    assert dd is not None and dd[p.index["A"]] == pytest.approx(0.0)


def test_drawdowns_and_monthly_spans() -> None:
    n = 30
    swing = [100.0] * 5 + [110.0, 120.0, 90.0, 95.0] + [95.0] * (n - 9)
    p = panel({"X": swing, "SPY": growth(0.0, n)})
    fw = val.Forward(p)
    dd = fw.drawdowns(p.days[4], 10)
    assert dd is not None and dd[p.index["X"]] == pytest.approx(90 / 120 - 1)
    span = fw.span(p.days[4], p.days[9])
    assert span is not None and span[p.index["X"]] == pytest.approx(95 / 100 - 1)


def test_list_records_charge_costs_and_judge_avoid_lists_by_falling_behind() -> None:
    p = panel({"WIN": growth(0.002), "LOSE": growth(-0.002), "SPY": growth(0.0005), "XLK": growth(0.001)})
    fw = val.Forward(p)
    days = p.days[:250]

    def tech(_: date, __: str) -> str:
        return "BusEq"

    buy = val.list_record(fw, [(d, ["WIN"]) for d in days], "outperform", tech, step=1)
    month = buy["1m"]
    assert month["mean_excess_spy"] == pytest.approx((1.002 / 1.0005) ** 21 - 1, rel=0.05)
    assert month["mean"] == pytest.approx(month["mean_excess_spy"] - val.COST_ROUND_TRIP)
    assert month["mean_excess_sector"] < month["mean_excess_spy"]          # XLK did better than SPY
    assert month["hit_rate"] == 1.0 and month["p"] < 0.01
    assert month["independent"] == pytest.approx(250 / 21, abs=0.1)
    avoid = val.list_record(fw, [(d, ["LOSE"]) for d in days], "underperform", tech, step=1)
    assert avoid["1m"]["mean"] > 0 and avoid["1m"]["hit_rate"] == 1.0
    wrong = val.list_record(fw, [(d, ["LOSE"]) for d in days], "outperform", tech, step=1)
    assert wrong["1m"]["p"] > 0.99
    monthly = val.month_sample([(d, ["WIN"]) for d in days])
    assert len(monthly) == 12 and monthly[0][0] == max(d for d in days if d.month == 1 and d.year == 2025)
    curve = val.growth_curve(fw, monthly, "outperform")
    assert curve[0]["list"] == 1.0 and curve[-1]["list"] > curve[-1]["spy"] > 1.0


def test_grades_need_enough_independent_evidence() -> None:
    assert val.grade(None, "outperform", "live")["grade"] == "early"
    base = {"independent": 30.0, "p": 0.001, "p_holm": 0.01}
    assert val.grade(base, "outperform", "live")["grade"] == "working"
    assert val.grade({**base, "p_holm": 0.2, "p": 0.06}, "outperform", "live")["grade"] == "promising"
    assert val.grade({**base, "p_holm": 1.0, "p": 0.99}, "outperform", "live")["grade"] == "backwards"
    assert val.grade({**base, "p_holm": 1.0, "p": 0.5}, "outperform", "live")["grade"] == "not_working"
    assert val.grade({**base, "independent": 5.0}, "outperform", "live")["grade"] == "early"
    assert val.grade(base, "describe", "live")["grade"] == "info"


def test_fit_follows_positive_ic_and_shrinks_toward_the_defaults() -> None:
    rows = [{d: (0.08 if d == "momentum" else -0.02) for d in val.LEARNABLE} for _ in range(24)]
    w = val.fit(rows)
    lam = 24 / 36
    total = sum(dk.DEFAULT_WEIGHTS[d] for d in val.LEARNABLE)
    assert w["momentum"] == pytest.approx((1 - lam) * dk.DEFAULT_WEIGHTS["momentum"] + lam * total, abs=1e-3)
    assert w["valuation"] == pytest.approx((1 - lam) * dk.DEFAULT_WEIGHTS["valuation"], abs=1e-3)
    assert w["crowd"] == dk.DEFAULT_WEIGHTS["crowd"] and w["options"] == dk.DEFAULT_WEIGHTS["options"]
    assert val.fit([{d: None for d in val.LEARNABLE}]) == {**dk.DEFAULT_WEIGHTS}


def synthetic_months(fw: val.Forward, p: Panel, informative: str) -> list[val.Month]:
    """Month-ends where one desk's score follows the next month's return and the others are noise."""
    rng = random.Random(3)
    out = []
    for asof in val.rebuild_days(p, min_history=20):
        r = fw.returns(asof, 21)
        if r is None:
            continue
        scores: dict[str, dict[str, tuple[float, float]]] = {d: {} for d in dk.DESKS}
        for s in p.symbols:
            if s == "SPY":
                continue
            for d in val.LEARNABLE:
                signal = float(r[p.index[s]]) * 1000 if d == informative else 0.0
                scores[d][s] = (50 + signal + rng.gauss(0, 5 if d == informative else 20), 1.0)
        out.append(val.Month(asof, len(p.symbols) - 1, {s: "Other" for s in p.symbols if s != "SPY"}, scores, {}))
    return out


def test_learned_weights_come_from_the_past_and_beat_the_defaults_out_of_sample() -> None:
    rng = np.random.default_rng(5)
    n = len(DAYS)
    series = {f"S{i:02d}": list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))) for i in range(40)}
    series["SPY"] = growth(0.0003)
    p = panel(series)
    fw = val.Forward(p)
    history = synthetic_months(fw, p, "momentum")
    learned = val.learn(fw, history)
    assert learned["months"] == len(history) >= 14
    first = learned["oos"][0]
    assert first["train_months"] >= val.MIN_TRAIN_MONTHS
    # walk-forward: the first out-of-sample month only trained on months whose returns were complete before it
    trained_on = [m for m in history if (w := fw.window(m.asof, 21)) and p.days[w[1]] < date.fromisoformat(first["asof"])]
    assert first["train_months"] == len(trained_on)
    assert learned["adopted"] and learned["learned_ic"] > learned["default_ic"]
    assert learned["candidate"]["momentum"] > dk.DEFAULT_WEIGHTS["momentum"] * 1.5
    noise = val.learn(fw, synthetic_months(fw, p, "none"))
    assert noise["oos_months"] >= val.MIN_TRAIN_MONTHS


def test_desk_records_measure_ic_spread_and_drawdowns() -> None:
    rng = np.random.default_rng(9)
    n = len(DAYS)
    vol = {f"V{i:02d}": 0.005 + 0.002 * i for i in range(30)}
    series = {s: list(100 * np.exp(np.cumsum(rng.normal(0, v, n)))) for s, v in vol.items()}
    series["SPY"] = growth(0.0003)
    p = panel(series)
    fw = val.Forward(p)
    obs = [(d, {s: -v for s, v in vol.items()}) for d in val.rebuild_days(p, min_history=20)]   # calm names score high
    rec = val.desk_record(fw, obs, "drawdown", step=21)
    assert rec["n"] > 10 and rec["mean"] > 0 and rec["p"] < 0.05       # high scores had smaller drawdowns
    # a quarter's drawdown needs three months after the day; the month's IC needs one
    assert rec["ic"]["n"] == len(rec["series"]) == rec["n"] + 3
