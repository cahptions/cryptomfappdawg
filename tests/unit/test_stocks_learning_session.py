"""Learning follows the calendar's session close (cal.session_close), so a half day ends trading, collection and the
expiry settlement wait at 1 PM instead of a hard-coded 4 PM."""

from datetime import UTC, date, datetime, time, timedelta

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import learning, learning_service
from desk.stocks.paper.book import PaperBook
from tests.stocks_world import make_world
from tests.unit.test_stocks_learning import AT, chain
from tests.unit.test_stocks_learning_settlement import WEEKLY, bought, close

DAY = date(2026, 10, 21)        # an ordinary full session, made a half day here (whatever the calendar learns later)


def early(monkeypatch, *days):
    full = cal.session_close
    monkeypatch.setattr(cal, "session_close",
                        lambda d: datetime.combine(d, time(13), cal.NEW_YORK) if d in days else full(d))


def test_the_regular_session_ends_at_the_calendar_close(monkeypatch):
    two_pm = datetime.combine(DAY, time(14), cal.NEW_YORK).astimezone(UTC)
    assert learning.regular_session(two_pm)                                    # an ordinary close: still open
    early(monkeypatch, DAY)
    assert not learning.regular_session(two_pm)
    assert learning.regular_session(two_pm - timedelta(hours=2))
    assert not learning.regular_session(datetime.combine(DAY, time(9, 29), cal.NEW_YORK))


async def test_after_a_half_day_close_learning_neither_trades_nor_collects(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        early(monkeypatch, AT.date())
        after = datetime.combine(AT.date(), time(14), cal.NEW_YORK).astimezone(UTC)
        chain(world, after)
        result = learning.tick(conn, world.market, after)
        assert result["phase"].startswith("Market closed") and result["orders"] == 0
        assert conn.execute("SELECT COUNT(*) FROM stk_pending").fetchone()[0] == 0
        collected = await learning_service.collect(world.paths, load_config(world.paths.config_dir), conn, world.market,
                                                   after)
        assert collected == {"phase": "Market closed"}
    finally:
        conn.close()
        world.close()


def test_an_expiry_on_a_half_day_settles_after_its_1_pm_close(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        weekly = bought(world, conn)
        close(world, 103.0)
        early(monkeypatch, WEEKLY)
        half_past_one = datetime.combine(WEEKLY, time(13, 30), cal.NEW_YORK).astimezone(UTC)
        assert learning.tick(conn, world.market, half_past_one)["settled"] == len(weekly)
        assert not [p for p in PaperBook(conn).positions() if p.expires == WEEKLY]
    finally:
        conn.close()
        world.close()
