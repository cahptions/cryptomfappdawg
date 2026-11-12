"""Every collected option chain is kept (they are the only real bid/ask history the app has). Only past the disk budget
(``stocks.option_chain_budget_gb``; 0 = no limit) are whole sessions deleted, oldest first, never today's."""

from __future__ import annotations

from pathlib import Path

from desk.core.config import load_config
from desk.stocks import collect
from tests.conftest import REPO
from tests.stocks_world import add_chains, make_world


def test_chains_are_kept_and_only_the_oldest_sessions_go_past_the_budget(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=8)
    try:
        days = w.days[-6:]
        add_chains(w, ["S00", "S01"], days)
        market = w.market
        stored = market.chain_storage()
        assert stored["sessions"] == 6 and stored["first"] == days[0].isoformat()
        assert stored["bytes"] > 0 and stored["bytes_per_session"] == round(stored["bytes"] / 6)
        # no limit, or under the budget: nothing goes, however old
        assert market.prune_option_chains(None, days[-1])["sessions"] == 0
        assert market.prune_option_chains(stored["bytes"], days[-1])["sessions"] == 0
        assert market.chain_storage()["sessions"] == 6
        # over it: whole sessions, oldest first, until under
        per_day = {r[0]: r[1] for r in market.conn.execute(
            "SELECT day, SUM(length(chain_zlib)) FROM stk_option_chains GROUP BY day")}
        budget = stored["bytes"] - per_day[days[0].toordinal()] - 1
        pruned = market.prune_option_chains(budget, days[-1])
        assert pruned["sessions"] == 2 and pruned["through"] == days[1].isoformat()
        assert pruned["bytes"] == per_day[days[0].toordinal()] + per_day[days[1].toordinal()]
        after = market.chain_storage()
        assert after["first"] == days[2].isoformat() and after["bytes"] <= budget
        # today's chains are never deleted, even when they alone exceed the budget
        assert market.prune_option_chains(1, days[-1])["through"] == days[-2].isoformat()
        assert market.chain_storage()["sessions"] == 1 and market.option_chain("S00", days[-1])[0] == days[-1]
    finally:
        w.close()


def test_the_budget_setting_defaults_to_10_gb_and_0_means_no_limit() -> None:
    settings = load_config(REPO / "config").settings.stocks
    assert settings.option_chain_budget_gb == 10
    assert collect.chain_budget(settings.option_chain_budget_gb) == collect.CHAIN_BUDGET_BYTES == 10 * 10**9
    assert collect.chain_budget(0) is None and collect.chain_budget(0.5) == 500_000_000
