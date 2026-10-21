"""The KESTREL Stocks learning lab promotes a desk combination only when picking combinations walk-forward beat the
committee on months it never saw, keeps at most three, and never rewrites a promoted strategy."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import desks as dk
from desk.stocks import validation as val
from desk.stocks.paper import lab
from desk.stocks.prices import Panel
from desk.stocks.repo import StocksDatabases
from tests.conftest import REPO

NOW = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)
DAYS = cal.trading_days(date(2023, 1, 2), date(2026, 6, 30))


def market_panel(n: int = 60, seed: int = 4) -> Panel:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, (n + 1, len(DAYS))), axis=1))
    close[-1] = 100 * np.exp(np.cumsum(np.full(len(DAYS), 0.0003)))           # SPY
    open_ = np.concatenate([close[:, :1], close[:, :-1]], axis=1)
    symbols = [f"S{i:02d}" for i in range(n)] + ["SPY"]
    return Panel(symbols, DAYS, open_, close, close, close, np.full(close.shape, 1e6))


def months_with_signal(fw: val.Forward, p: Panel, informative: tuple[str, ...]) -> list[val.Month]:
    rng = random.Random(1)
    ends = val.rebuild_days(p, min_history=5)
    out = []
    for k, asof in enumerate(ends):
        nxt = fw.span(asof, ends[k + 1]) if k + 1 < len(ends) else None
        scores: dict[str, dict[str, tuple[float, float]]] = {d: {} for d in dk.DESKS}
        for s in p.symbols[:-1]:
            future = 0.0 if nxt is None else float(nxt[p.index[s]])
            for d in val.LEARNABLE:
                signal = future * 400 if d in informative else 0.0
                scores[d][s] = (50 + signal + rng.gauss(0, 8 if d in informative else 25), 1.0)
        out.append(val.Month(asof, len(p.symbols) - 1, {s: "Other" for s in p.symbols[:-1]}, scores, {}))
    return out


def test_variants_cover_one_two_and_three_desk_combinations() -> None:
    vs = lab.variants()
    assert len(vs) == (6 + 15 + 20) * 2 and len({v.key for v in vs}) == len(vs)
    month = val.Month(date(2026, 1, 30), 3, {"A": "x", "B": "x", "C": "x"},
                      {"valuation": {"A": (90, 1), "B": (50, 1), "C": (70, 1)}, "momentum": {"A": (10, 1), "B": (60, 1)}}, {})
    assert lab.picks(month, ("valuation",), 2) == ["A", "C"]
    assert lab.picks(month, ("valuation", "momentum"), 3) == ["B", "A"]          # C has no momentum score


def test_the_lab_finds_a_planted_combination_walk_forward() -> None:
    p = market_panel()
    fw = val.Forward(p)
    history = months_with_signal(fw, p, ("momentum", "growth"))
    result = lab.evaluate(fw, history)
    assert result["oos_months"] >= lab.MIN_OOS and result["passed"], result["why"]
    first = result["walk_forward"][0]
    assert first["train_months"] == lab.MIN_TRAIN                               # only months complete before it
    assert "momentum" in result["best_now"] or "growth" in result["best_now"]
    assert result["oos_vs_committee"] > 0
    noise = lab.evaluate(fw, months_with_signal(fw, p, ()))
    assert noise["oos_months"] >= lab.MIN_OOS and not noise["passed"]           # no signal, nothing promoted
    assert result["p_vs_committee"] < lab.MAX_P
    st = lab.as_strategy(result["best_now"], result)
    assert st.learned and st.style == "rebalance" and st.p["rule"] == "blend" and st.p["desks"]
    assert st.id == lab.as_strategy(result["best_now"], {**result, "why": "other"}).id     # named by its rule


def test_promotion_keeps_three_and_never_rewrites(tmp_path: Path) -> None:
    from desk.stocks.repo import StocksResearch
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    research = StocksResearch(dbs.research)
    try:
        evidence = {"variants": 82, "why": "test"}
        keys = ["valuation/10", "quality/10", "momentum/20", "growth+momentum/10"]
        retired: list[str] = []
        for k, key in enumerate(keys):
            retired += lab.promote(research, lab.as_strategy(key, evidence), evidence,
                                   datetime(2026, 10, 1 + k, tzinfo=UTC))
        assert lab.promote(research, lab.as_strategy(keys[0], evidence), evidence, NOW) == []     # already there
        strategies, gone = lab.strategies(research)
        assert [s.id for s in strategies] == [lab.as_strategy(key, evidence).id for key in keys]
        assert gone == {lab.as_strategy(keys[0], evidence).id} and retired == [lab.as_strategy(keys[0], evidence).id]
        with pytest.raises(Exception, match="never changes"):
            research.conn.execute("UPDATE stk_lab_strategies SET spec_json = '{}'")
        with pytest.raises(Exception, match="append-only"):
            research.conn.execute("DELETE FROM stk_lab_strategies")
        out = lab.run(research, market_panel(), now=NOW, mode="paper")
        assert out["promoted"] is None and "No rebuilt months" in out["why"]
    finally:
        dbs.close()
