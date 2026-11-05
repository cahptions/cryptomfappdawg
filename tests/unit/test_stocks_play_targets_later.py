"""A saved idea that predates profit targets gets them when it is read, from its own saved legs, entry and forecast
(or its saved one-standard-deviation move), labelled as computed later; one that kept neither gets none."""

from __future__ import annotations

from typing import Any

import pytest

from desk.stocks import plays as pl
from desk.stocks.play_targets import computed_later
from tests.unit.test_stocks_play_budgets import AT, chain_table, context


def _picked(category: str = "debit_spreads", **underlying: Any) -> dict[str, Any]:
    tb = chain_table(40.)
    for k, v in underlying.items():
        setattr(tb.u, k, v)
    [candidate] = pl.SELECTORS[category](tb, context())
    return pl.finish(candidate, context())


@pytest.mark.parametrize("category,extra", [("debit_spreads", {}), ("likely_itm", {}),
                                            ("iron_condors", {"committee": 50., "iv_rank": 80.})])
def test_targets_computed_later_match_the_ones_a_new_pick_shows(category: str, extra: dict[str, Any]) -> None:
    play = _picked(category, **extra)
    original = play.pop("probability_targets")
    later = computed_later(play, AT.date())
    assert later is not None and later["computed"] == "later" and later["basis"] == "its saved KESTREL forecast"
    for k, v in original["returns"].items():
        assert later["returns"][k] == pytest.approx(v, abs=2e-3)          # the forecast is saved to four decimals
    assert (later["max_gain_probability"] is None) == (original["max_gain_probability"] is None)
    assert "Computed when this saved idea was opened" in later["note"]
    assert computed_later({**play, "probability_targets": original}, AT.date()) is None   # never replaces real ones


def test_an_idea_without_a_forecast_uses_its_expected_move_or_gets_none() -> None:
    play = _picked()
    play.pop("probability_targets")
    forecast = play.pop("forecast")
    moved = computed_later(play, AT.date())
    assert moved is not None and moved["basis"].startswith("its saved one-standard-deviation move")
    assert 0 < moved["returns"]["0"] < 1
    assert forecast["sigma"] > 0
    assert computed_later({k: v for k, v in play.items() if k != "expected_move"}, AT.date()) is None
    assert computed_later({**play, "underlying_price": None}, AT.date()) is None   # a sighting with no stock price
