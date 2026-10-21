"""KESTREL Stocks data layer: calendar, storage rules, point-in-time fundamentals, split-adjusted prices, options math."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.contracts import Bar, Fact, InsiderTrade, Listing, OptionQuote, Split
from desk.stocks.fundamentals import CONCEPTS, Fundamentals, dcf_value, implied_growth, metrics, ttm
from desk.stocks.prices import load_panel, split_factors
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch
from desk.stocks.sectors import financial_type, sector
from desk.stocks.sources.sec import parse_companyfacts
from desk.storage.db import StorageError
from tests.conftest import REPO

F = REPO / "tests" / "fixtures" / "stocks"
NOW = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)


@pytest.fixture
def dbs(tmp_path: Path) -> StocksDatabases:
    d = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield d
    d.close()


# ── calendar and sectors ──────────────────────────────────────────────────────────────────────────
def test_nyse_holidays_2026_and_sessions() -> None:
    assert cal.holidays(2026) == {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
                                  date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
                                  date(2026, 11, 26), date(2026, 12, 25)}
    assert date(2027, 12, 31) not in cal.holidays(2027) and date(2027, 12, 24) in cal.holidays(2027)   # Christmas Sat
    assert date(2022, 1, 1).weekday() == 5 and date(2021, 12, 31) not in cal.holidays(2021)            # New Year Sat
    assert cal.previous_trading_day(date(2026, 10, 19)) == date(2026, 10, 16)
    assert cal.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)
    before_close = datetime(2026, 10, 21, 19, 59, tzinfo=UTC)                                            # 15:59 New York
    assert cal.last_closed_session(before_close) == date(2026, 10, 20)
    assert cal.last_closed_session(NOW, timedelta(minutes=75)) == date(2026, 10, 21)


def test_sectors_follow_sic_codes() -> None:
    assert (sector("3571"), sector("6021"), sector("7372"), sector(None), sector("9999")) == (
        "BusEq", "Money", "BusEq", "Other", "Other")
    assert (financial_type("6021"), financial_type("6798"), financial_type("6311"), financial_type("3571")) == (
        "bank", "reit", "insurer", None)


# ── storage rules ─────────────────────────────────────────────────────────────────────────────────
def test_listings_are_upserted_and_unlisted_names_are_kept(dbs: StocksDatabases) -> None:
    market = StocksMarket(dbs.market)
    a = Listing("AAA", "A Inc Common Stock", "NYSE", False, False, "common")
    b = Listing("BBB", "B Inc Common Stock", "Nasdaq", False, False, "common")
    assert market.save_listings([a, b], {"AAA": 1}, NOW) == {"new": 2, "changed": 0, "inactive": 0}
    assert market.save_listings([a], {"AAA": 1}, NOW + timedelta(days=1)) == {"new": 0, "changed": 0, "inactive": 1}
    rows = {r["symbol"]: r for r in market.listings(active=False)}
    assert rows["BBB"]["active"] == 0 and rows["AAA"]["cik"] == 1
    with pytest.raises(Exception, match="append-only"):
        dbs.market.execute("DELETE FROM stk_listing_events")


def test_bars_are_insert_once_and_lists_append_only(dbs: StocksDatabases) -> None:
    market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
    day = date(2026, 10, 20)
    assert market.save_bars(day, [Bar("AAA", day, 10, 11, 9, 10.5, 1000)], "test", NOW) == 1
    assert market.save_bars(day, [Bar("AAA", day, 99, 99, 99, 99, 1)], "test", NOW) == 0   # first value stands
    assert market.bars("AAA")[0].close == 10.5
    with pytest.raises(Exception, match="append-only"):
        dbs.market.execute("UPDATE stk_bars SET close = 1")
    rows = [{"symbol": "AAA", "reason": "cheap", "value": 1.0}]
    assert research.save_list(day, "undervalued", rows, "spec", NOW)
    assert not research.save_list(day, "undervalued", [{"symbol": "ZZZ", "reason": "x"}], "spec", NOW)
    assert [r["symbol"] for r in research.list_on("undervalued")[1]] == ["AAA"]
    with pytest.raises(Exception, match="append-only"):
        dbs.research.execute("DELETE FROM stk_list_snapshots")


def test_specs_are_frozen_by_version(dbs: StocksDatabases) -> None:
    market = StocksMarket(dbs.market)
    digest = market.register_spec("undervalued", 1, {"rule": "a"}, NOW)
    assert market.register_spec("undervalued", 1, {"rule": "a"}, NOW) == digest
    with pytest.raises(StorageError, match="without a new version"):
        market.register_spec("undervalued", 1, {"rule": "b"}, NOW)
    assert market.register_spec("undervalued", 2, {"rule": "b"}, NOW) != digest


def test_paper_ledger_is_paper_only(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    d = StocksDatabases.open(paths)
    d.ledger.execute("UPDATE ledger_meta SET value = 'LIVE' WHERE key = 'mode'")
    d.close()
    with pytest.raises(StorageError, match="PAPER only"):
        StocksDatabases.open(paths)


def test_insider_rows_number_per_filing_and_owner(dbs: StocksDatabases) -> None:
    market = StocksMarket(dbs.market)
    t = InsiderTrade("acc-1", 1, "AAA", "Owner", 9, True, False, None, False, date(2026, 10, 1), "P", 100, 10.0, True, 100,
                     True, False)
    stored = market.save_insider_trades([(t, date(2026, 10, 2)), (t, date(2026, 10, 2))])
    assert stored == 2 and market.save_insider_trades([(t, date(2026, 10, 2))]) == 0
    assert [r["line"] for r in market.insider_trades(date(2026, 1, 1))] == [0, 1]


# ── point-in-time fundamentals ────────────────────────────────────────────────────────────────────
def facts(symbol: str) -> Fundamentals:
    return Fundamentals(parse_companyfacts((F / f"sec_companyfacts_{symbol}.json").read_bytes(), CONCEPTS))


def test_ttm_uses_only_values_filed_before_the_day() -> None:
    aapl = facts("AAPL")
    revenue = aapl.series("revenue", date(2026, 10, 21))
    flow = ttm(revenue, date(2026, 10, 21))
    assert flow is not None and flow.source in ("annual+ytd", "annual")
    filed_after = max(f.filed for f in revenue)
    earlier = ttm(revenue, filed_after)                         # filed that very day: not yet known
    later = ttm(revenue, filed_after + timedelta(days=1))
    assert earlier is not None and later is not None and later.end >= earlier.end
    assert all(f.filed < filed_after for f in aapl.series("revenue", filed_after))


def test_ttm_is_annual_plus_ytd_minus_prior_ytd() -> None:
    def fact(start: str, end: str, value: float, filed: str) -> Fact:
        return Fact("us-gaap:Revenues", "USD", date.fromisoformat(start), date.fromisoformat(end), value, "10-Q",
                    date.fromisoformat(filed), None, None, filed)
    rows = [fact("2024-01-01", "2024-12-31", 100.0, "2025-02-15"),
            fact("2024-01-01", "2024-06-30", 45.0, "2024-08-01"),
            fact("2025-01-01", "2025-06-30", 60.0, "2025-08-01")]
    assert ttm(rows, date(2025, 8, 1)).value == 100.0                       # the YTD filed that day is not known yet
    flow = ttm(rows, date(2025, 8, 2))
    assert flow.value == 115.0 and flow.end == date(2025, 6, 30) and flow.source == "annual+ytd"


def test_real_company_metrics_are_plausible() -> None:
    m = metrics(facts("AAPL"), date(2026, 10, 21), 338.0)
    assert 3e12 < m["market_cap"] < 7e12 and 20 < m["pe"] < 60 and 0.4 < m["gross_margin"] < 0.55
    assert m["piotroski"] is not None and 0 <= m["piotroski"] <= 9 and m["altman_z"] > 3
    bank = metrics(facts("JPM"), date(2026, 10, 21), 300.0, financial="bank")
    assert bank["ev_ebit"] is None and bank["p_fcf"] is None and bank["piotroski"] is None and 0.05 < bank["roe"] < 0.3
    reit = metrics(facts("O"), date(2026, 10, 21), 58.0, financial="reit")
    assert reit["p_ffo"] is not None and 5 < reit["p_ffo"] < 40
    before_any = metrics(facts("AAPL"), date(2019, 1, 1), 100.0)
    assert before_any["revenue"] is None and before_any["pe"] is None                  # fixture starts mid-2020


def test_reverse_dcf_inverts_the_dcf() -> None:
    ev = dcf_value(100.0, 0.08, 0.10)
    g = implied_growth(ev, 100.0, 0.10)
    assert g is not None
    flat = 0.0
    total, cash = 0.0, 100.0
    for t in range(1, 11):
        cash *= 1 + g
        total += cash / 1.1 ** t
    flat = total + cash * 1.025 / (0.10 - 0.025) / 1.1 ** 10
    assert flat == pytest.approx(ev, rel=1e-6)
    assert implied_growth(1000.0, -5.0, 0.1) is None


# ── prices ────────────────────────────────────────────────────────────────────────────────────────
def test_splits_adjust_history_when_read_and_gaps_stay_missing(dbs: StocksDatabases) -> None:
    market = StocksMarket(dbs.market)
    days = cal.trading_days(date(2026, 10, 12), date(2026, 10, 16))
    for i, d in enumerate(days):
        bars = [Bar("SPLT", d, 100.0, 100.0, 100.0, 100.0 if i < 3 else 50.0, 1000)]
        if i != 1:
            bars.append(Bar("GAP", d, 10, 10, 10, 10, 1))
        market.save_bars(d, bars, "test", NOW)
    market.save_splits([Split("SPLT", days[3], 1, 2)], NOW)
    panel = load_panel(market, days[0])
    s = panel.series("SPLT")
    assert list(s) == [50.0, 50.0, 50.0, 50.0, 50.0]
    assert panel.series("SPLT", "volume")[0] == 2000
    assert math.isnan(panel.series("GAP")[1])
    raw = load_panel(market, days[0], adjust=False)
    assert raw.series("SPLT")[0] == 100.0                      # stored bars were never rewritten
    f = split_factors([Split("X", date(2026, 10, 14), 1, 4)], ["X"], days)
    assert list(f[0]) == [4, 4, 1, 1, 1]
    assert panel.day_index(date(2026, 10, 18)) == 4 and panel.day_index(date(2026, 1, 1)) == -1


# ── options ───────────────────────────────────────────────────────────────────────────────────────
def test_option_pricing_greeks_and_implied_volatility() -> None:
    s, t, r = 42.18, 32 / 365, 0.043
    price = opt.bs_price("call", s, 45, t, 0.58, r)
    assert price == pytest.approx(1.84, abs=0.01)
    assert opt.implied_vol("call", price, s, 45, t, r) == pytest.approx(0.58, abs=1e-4)
    call, put = opt.greeks("call", s, 45, t, 0.58, r), opt.greeks("put", s, 45, t, 0.58, r)
    assert call.delta == pytest.approx(0.39, abs=0.01) and call.delta - put.delta == pytest.approx(1.0, abs=1e-9)
    parity = opt.bs_price("call", s, 45, t, 0.58, r) - opt.bs_price("put", s, 45, t, 0.58, r)
    assert parity == pytest.approx(s - 45 * math.exp(-r * t), abs=1e-9)
    assert opt.implied_vol("call", 0.0001, s, 30, t, r) is None                 # below intrinsic value


def test_strategy_profiles_and_the_degen_check() -> None:
    s, t, r = 42.18, 32 / 365, 0.043
    c45, c50 = opt.bs_price("call", s, 45, t, 0.58, r), opt.bs_price("call", s, 50, t, 0.58, r)
    single = opt.strategy_profile([opt.Leg("call", 45, None, 1, c45)], s, t, 0.58, r)
    assert single["max_profit"] is None and single["max_loss"] == pytest.approx(-c45 * 100)
    assert single["breakevens"][0] == pytest.approx(45 + c45, abs=0.02)
    assert single["chance_of_profit"] == pytest.approx(opt.prob_above(s, 45 + c45, t, 0.58, r), abs=0.01)
    spread = opt.strategy_profile([opt.Leg("call", 45, None, 1, c45), opt.Leg("call", 50, None, -1, c50)], s, t, 0.58, r)
    assert spread["max_profit"] == pytest.approx((5 - c45 + c50) * 100, rel=1e-3)
    assert spread["max_loss"] == pytest.approx(-(c45 - c50) * 100, rel=1e-3)
    assert opt.degen_check(0.08, 3, 24.0) is not None and opt.degen_check(0.5, 30, 300.0) is None


def test_chain_summary_finds_the_expected_move_and_unusual_volume() -> None:
    today, exp, s, r = date(2026, 10, 21), date(2026, 11, 20), 100.0, 0.04
    t = opt.year_fraction(today, exp)
    quotes = []
    for k in range(80, 125, 5):
        for kind in ("call", "put"):
            v = 0.30 + (0.002 * (100 - k) if kind == "put" else 0.0)
            mid = opt.bs_price(kind, s, k, t, v, r)
            vol = 9000 if (kind, k) == ("call", 110) else 200
            quotes.append(OptionQuote(f"X{k}{kind[0]}", "X", kind, float(k), exp, mid * 0.98, mid * 1.02, mid, vol, 1000,
                                      800, None))
    summary = opt.daily_summary({exp: quotes}, s, today, r)
    [e] = summary["expiries"]
    assert e["atm_iv"] == pytest.approx(0.30, abs=0.01)
    assert e["expected_move"] == pytest.approx(0.8 * 0.30 * math.sqrt(t), rel=0.06)       # straddle ≈ 0.8 σ√t
    assert e["skew_25d"] is not None and e["skew_25d"] > 0                                 # puts priced richer
    assert summary["unusual"][0]["strike"] == 110.0 and summary["unusual"][0]["ratio"] > 10
    assert opt.iv_rank([0.2] * 10 + [0.4] * 10, 0.3) == pytest.approx((50.0, 50.0))
    closes = list(np.linspace(100, 130, 200))
    assert opt.realized_move_frequency(closes, 20, 0.5) == 0.0
