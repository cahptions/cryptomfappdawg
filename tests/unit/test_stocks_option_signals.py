"""Option-implied stock signals (desk.stocks.option_signals): each measure on synthetic chains with known answers, the
measures that need earlier sessions read point in time, missing data recorded as missing, and the resumable backfill
from stored chains."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import option_signals as osg
from desk.stocks import options as opt
from desk.stocks.contracts import OptionQuote
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO

DAY = date(2026, 11, 2)
S, R = 100.0, 0.04
STRIKES = [70 + 2.5 * i for i in range(25)]          # 70 … 130


def chain(day: date = DAY, *, s: float = S, days: tuple[int, ...] = (30, 90),
          iv: Callable[[str, float, int], float] = lambda kind, k, d: 0.30,
          oi: Callable[[float], int] = lambda k: 500, volume: int = 40,
          half_spread: float = 0.02) -> dict[date, list[OptionQuote]]:
    """Quotes priced exactly at Black-Scholes with ``iv(kind, strike, days)``, bid/ask ``half_spread`` around."""
    quoted = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=21)
    out: dict[date, list[OptionQuote]] = {}
    for d in days:
        expiry = day + timedelta(days=d)
        quotes = []
        for k in STRIKES:
            for kind in ("call", "put"):
                mid = opt.bs_price(kind, s, k, opt.year_fraction(day, expiry), iv(kind, k, d), R)
                if mid < 0.05:
                    continue
                occ = f"XYZ{expiry:%y%m%d}{kind[0].upper()}{round(k * 1000):08d}"
                quotes.append(OptionQuote(occ, "XYZ", kind, k, expiry, mid * (1 - half_spread), mid * (1 + half_spread),
                                          mid, volume, oi(k), None, quoted))
        out[expiry] = quotes
    return out


def test_flat_smile_has_no_spread_no_skew_and_its_level_everywhere() -> None:
    m = osg.measure(chain(), S, DAY, R)
    assert m["iv_spread"] == pytest.approx(0.0, abs=1e-4)
    assert m["skew"] == pytest.approx(0.0, abs=1e-4)
    assert m["iv30"] == pytest.approx(0.30, abs=1e-4) and m["iv90"] == pytest.approx(0.30, abs=1e-4)
    assert m["call_iv30"] == pytest.approx(0.30, abs=1e-4) and m["put_iv30"] == pytest.approx(0.30, abs=1e-4)
    assert m["pairs"] > osg.MIN_PAIRS and m["liquid"] == m["contracts"] and not m["detail"]["missing"]


def test_calls_richer_than_puts_give_the_spread_weighted_by_open_interest() -> None:
    # calls 2 points richer at every strike: the spread is exactly 0.02 whatever the weights
    m = osg.measure(chain(iv=lambda kind, k, d: 0.32 if kind == "call" else 0.30), S, DAY, R)
    assert m["iv_spread"] == pytest.approx(0.02, abs=1e-4)
    # one strike's calls richer: its open interest decides how much it moves the average
    def one_rich(kind: str, k: float, d: int) -> float:
        return 0.34 if kind == "call" and k == S else 0.30
    m = osg.measure(chain(iv=one_rich, oi=lambda k: 900 if k == S else 100, days=(30,)), S, DAY, R)
    pairs = m["pairs"]
    assert m["iv_spread"] == pytest.approx(0.04 * 900 / (900 + 100 * (pairs - 1)), abs=1e-4)


def test_expensive_out_of_the_money_puts_are_the_skew() -> None:
    def smile(kind: str, k: float, d: int) -> float:
        return 0.40 if kind == "put" and k < 0.97 * S else 0.30
    m = osg.measure(chain(iv=smile), S, DAY, R)
    assert m["skew"] == pytest.approx(0.10, abs=1e-4)
    legs = m["detail"]["skew_legs"]
    assert legs["days"] == 30 and legs["call_strike"] == 100.0 and legs["put_strike"] < 97
    assert osg.PUT_DELTA[0] <= abs(legs["put_delta"]) <= osg.PUT_DELTA[1]


def test_term_slope_and_constant_maturity() -> None:
    m = osg.measure(chain(iv=lambda kind, k, d: 0.30 if d == 30 else 0.36), S, DAY, R)
    assert m["iv30"] == pytest.approx(0.30, abs=1e-4) and m["iv90"] == pytest.approx(0.36, abs=1e-4)
    # between the expiries: interpolated in total variance
    assert osg.at_days([(20, 0.3), (40, 0.4)], 30, 12) == pytest.approx(math.sqrt((0.09 * 20 + 0.16 * 40) / 2 / 30))
    assert osg.at_days([(45, 0.3)], 30, 12) is None and osg.at_days([(40, 0.3)], 30, 12) == 0.3


def test_option_volume_counts_expiries_within_120_days() -> None:
    m = osg.measure(chain(days=(30, 90, 200), volume=10), S, DAY, R)
    near = sum(len(q) for e, q in chain(days=(30, 90)).items())
    assert m["option_volume"] == near * 10


def test_wide_or_tiny_quotes_are_not_used_and_missing_says_why() -> None:
    m = osg.measure(chain(half_spread=0.4), S, DAY, R)        # (ask − bid) / mid = 0.8
    assert m["liquid"] == 0 and m["iv_spread"] is None and m["skew"] is None and m["iv30"] is None
    assert set(m["detail"]["missing"]) >= {"iv_spread", "skew", "iv30", "iv90"}
    assert "pairs" in m["detail"]["missing"]["iv_spread"]
    # no open interest: pairs exist but carry no weight
    m = osg.measure(chain(oi=lambda k: 0), S, DAY, R)
    assert m["iv_spread"] is None and "open interest" in m["detail"]["missing"]["iv_spread"]


def test_borrow_from_a_schwab_quote() -> None:
    assert osg.borrow_of({"borrow_rate": 7.5, "hard_to_borrow": True}) == (7.5, 1)
    assert osg.borrow_of({"hard_to_borrow": False}) == (None, 0)
    assert osg.borrow_of({}) == (None, None) and osg.borrow_of(None) == (None, None)


# ── stored rows and the measures that need earlier sessions ──────────────────────────────────────
@pytest.fixture
def market(tmp_path: Path) -> Iterator[StocksMarket]:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    try:
        yield StocksMarket(dbs.market)
    finally:
        dbs.close()


SESSIONS = cal.trading_days(date(2026, 7, 1), date(2026, 11, 30))


def store(market: StocksMarket, symbol: str, day: date, *, call: float = 0.3, put: float = 0.3, volume: int = 1000,
          shares: float = 1e6, borrow: tuple[float | None, int | None] = (None, None),
          computed: datetime | None = None, liquid: int = 50) -> None:
    m: dict[str, Any] = {"iv_spread": call - put, "pairs": 10, "skew": 0.05, "call_iv30": call, "put_iv30": put,
                         "iv30": (call + put) / 2, "iv90": (call + put) / 2 + 0.02, "option_volume": volume,
                         "contracts": 60, "liquid": liquid, "detail": {"missing": {}}}
    at = computed or datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=22)
    osg.save(market.conn, symbol, day, "scan", m, price=S, stock_volume=shares, borrow=borrow, rv22=0.25,
             observed_at=at, now=at)


def test_changes_compare_with_about_five_sessions_earlier(market: StocksMarket) -> None:
    days = SESSIONS[:30]
    for i, d in enumerate(days):
        store(market, "XYZ", d, call=0.30 + 0.01 * i, put=0.40 - 0.002 * i)
    got = osg.read(market.conn, days[-1])["XYZ"]
    assert got["d_call_iv"] == pytest.approx(0.05) and got["d_put_iv"] == pytest.approx(-0.01)
    assert got["term_slope"] == pytest.approx(0.02) and got["iv_rv"] == pytest.approx(got["iv30"] / 0.25, rel=1e-4)
    assert got["iv_spread"] == pytest.approx(0.30 + 0.29 - 0.40 + 0.058, abs=1e-6)
    # with no row 4-8 sessions back the change is missing, not zero
    market.conn.execute("DELETE FROM stk_option_signals WHERE day<? AND day>=?", (days[-1].toordinal(),
                                                                                 days[-9].toordinal()))
    got = osg.read(market.conn, days[-1])["XYZ"]
    assert got["d_call_iv"] is None and got["d_put_iv"] is None


def test_option_to_stock_volume_against_its_own_median(market: StocksMarket) -> None:
    days = SESSIONS[:40]
    for d in days[:-1]:
        store(market, "XYZ", d, volume=1000, shares=1e6)            # O/S 0.1
    store(market, "XYZ", days[-1], volume=3000, shares=1e6)         # O/S 0.3: three times its usual
    got = osg.read(market.conn, days[-1])["XYZ"]
    assert got["os_ratio"] == pytest.approx(0.3) and got["os_abnormal"] == pytest.approx(math.log(3), rel=1e-4)
    # too little history for a median: the abnormal measure is missing, the level is not
    got = osg.read(market.conn, days[osg.OS_MIN - 1])["XYZ"]
    assert got["os_ratio"] == pytest.approx(0.1) and got["os_abnormal"] is None


def test_reading_a_past_day_never_sees_a_later_one(market: StocksMarket) -> None:
    days = SESSIONS[:30]
    for i, d in enumerate(days):
        store(market, "XYZ", d, call=0.30 + 0.01 * i, volume=1000 + i)
    asof = days[20]
    before = osg.read(market.conn, asof)
    for d in SESSIONS[30:45]:                                        # wild values after the day
        store(market, "XYZ", d, call=5.0, put=0.01, volume=10**9, shares=1.0, borrow=(99.0, 1))
    assert osg.read(market.conn, asof) == before
    assert before["XYZ"]["day"] == asof.isoformat()
    # rows written after a moment are invisible to a read as of that moment
    late = datetime.combine(SESSIONS[50], datetime.min.time(), UTC)
    store(market, "LATE", SESSIONS[50], computed=late + timedelta(days=1))
    assert "LATE" not in osg.read(market.conn, SESSIONS[50], computed_by=late)


def test_stale_thin_and_missing_rows(market: StocksMarket) -> None:
    store(market, "OLD", SESSIONS[0])
    store(market, "THIN", SESSIONS[10], liquid=osg.MIN_LIQUID - 1)
    store(market, "EASY", SESSIONS[10], borrow=(None, 0))
    store(market, "HARD", SESSIONS[10], borrow=(12.5, 1))
    got = osg.read(market.conn, SESSIONS[10])
    assert "OLD" not in got and "THIN" not in got
    assert got["EASY"]["borrow"] == 0.0 and got["HARD"]["borrow"] == 12.5 and got["HARD"]["hard_to_borrow"] is True
    store(market, "NONE", SESSIONS[10])
    assert osg.read(market.conn, SESSIONS[10])["NONE"]["borrow"] is None


def test_percentiles_rank_among_stocks_with_the_signal() -> None:
    p = osg.percentiles({"A": {"skew": 0.1}, "B": {"skew": 0.3}, "C": {"skew": 0.2}, "D": {"skew": None}})
    assert p["A"]["skew"] == 0.0 and p["C"]["skew"] == 0.5 and p["B"]["skew"] == 1.0 and "skew" not in p.get("D", {})


def test_backfill_from_stored_chains_is_resumable(market: StocksMarket) -> None:
    days = SESSIONS[:3]
    for d in days:
        at = cal.session_close(d) - timedelta(minutes=30)
        for expiry, quotes in chain(d).items():
            market.save_option_chain("XYZ", d, expiry, at, S, quotes, source="schwab")
        market.conn.execute("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                            ("XYZ", d.toordinal(), S, S, S, S, 2e6))
    during = cal.session_close(days[-1]) - timedelta(hours=2)          # the last session still trading
    assert osg.backfill(market, during, limit=1)["computed"] == 1
    assert osg.pending(market.conn, cal.last_closed_session(during, osg.SETTLE)) == [("XYZ", days[0].toordinal())]
    now = cal.session_close(days[-1]) + timedelta(hours=1)
    assert osg.pending(market.conn, days[-1])[0] == ("XYZ", days[-1].toordinal())       # newest first
    second = osg.backfill(market, now)
    assert second["computed"] == 2 and second["remaining"] == 0 and second["rows"] == 3
    assert osg.backfill(market, now)["computed"] == 0                                     # nothing asked twice
    row = market.conn.execute("SELECT * FROM stk_option_signals WHERE day=?", (days[0].toordinal(),)).fetchone()
    assert row["source"] == "collected" and row["stock_volume"] == 2e6 and row["borrow_rate"] is None
    assert row["iv30"] == pytest.approx(0.30, abs=1e-3)
