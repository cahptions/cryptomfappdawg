"""Presentation only: gaps and splits remain honest, option views are bounded, no research records are changed."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from desk.core.paths import DeskPaths
from desk.stocks.contracts import Bar, OptionQuote, Split
from desk.stocks.hub import adjusted_bars, option_table, price_card
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO


def test_split_adjusted_display_does_not_rewrite_observations_or_hide_gaps(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secret.env")
    dbs = StocksDatabases.open(paths)
    try:
        market = StocksMarket(dbs.market)
        before, after = date(2026, 10, 19), date(2026, 10, 21)  # Tuesday is missing, never filled.
        now = datetime(2026, 10, 21, 22, tzinfo=UTC)
        market.save_bars(before, [Bar("ABC", before, 100, 102, 99, 100, 1000)], "synthetic", now)
        market.save_bars(after, [Bar("ABC", after, 50, 52, 49, 51, 2000)], "synthetic", now)
        market.save_splits([Split("ABC", after, 1, 2)], now)
        card = price_card(dbs.market, "ABC")
        assert len(card["history"]) == 2 and card["change"] == pytest.approx(0.02)
        assert card["history"][0]["close"] == 50 and card["history"][1]["gap_before"]
        assert card["consecutive_sessions"] is False
        assert market.bars("ABC")[0].close == 100  # Stored bars are unchanged.
        rows = [{"day": before.toordinal(), "close": 100, "volume": 1000},
                {"day": after.toordinal(), "close": 51, "volume": 2000}]
        assert adjusted_bars(dbs.market, "ABC", rows)[0]["volume"] == 2000
    finally:
        dbs.close()


def test_option_page_returns_a_bounded_selected_expiry_with_its_actual_source(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secret.env")
    dbs = StocksDatabases.open(paths)
    try:
        market = StocksMarket(dbs.market)
        today, expiry, second = date(2026, 10, 21), date(2026, 11, 20), date(2026, 12, 18)
        now = datetime(2026, 10, 21, 22, tzinfo=UTC)
        quotes = [OptionQuote(f"ABC261120C{k*1000:08}", "ABC", "call", k, expiry, 1, 1.1, 1.05, 3, 100, None, now)
                  for k in range(20, 200)]
        market.save_option_chain("ABC", today, expiry, now, 100, quotes, source="synthetic_test")
        market.save_option_chain("ABC", today, second, now, 100, [], source="synthetic_test")
        view = option_table(dbs.market, "ABC")
        assert view["source"] == "synthetic_test" and len(view["contracts"]) == 40
        assert view["expiration"] == expiry.isoformat()
        assert all(q["quoted_at"] for q in view["contracts"])
        assert option_table(dbs.market, "ABC", expiration=second)["contracts"] == []
        assert option_table(dbs.market, "UNKNOWN")["asof"] is None
    finally:
        dbs.close()
