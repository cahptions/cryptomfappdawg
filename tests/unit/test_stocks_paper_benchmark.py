"""PAPER accounts are paid dividends, so SPY is compared on its total return, not its price alone."""

import pytest

from desk.stocks.backtest import memory_db
from desk.stocks.contracts import Dividend, Split
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.report import report, spy_total
from desk.stocks.paper.strategies import BY_STRATEGY
from desk.storage.db import DbKind
from tests.stocks_world import NOW, make_world


def test_spy_total_return_reinvests_dividends_and_applies_splits(tmp_path):
    w = make_world(tmp_path, n=8)
    try:
        start = w.days[-30]
        closes = {b.day: b.close for b in w.market.bars("SPY", start)}
        price_only = closes[w.days[-1]] / closes[start] - 1
        ex = w.days[-15]
        w.market.save_dividends([Dividend("SPY", ex, 1.75, None, 4, "CD")], NOW)
        index = spy_total(w.market, start)
        prior = closes[w.days[-16]]
        assert index[ex] / index[w.days[-16]] == pytest.approx((closes[ex] + 1.75) / prior)
        assert index[w.days[-1]] - 1 > price_only
        w.market.save_splits([Split("SPY", w.days[-5], 1, 2)], NOW)
        split = spy_total(w.market, start)
        assert split[w.days[-5]] / split[w.days[-6]] == pytest.approx(closes[w.days[-5]] / (closes[w.days[-6]] / 2))
    finally:
        w.close()


def test_strategy_report_compares_with_spy_total_return(tmp_path):
    w = make_world(tmp_path, n=8)
    try:
        st = BY_STRATEGY["momentum"]
        book = PaperBook(memory_db(DbKind.STOCKS_LEDGER))
        book.open_account(st.id, 10000, NOW, w.days[-30])
        for day in w.days[-30:]:
            book.snapshot(day, st.id, NOW, 10000.0, 0.0)
        closes = {b.day: b.close for b in w.market.bars("SPY", w.days[-30])}
        before = report(book, w.market, (st,))["accounts"][0]["spy_return"]
        assert before == pytest.approx(closes[w.days[-1]] / closes[w.days[-30]] - 1)
        w.market.save_dividends([Dividend("SPY", w.days[-10], 2.0, None, 4, "CD")], NOW)
        after = report(book, w.market, (st,))["accounts"][0]["spy_return"]
        assert after == pytest.approx((1 + before) * (closes[w.days[-10]] + 2.0) / closes[w.days[-10]] - 1)
    finally:
        w.close()
