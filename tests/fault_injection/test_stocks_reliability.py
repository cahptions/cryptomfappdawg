"""Adversarial source/cache/transaction inputs in isolated Stocks databases; never operator market data."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import AsyncMock

import numpy as np
import pytest

from desk.core.config import load_config
from desk.core.errors import SchemaError
from desk.stocks.analysis import Analyzer
from desk.stocks.contracts import Split
from desk.stocks.prices import split_factors
from desk.stocks.sources import feeds, massive, sec
from desk.stocks.sources.massive import MassiveClient
from desk.storage.db import transaction
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def test_future_split_cannot_change_an_earlier_panel() -> None:
    days = [date(2026, 10, 19), date(2026, 10, 20)]
    future = Split("A", date(2026, 10, 21), 1, 10)
    np.testing.assert_array_equal(split_factors([future], ["A"], days), [[1, 1]])
    np.testing.assert_array_equal(split_factors([future], ["A"], [*days, future.execution_date]), [[10, 10, 1]])


def test_fundamental_cache_updates_when_new_source_facts_arrive(tmp_path) -> None:
    world = make_world(tmp_path, n=12)
    try:
        cik = world.specs[0].cik
        settings = load_config(REPO / "config").settings.stocks
        before = Analyzer(world.market, world.research, settings).bases({cik: None}, NOW.date(), cache=True)[cik]
        facts = world.market.facts(cik)
        for name, rows in facts.items():
            if name == "us-gaap:Revenues":
                facts[name] = [replace(f, value=f.value * 2) for f in rows]
        world.market.save_facts(cik, facts, "new-synthetic-source-revision", NOW)
        after = Analyzer(world.market, world.research, settings).bases({cik: None}, NOW.date(), cache=True)[cik]
        assert after["revenue"] == pytest.approx(before["revenue"] * 2)
    finally:
        world.close()


@pytest.mark.parametrize("parser", [massive.parse_grouped, massive.parse_splits, massive.parse_dividends,
                                   sec.parse_tickers_exchange, sec.parse_submissions,
                                   lambda body: sec.parse_companyfacts(body, ["us-gaap:Revenues"])])
@pytest.mark.parametrize("body", [b"null", b"[]", b'"not-an-object"'])
def test_bad_json_shape_is_a_schema_failure(parser, body: bytes) -> None:
    with pytest.raises(SchemaError):
        parser(body)


@pytest.mark.parametrize("field,value", [("o", 0), ("c", -1), ("h", 1), ("l", 200), ("v", -1), ("c", "NaN")])
def test_invalid_grouped_bar_never_becomes_market_data(field: str, value) -> None:
    row = {"T": "A", "o": 100, "h": 110, "l": 90, "c": 105, "v": 1000, "t": 1792526400000}
    row[field] = value
    with pytest.raises(SchemaError):
        massive.parse_grouped(json.dumps({"status": "OK", "results": [row]}).encode())


async def test_repeated_corporate_action_cursor_is_not_success() -> None:
    client = MassiveClient(None)  # _get is isolated from HTTP/rate limits for this parser-level fault
    client._get = AsyncMock(return_value=b'{"results": [], "next_url": "https://api.massive.com/x?cursor=again"}')
    with pytest.raises(SchemaError):
        await client.splits(date(2026, 1, 1))
    assert client._get.await_count <= 3


def test_commit_failure_rolls_back_and_connection_can_be_reused() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE child(id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
    try:
        with pytest.raises(sqlite3.IntegrityError), transaction(conn):
            conn.execute("INSERT INTO child VALUES (7)")
        assert not conn.in_transaction
        assert conn.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0
        with transaction(conn):
            conn.execute("INSERT INTO parent VALUES (7)")
    finally:
        conn.close()


def test_sqlite_page_limit_failure_rolls_back_without_masking_disk_full() -> None:
    conn = sqlite3.connect(':memory:', isolation_level=None)
    conn.execute('CREATE TABLE payload(data BLOB)')
    pages = conn.execute('PRAGMA page_count').fetchone()[0]
    conn.execute(f'PRAGMA max_page_count={pages}')
    try:
        with pytest.raises(sqlite3.OperationalError, match='full'), transaction(conn):
            conn.execute('INSERT INTO payload VALUES (zeroblob(1048576))')
        assert not conn.in_transaction
        assert conn.execute('SELECT COUNT(*) FROM payload').fetchone()[0] == 0
        with transaction(conn):
            conn.execute("INSERT INTO payload VALUES (X'01')")
    finally:
        conn.close()


@pytest.mark.parametrize('body', [b'not zip', b'', b'PK\x03'])
def test_bad_sec_archive_is_a_named_source_failure(body: bytes) -> None:
    with pytest.raises(SchemaError):
        sec.parse_fails_zip(body)


@pytest.mark.parametrize('body', ['<html>Maintenance</html>', 'Date,10 Yr\n10/99/2026,4.0',
                                  'Date,10 Yr\n10/21/2026,NaN'])
def test_bad_treasury_never_becomes_rate_data(body: str) -> None:
    with pytest.raises(SchemaError):
        feeds.parse_treasury_curve(body)


def test_bad_provider_values_are_not_copied_into_public_error_messages() -> None:
    sentinel = 'PRIVATE-RESPONSE-SENTINEL'
    for parser, body in ((lambda data: feeds.parse_fred_csv(data, 'VIXCLS'),
                          f'DATE,VIXCLS\n2026-10-21,{sentinel}'),
                         (feeds.parse_short_interest,
                          f'symbolCode,settlementDate,currentShortPositionQuantity\nA,2026-10-21,{sentinel}'.encode())):
        with pytest.raises(SchemaError) as error:
            parser(body)
        assert sentinel not in str(error.value)


def test_cash_dividend_uses_actual_cash_not_later_split_adjustment() -> None:
    body = b'{"results":[{"ticker":"A","ex_dividend_date":"2026-10-20","cash_amount":2,"split_adjusted_cash_amount":1}]}'
    assert massive.parse_dividends(body)[0][0].cash_amount == 2


def test_option_missing_volatility_and_crossed_market_are_not_executable_prices() -> None:
    from desk.stocks.contracts import OptionQuote
    from desk.stocks.paper import fills

    day = NOW.date()
    leg = {'occ': 'X', 'kind': 'call', 'strike': 100, 'expiration': (day + timedelta(days=30)).isoformat(), 'quantity': 1}
    assert fills.model_price(leg, 120, day, 0.04) is None
    assert fills.close_legs([leg], {}, 120, day, 0.04).problem
    crossed = OptionQuote('X', 'A', 'call', 100, day, 10, 5, 7, 0, 0, None, NOW)
    assert crossed.mid is None
    assert fills.close_legs([leg], {'X': crossed}, 120, day, 0.04).problem
    modeled = fills.close_legs([{**leg, 'last_iv': 0.3}], {}, 120, day, 0.04)
    assert modeled.problem is None and modeled.modeled


def test_wrong_currency_and_misaligned_financial_years_are_not_combined() -> None:
    from desk.stocks.fundamentals import metrics, scores
    from tests.unit.test_stocks_fundamental_scores import ASOF, company

    f = company()
    f.facts['us-gaap:Revenues'] = [replace(row, unit='EUR') for row in f.facts['us-gaap:Revenues']]
    assert metrics(f, ASOF, 100)['revenue'] is None
    f = company()
    f.facts['us-gaap:NetIncomeLoss'] = [replace(row, end=row.end - timedelta(days=365))
                                      for row in f.facts['us-gaap:NetIncomeLoss']]
    assert scores(f, ASOF)['piotroski'] is None


def test_killed_stocks_session_preserves_committed_cash_and_rolls_back_position(tmp_path) -> None:
    import select
    import subprocess
    import sys
    import textwrap

    from desk.stocks.paper.book import PaperBook

    world = make_world(tmp_path, n=12)
    child = None
    try:
        book = PaperBook(world.dbs.ledger)
        book.open_account('crash-test', 1000, NOW, NOW.date())
        script = textwrap.dedent('''
            import signal, sys
            from pathlib import Path
            from desk.core.paths import DeskPaths
            from desk.stocks.paper.book import PaperBook
            from desk.stocks.repo import StocksDatabases
            from tests.conftest import REPO
            from tests.stocks_world import NOW
            paths = DeskPaths(Path(sys.argv[1]), REPO / 'config', Path(sys.argv[2]))
            dbs = StocksDatabases.open(paths)
            book = PaperBook(dbs.ledger)
            with book.atomic():
                decision = book.decide(NOW, NOW.date(), 'crash-test', 'test', 'A', 'ENTER', [], {})
                book.open_position(account='crash-test', spec_sha256='test', asset='stock', symbol='A',
                    structure='stock', legs=[], decision_id=decision, at=NOW, day=NOW.date(), cost=500,
                    fees=0, max_loss=None, stop_price=90, expires=None, plan={})
                print('UNCOMMITTED', flush=True)
                signal.pause()
        ''')
        child = subprocess.Popen([sys.executable, '-c', script, str(world.paths.home), str(world.paths.secrets_file)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert select.select([child.stdout], [], [], 5)[0], 'child did not enter its transaction'
        assert child.stdout.readline().strip() == 'UNCOMMITTED'
        assert book.cash('crash-test') == 1000  # another reader cannot see the uncommitted debit
        child.kill()
        child.communicate(timeout=5)
        assert book.cash('crash-test') == 1000
        assert book.positions('crash-test') == []
        assert world.dbs.ledger.execute('SELECT COUNT(*) FROM stk_decisions').fetchone()[0] == 0
        assert world.dbs.ledger.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        # Recovery is writable, and retrying the original account initialization cannot double its deposit.
        assert not book.open_account('crash-test', 1000, NOW, NOW.date())
        with book.atomic():
            book.add_cash('crash-test', NOW, NOW.date(), -1_000_000, 'FEE')
        assert book.cash('crash-test') == 999
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
        world.close()


def test_option_snapshot_observed_after_decision_is_unavailable(tmp_path) -> None:
    world = make_world(tmp_path, n=12)
    try:
        expiry = NOW.date() + timedelta(days=30)
        world.market.save_option_chain('A', NOW.date(), expiry, NOW + timedelta(seconds=1), 100, [])
        assert world.market.option_chain('A', NOW.date(), observed_by=NOW) == (None, {})
        assert world.market.option_chain('A', NOW.date(), observed_by=NOW + timedelta(seconds=2))[0] == NOW.date()
    finally:
        world.close()
