"""Stock PAPER sizing is separate from crypto fees and bounded by cash, liquidity and planned stop loss."""

from types import SimpleNamespace

import pytest

from desk.core.config import load_config
from desk.stocks.contracts import Bar
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.engine import PaperEngine, StepReport
from desk.stocks.paper.strategies import STRATEGIES, Candidate
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


@pytest.mark.parametrize('strategy,capital,dollar_volume,expected', [
    ('breakout', 10000, 100000000, 1500),  # 15%, more than the old 10% ticket
    ('earnings_drift', 10000, 100000000, 1500),
    ('insider_cluster', 10000, 100000000, 1000),  # a 20% stop limits the ticket to 10% at 2% planned account risk
    ('breakout', 500, 100000000, 75),
    ('breakout', 10000, 100000, 500),  # 0.5% participation cap
])
def test_stock_ticket_respects_account_risk_and_capacity(tmp_path, monkeypatch, strategy, capital, dollar_volume, expected):
    world = make_world(tmp_path, n=12)
    try:
        settings = load_config(REPO / 'config').settings.stocks
        st = next(s for s in STRATEGIES if s.id == strategy)
        book = PaperBook(world.dbs.ledger)
        book.open_account(st.id, capital, NOW, NOW.date())
        engine = PaperEngine(world.market, book, settings, (st,))
        monkeypatch.setattr('desk.stocks.paper.engine.candidates', lambda *args: [Candidate('A', 75, ['fixture'], {})])
        day = SimpleNamespace(ctx=SimpleNamespace(price={'A': {'dollar_volume_20d': dollar_volume}}))
        bars = {'A': Bar('A', NOW.date(), 100, 110, 90, 100, dollar_volume / 100)}
        engine._entries(st, NOW.date(), NOW, bars, day, {}, [], StepReport(NOW.date()))
        [order] = book.pending()
        assert order.order['notional'] == pytest.approx(expected)
        assert expected <= capital * 0.20
        distance = order.order['plan'].get('stop') or order.order['plan'].get('trail')
        assert expected * distance <= capital * 0.02 + 0.01
        assert expected <= dollar_volume * 0.005
    finally:
        world.close()


def test_equity_commissions_and_costs_do_not_use_crypto_rates():
    from desk.stocks.paper import fills

    bought = fills.buy_at_open(Bar('A', NOW.date(), 100, 101, 99, 100, 1000000), 1500, 100000000)
    assert bought.fees == 0
    sold = fills.sell_at(100, bought.shares, 100000000, 'test')
    assert 0 < sold.fees < 0.10
    assert (bought.cost - sold.cost + sold.fees) / bought.cost < 0.002
    assert fills.OPTION_FEE_PER_CONTRACT == 0.65  # this reassessment does not remove option contract fees


def test_prior_sizing_version_cannot_grade_the_current_strategy(tmp_path):
    from desk.stocks.paper.report import account_report, grade

    world = make_world(tmp_path, n=12)
    try:
        st = next(s for s in STRATEGIES if s.id == 'breakout')
        book = PaperBook(world.dbs.ledger)
        book.open_account(st.id, 10000, NOW, NOW.date())
        book.decide(NOW, NOW.date(), st.id, 'previous-version', 'A', 'ENTER', [], {})
        result = account_report(book, world.market, st, None, {})
        result.update(sessions=1000, trades=1000, vs_baseline={'p': 0.001, 'p_holm': 0.001})
        assert result['mixed_specifications']
        assert grade(result)['grade'] == 'early'
    finally:
        world.close()
