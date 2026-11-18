"""The Options desk (desk.stocks.desks.options): research-signed option signals z-scored across the day's stocks, no
vote until its own live record is graded working, and each signal's forward record for Evidence."""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import desks as dk
from desk.stocks import option_signals as osg
from desk.stocks import validation as val
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO
from tests.unit.test_stocks_validation import DAYS, growth, panel

SYMBOLS = [f"S{i:02d}" for i in range(15)]


def ctx(signals: dict[str, dict[str, Any]]) -> dk.Context:
    return dk.Context(DAYS[-1], list(SYMBOLS), {}, dict.fromkeys(SYMBOLS, "Other"), {}, {}, {}, option_signals=signals)


def neutral(**over: float) -> dict[str, Any]:
    return {"iv_spread": 0.0, "skew": 0.05, "os_ratio": 0.1, "d_call_iv": 0.0, "d_put_iv": 0.0, "borrow": 0.0,
            "iv30": 0.3, "day": DAYS[-1].isoformat(), "source": "scan", **over}


def test_each_signal_moves_the_score_the_way_its_research_says() -> None:
    for key, sign in osg.DESK_SIGNS.items():
        base = neutral()
        signals = {s: neutral(**{key: base[key] + 0.01 * i + (0.01 if key == "os_ratio" else 0)})
                   for i, s in enumerate(SYMBOLS)}
        out = dk.options(ctx(signals), SYMBOLS)
        low, high = out[SYMBOLS[0]].score, out[SYMBOLS[-1]].score
        assert low is not None and high is not None
        assert (high > 50 > low) if sign > 0 else (high < 50 < low), key
        assert out[SYMBOLS[-1]].facts["z"][key] == pytest.approx(sign * dk.zscores(
            {s: dk.option_value(v, key) for s, v in signals.items()})[SYMBOLS[-1]], abs=1e-3)


def test_the_composite_is_the_normal_curve_of_the_mean_signed_z() -> None:
    signals = {s: neutral(iv_spread=0.01 * i, skew=0.2 - 0.01 * i) for i, s in enumerate(SYMBOLS)}
    out = dk.options(ctx(signals), SYMBOLS)
    top = out[SYMBOLS[-1]]
    z = top.facts["z"]
    assert z["iv_spread"] == pytest.approx(z["skew"]) and z["iv_spread"] > 1
    assert top.score == pytest.approx(100 * dk.norm_cdf(sum(z.values()) / len(z)), abs=0.1)
    assert top.confidence == 1.0 and top.verdict in ("Bullish option signals", "Leaning bullish")
    assert any("Cremers & Weinbaum" in r for r in top.reasons)
    assert any(strength > 0.25 for strength, _ in top.signals)


def test_z_scores_are_capped_and_need_ten_stocks() -> None:
    values: dict[str, float | None] = {s: 0.0 for s in SYMBOLS}
    values["S00"] = 1000.0
    assert dk.zscores(values)["S00"] == dk.OPTION_Z_CAP
    assert dk.zscores(dict(list(values.items())[:9])) == {}
    few = {s: neutral() for s in SYMBOLS[:9]}
    out = dk.options(ctx(few), SYMBOLS)
    assert all(r.score is None and r.verdict == "No option signals yet" for r in out.values())


def test_missing_signals_count_as_missing_not_zero() -> None:
    signals = {s: neutral(iv_spread=0.01 * i) for i, s in enumerate(SYMBOLS)}
    signals["S03"] = {**neutral(iv_spread=0.03), "borrow": None, "d_call_iv": None, "d_put_iv": None}
    signals.pop("S04")
    out = dk.options(ctx(signals), SYMBOLS)
    assert out["S03"].confidence == pytest.approx(3 / 6) and "borrow" not in out["S03"].facts["z"]
    assert out["S04"].score is None and out["S04"].confidence == 0
    assert dk.option_value({"os_ratio": 0}, "os_ratio") is None
    assert dk.option_value({"os_ratio": math.e}, "os_ratio") == pytest.approx(1.0)


def test_the_desk_has_no_vote_until_its_live_record_is_graded_working() -> None:
    assert dk.DEFAULT_WEIGHTS["options"] == 0.0 and "options" in val.LIVE_ONLY_DESKS
    loud = dk.DeskResult(99.0, 1.0, "", [])
    quiet = dk.DeskResult(50.0, 1.0, "", [])
    plain = dk.committee({"valuation": quiet}, dk.DEFAULT_WEIGHTS)
    assert dk.committee({"valuation": quiet, "options": loud}, dk.DEFAULT_WEIGHTS)["score"] == plain["score"]
    for grade, weight in (("early", 0.0), ("promising", 0.0), ("not_working", 0.0), ("working", val.PROVEN_LIVE_WEIGHT)):
        weights = dict(dk.DEFAULT_WEIGHTS)
        desks = {d: {"grade": {"grade": "early"}} for d in val.LIVE_ONLY_DESKS}
        desks["options"] = {"grade": {"grade": grade}}
        assert val.promote_live(desks, weights) == (["options"] if weight else [])
        assert weights["options"] == weight


@pytest.fixture
def market(tmp_path: Path) -> Iterator[StocksMarket]:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    try:
        yield StocksMarket(dbs.market)
    finally:
        dbs.close()


def test_its_live_record_restarts_with_the_new_signals(market: StocksMarket) -> None:
    assert val.redefined_from(market, "options") is None
    at = datetime(2026, 11, 19, 1, 0, tzinfo=UTC)                       # 8 PM in New York on the 18th
    market.register_spec("stocks_desks", 3, {"v": 3}, at - timedelta(days=30))
    assert val.redefined_from(market, "options") is None
    market.register_spec("stocks_desks", dk.METHOD_VERSION, dk.method_content(), at)
    assert val.redefined_from(market, "options") == date(2026, 11, 18)


def test_each_signal_has_its_own_forward_record(market: StocksMarket) -> None:
    rates = {s: 0.001 * (i - 7) for i, s in enumerate(SYMBOLS)}
    fw = val.Forward(panel({**{s: growth(r) for s, r in rates.items()}, "SPY": growth(0.0)}, DAYS))
    for d in DAYS[-200:-60]:
        at = datetime.combine(d, datetime.min.time(), UTC) + timedelta(hours=22)
        for s, r in rates.items():
            m = {"iv_spread": r, "pairs": 9, "skew": -r, "call_iv30": 0.3, "put_iv30": 0.3, "iv30": 0.3, "iv90": 0.3,
                 "option_volume": 100, "contracts": 40, "liquid": 40, "detail": {"missing": {}}}
            osg.save(market.conn, s, d, "scan", m, price=10.0, stock_volume=1e6, borrow=(None, None), rv22=0.3,
                     observed_at=at, now=at)
    rec = val.option_signal_record(market, fw)
    assert rec["sessions"] == 140 and rec["sampled"] == 28
    assert rec["iv_spread"]["ic"]["mean"] == pytest.approx(1.0) and rec["skew"]["ic"]["mean"] == pytest.approx(1.0)
    assert rec["iv_spread"]["in_desk"] and not rec["term_slope"]["in_desk"]
    assert rec["borrow"]["ic"]["n"] == 0                                 # never recorded: no record, not a zero one
