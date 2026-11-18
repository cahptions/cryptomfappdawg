"""The earnings model: point-in-time report dates and timing, the results-day move options price (Dubinsky–Johannes),
past moves (timing-aware, split-adjusted), the expected move and its verdict, the daily job, Plays' Earnings section
and the stock page's block. Synthetic worlds and chains only."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from desk.stocks import calendar as cal
from desk.stocks import earnings_dates as ed
from desk.stocks import earnings_model as em
from desk.stocks import options as opt
from desk.stocks import plays as pl
from desk.stocks.contracts import OptionQuote
from desk.stocks.repo import day_of
from tests.stocks_world import ASOF, NOW, StocksWorld, make_world, occ

NY = cal.NEW_YORK
VOL = 0.30                                    # ordinary annualized volatility of the synthetic chains
NORMAL = VOL * VOL / 252                      # an ordinary session's variance


def fridays(after: date, n: int) -> list[date]:
    first = after + timedelta(days=(4 - after.weekday()) % 7 or 7)
    return [first + timedelta(days=7 * k) for k in range(n)]


def event_chain(w: StocksWorld, symbol: str, day: date, observed: datetime, s0: float, exit_: date, *,
                event: float = 0.06, expiries: list[date] | None = None, spread: float = 0.04) -> None:
    """A chain whose expiries price ``NORMAL`` a session plus a results day of standard deviation ``event`` on
    ``exit_``: total variance v·n, and v·(n − 1) + event² for expiries on or after the results."""
    rate = pl._rate(w.market, day)
    step = 1.0 if s0 < 100 else 2.5
    strikes = [round(k * step, 2) for k in range(int(s0 * 0.75 / step), int(s0 * 1.25 / step) + 1)]
    for e in expiries or fridays(day, 5):
        n, t = em.sessions_to(day, e), opt.year_fraction(day, e)
        total = NORMAL * n + (event * event - NORMAL if e >= exit_ else 0.0)
        iv = math.sqrt(total / t)
        quotes = []
        for kind in ("call", "put"):
            for k in strikes:
                mid = opt.bs_price(kind, s0, k, t, iv, rate)
                if mid < 0.05:
                    continue
                half = max(mid * spread / 2, 0.01)
                quotes.append(OptionQuote(occ(symbol, e, kind, k), symbol, kind, k, e, round(mid - half, 2),
                                          round(mid + half, 2), round(mid, 2), 800, 5000, 700, observed))
        w.market.save_option_chain(symbol, day, e, observed, s0, quotes)


def close_on(w: StocksWorld, symbol: str, day: date) -> float:
    return float(w.market.conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?",
                                       (symbol, day_of(day))).fetchone()[0])


def past_moves(w: StocksWorld, symbol: str, moves: list[float], *, implied: float | None = None) -> None:
    """Stored past reports (oldest first) ending before ASOF, as the daily job would have written them."""
    for k, m in enumerate(reversed(moves)):
        report = date(2026, 7, 30) - timedelta(days=91 * k)
        entry, exit_ = ed.sessions(report, ed.AFTER)
        w.market.conn.execute("INSERT OR REPLACE INTO stk_earnings_moves VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (symbol, day_of(report), ed.AFTER, "sec_8k", day_of(entry), day_of(exit_), m, implied,
                               None, None, None, None, em.VERSION, None, NOW.isoformat()))


EXPECT = math.sqrt(2 / math.pi)


def test_timing_from_the_acceptance_time_and_the_sessions_it_moves() -> None:
    wed = date(2026, 10, 28)
    at = lambda h, m=0: datetime.combine(wed, time(h, m), NY).astimezone(UTC)   # noqa: E731
    assert ed.timing_of(at(7, 5), wed) == (wed, ed.BEFORE)
    assert ed.timing_of(at(12), wed) == (wed, ed.DURING)
    assert ed.timing_of(at(16, 5), wed) == (wed, ed.AFTER)
    saturday = datetime.combine(date(2026, 10, 31), time(10), NY).astimezone(UTC)
    assert ed.timing_of(saturday, date(2026, 10, 31)) == (date(2026, 11, 2), ed.BEFORE)
    assert ed.timing_of(None, wed) == (wed, None)
    tue, thu = date(2026, 10, 27), date(2026, 10, 29)
    assert ed.sessions(wed, ed.BEFORE) == (tue, wed) and ed.sessions(wed, ed.DURING) == (tue, wed)
    assert ed.sessions(wed, ed.AFTER) == (wed, thu)
    assert ed.sessions(wed, None) == (tue, thu)                           # unknown timing: both sessions around it
    assert ed.sessions(date(2026, 10, 31), ed.AFTER) == (date(2026, 10, 30), date(2026, 11, 2))


def test_the_calendar_is_point_in_time_and_keeps_confirmed_and_estimated_apart(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        ed.save(conn, [{"symbol": "S10", "date": "2026-10-28", "when": ed.AFTER}], {"S10"}, NOW - timedelta(days=2))
        early = ed.known(conn, ASOF, NOW - timedelta(days=3))
        assert early["S10"]["confirmed"] is False and early["S10"]["source"] == ed.SEC_LABEL   # not seen yet
        ed.save(conn, [{"symbol": "S10", "date": "2026-10-28", "when": ed.AFTER}], {"S10"}, NOW)
        now = ed.known(conn, ASOF, NOW)
        assert now["S10"] | {"seen": None} == {
            "date": "2026-10-28", "to": "2026-10-28", "confirmed": True, "timing": ed.AFTER, "source": ed.LABEL,
            "seen": None, "entry": "2026-10-28", "exit": "2026-10-29"}
        # S11 has no announced date: estimated from its results filings (every ~91 days, last on Jul 30), a window
        guess = now["S11"]
        assert guess["confirmed"] is False and guess["timing"] is None and guess["date"] < guess["to"]
        assert date.fromisoformat(guess["date"]) <= date(2026, 10, 29) <= date.fromisoformat(guess["to"])
        # the next day the date moves to Oct 29 before the open: the old date drops out from then on, not before
        later = NOW + timedelta(days=1)
        ed.save(conn, [{"symbol": "S10", "date": "2026-10-29", "when": ed.BEFORE}], {"S10"}, later)
        moved = ed.known(conn, ASOF, later + timedelta(hours=1))["S10"]
        assert (moved["date"], moved["entry"], moved["exit"]) == ("2026-10-29", "2026-10-28", "2026-10-29")
        assert ed.known(conn, ASOF, NOW)["S10"]["date"] == "2026-10-28"     # what was known then stays known
    finally:
        w.close()


def test_past_moves_are_timing_aware_and_split_adjusted(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        report = date(2026, 7, 30)                                         # accepted 4:30 PM: after the close
        entry, exit_ = ed.sessions(report, ed.AFTER)
        a, b = close_on(w, "S10", entry), close_on(w, "S10", exit_)
        assert em.realized_move(conn, "S10", entry, exit_) == (pytest.approx(b / a - 1), None)
        # a 2-for-1 split on the exit session: its close is per new share, so the entry close counts as a / 2
        conn.execute("INSERT INTO stk_splits VALUES ('S10', ?, 1, 2, ?)", (day_of(exit_), NOW.isoformat()))
        assert em.realized_move(conn, "S10", entry, exit_)[0] == pytest.approx(b / (a / 2) - 1)
        before = ed.sessions(report, ed.BEFORE)                            # the same day before the open
        assert before == (date(2026, 7, 29), report)
        missing = em.realized_move(conn, "S10", date(2020, 1, 2), date(2020, 1, 3))
        assert missing[0] is None and "No stored close" in str(missing[1])
    finally:
        w.close()


def atm(day: date, e: date, *, exit_: date, event: float) -> em.Atm:
    n, t = em.sessions_to(day, e), opt.year_fraction(day, e)
    total = NORMAL * n + (event * event - NORMAL if e >= exit_ else 0.0)
    return em.Atm(e, n, t, math.sqrt(total / t), 0.0, 100.0)


def test_the_priced_move_separates_the_results_day_with_an_expiry_before_it() -> None:
    day, entry, exit_ = date(2026, 10, 21), date(2026, 10, 28), date(2026, 10, 29)
    points = [atm(day, e, exit_=exit_, event=0.06) for e in fridays(day, 4)]
    got = em.implied(points, 100.0, entry, exit_)
    assert got["basis"] == "pre_expiry" and got["expiry"] == "2026-10-30"
    assert got["sigma"] == pytest.approx(0.06, rel=1e-4) and got["move"] == pytest.approx(0.06 * EXPECT, rel=1e-4)
    assert got["normal"] == pytest.approx(math.sqrt(NORMAL), rel=1e-4)


def test_two_expiries_after_the_results_read_the_ordinary_day_and_gaps_say_why() -> None:
    day = entry = date(2026, 7, 30)
    exit_ = date(2026, 7, 31)
    points = [atm(day, e, exit_=exit_, event=0.08) for e in fridays(day, 3)]
    got = em.implied(points, 100.0, entry, exit_)
    assert got["basis"] == "post_expiries" and got["move"] == pytest.approx(0.08 * EXPECT, rel=1e-4)
    assert em.implied(points[:1], 100.0, entry, exit_)["note"].startswith("Needs an expiry before the results")
    assert em.implied([], 100.0, entry, exit_)["note"].startswith("No expiry after the results")
    calm = [atm(day, e, exit_=exit_, event=0.0) for e in fridays(day, 3)]     # a results day priced at zero
    assert em.implied(calm, 100.0, entry, exit_)["note"] == "The expiries price no extra move for the results day"
    inverted = [em.Atm(p.expiry, p.sessions, p.years, iv, 0.0, 100.0)
                for p, iv in zip(points, (0.9, 0.2, 0.1), strict=True)]
    assert "less variance" in em.implied(inverted, 100.0, entry, exit_)["note"]


def test_a_priced_chain_gives_the_move_and_the_straddle_of_the_first_expiry_after(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        s0 = close_on(w, "S10", ASOF)
        exit_ = date(2026, 10, 29)
        event_chain(w, "S10", ASOF, NOW, s0, exit_, event=0.07)
        _, chains = w.market.option_chain("S10", ASOF)
        points = em.atm_points(chains, s0, ASOF, pl._rate(w.market, ASOF))
        got = em.implied(points, s0, date(2026, 10, 28), exit_)
        assert got["move"] == pytest.approx(0.07 * EXPECT, rel=0.06)
        first = next(p for p in points if p.expiry >= exit_)
        assert got["straddle"] == pytest.approx(first.straddle / s0, rel=1e-3)
        assert got["straddle"] > got["move"]                              # the straddle also prices the other days
    finally:
        w.close()


def test_expected_move_verdict_and_confidence() -> None:
    e = em.edge([0.10, -0.08, 0.12, -0.10], 0.05, 1.0)                   # n = 4: half the stock's own record
    assert e["typical"] == pytest.approx(0.10) and e["weight"] == pytest.approx(0.5)
    assert e["expected"] == pytest.approx(0.075) and e["ratio"] == pytest.approx(1.5) and e["verdict"] == "cheap"
    assert em.edge([0.02] * 8, 0.05, 1.0)["verdict"] == "expensive"
    assert em.edge([0.05, 0.06, 0.04, 0.05], 0.05, 1.0)["verdict"] == "fair"
    few = em.edge([0.2, 0.2, 0.2], 0.05, 1.0)
    assert few["verdict"] is None and few["expected"] is not None and "needs 4" in few["note"]
    assert em.edge([0.10] * 4, None, 1.0)["note"].startswith("No priced move")
    # prior: with no own record the expected move is the priced move × the sector's ratio
    assert em.edge([], 0.05, 1.3)["expected"] == pytest.approx(0.065)
    # confidence: a long, consistent record far from the priced move is high; a short one is low
    assert em.edge([0.10, 0.11] * 4, 0.05, 1.0)["confidence"] == "high"
    assert em.edge([0.10, 0.02, 0.15, 0.09], 0.05, 1.0)["confidence"] == "low"


def test_the_prior_is_the_sectors_ratio_else_all_stocks_else_one(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        assert em.Priors(conn, ASOF).ratio("S10")[0] == 1.0
        past_moves(w, "S09", [0.06] * 6, implied=0.04)                     # another sector: all stocks 1.5
        got = em.Priors(conn, ASOF).ratio("S10")
        assert got[0] == pytest.approx(1.5) and got[1].startswith("all stocks")
        sector = em.sectors(conn)["S10"]
        same = [s for s, sec in em.sectors(conn).items() if sec == sector and s != "S10"]
        past_moves(w, same[0], [0.03] * em.SECTOR_MIN, implied=0.04)       # 20 reports in S10's sector: 0.75
        got = em.Priors(conn, ASOF).ratio("S10")
        assert got[0] == pytest.approx(0.75) and got[1].startswith(f"{sector} sector")
        assert em.Priors(conn, date(2020, 1, 1)).ratio("S10")[0] == 1.0    # nothing known before then
    finally:
        w.close()


def test_the_daily_job_stores_realized_and_priced_moves_resumably(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        entry = date(2026, 7, 30)                                         # S10's last report, after the close
        by_close = datetime.combine(entry, time(15, 50), NY)
        event_chain(w, "S10", entry, by_close, close_on(w, "S10", entry), date(2026, 7, 31), event=0.07)
        late = datetime.combine(entry, time(17, 0), NY)                   # after 4:15 PM: not the chain before results
        event_chain(w, "S07", entry, late, close_on(w, "S07", entry), date(2026, 7, 31))
        total = len(em.pending(conn, ASOF))
        first = em.backfill(w.market, NOW, limit=5)
        assert first["computed"] == 5 and first["remaining"] == total - 5 > 0
        rest = em.backfill(w.market, NOW)
        assert rest["computed"] == total - 5 and rest["remaining"] == 0 and rest["rows"] == total
        assert em.backfill(w.market, NOW)["computed"] == 0
        row = conn.execute("SELECT * FROM stk_earnings_moves WHERE symbol='S10' AND report_date=?",
                           (day_of(entry),)).fetchone()
        assert (row["timing"], row["source"], row["entry_day"], row["exit_day"]) == (
            ed.AFTER, "sec_8k", day_of(entry), day_of(date(2026, 7, 31)))
        a, b = close_on(w, "S10", entry), close_on(w, "S10", date(2026, 7, 31))
        assert row["realized"] == pytest.approx(b / a - 1, rel=1e-4)
        assert row["implied"] == pytest.approx(0.07 * EXPECT, rel=0.06) and row["basis"] == "post_expiries"
        assert row["straddle"] > 0 and row["chain_observed_at"].startswith("2026-07-30T19:50")
        other = conn.execute("SELECT implied, note FROM stk_earnings_moves WHERE symbol='S07' AND report_date=?",
                             (day_of(entry),)).fetchone()
        assert other["implied"] is None and "No chain stored" in other["note"]
        assert first["rows"] == 5 and rest["with_implied"] == 1
    finally:
        w.close()


def test_no_look_ahead(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        s0 = close_on(w, "S10", ASOF)
        ed.save(conn, [{"symbol": "S10", "date": "2026-10-28", "when": ed.AFTER}], {"S10"}, NOW - timedelta(days=1))
        event_chain(w, "S10", ASOF, NOW, s0, date(2026, 10, 29))
        past_moves(w, "S10", [0.08, -0.07, 0.09, -0.06, 0.1], implied=0.05)
        _, chains = w.market.option_chain("S10", ASOF)
        points = em.atm_points(chains, s0, ASOF, pl._rate(w.market, ASOF))

        def seen() -> tuple[dict, dict | None]:
            known = ed.known(conn, ASOF, NOW)
            return known, em.outlook(conn, "S10", ASOF, known["S10"], points, s0, em.Priors(conn, ASOF))

        before = seen()
        # wild later data: a past report whose exit is the decision day, a calendar date first seen later,
        # a results filing accepted later, and huge priced moves on reports after the decision
        conn.execute("INSERT INTO stk_earnings_moves VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("S10", day_of(date(2026, 10, 20)), ed.AFTER, "sec_8k", day_of(date(2026, 10, 20)), day_of(ASOF),
                      0.9, 0.01, None, None, None, None, em.VERSION, None, NOW.isoformat()))
        conn.execute("INSERT INTO stk_earnings_moves VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("S11", day_of(date(2026, 11, 5)), ed.AFTER, "sec_8k", day_of(date(2026, 11, 5)),
                      day_of(date(2026, 11, 6)), 0.9, 0.01, None, None, None, None, em.VERSION, None,
                      NOW.isoformat()))
        ed.save(conn, [{"symbol": "S10", "date": "2026-10-23", "when": ed.BEFORE},
                       {"symbol": "S11", "date": "2026-10-26", "when": ed.BEFORE}], {"S10", "S11"},
                NOW + timedelta(hours=2))
        cik = conn.execute("SELECT cik FROM stk_listings WHERE symbol='S11'").fetchone()[0]
        conn.execute("INSERT INTO stk_filings VALUES ('late-8k', ?, '8-K', ?, ?, NULL, '2.02,9.01', 'x.htm', ?)",
                     (cik, day_of(ASOF + timedelta(days=1)), (NOW + timedelta(days=1)).isoformat(), NOW.isoformat()))
        assert seen() == before
        assert before[1] is not None and before[1]["reports"] == 5 and before[1]["verdict"] is not None
    finally:
        w.close()


def earnings_data(tmp_path: Path) -> StocksWorld:
    """S10's options look cheap for its Oct 28 report, S11's expensive, S09's fairly priced (8 past reports each)."""
    w = make_world(tmp_path, n=12)
    ed.save(w.market.conn, [{"symbol": s, "date": "2026-10-28", "when": ed.AFTER} for s in ("S09", "S10", "S11")],
            {"S09", "S10", "S11"}, NOW - timedelta(days=1))
    for symbol, typical in (("S10", 0.11), ("S11", 0.012), ("S09", 0.048)):
        event_chain(w, symbol, ASOF, NOW, close_on(w, symbol, ASOF), date(2026, 10, 29), event=0.06)
        past_moves(w, symbol, [typical if k % 2 else -typical for k in range(8)])
    return w


def test_the_stock_page_block(tmp_path: Path) -> None:
    w = earnings_data(tmp_path)
    try:
        conn = w.market.conn
        view = em.view(conn, "S10", NOW)
        assert view is not None and view["title"] == "Options look cheap for this report"
        assert view["when"] == "Results on Oct 28, after the close (announced)"
        assert view["report"]["confirmed"] is True and view["report"]["timing"] == ed.AFTER
        assert view["implied"] < view["expected"] < view["typical"] and view["reports"] == 8
        assert view["text"].startswith("Options price a ±")
        assert em.view(conn, "S11", NOW)["title"] == "Options look expensive for this report"    # type: ignore[index]
        assert em.view(conn, "S09", NOW)["title"] == "Options look fairly priced for this report"  # type: ignore[index]
        assert em.view(conn, "S10", NOW + timedelta(days=em.FRESH_DAYS + 1)) is None     # the chain is too old
        assert em.view(conn, "S07", NOW) is None                              # no chain stored
    finally:
        w.close()
