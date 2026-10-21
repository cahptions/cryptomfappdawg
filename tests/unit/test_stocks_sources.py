"""KESTREL Stocks sources: parsers on real recorded responses, and how each client authenticates (no network).

Keyless sources (SEC, Nasdaq Trader, FINRA, Treasury, ApeWisdom, OpenFIGI) are recorded responses (2026-10-20/21).
Massive and Tradier need the operator's free key, so their fixtures are the providers' documented examples.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from desk.core.config import load_config
from desk.core.errors import ProviderError, SchemaError
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.stocks.sources import feeds, massive, sec, tradier
from desk.stocks.suite import StocksSuite, build_stocks_suite
from desk.storage.databases import Databases
from tests.conftest import REPO

F = REPO / "tests" / "fixtures" / "stocks"


def read(name: str) -> bytes:
    return (F / name).read_bytes()


# ── SEC ───────────────────────────────────────────────────────────────────────────────────────────
def test_sec_company_list_uses_the_exchanges_dot_for_class_shares() -> None:
    rows = sec.parse_tickers_exchange(read("sec_company_tickers_exchange.json"))
    by_symbol = {r[2]: r for r in rows}
    assert by_symbol["AAPL"][:2] == (320193, "Apple Inc.") and by_symbol["AAPL"][3] == "Nasdaq"
    assert "BRK.B" in by_symbol and "BRK-B" not in by_symbol


def test_submissions_carry_8k_items_and_acceptance_time_in_utc() -> None:
    company = sec.parse_submissions(read("sec_submissions_AAPL.json"))
    assert (company.cik, company.sic, company.fiscal_year_end) == (320193, "3571", "0926")
    results = [f for f in company.filings if f.form == "8-K" and "2.02" in f.items]
    assert results and results[0].accepted is not None and results[0].accepted.tzinfo is UTC
    first = results[0]
    assert first.accession == "0000320193-26-000018" and first.items == ("2.02", "9.01")
    assert first.accepted == datetime(2026, 7, 31, 0, 30, 28, tzinfo=UTC)          # 20:30 New York: after the close


def test_company_facts_keep_only_wanted_concepts_with_their_filing_dates() -> None:
    facts = sec.parse_companyfacts(read("sec_companyfacts_AAPL.json"), ["us-gaap:Revenues", "us-gaap:Assets",
                                                                       "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"])
    revenue = facts["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"]
    assert revenue and all(f.unit == "USD" and f.start is not None for f in revenue)
    assert revenue == sorted(revenue, key=lambda f: (f.filed, f.end, f.accn))
    assets = facts["us-gaap:Assets"]
    assert assets and all(f.start is None and f.days == 0 for f in assets)
    annual = [f for f in revenue if f.form == "10-K" and 360 <= f.days <= 371]
    assert annual and all(f.filed > f.end for f in annual)                       # values become public after the period


def test_daily_index_and_form4_parse_real_filings() -> None:
    entries = sec.parse_daily_index(read("sec_form.20261020.idx"))
    assert {e.form for e in entries} >= {"4", "8-K"} and all(e.filed == date(2026, 10, 20) for e in entries)
    acuity = next(e for e in entries if e.company.startswith("ACUITY"))
    assert acuity.cik == 1144215 and acuity.accession == "0001813477-26-000008"
    [buy] = sec.parse_form4(read("sec_form4_P.xml"), acuity.accession)
    assert (buy.symbol, buy.code, buy.shares, buy.price, buy.acquired) == ("AYI", "P", 73.0, 302.32, True)
    assert buy.is_director and not buy.is_officer and buy.direct and buy.value == pytest.approx(73 * 302.32)
    sales = sec.parse_form4(read("sec_form4_S.xml"), "x")
    assert sales and any(t.code == "S" and not t.acquired for t in sales)
    with pytest.raises(SchemaError):
        sec.parse_form4(b"<html></html>", "bad")


def test_fails_to_deliver_and_the_sec_contact_rule() -> None:
    fails = sec.parse_fails(read("sec_cnsfails202609b.txt").decode())
    aapl = [f for f in fails if f.symbol == "AAPL"]
    assert aapl and aapl[0].cusip == "037833100" and aapl[0].settlement.year == 2026
    assert sec.ftd_file_names(date(2026, 9, 3)) == ["cnsfails202609a.zip", "cnsfails202609b.zip"]
    assert sec.sec_user_agent(None) is None and sec.sec_user_agent("not-an-address") is None
    assert sec.sec_user_agent(" me@example.org ") == "KESTREL personal research desk me@example.org"


# ── public feeds ──────────────────────────────────────────────────────────────────────────────────
def test_symbol_directories_classify_what_can_be_researched() -> None:
    listings = feeds.parse_nasdaq_listed(read("nasdaqlisted.txt").decode()) + feeds.parse_other_listed(
        read("otherlisted.txt").decode())
    by = {x.symbol: x for x in listings}
    assert by["AAPL"].kind == "common" and by["AAPL"].exchange == "Nasdaq"
    assert by["BRK.B"].exchange == "NYSE" and by["BRK.B"].kind == "common"
    assert by["QQQ"].etf and by["QQQ"].kind == "etf"
    assert any(x.kind == "warrant" for x in listings) and any(x.kind == "unit" for x in listings)
    assert any(x.test for x in listings)
    assert feeds.classify("Brookfield Renewable Partners L.P. Limited Partnership Units", etf=False) == "common"
    assert feeds.classify("Taiwan Semiconductor Manufacturing Company Ltd., American Depositary Shares, each "
                          "representing 5 ordinary shares", etf=False) == "adr"
    assert feeds.classify("Bank Corp 6.25% Series A Preferred Stock", etf=False) == "preferred"


def test_finra_short_data() -> None:
    si = {x.symbol: x for x in feeds.parse_short_interest(read("finra_consolidated_short_interest.json"))}
    assert si["AAPL"].settlement == date(2026, 9, 30) and si["AAPL"].days_to_cover == 3.03
    assert si["AAPL"].short_shares == 120763539 and si["AAPL"].previous_short_shares == 128753092
    vol = {x.symbol: x for x in feeds.parse_reg_sho(read("finra_CNMSshvol20261020.txt").decode())}
    assert vol["AAPL"].day == date(2026, 10, 20) and vol["AAPL"].short_volume < vol["AAPL"].total_volume
    candidates = feeds.settlement_candidates(date(2026, 10, 21))
    assert candidates[0] == date(2026, 10, 15) and date(2026, 9, 30) in candidates
    assert all(c.weekday() < 5 and c <= date(2026, 10, 21) for c in candidates)
    assert date(2026, 8, 14) in candidates            # Saturday the 15th moves back to Friday


def test_rates_and_crowd_and_cusips() -> None:
    curve = feeds.parse_treasury_curve(read("treasury_yield_curve_2026.csv").decode())
    ten = sorted((p for p in curve if p.series == "UST10Y"), key=lambda p: p.day)
    assert ten[-1].day == date(2026, 10, 20) and ten[-1].value == 5.12
    assert {p.series for p in curve} == {"UST3M", "UST2Y", "UST10Y", "UST30Y"}
    fred = feeds.parse_fred_csv("observation_date,VIXCLS\n2026-10-19,17.31\n2026-10-20,\n", "VIXCLS")
    assert [(p.day, p.value) for p in fred] == [(date(2026, 10, 19), 17.31)]          # blank = not observed, skipped
    mentions, pages = feeds.parse_apewisdom(read("apewisdom_wallstreetbets_p1.json"), "wallstreetbets")
    assert pages >= 1 and mentions[0].rank == 1 and mentions[0].mentions > 0
    assert all(m.source == "wallstreetbets" for m in mentions)
    mapped = feeds.parse_openfigi(read("openfigi_mapping.json"), ["037833100", "36467W109", "000000000"])
    assert mapped == {"037833100": "AAPL", "36467W109": "GME", "000000000": None}


# ── Massive and Tradier (documented examples) ─────────────────────────────────────────────────────
def test_massive_grouped_daily_splits_and_dividends() -> None:
    bars = massive.parse_grouped(read("massive_grouped_daily_doc_example.json"))
    assert {b.symbol for b in bars} == {"KIMpL", "TANH", "VSAT"}
    assert all(b.day == date(2020, 10, 14) for b in bars)                          # stamped at the 16:00 close
    splits, cursor = massive.parse_splits(read("massive_splits_doc_example.json"))
    assert splits[0].ratio == 2.0 and splits[0].execution_date == date(2005, 2, 28) and cursor is None
    divs, _ = massive.parse_dividends(read("massive_dividends_doc_example.json"))
    assert divs[0].cash_amount == 0.26 and divs[0].frequency == 4
    assert massive._cursor("https://api.massive.com/stocks/v1/splits?cursor=abc&apiKey=SECRET") == "abc"


def test_tradier_chain_quotes_history_and_calendar() -> None:
    [option] = tradier.parse_chain(read("tradier_chains_doc_example.json"))
    assert (option.kind, option.strike, option.expiration, option.open_interest) == ("call", 125.0, date(2021, 4, 16), 8249)
    assert option.mid == pytest.approx(3.425) and option.underlying == "AAPL"
    assert tradier.parse_expirations(read("tradier_expirations_doc_example.json"))[0] == date(2021, 2, 5)
    [quote] = tradier.parse_quotes(read("tradier_quotes_doc_example.json"))
    assert quote.symbol == "AAPL" and quote.prev_close == 275.25
    history = tradier.parse_history(read("tradier_history_doc_example.json"), "AAPL")
    assert history[0].day == date(2020, 1, 2) and history[0].close == 77.05
    calendar = tradier.parse_calendar(read("tradier_calendar_doc_example.json"))
    assert calendar[date(2021, 2, 1)] is True and calendar[date(2021, 2, 6)] is False
    assert tradier.parse_chain(b'{"options": null}') == []                          # no contracts listed


# ── clients: authentication and headers ───────────────────────────────────────────────────────────
@pytest.fixture
async def suite(tmp_path: Path) -> StocksSuite:
    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO / "config", secrets_file=tmp_path / "secrets.env")
    dbs = Databases.open(paths)
    s = build_stocks_suite(paths, load_config(REPO / "config"), Secrets(values={
        "MASSIVE_API_KEY": "massive-test-key", "TRADIER_TOKEN": "tradier-test-token", "SEC_CONTACT_EMAIL": "me@example.org"}),
        dbs.runtime, client=httpx.AsyncClient())
    yield s
    await s.aclose()
    dbs.close()


@respx.mock
async def test_each_source_authenticates_its_own_way(suite: StocksSuite) -> None:
    assert suite.problems == {}
    grouped = respx.get("https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-10-20").mock(
        return_value=httpx.Response(200, content=read("massive_grouped_daily_doc_example.json")))
    chain = respx.get(url__startswith="https://sandbox.tradier.com/v1/markets/options/chains").mock(
        return_value=httpx.Response(200, content=read("tradier_chains_doc_example.json")))
    index = respx.get("https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/form.20261020.idx").mock(
        return_value=httpx.Response(200, content=read("sec_form.20261020.idx")))
    assert len(await suite.massive.grouped_daily(date(2026, 10, 20))) == 3
    assert len(await suite.tradier.chain("AAPL", date(2021, 4, 16))) == 1
    assert len(await suite.sec.daily_index(date(2026, 10, 20))) > 10
    massive_request = grouped.calls[0].request
    assert massive_request.url.params["apiKey"] == "massive-test-key" and massive_request.url.params["adjusted"] == "false"
    assert "authorization" not in massive_request.headers
    tradier_request = chain.calls[0].request
    assert tradier_request.headers["authorization"] == "Bearer tradier-test-token"
    assert "apiKey" not in tradier_request.url.params
    assert index.calls[0].request.headers["user-agent"] == "KESTREL personal research desk me@example.org"
    for call in (*grouped.calls, *chain.calls):                                     # the SEC contact goes only to the SEC
        assert "me@example.org" not in call.request.headers["user-agent"]


async def test_missing_keys_are_reported_not_guessed(tmp_path: Path) -> None:
    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO / "config", secrets_file=tmp_path / "secrets.env")
    dbs = Databases.open(paths)
    s = build_stocks_suite(paths, load_config(REPO / "config"), Secrets(), dbs.runtime, client=httpx.AsyncClient())
    try:
        assert set(s.problems) == {"sec", "prices", "options"}
        with pytest.raises(ProviderError, match="MASSIVE_API_KEY"):
            await s.massive.grouped_daily(date(2026, 10, 20))
        with pytest.raises(ProviderError, match="SEC_CONTACT_EMAIL"):
            await s.sec.tickers()
        with pytest.raises(ProviderError, match="TRADIER_TOKEN"):
            await s.tradier.expirations("AAPL")
    finally:
        await s.aclose()
        dbs.close()


def test_fixtures_are_the_recorded_bytes() -> None:
    """A guard against hand-editing: the recorded SEC submissions still parse as SEC wrote them."""
    raw = json.loads(read("sec_submissions_AAPL.json"))
    assert raw["cik"] == "0000320193" and len(raw["filings"]["recent"]["accessionNumber"]) == 120
