"""The Robinhood learning lab: no look-ahead, honest fills, and judging only on unseen months."""

from __future__ import annotations

import math
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import numpy as np
import pytest

from desk.rh import learn
from desk.rh.contracts import Candle

START = datetime(2023, 1, 1, tzinfo=UTC)


def test_reference_capital_scales_dollar_illustrations_without_changing_returns() -> None:
    panel = learn.build_panel({"BTC-USD": bars("BTC-USD", [100.0] * 400)})
    trades = [learn.Trade("test", 0, 350, 355, 0.10, 0.03, "TIME")]
    old = learn.summarize(trades, panel, 1, 7, ticket_usd=10)
    current = learn.summarize(trades, panel, 1, 7, ticket_usd=50)
    assert old["mean_net"] == current["mean_net"] == 0.10
    assert old["curve"][0][1] == 1.0 and current["curve"][0][1] == 5.0
    assert current["ticket_usd"] == 50 and "not an investable" in current["curve_kind"]


def bars(symbol: str, closes: list[float], *, volume: float = 1_000_000.0) -> list[Candle]:
    out, prev = [], closes[0]
    for i, close in enumerate(closes):
        out.append(Candle(symbol, 86400, START + timedelta(days=i), Decimal(repr(prev)),
                          Decimal(repr(max(prev, close) * 1.01)), Decimal(repr(min(prev, close) * 0.99)),
                          Decimal(repr(close)), Decimal(repr(volume))))
        prev = close
    return out


def walk(seed: int, n: int, drift: float = 0.0, vol: float = 0.03, start: float = 100.0) -> list[float]:
    rng = random.Random(seed)
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(rng.gauss(drift, vol)))
    return out


def trending(seed: int, n: int) -> list[float]:
    """Repeating 40-day climbs of ~1.2% a day, then a 12% drop and 20 quiet days: breakouts that pay."""
    rng = random.Random(seed)
    out, price = [], 50.0
    phase = rng.randrange(60)
    for i in range(n):
        step = (i + phase) % 60
        price *= 1.012 if step < 40 else (0.88 if step == 40 else 1.0)
        out.append(price * (1 + rng.gauss(0, 0.002)))
    return out


def test_candidates_are_unique_and_the_lab_is_versioned() -> None:
    ids = [c.id for c in learn.candidates()]
    assert len(ids) == len(set(ids)) == 255
    assert {c.family for c in learn.candidates()} == {"TREND", "XMOM", "REVERT", "TIMING"}
    assert learn.lab_sha256() == learn.lab_sha256()
    assert all(c.describe() for c in learn.candidates())


def test_features_never_use_the_bar_they_are_decided_on() -> None:
    history = {"BTC-USD": bars("BTC-USD", walk(1, 300)), "ALT-USD": bars("ALT-USD", walk(2, 300))}
    panel = learn.build_panel(history)
    t = 250
    before = {k: panel.f[k][:, t].copy() for k in ("hh20", "ll10", "sma50", "atr20", "rsi2")}
    changed = dict(history)
    altered = list(changed["ALT-USD"])
    b = altered[t + 1]
    altered[t + 1] = Candle(b.product, b.granularity, b.start, b.open * 3, b.high * 3, b.low, b.close * 3, b.volume)
    changed["ALT-USD"] = altered
    later = learn.build_panel(changed)
    for k, value in before.items():
        assert np.array_equal(later.f[k][:, t], value, equal_nan=True), k        # tomorrow cannot leak into today
    assert later.f["hh20"][1, t + 2] != panel.f["hh20"][1, t + 2]               # but it counts from the day after
    shift = learn.build_panel({"X-USD": bars("X-USD", [float(i + 1) for i in range(30)])})
    assert shift.f["hh10"][0, 20] == pytest.approx(max((i + 1) * 1.01 for i in range(10, 20)))


def test_simulated_trades_do_not_change_when_the_future_changes() -> None:
    history = {"BTC-USD": bars("BTC-USD", walk(3, 700, 0.001)), "ALT-USD": bars("ALT-USD", trending(4, 700))}
    full = learn.build_panel(history)
    cut = learn.build_panel(history, until=(START + timedelta(days=599)).date())
    cost = np.full(2, 0.02)
    for cand in learn.candidates()[::17]:
        early = [t for t in learn.simulate(cand, cut, cost) if not t.open_at_end]
        later = {(t.cand, t.s, t.entry_t): t for t in learn.simulate(cand, full, cost)}
        for trade in early:
            assert later[(trade.cand, trade.s, trade.entry_t)] == trade, cand.id


def test_a_gap_through_the_stop_fills_at_the_open_and_costs_are_paid_both_ways() -> None:
    closes = [100.0] * 260
    history = {"BTC-USD": bars("BTC-USD", closes), "ALT-USD": bars("ALT-USD", closes)}
    panel = learn.build_panel(history)
    panel.o[1, 250], panel.low[1, 252], panel.o[1, 252] = 100.0, 60.0, 70.0
    trade = learn.walk("TREND", {"x": 10, "k": 3.0, "hold": 60}, panel, 1, 250, 95.0, 0.02, "t")
    assert (trade.exit_t, trade.reason) == (252, "HARD_STOP")
    assert trade.net == pytest.approx(70 * 0.99 / (100 * 1.01) - 1)
    held = learn.walk("REVERT", {"hold": 3}, panel, 1, 240, 50.0, 0.02, "t")
    assert (held.exit_t, held.reason) == (243, "MAX_HOLD")       # decided at the close of day 242, sold next open


def test_ranking_only_uses_outcomes_known_before_the_fold() -> None:
    known = learn.Trade("a", 0, 10, 20, 0.5, 0.0, "X")
    unknown = learn.Trade("b", 0, 10, 40, 5.0, 0.0, "X")
    ranked = learn.rank({"a": [known] * 3, "b": [unknown] * 3}, 0, 30)
    assert [r[0] for r in ranked] == ["a"]


def test_positions_are_capped_like_paper() -> None:
    trades = [learn.Trade("c", s, 10, 30, 0.1, 0.0, "X") for s in range(6)] + [learn.Trade("c", 0, 12, 20, 0.1, 0.0, "X")]
    kept = learn._capped(trades)
    assert len(kept) == learn.MAX_POSITIONS and {t.s for t in kept} == {0, 1, 2, 3}


def test_a_real_edge_is_found_on_unseen_months() -> None:
    n = learn.WARMUP_DAYS + learn.TRAIN_DAYS + 14 * learn.TEST_DAYS
    history = {"BTC-USD": bars("BTC-USD", walk(10, n, 0.0, 0.01), volume=1e9)}
    for i in range(6):
        history[f"T{i}-USD"] = bars(f"T{i}-USD", trending(20 + i, n), volume=1e8)
    panel = learn.build_panel(history)
    result = learn.run_lab(panel, np.full(len(panel.symbols), 0.02), now=datetime(2026, 10, 20, tzinfo=UTC))
    assert len(result.folds) >= learn.MIN_FOLDS
    assert result.promoted in learn.PROCEDURES
    row = result.procedures[result.promoted]
    assert row["passed"] and row["mean_net"] > 0 and row["mean_excess_vs_btc"] > 0
    assert result.selection["best"]


def test_noise_is_not_promoted() -> None:
    n = learn.WARMUP_DAYS + learn.TRAIN_DAYS + 14 * learn.TEST_DAYS
    history = {"BTC-USD": bars("BTC-USD", walk(30, n), volume=1e9)}
    for i in range(6):
        history[f"N{i}-USD"] = bars(f"N{i}-USD", walk(40 + i, n), volume=1e8)
    panel = learn.build_panel(history)
    result = learn.run_lab(panel, np.full(len(panel.symbols), 0.02), now=datetime(2026, 10, 20, tzinfo=UTC))
    assert result.promoted is None
    assert all(row["failures"] for row in result.procedures.values())


def test_rebalance_calendar_does_not_depend_on_where_data_starts() -> None:
    panel = learn.build_panel({"BTC-USD": bars("BTC-USD", walk(5, 120))})
    sundays = [t for t in range(len(panel.days)) if learn.rebalance_day(panel, t)]
    assert all(panel.days[t].weekday() == 6 for t in sundays)
    monthly = [t for t in sundays if learn.rebalance_day(panel, t, 4)]
    assert [(panel.days[b] - panel.days[a]).days for a, b in pairwise(monthly)] == [28] * (len(monthly) - 1)
    assert all(((panel.days[t] - date(1970, 1, 4)).days // 7) % 4 == 0 for t in monthly)


def test_learning_can_only_ever_reach_paper() -> None:
    """Real-money code may not import the Robinhood desk or its lab, and the Robinhood client cannot place orders."""
    import ast

    from desk.core.paths import REPO_ROOT
    from desk.rh.client import RobinhoodClient

    root = REPO_ROOT / "src" / "desk"
    offenders = []
    for package in ("live", "signer"):
        for path in (root / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                offenders += [f"{path.name}: {n}" for n in names if n.startswith("desk.rh")]
    assert offenders == []
    methods = [m for m in dir(RobinhoodClient) if not m.startswith("_")]
    assert not [m for m in methods if any(word in m for word in ("order", "place", "cancel", "buy", "sell"))]
